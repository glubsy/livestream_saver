from typing import Iterable, Optional, Any, List, Dict, Union
from os import sep, makedirs, getcwd, environ, getenv
from sys import platform
from sys import argv
import argparse
import json
import logging
from pathlib import Path
from configparser import ConfigParser, ExtendedInterpolation
import traceback
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import re
from shlex import split
from time import sleep

from livestream_saver import extract, util
import livestream_saver
from livestream_saver.channel import YoutubeChannel, VideoPost
from livestream_saver.download import YoutubeLiveStream
from livestream_saver.merge import merge, get_metadata_info
from livestream_saver.util import get_channel_id, event_props
from livestream_saver.request import YoutubeUrllibSession
from livestream_saver.notifier import NotificationDispatcher, WebHookFactory
from livestream_saver.hooks import HookCommand

log = logging.getLogger('livestream_saver')
log.setLevel(logging.DEBUG)

NOTIFIER = NotificationDispatcher()


# HACK forcing use of yt-dlp for the time being.
use_ytdl = True


def parse_args(config) -> argparse.Namespace:
    parent_parser = argparse.ArgumentParser(
        description='Monitor a Youtube channel for any active live stream and \
record live streams from the first segment.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        argument_default=argparse.SUPPRESS,
        add_help=False
    )

    # Flags which are common to all sub-commands here
    log_levels = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
    parent_parser.add_argument('--log-level', action='store', type=str.upper,
        default=argparse.SUPPRESS,
        choices=log_levels,
        help=f'Log level. (Default: {config.get("DEFAULT", "log_level")})'
    )
    parent_parser.add_argument('-c', '--config-file',
        action='store', type=str,
        default=argparse.SUPPRESS,
        help='Path to config file to use.'\
             f' (Default: {config.get("DEFAULT", "config_file")})'
    )

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(
        dest='sub-command',
        help='Required sub-command.',
        required=True
    )

    # Sub-command "monitor"
    monitor_parser = subparsers.add_parser('monitor',
        help='Monitor a given Youtube channel for activity.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        parents=[parent_parser]
    )
    monitor_parser.set_defaults(func=monitor_mode)
    monitor_parser.add_argument('URL',
        nargs="?",
        default=None, type=str, metavar="YOUTUBE_CHANNEL_URL",
        help='The Youtube channel to monitor for live streams. \
Either a full youtube URL, /channel/ID, or /c/name format.'
    )
    monitor_parser.add_argument('--cookies',
        action='store', type=str, metavar="COOKIES_PATH",
        default=argparse.SUPPRESS,
        help='Path to Netscape formatted cookies file.'
    )
    monitor_parser.add_argument('-q', '--max-video-quality', action='store',
        default=argparse.SUPPRESS, type=int,
        help='Use best available video resolution up to this height in pixels.'\
             ' Example: "360" for maximum height 360p. Get the highest available'
             ' resolution by default.'
    )
    monitor_parser.add_argument('-o', '--output-dir', action='store',
        default=argparse.SUPPRESS, type=str,
        help='Output directory where to save channel data.'\
            f' (Default: {config.get("monitor", "output_dir", fallback=getcwd())})'
    )
    monitor_parser.add_argument('--channel-name', action='store',
        default=argparse.SUPPRESS, type=str,
        help='User-defined name of the channel to monitor. Will fallback to \
channel ID deduced from the URL otherwise.'
    )
    monitor_parser.add_argument('-s', '--section', action='store',
        default=None, type=str,
        help=(
            'Override values from the section [monitor NAME] found in config file.'
            ' If none is specified, will load the first section in config with that name pattern.'
        )
    )

    monitor_group = monitor_parser.add_mutually_exclusive_group()
    monitor_parser.add_argument('-d', '--delete-source',
        action='store_true',
        help='Delete source segment files once the final \
merging of them has been done.'
    )
    monitor_group.add_argument('-n', '--no-merge',
        action='store_true',
        help='Do not merge segments after live streams has ended.'
    )
    monitor_parser.add_argument('-k', '--keep-concat',
        action='store_true',
        help='Keep concatenated intermediary files even if \
merging of streams has been successful. Only useful for troubleshooting.'
    )
    # monitor_parser.add_argument('--interactive', action='store_true',
    #    help='Allow user input to skip the current download.')
    monitor_parser.add_argument('--scan-delay',
        action='store', type=float,
        default=argparse.SUPPRESS,
        help='Interval in minutes to scan for channel activity.'\
             f' (Default: {config.getfloat("monitor", "scan_delay")})'
    )
    monitor_parser.add_argument('--email-notifications',
        action='store_true',
        default=argparse.SUPPRESS,
        help='Enables sending e-mail reports to administrator.'\
            f' (Default: {config.getboolean("monitor", "email_notifications")})'
    )
    monitor_parser.add_argument('--skip-download',
        action='store_true',
        default=argparse.SUPPRESS,
        help='Skip the download phase (useful to run hook scripts instead).'\
            f' (Default: {config.getboolean("monitor", "skip_download")})'
    )
    monitor_parser.add_argument('--ignore-quality-change',
        action='store_true',
        default=argparse.SUPPRESS,
        help='If stream resolution changes during live-stream, keep downloading anyway.'\
            f' (Default: {config.getboolean("monitor", "ignore_quality_change")})'
    )


    # Sub-command "download"
    download_parser = subparsers.add_parser('download',
        help='Download a given live stream by URL.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        parents=[parent_parser]
    )
    download_parser.set_defaults(func=download_mode)
    download_parser.add_argument('URL',
        type=str, metavar="YOUTUBE_VIDEO_URL",
        help='Youtube video stream URL to download.'
    )
    download_parser.add_argument('--cookies',
        action='store', type=str, metavar="COOKIES_PATH",
        default=argparse.SUPPRESS,
        help='Path to Netscape formatted cookies file.'
    )
    download_parser.add_argument('-q', '--max-video-quality',
        action='store', type=int,
        default=argparse.SUPPRESS,
        help='Use best available video resolution up to this height in pixels.'\
             ' Example: "360" for maximum height 360p. Get the highest available'
             ' resolution by default.'
    )
    download_parser.add_argument('-o', '--output-dir',
        action='store', type=str,
        default=argparse.SUPPRESS,
        help='Output directory where to write downloaded chunks.'\
              f' (Default: {config.get("download", "output_dir", fallback=getcwd())})'
    )
    download_group = download_parser.add_mutually_exclusive_group()
    download_group.add_argument('-d', '--delete-source',
        action='store_true',
        help='Delete source files once final merge has been done.'
    )
    download_group.add_argument('-n', '--no-merge',
        action='store_true',
        help='Do not merge segments after live streams has ended.'
    )
    download_parser.add_argument('-k', '--keep-concat',
        action='store_true',
        help='Keep concatenated intermediary files even if merging of \
streams has been successful. Only useful for troubleshooting.'
    )
    download_parser.add_argument('--scan-delay',
        action='store', type=float,
        default=argparse.SUPPRESS,
        help='Interval in minutes to scan for status update.'\
             f' (Default: {config.getfloat("download", "scan_delay")})'
    )
    download_parser.add_argument('--email-notifications',
        action='store_true',
        default=argparse.SUPPRESS,
        help='Enable sending e-mail reports to administrator.'\
            f' (Default: {config.getboolean("download", "email_notifications")})'
    )
    download_parser.add_argument('--skip-download',
        action='store_true',
        default=argparse.SUPPRESS,
        help='Skip the download phase (useful to run hook scripts instead).'\
            f' (Default: {config.getboolean("download", "skip_download")})'
    )
    download_parser.add_argument('--ignore-quality-change',
        action='store_true',
        default=argparse.SUPPRESS,
        help='If stream resolution changes during live-stream, keep downloading anyway.'\
            f' (Default: {config.getboolean("download", "ignore_quality_change")})'
    )

    # Sub-command "merge"
    merge_parser = subparsers.add_parser('merge',
        help='Merge downloaded segments into one single video file.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        parents=[parent_parser]
    )
    merge_parser.set_defaults(func=merge_mode)
    merge_parser.add_argument('PATH',
        type=str,
        help='Path to directory holding vid/aud sub-directories \
in which segments have been downloaded as well as the metadata.txt file.'
    )
    merge_parser.add_argument('-d', '--delete-source',
        action='store_true',
        help='Delete source files (vid/aud) once final merging of \
streams has been successfully done.'
    )
    merge_parser.add_argument('-k', '--keep-concat',
        action='store_true',
        help='Keep concatenated intermediary files even if merging of \
streams has been successful. This is only useful for debugging.'
    )
    merge_parser.add_argument('-o', '--output-dir',
        action='store', type=str,
        default=None,
        help='Output directory where to write final merged file.'
    )

    # Sub-command "test-notification"
    monitor_parser = subparsers.add_parser('test-notification',
        help='Send a test e-mail to check if your configuration works.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        parents=[parent_parser]
    )
    return parser.parse_args()


# def parse_regex_str(regex_str) -> Optional[re.Pattern]:
#     if not regex_str:
#         return None
#     regex_str = regex_str.strip("\"\'")
#     pattern = re.compile(regex_str)
#     if not pattern.pattern:
#         # could be 0 len string that matches everything!
#         return None
#     return pattern


def _get_hook_from_config(
    config: ConfigParser,
    section: str, hook_name: str, suffix: str = "_command"
) -> Optional[Union[HookCommand, WebHookFactory]]:
    """Generate a HookCommand or WebHookFactory if one has been requested
    within a section. They mostly share the same options."""
    # Remember this will try to get from the DEFAULT section if not found
    # and THEN use the fallback value.
    full_hook_name = hook_name + suffix
    cmd = None
    url = None
    if suffix == "_command":
        cmd = config.getlist(section, full_hook_name, fallback=None)
    else:
        # This is the data payload part
        cmd = config.get(section, full_hook_name, fallback=None)
        url = config.get(section, full_hook_name + "_url", fallback=None)
        if not url:
            return
    if not cmd:
        return

    if not config.getboolean(section, full_hook_name + "_enabled", fallback=False):
        return
    logged = config.getboolean(section, full_hook_name + "_logged", fallback=False)

    try:
        allow_regex = _get_regex_from_config(section, config, full_hook_name + "_allow_regex")
    except EmptyRegexException:
        log.debug(f"Empty value in [{section}] {full_hook_name + '_allow_regex'}")
        allow_regex = None

    try:
        block_regex = _get_regex_from_config(section, config, full_hook_name + "_block_regex")
    except EmptyRegexException:
        log.debug(f"Empty value in [{section}] {full_hook_name + '_block_regex'}")
        block_regex = None

    # allow_regex = None
    # if allow_regex_str := config.get(
    #     section, hook_name + "_allow_regex", fallback=None):
    #     allow_regex = parse_regex_str(allow_regex_str)

    # block_regex = None
    # if block_regex_str := config.get(
    # section, hook_name + "_block_regex", fallback=None):
    #     block_regex = parse_regex_str(block_regex_str)

    if suffix == "_command" and isinstance(cmd, list):
        hook = HookCommand(
            cmd=cmd,
            logged=logged,
            event_name=hook_name,
            allow_regex=allow_regex,
            block_regex=block_regex
        )
        # The same object is used on many different videos, others are only used
        # as part of the live stream object, so they are usually called once only anyway.
        if hook_name in ("on_upcoming_detected", "on_video_detected"):
            hook.call_only_once = False
        return hook
    if suffix == "_webhook":
        hook = WebHookFactory(
            url=url,
            payload=cmd,
            logged=logged,
            event_name=hook_name,
            allow_regex=allow_regex,
            block_regex=block_regex
        )
        return hook

def get_hooks_for_section(section: str, config: ConfigParser, hooktype: str) -> Dict:
    """hooktype is either _command or _webhook."""
    cmds = {}
    for hook_name in event_props:
        try:
            if hookcommand := _get_hook_from_config(
            config, section, hook_name, hooktype):
                cmds[hook_name] = hookcommand
        except Exception:
            log.exception(
                f"Error parsing config [{section}] \"{hook_name + hooktype}\"")
    return cmds


class EmptyRegexException(Exception):
    """Pattern is empty."""
    pass


def _get_regex_from_config(
    section: str, config: ConfigParser, regex: str) -> Optional[re.Pattern]:
    if regex_str := config.get(section, regex, fallback=None):
        regex_str = regex_str.strip("\"\'")
        log.debug(f"For \"{regex}\" found \"{regex_str}\" from section {section}")
        if regex_str in (r'""', r"''"):
            raise EmptyRegexException
        pattern = re.compile(regex_str, re.I|re.M)
        if not pattern.pattern:
            # Could be 0-length that matches everything!
            raise EmptyRegexException
        return pattern
    return None


def _get_target_params(
    config: ConfigParser,
    args: Dict,
    sub_cmd: str,
    override: Optional[str]) -> Dict:
    """
    Deal with case where URL positional argument is not supplied by the user,
    but could be supplied in config file in the appropriate section.
    """
    # This could be called from either Monitor or Download mode.
    # TODO We could compare the URL supplied as argument with the
    # [channel monitor] URL value and automatically assign the corresponding
    # scan_delay if it's not already present in the CLI args.

    params = {
        "URL": args.get("URL", None),
        "channel_name": args.get("channel_name", None),
        "scan_delay": config.getfloat(sub_cmd, "scan_delay", vars=args),
        "skip_download": config.getboolean(sub_cmd, "skip_download", vars=args),
        "ignore_quality_change": config.getboolean(sub_cmd, "ignore_quality_change", vars=args),
        "hooks": get_hooks_for_section(sub_cmd, config, "_command"),
        "webhooks": get_hooks_for_section(sub_cmd, config, "_webhook"),
        "cookies": config.get(sub_cmd, "cookies", vars=args, fallback=None),
        "filters": {
            "allow_regex": None,
            "block_regex": None
        }
    }

    # This may throw, it should crash the program to avoid bad surprises
    if sub_cmd == "monitor":
        for regex_str in ("allow_regex", "block_regex"):
            try:
                params["filters"][regex_str] = _get_regex_from_config(
                    sub_cmd, config, regex_str
                )
            except EmptyRegexException:
                log.debug(f"Empty value in {sub_cmd} section for {regex_str}")
                pass

    # The user already has explicitly passed this argument, so we can ignore
    # any [monitor channel] section.
    if params.get("URL") is not None or sub_cmd != "monitor":
        return params

    # These sections should override default values from the "monitor" section.
    # Use the section specified on the CLI, otherwise use the first one we find.
    for section in config.sections():
        if not section.startswith("monitor"):
            continue
        # We need at least one space between monitor and the channel name
        if len(section) <= len("monitor "):
            continue
        if override is None or override == section[len("monitor "):]:
            log.info(f"Using custom monitor section \"{section}\".")
            params["URL"] = config.get(
                section, "URL", fallback=None
            )
            params["channel_name"] = config.get(
                section, "channel_name", vars=args, fallback=None
            )
            params["cookies"] = config.get(
                section, "cookies", vars=args, fallback=None
            )
            # Use the value from monitor section if missing
            params["scan_delay"] = config.getfloat(
                section, "scan_delay", vars=args,
                fallback=params["scan_delay"]
            )
            params["skip_download"] = config.getboolean(
                section, "skip_download", vars=args,
                fallback=params["skip_download"]
            )
            params["ignore_quality_change"] = config.getboolean(
                section, "ignore_quality_change", vars=args,
                fallback=params["ignore_quality_change"]
            )

            # Update any hook already present with those defined in that section
            overriden_hooks = get_hooks_for_section(section, config, "_command")
            params["hooks"].update(overriden_hooks)

            overriden_webhooks = get_hooks_for_section(section, config, "_webhook")
            params["webhooks"].update(overriden_webhooks)

            # Update regex if it has been specified in the section
            for regex_str in ("allow_regex", "block_regex"):
                try:
                    if re := _get_regex_from_config(section, config, regex_str):
                        params["filters"][regex_str] = re
                except EmptyRegexException:
                    log.debug(f"Empty value in {section} section for {regex_str}.")
                    # In case of empty string, remove any regex that could have
                    # been set from the monitor section.
                    params["filters"][regex_str] = None
            # Use the first section encountered if override is None
            break

    if params.get("URL") is None:
        raise Exception(
            "No URL specified, neither as a command-line argument nor in"
            " a [monitor {channel}] section of the config file."
            " Config file path was:"
            f" \"{config.get('DEFAULT', 'config_file', vars=args)}\""
        )
    return params


TIME_VARIANCE = 3.0  # in minutes
# TODO allow configuration by user
MAX_SIMULTANEOUS_LIVE_DOWNLOAD = 2

video_queue: Queue[VideoPost] = Queue(maxsize=4)
video_processing = set()
video_processed = set()


def video_feeder(queue: Queue, channel: YoutubeChannel, scan_delay: float):
    while True:
        try:
            live_videos = channel.filter_videos('isLiveNow')
            log.debug(
                "Live videos found for channel "
                f"\"{channel.get_channel_name()}\": "
                f"{live_videos if len(live_videos) else None}"
            )

            for v in live_videos[:MAX_SIMULTANEOUS_LIVE_DOWNLOAD]:
                if v not in video_processing:
                    queue.put(v)
        except Exception as e:
            # Handle urllib.error.URLError <urlopen error [Errno -3] Temporary failure in name resolution>
            log.exception(f"Error while getting live videos: {e}")
        util.wait_block(min_minutes=scan_delay, variance=TIME_VARIANCE)


def download_task(
    video: VideoPost, 
    config: ConfigParser, 
    args: Dict, 
    session: YoutubeUrllibSession
):
    if video in video_processing or video in video_processed:
        logging.debug(f"Video already processed or being processed: {video}")
        return
    video_processing.add(video)

    video_id = video.get("videoId")
    # Build the full "/watch?v=..." URL
    video_url = f"https://www.youtube.com{video.get('url')}" 

    if not video_id:
        try:
            video_id = extract.get_video_id(video_url)
        except ValueError as e:
            log.critical(e)
            video_processing.remove(video)
            return

    log.info(
        f"Found live: {video_id}. Title: \"{video.get('title')}\".")

    if output_dir := args.get("output_dir"):
        sub_output_dir = output_dir / f"stream_capture_{video_id}"
    else:
        sub_output_dir = None

    skip_download = args.get("skip_download", False)

    # FIXME this class could be a subclass of VideoPost
    live_video = YoutubeLiveStream(
        video_id=video_id,
        url=video_url,
        output_dir=sub_output_dir,
        session=session,
        notifier=NOTIFIER,
        max_video_quality=config.getint(
            "monitor", "max_video_quality", vars=args, fallback=None),
        hooks=args["hooks"],
        skip_download=skip_download,
        filters=args["filters"],
        ignore_quality_change=config.getboolean(
            "monitor", "ignore_quality_change", vars=args, fallback=False),
        log_level=config.get("monitor", "log_level", vars=args),
        initial_metadata=video,
        use_ytdl=use_ytdl,
        ytdl_opts=deepcopy(args["ytdlp_config"])
    )

    # ls.get_metadata(force=True)
    live_video.trigger_hooks("on_download_initiated")

    download_wanted = not skip_download
    if not skip_download:
        download_wanted = live_video.pre_download_checks()

    if download_wanted:
        try:
            live_video.download()
        except Exception as e:
            log.exception(
                f"Got error in stream download but continuing...\n {e}")

    if live_video.skip_download or not download_wanted:
        NOTIFIER.send_email(
            subject=(
                f"Skipped download of {video.channel.get_channel_name()} - "
                f"{live_video.title} {video_id}"),
            message_text=f"Hooks scheduled to run were: {args.get('hooks')}"
        )
        # We have already waited on the current stream for status update
        # Add a small wait to read debug output in case of network errors
        # cf issue #76
        util.wait_block(min_minutes=0.5, variance=0.1)
        return

    if live_video.done:
        log.info(f"Finished downloading {video_id}.")
        NOTIFIER.send_email(
            subject=(
                f"Finished downloading {video.channel.get_channel_name()} -"
                f"{live_video.title} {video_id}"),
            message_text=f""
        )
        if not config.getboolean("monitor", "no_merge", vars=args) \
                and not use_ytdl:
            log.info("Merging segments...")
            # TODO in a separate thread?
            try:
                merge(
                    info=live_video.video_info,
                    data_dir=live_video.output_dir,
                    keep_concat=config.getboolean(
                        "monitor", "keep_concat", vars=args),
                    delete_source=config.getboolean(
                        "monitor", "delete_source", vars=args)
                )
            except Exception as e:
                log.error(e)

            # TODO pass arguments about (un)successful merge
            live_video.trigger_hooks("on_merge_done")

            # TODO get the updated stream title from the channel page if
            # the stream was recorded correctly?
    if live_video.error:
        NOTIFIER.send_email(
            subject=f"Error downloading stream {live_video.video_id}",
            message_text=(
                f"Error was: {live_video.error}\n"
                "Resuming monitoring..."
            )
        )
        log.critical("Error during stream download! Resuming monitoring...")

    video_processed.add(video)
    video_processing.remove(video)



def monitor_mode(config: ConfigParser, args: Dict[str, Any]):
    URL = args["URL"]
    channel_id = args["channel_id"]
    scan_delay = args["scan_delay"]

    session = YoutubeUrllibSession(
        cookiefile_path=args.get("cookies"),
        notifier=NOTIFIER
    )

    URL = util.sanitize_channel_url(URL)
    channel = YoutubeChannel(
        URL, channel_id, session,
        output_dir=args["output_dir"],
        hooks=args["hooks"],
        notifier=NOTIFIER
    )
    log.info(f"Monitoring channel: {channel._id}")

    feeder_thread = threading.Thread(
        target=video_feeder, 
        args=(video_queue, channel, scan_delay)
    )
    feeder_thread.daemon = True
    feeder_thread.start()

    with ThreadPoolExecutor(
        max_workers=MAX_SIMULTANEOUS_LIVE_DOWNLOAD
    ) as executor:
        # TODO better handle keyboard interrupts
        while feeder_thread.is_alive():
            executor.submit(
                download_task, video_queue.get(), config, args, channel.session
            )
            log.debug("waiting 5 seconds")
            sleep(5)  # probably not necessary if queue.get blocks

    feeder_thread.join()


# FIXME remove
def monitor_mode_old(config: ConfigParser, args: Dict[str, Any]):
    URL = args["URL"]
    channel_id = args["channel_id"]
    scan_delay = args["scan_delay"]

    session = YoutubeUrllibSession(
        cookiefile_path=args.get("cookies"),
        notifier=NOTIFIER
    )

    URL = util.sanitize_channel_url(URL)

    ch = YoutubeChannel(
        URL, channel_id, session,
        output_dir=args["output_dir"],
        hooks=args["hooks"],
        notifier=NOTIFIER
    )
    log.info(f"Monitoring channel: {ch._id}")
    while True:
        live_videos = []
        try:
            # Filter and get only current live streams
            live_videos = ch.filter_videos('isLiveNow')

            # Calling this might trigger hooks twice for upcoming videos
            # will probably become obsolete soon.
            # ch.get_upcoming_videos(update=False)

            # TODO print to stdout and overwrite line
            log.debug(
                "Live videos found for channel "
                f"\"{ch.get_channel_name()}\": "
                f"{live_videos if len(live_videos) else None}"
            )
        except Exception as e:
            # Handle urllib.error.URLError <urlopen error [Errno -3] Temporary failure in name resolution>
            log.exception(f"Error while getting live videos: {e}")

        if len(live_videos) == 0:
            util.wait_block(min_minutes=scan_delay, variance=TIME_VARIANCE)
            continue

        # TODO there might be more than one active live stream at a time
        target_live = live_videos[0]

        video_id = target_live.get("videoId")
        video_url = f"https://www.youtube.com{target_live.get('url')}" # "/watch?v=..."

        if not video_id:
            try:
                video_id = extract.get_video_id(video_url)
            except ValueError as e:
                log.critical(e)
                util.wait_block(min_minutes=scan_delay, variance=TIME_VARIANCE)
                continue

        log.info(
            f"Found live: {video_id}. Title: \"{target_live.get('title')}\".")

        if output_dir := args.get("output_dir"):
            sub_output_dir = output_dir / f"stream_capture_{video_id}"
        else:
            sub_output_dir = None

        skip_download = args.get("skip_download", False)
        ls = YoutubeLiveStream(
            video_id=video_id,
            url=video_url,
            output_dir=sub_output_dir,
            session=ch.session,
            notifier=NOTIFIER,
            max_video_quality=config.getint(
                "monitor", "max_video_quality", vars=args, fallback=None),
            hooks=args["hooks"],
            skip_download=skip_download,
            filters=args["filters"],
            ignore_quality_change=config.getboolean(
                "monitor", "ignore_quality_change", vars=args, fallback=False),
            log_level=config.get("monitor", "log_level", vars=args),
            initial_metadata=target_live,
            use_ytdl=use_ytdl,
            ytdl_opts=args["ytdlp_config"]
        )

        # ls.get_metadata(force=True)
        ls.trigger_hooks("on_download_initiated")

        download_wanted = not skip_download
        if not skip_download:
            download_wanted = ls.pre_download_checks()

        if download_wanted:
            try:
                ls.download()
            except Exception as e:
                log.exception(
                    f"Got error in stream download but continuing...\n {e}")

        if ls.skip_download or not download_wanted:
            NOTIFIER.send_email(
                subject=(
                    f"Skipped download of {ch.get_channel_name()} - "
                    f"{ls.title} {video_id}"),
                message_text=f"Hooks scheduled to run were: {args.get('hooks')}"
            )
            # We have already waited on the current stream for status update
            # Add a small wait to read debug output in case of network errors
            # cf issue #76
            util.wait_block(min_minutes=0.5, variance=0.1)
            continue

        if ls.done:
            log.info(f"Finished downloading {video_id}.")
            NOTIFIER.send_email(
                subject=(
                    f"Finished downloading {ch.get_channel_name()} -"
                    f"{ls.title} {video_id}"),
                message_text=f""
            )
            if not config.getboolean("monitor", "no_merge", vars=args) \
                    and not use_ytdl:
                log.info("Merging segments...")
                # TODO in a separate thread?
                try:
                    merge(
                        info=ls.video_info,
                        data_dir=ls.output_dir,
                        keep_concat=config.getboolean(
                            "monitor", "keep_concat", vars=args),
                        delete_source=config.getboolean(
                            "monitor", "delete_source", vars=args)
                    )
                except Exception as e:
                    log.error(e)

                # TODO pass arguments about (un)successful merge
                ls.trigger_hooks("on_merge_done")

                # TODO get the updated stream title from the channel page if
                # the stream was recorded correctly?
        if ls.error:
            NOTIFIER.send_email(
                subject=f"Error downloading stream {ls.video_id}",
                message_text=(
                    f"Error was: {ls.error}\n"
                    "Resuming monitoring..."
                )
            )
            log.critical("Error during stream download! Resuming monitoring...")
            pass

        util.wait_block(min_minutes=scan_delay, variance=TIME_VARIANCE)


def download_mode(config: ConfigParser, args: Dict[str, Any]):
    session = YoutubeUrllibSession(
        cookiefile_path=args.get("cookies"), notifier=NOTIFIER
    )

    ls = YoutubeLiveStream(
        video_id=args["video_id"],
        url=args.get("URL"),
        output_dir=args["output_dir"],
        session=session,
        notifier=NOTIFIER,
        max_video_quality=config.getint(
            "download", "max_video_quality", vars=args, fallback=None
        ),
        hooks=args["hooks"],
        skip_download=config.getboolean(
            "download", "skip_download", vars=args, fallback=False
        ),
        # no filters in this mode, we assume the user knows what they're doing
        filters={},
        ignore_quality_change=config.getboolean(
            "download", "ignore_quality_change", vars=args, fallback=False),
        log_level=config.get("download", "log_level", vars=args),
        use_ytdl=use_ytdl,
        ytdl_opts=args["ytdlp_config"]
    )

    ls.trigger_hooks("on_download_initiated")
    ls.download(config.getfloat("download", "scan_delay", vars=args))

    if ls.done \
        and not config.getboolean("download", "no_merge", vars=args) \
            and not use_ytdl:
        log.info("Merging segments...")
        try:
            merge(
                info=ls.video_info,
                data_dir=ls.output_dir,
                keep_concat=config.getboolean("download", "keep_concat", vars=args),
                delete_source=config.getboolean("download", "delete_source", vars=args)
            )
        except Exception as e:
            log.error(e)

        # TODO pass arguments about successful merge
        ls.trigger_hooks("on_merge_done")
    return 0


def merge_mode(config, args):
    data_path = Path(args["PATH"]).resolve()
    info = get_metadata_info(data_path)
    written_file = merge(
        info=info,
        data_dir=data_path,
        output_dir=config.get("merge", "output_dir", vars=args),
        keep_concat=config.getboolean("merge", "keep_concat", vars=args),
        delete_source=config.getboolean("merge", "delete_source", vars=args)
    )

    if not written_file:
        log.critical("Something failed. Please report the issue with logs.")
        return 1
    return 0


def log_enabled(config, args, mode_str):
    """Sanitize log level input value, return False if disabled by user."""
    if level := config.get(mode_str, "log_level", vars=args):
        # if level == "NONE":
        #     return False
        log_level = getattr(logging, level, None)
        if not isinstance(log_level, int):
            raise ValueError(f'Invalid log-level for {mode_str} mode: {level}')
    return True


def setup_logger(*, output_filepath, loglevel, log_to_file=True) -> logging.Logger:
    # This uses the global variable "logger"
    if loglevel is None:
        log.disabled = True
        return log

    if isinstance(loglevel, str):
        loglevel = str.upper(loglevel)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
    )

    if log_to_file:
        logfile = logging.FileHandler(
            filename=output_filepath, delay=True, encoding='utf-8'
        )
        # FIXME DEBUG by default for file
        logfile.setLevel(logging.DEBUG)
        logfile.setFormatter(formatter)
        log.addHandler(logfile)

    # Console stdout handler
    conhandler = logging.StreamHandler()
    conhandler.setLevel(loglevel)
    conhandler.setFormatter(formatter)
    log.addHandler(conhandler)
    return log


def init_config() -> ConfigParser:
    """Get a ConfigParser with sane default values."""
    # Create user config directory if it doesn't already exist
    conf_filename = "livestream_saver.cfg"

    if platform == "win32":
        config_dir = Path.home()
    elif env_path := environ.get("LSS_CONFIG_DIR"):
        config_dir = Path(env_path)
    else:
        config_dir = Path.home() / ".config" / "livestream_saver"
        # config_dir.mkdir(exist_ok=True)

    CONFIG_DEFAULTS = {
        "config_dir": str(config_dir),
        "config_file": config_dir / conf_filename,
        "log_level": "INFO",
        "output_dir": environ.get("LSS_OUTPUT_DIR", ""),
        "cookies": environ.get("LSS_COOKIES_FILE", ""),

        "delete_source": "False",
        "keep_concat": "False",
        "no_merge": "False",
        "skip_download": "False",
        "email_notifications": "False",
        "ignore_quality_change": "False",
    }
    other_defaults = {
        "email": {
            "smtp_server": "",
            "smtp_port": "",
            "smtp_login": "",
            "smtp_password": "",
            "to_email": "",
            "from_email": "",
        },
        "monitor": {
            "scan_delay": 15.0  # minutes
        },
        "download": {
            "scan_delay": 2.0  # minutes
        }
    }

    def parse_as_list(data: str) -> Optional[List[str]]:
        """Split a string into a valid shell command as a list of string."""
        # if not data:
        #     return None
        return split(data)

    # We cannot use the basic interpolation or it would conflict with the
    # output filename template syntax from yt-dlp. "None" works well but
    # we would lose the ability to use variables.
    config = ConfigParser(
        CONFIG_DEFAULTS,
        interpolation=ExtendedInterpolation(),
        converters={'list': parse_as_list}
    )
    # Set defaults for each section
    config.read_dict(other_defaults)
    config.add_section("merge")
    config.add_section("test-notification")  # FIXME this one is useless
    return config


def update_config(config: ConfigParser, args: Dict[str, Any]) -> None:
    """
    Load the configuration file specified as argument into config object.
    If none is specified, load the configuration file located in the current
    working directory if possible.
    """
    conf_file = args.get("config_file")
    if conf_file:
        if not Path(conf_file).is_file():
            logging.critical(
                f"Config file \"{conf_file}\" is not a valid file. "
                "Continuing with default values only!")
            return
        read_conf_files = config.read(conf_file)
        if not read_conf_files:
            logging.critical(f"Failed to read config file from {conf_file}")
        return
    # No config file specified, get it from ~/.config by default
    conf_file = Path(config.get("DEFAULT", "config_file"))
    if not conf_file.exists():
        # try in the current working directory just in case
        conf_file = Path.cwd() / "livestream_saver.cfg"
        if not conf_file.exists():
            return
    read_conf_files = config.read(conf_file)
    if not read_conf_files:
        logging.critical(f"Failed to read config file from {conf_file}.")


def get_from_env(lookup_keys: Iterable[str]) -> Optional[Dict]:
    """Get keys found in lookup_keys from the environment variables if any.
    Keys that start with the string will also be loaded as their own key/value."""
    env_vars = {}
    for env_key, env_value in environ.items():
        for key in lookup_keys:
            if key == env_key or key == env_key.upper() \
            or env_key.startswith(key) or env_key.startswith(key.upper()):
                # Keys are converted to lower case when loaded into the
                # config parser, so we may overwrite to avoid duplicates.
                env_vars[env_key.lower()] = env_value
    if len(env_vars) > 0:
        return env_vars


def load_commented_json(path: Path) -> Dict:
    """
    Remove comments from JSON file and load as mapping.
    """
    with open(path, "r") as f:
        return json.loads(
            '\n'.join(row for row in f if not row.lstrip().startswith("//")))


def main():
    config: ConfigParser = init_config()

    # Update "env" section with variables from env so that they can be
    # used in config via interpolation when loading the config file.
    # For now we only look for the urls (sometimes holding secret tokens).
    if found_vars := get_from_env(("webhook_url",)):
        env_vars = { "env": found_vars }
        config.read_dict(env_vars)

    # We'll need to mutate args further down
    args: Dict[str, Any] = vars(parse_args(config))
    update_config(config, args)

    # DEBUG
    # for section in config.sections():
    #     for option in config.options(section):
    #         print(f"[{section}] {option} = {config.get(section, option)}")

    global NOTIFIER

    sub_cmd = args.get("sub-command")
    if sub_cmd is None:
        print("No sub-command used. Exiting.")
        return

    global log
    log_enabled(config, args, sub_cmd)

    ytdlp_conf = "ytdlp_config.json"
    config_dir = Path(config.get("DEFAULT", "config_dir"))
    ytdlp_conf_file = config_dir / ytdlp_conf
    if not ytdlp_conf_file.exists():
        log.warning(f"{ytdlp_conf_file} not found. Falling back to using template.")
        ytdlp_conf_file = Path(__file__).parent.parent.absolute() / ytdlp_conf
    args["ytdlp_config"] = load_commented_json(ytdlp_conf_file)

    logfile_path = Path("")  # cwd by default
    if sub_cmd == "monitor":
        params = _get_target_params(
            config, args, sub_cmd=sub_cmd, override=args["section"])
        NOTIFIER.webhooks = params.get("webhooks", {})
        NOTIFIER.setup(config, args)

        args["URL"] = params.get("URL")
        channel_name = params.get("channel_name")
        args["channel_name"] = channel_name
        args["scan_delay"] = params.get("scan_delay")
        args["hooks"] = params.get("hooks")
        args["cookies"] = params.get("cookies")
        args["skip_download"] = params.get("skip_download")
        args["ignore_quality_change"] = params.get("ignore_quality_change")
        args["filters"] = params.get("filters", {})

        channel_id = get_channel_id(args["URL"], service_name="youtube")
        args["channel_id"] = channel_id

        # We need to setup output dir before instantiating downloads
        # because we use it to store our logs
        output_dir: Optional[str] = config.get(
            "monitor", "output_dir", vars=args, fallback=None)
        output_path = Path(output_dir) if output_dir else Path()

        makedirs(output_path, exist_ok=True)
        args["output_dir"] = Path(output_dir) if output_dir else None

        logfile_path = output_path / f'monitor_{channel_id}.log'

        log = setup_logger(
            output_filepath=logfile_path,
            loglevel=config.get(sub_cmd, "log_level", vars=args)
        )
        log.debug(f"Regex filters {[f'{k}: {v}' for k, v in args['filters'].items()]}")
        args["logger"] = log
    elif sub_cmd  == "download":
        output_dir = config.get(
            "download", "output_dir", vars=args, fallback=None
        )
        URL = args.get("URL", "")  # pass empty string for get_video_id()
        args["hooks"] = get_hooks_for_section(sub_cmd, config, "_command")
        args["cookies"] = config.get(sub_cmd, "cookies", vars=args, fallback=None)
        args["ignore_quality_change"] = config.getboolean(
            sub_cmd, "ignore_quality_change", vars=args, fallback=False)

        NOTIFIER.webhooks = get_hooks_for_section(sub_cmd, config, "_webhook")
        NOTIFIER.setup(config, args)

        video_id = extract.get_video_id(url=URL)
        args["video_id"] = video_id


        output_path = Path(output_dir) if output_dir else None
        args["output_dir"] = output_path

        if not use_ytdl:
            output_dir = util.create_output_dir(
                output_dir=output_path, video_id=video_id)
        else:
            # We don't want a stream_capture_xxxx directory, but we need to
            # ensure output path exists because we create the download log now
            output_dir = util.create_output_dir(
                output_dir=output_path, video_id=None)

        logfile_path = output_dir / f"download_{video_id}.log"
        log = setup_logger(
            output_filepath=logfile_path,
            loglevel=config.get(sub_cmd, "log_level", vars=args)
        )
        args["logger"] = log
    elif sub_cmd == "merge":
        logfile_path = Path(args["PATH"]) / "merge.log"
        setup_logger(
            output_filepath= logfile_path,
            loglevel=config.get(sub_cmd, "log_level", vars=args)
        )
    elif sub_cmd == "test-notification":
        logfile_path = getcwd()
        setup_logger(
            output_filepath=logfile_path,
            loglevel=config.get(sub_cmd, "log_level", vars=args),
            log_to_file=False
        )

        # Load one webhook from DEFAULT section only
        # FIXME ideally we would need to use the exact same logic used with
        # regular sub-commands here. But we ignore regexes, and enabled options.
        default_keys = ("webhook_url", "webhook_data")
        print(f"Looking for keys {default_keys} in config's [webhook] section...")

        url = config.get("webhook", default_keys[0])
        payload = config.get("webhook", default_keys[1])
        if url is not None and payload is not None:
            print("Found valid webhook in config file...")
            NOTIFIER.webhooks = {
                "test_event":
                    WebHookFactory(
                        url=config.get("webhook", default_keys[0]),
                        payload=config.get("webhook", default_keys[1]),
                        logged=True,
                        event_name="test_event",
                        allow_regex=None, block_regex=None
                    )
            }
        else:
            print(
                "Error loading the webhook from [webhook] section in config."
                " Will skip testing webhook over the wire."
            )

        NOTIFIER.setup(config, args)

        if not NOTIFIER.email_handler.disabled:
            NOTIFIER.send_email(
                subject=f"{__file__.split(sep)[-1]} test email.",
                message_text="The current configuration works fine!\nYay.\n"
                            "You will receive notifications if the program "
                            "encounters an issue while doing its tasks."
            )
            print(
                f"Sent test e-mail to {NOTIFIER.email_handler.receiver_email} via "
                f"SMTP server {NOTIFIER.email_handler.smtp_server}:{NOTIFIER.email_handler.smtp_port}... "
                "Check your inbox!"
            )

        if len(NOTIFIER.webhooks):
            # Only test webhook_url and webhook_data from DEFAULT section
            # to avoid spamming inadvertently.
            fake_metadata = {
                "videoId": "XXXXXXXXXXX",
                "title": "test title",
                "author": "test author",
                "startTime": "1669028800",
                "description": "test description",
            }
            NOTIFIER.call_webhook(hook_name="test_event", args=fake_metadata)
        else:
            print("No webhook could be loaded from config file.")

        if not NOTIFIER.email_handler.disabled or NOTIFIER.webhooks:
            NOTIFIER.q.join()
        return
    else:
        print("Wrong sub-command. Exiting.")
        return

    if "cookiefile" not in args["ytdlp_config"] and (cookies := args.get("cookies")):
        args["ytdlp_config"]["cookiefile"] = cookies

    error = 0
    try:
        error = args["func"](config, args)
    except Exception as e:
        error = 1
        from sys import exc_info
        exc_type, exc_value, exc_traceback = exc_info()
        log.exception(e)
        NOTIFIER.send_email(
            subject=f"{argv[0].split(sep)[-1]} crashed!",
            message_text=f"Mode: {args.get('sub-command', '').split('_')[0]}\n" \
            + f"Exception was: {e}\n" \
            + "\n".join(
                traceback.format_exception(
                    exc_type, exc_value,exc_traceback
                )
            ),
            attachments=[logfile_path]
        )

    # We need to join here (or sleep long enough) otherwise any email still
    # in the queue will fail to get sent because we exited too soon!
    if not NOTIFIER.email_handler.disabled or NOTIFIER.webhooks:
        NOTIFIER.q.join()
        # NOTIFIER.thread.join()
    logging.shutdown()
    return error


if __name__ == "__main__":
    exit(main())
