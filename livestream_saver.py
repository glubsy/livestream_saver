from os import sep, makedirs, getcwd
from sys import argv
import argparse
import logging
from pathlib import Path
from configparser import ConfigParser
import traceback

from livestream_saver import extract, util
from livestream_saver.monitor import YoutubeChannel, wait_block
from livestream_saver.download import YoutubeLiveBroadcast
from livestream_saver.merge import merge, get_metadata_info
from livestream_saver.util import get_channel_id
from livestream_saver.request import YoutubeUrllibSession
from livestream_saver.smtp import NotificationHandler

logger = logging.getLogger('livestream_saver')
logger.setLevel(logging.DEBUG)

notif_h = NotificationHandler()

DEFAULT_VIDEO_CODEC = "mp4" # or webm
DEFAULT_AUDIO_CODEC = "mp4" # or webm


def parse_args(config):
    """
    Return a dict view of the argparse.Namespace.
    """
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
    parent_parser.add_argument('-c', '--conf-file',
        action='store', type=str,
        default=argparse.SUPPRESS,
        help='Path to config file to use.'\
             f' (Default: {config.get("DEFAULT", "conf_file")})'
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
    monitor_parser.add_argument('--cookie',
        action='store', type=str, metavar="COOKIE_PATH",
        default=argparse.SUPPRESS,
        help='Path to Netscape formatted cookie file.'
    )
    monitor_parser.add_argument('-f', '--itags', 
        action='store', type=str,
        default=argparse.SUPPRESS, 
        help='Try getting these itags (separated by +), otherwise fall back to '
             'getting the best quality, up to optional max resolution if '
             'specified. Example: 134+140'
    )
    monitor_parser.add_argument('-q', '--max-video-resolution', action='store',
        default=argparse.SUPPRESS, type=str,
        help='Try getting the best available video resolution up to this height '
             'in pixels. '
             'Example: "360" for a maximum height of 360 pixels. '
             'If not specified, will get the highest resolution available. '
             'Common values: 1080, 720, 480, 360, 240, 144.'
    )
    monitor_parser.add_argument('--video-codec', 
        action='store', type=str,
        default=argparse.SUPPRESS, 
        help='Filter available streams with this preferred video codec. '
              f'Default: {DEFAULT_VIDEO_CODEC}. Example: webm (for vp8/9).'
    )
    monitor_parser.add_argument('--audio-codec',
        action='store', type=str,
        default=argparse.SUPPRESS, 
        help='Filter available streams with this preferred audio codec. '
              f'Default: {DEFAULT_AUDIO_CODEC}. Example: webm (for opus/vorbis).'
    )
    monitor_parser.add_argument('-o', '--output-dir', 
        action='store', type=str,
        default=argparse.SUPPRESS, 
        help='Output directory where to save channel data. '
            f'(Default: {config.get("monitor", "output_dir")})'
    )
    monitor_parser.add_argument('--channel-name', 
        action='store', type=str,
        default=argparse.SUPPRESS, 
        help='User-defined name of the channel to monitor. Will fallback to \
channel ID deduced from the URL otherwise.'
    )

    monitor_group = monitor_parser.add_mutually_exclusive_group()
    monitor_parser.add_argument('-d', '--delete-source',
        action='store_true',
        help='Delete source segment files once the final '
             'merging of them has been done.'
    )
    monitor_group.add_argument('-n', '--no-merge',
        action='store_true',
        help='Do not merge segments after live streams has ended.'
    )
    monitor_parser.add_argument('-k', '--keep-concat',
        action='store_true',
        help='Keep concatenated intermediary files even if merging of streams '
             'has been successful. Only useful for troubleshooting.'
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
            f' (Default: {config.get("download", "email_notifications")})'
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
    download_parser.add_argument('--cookie',
        action='store', type=str, metavar="COOKIE_PATH",
        default=argparse.SUPPRESS,
        help='Path to Netscape formatted cookie file.'
    )
    download_parser.add_argument('-F', '--list-formats', 
        action='store_true',
        default=argparse.SUPPRESS,
        help='List available formats for that stream, then exit.'
    )
    download_parser.add_argument('-f', '--itags', 
        action='store', type=str,
        default=argparse.SUPPRESS, 
        help='Try getting these itags (separated by +), otherwise fall back to '
             'getting the best quality, up to optional max resolution if '
             'specified. Example: 134+140'
    )
    download_parser.add_argument('-q', '--max-video-resolution', action='store',
        default=argparse.SUPPRESS, type=str,
        help='Try getting the best available video resolution up to this height '
             'in pixels. '
             'Example: "360" for a maximum height of 360 pixels. '
             'If not specified, will get the highest resolution available. '
             'Common values: 1080, 720, 480, 360, 240, 144.'
    )
    download_parser.add_argument('--video-codec', 
        action='store', type=str,
        default=argparse.SUPPRESS, 
        help='Filter available streams with this preferred video codec. '
              f'Default: {DEFAULT_VIDEO_CODEC}. Example: webm (for vp8/9).'
    )
    download_parser.add_argument('--audio-codec', 
        action='store', type=str,
        default=argparse.SUPPRESS, 
        help='Filter available streams with this preferred audio codec. '
              f'Default: {DEFAULT_AUDIO_CODEC}. Example: webm (for opus/vorbis).'
    )
    download_parser.add_argument('-o', '--output-dir',
        action='store', type=str,
        default=argparse.SUPPRESS,
        help='Output directory where to write downloaded chunks.'\
              f' (Default: {config.get("download", "output_dir")})'
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
            f' (Default: {config.get("download", "email_notifications")})'
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

    return vars(parser.parse_args())


def _get_target_params(config, args):
    """
    Deal with case where URL positional argument is not supplied by user, but
    could be supplied in config file in appropriate section.
    Return tuple (URL, channel_name, scan_delay) 
    taken from arguments + config.
    """
    URL = args.get("URL", None)
    rv = (None, None, config.getfloat("monitor", "scan_delay"))

    if URL is not None:
        # We could also compare URL with [channel monitor] URL value and assign
        # the corresponding scan_delay if it's not present in the CLI args?
        rv = (
            URL,
            args.get("channel_name"),
            config.getfloat("monitor", "scan_delay", vars=args)
        )
        return rv

    if config.has_section("channel_monitor"):
        rv = (
            config.get("channel_monitor", "URL", fallback=None),
            config.get(
                "channel_monitor", "channel_name", vars=args, fallback=None
            ),
            config.getfloat("channel_monitor", "scan_delay", vars=args)
        )

    if rv[0] is None:
        raise Exception(
            "No URL specified, neither command-line argument nor in"
            " a [channel_monitor] section of the config file."
            " Config file path was:"
            f" \"{config.get('DEFAULT', 'conf_file', vars=args)}\""
        )
    return rv


def monitor_mode(config, args):
    URL = args["URL"]
    channel_id = args["channel_id"]
    scan_delay = args["scan_delay"]

    session = YoutubeUrllibSession(
        config.get("monitor", "cookie", vars=args, fallback=None),
        notifier=notif_h
    )

    URL = util.sanitize_channel_url(URL)

    ch = YoutubeChannel(URL, channel_id, session)

    logger.info(f"Monitoring channel: {ch.id}")

    while True:
        live_videos = ch.get_live_videos()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Live videos found for channel "
                f"\"{ch.get_channel_name()}\": {live_videos}"
            )

        if not live_videos:
            wait_block(min_minutes=scan_delay, variance=3.5)
            continue

        target_live = live_videos[0]
        _id = target_live.get('videoId')
        sub_output_dir = args["output_dir"] / f"stream_capture_{_id}"
        live_broadcast = None
        try:
            live_broadcast = YoutubeLiveBroadcast(
                url=f"https://www.youtube.com{target_live.get('url')}", # /watch?v=...
                output_dir=sub_output_dir,
                session=ch.session,
                video_id=_id,
                log_level=config.get("monitor", "log_level", vars=args)
            )
        except ValueError as e:
            # Constructor may throw
            logger.critical(e)
            wait_block(min_minutes=scan_delay, variance=3.5)
            continue

        logger.info(
            f"Found live: {_id} title: {target_live.get('title')}. "
        )

        # This should log to stream log file
        live_broadcast.print_available_streams()

        live_broadcast.filter_streams(
            vcodec=config.get(
                "monitor", "video-codec", vars=args, fallback=DEFAULT_VIDEO_CODEC
            ),
            acodec=config.get(
                "monitor", "audio-codec", vars=args, fallback=DEFAULT_AUDIO_CODEC
            ),
            itags=config.get(
                "monitor", "itags", vars=args, fallback=None
            ),
            maxq=config.get(
                "monitor", "max_video_resolution", vars=args, fallback=None
            )
        )
        logger.info(f"Selected streams: {live_broadcast.selected_streams}")

        try:
            logger.info(f"Downloading {_id}...")
            live_broadcast.download()
        except Exception as e:
            logger.exception(
                f"Got error in stream download but continuing...\n {e}"
            )
            pass

        if live_broadcast.done:
            logger.info(f"Finished downloading {_id}.")
            notif_h.send_email(
                subject=f"Finished downloading {ch.get_channel_name()} - \
{live_broadcast.ptyt.title} {_id}",
                message_text=f""
            )
            if not config.getboolean("monitor", "no_merge", vars=args):
                logger.info("Merging segments...")
                # TODO in a separate thread?
                merge(
                    info=live_broadcast.video_info,
                    data_dir=live_broadcast.output_dir,
                    keep_concat=config.getboolean(
                        "monitor", "keep_concat", vars=args
                    ),
                    delete_source=config.getboolean(
                        "monitor", "delete_source", vars=args
                    )
                )
                # TODO get the updated stream title from the channel page if
                # the stream was recorded correctly?
        if live_broadcast.error:
            notif_h.send_email(
                subject=f"Error downloading stream {live_broadcast.video_id}",
                message_text=f"Error was: {live_broadcast.error}\n"
                              "Resuming monitoring..."
            )
            logger.critical("Error during stream download! Resuming monitoring...")
            pass

        wait_block(min_minutes=scan_delay, variance=3.5)
    return 1


def download_mode(config, args):
    session = YoutubeUrllibSession(
        config.get("download", "cookie", vars=args, fallback=None),
        notifier=notif_h
    )

    try:
        live_broadcast = YoutubeLiveBroadcast(
            url=args.get("URL"),
            output_dir=args["output_dir"],
            session=session,
            video_id=args["video_id"],
            log_level=config.get("download", "log_level", vars=args)
        )
    except ValueError as e:
        logger.critical(e)
        return 1

    live_broadcast.print_available_streams(logger=logger)

    # FIXME avoid creating directories in this case
    if config.getboolean("download", "list_formats", vars=args, fallback=False):
        return 0

    live_broadcast.filter_streams(
        vcodec=config.get(
            "download", "video_codec", vars=args, fallback=DEFAULT_VIDEO_CODEC
        ),
        acodec=config.get(
            "download", "audio_codec", vars=args, fallback=DEFAULT_AUDIO_CODEC
        ),
        itags=config.get(
            "download", "itags", vars=args, fallback=None
        ),
        maxq=config.get(
            "download", "max_video_resolution", vars=args, fallback=None
        )
    )
    logger.info(f"Selected streams: {live_broadcast.selected_streams}")
    return 0

    live_broadcast.download(
        wait_delay=config.getfloat("download", "scan_delay", vars=args)
    )

    if live_broadcast.done and not config.getboolean("download", "no_merge", vars=args):
        logger.info("Merging segments...")
        merge(
            info=live_broadcast.video_info,
            data_dir=live_broadcast.output_dir,
            keep_concat=config.getboolean("download", "keep_concat", vars=args),
            delete_source=config.getboolean("download", "delete_source", vars=args)
        )
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
        logger.critical("Something failed. Please report the issue with logs.")
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


def setup_logger(*, output_filepath, loglevel, log_to_file=True):
    if loglevel is None:
        logger.disabled = True
        return

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
        logger.addHandler(logfile)

    # Console stdout handler
    conhandler = logging.StreamHandler()
    conhandler.setLevel(loglevel)
    conhandler.setFormatter(formatter)
    logger.addHandler(conhandler)


def init_config():
    """Get a ConfigParser with sane default values."""
    CWD = getcwd()
    CONFIG_DEFAULTS = {
        "conf_dir": CWD,
        "conf_file": CWD + sep + "livestream_saver.cfg",
        "output_dir": CWD,
        "log_level": "INFO",

        "delete_source": "False",
        "keep_concat": "False",
        "no_merge": "False",

        "email_notifications": "False",
        "list_formats": "False",
        "smtp_server": "",
        "smtp_port": "",
        "to_email": "",
        "from_email": "",
    }

    config = ConfigParser(
        CONFIG_DEFAULTS,
        # interpolation=None
    )
    # Set defaults for each section
    config.add_section("monitor")
    config.set("monitor", "scan_delay", "15.0")  # minutes
    config.set("monitor", "video_codec", DEFAULT_VIDEO_CODEC)
    config.set("monitor", "audio_codec", DEFAULT_AUDIO_CODEC)
    config.add_section("download")
    config.set("download", "scan_delay", "2.0")  # minutes
    config.set("download", "video_codec", DEFAULT_VIDEO_CODEC)
    config.set("download", "audio_codec", DEFAULT_AUDIO_CODEC)
    config.add_section("merge")
    config.add_section("test-notification")
    return config


def parse_config(config, args):
    conf_file = args.get("conf_file")
    if not conf_file:
        conf_file = config.get("DEFAULT", "conf_file")

    if Path(conf_file).exists():
        read_conf_files = config.read(conf_file)
        if not read_conf_files:
            logging.debug(f"Unable to read config file: {conf_file}")
    # else:
    #     args["conf_file"] = None

    return config


def main():
    config = init_config()

    args = parse_args(config)
    # print(f"parse_args() -> {args}")

    config = parse_config(config, args)
    # print(f"parse_config() -> {[opt for sect in config.sections() for opt in config.options(sect)]}")

    notif_h.setup(config, args)

    sub_cmd = args.get("sub-command")
    if sub_cmd is None:
        print("No sub-command used. Exiting.")
        return

    log_enabled(config, args, sub_cmd)

    logfile_path = Path("")
    if sub_cmd == "monitor":
        URL, channel_name, scan_delay = _get_target_params(config, args)
        args["URL"] = URL
        args["channel_name"] = channel_name
        args["scan_delay"] = scan_delay

        channel_id = get_channel_id(URL, "youtube")
        args["channel_id"] = channel_id

        # We need to setup output dir before instanciating downloads
        # because we use it to store our logs
        output_dir = Path(config.get("monitor", "output_dir", vars=args))
        if channel_name is not None:
            output_dir = output_dir / channel_name
        else:
            output_dir = output_dir / channel_id

        makedirs(output_dir, exist_ok=True)
        args["output_dir"] = output_dir

        logfile_path = output_dir / f'monitor_{channel_id}.log'

        setup_logger(
            output_filepath=logfile_path,
            loglevel=config.get(sub_cmd, "log_level", vars=args)
        )
    elif sub_cmd  == "download":
        output_dir = Path(
            config.get(
                "download", "output_dir", vars=args, fallback=getcwd()
            )
        )
        URL = args.get("URL", "")
        video_id = extract.get_video_id(url=URL)
        args["video_id"] = video_id
        output_dir = util.create_output_dir(
            output_dir=output_dir, video_id=video_id
        )
        args["output_dir"] = output_dir

        logfile_path = output_dir / "download.log"
        setup_logger(
            output_filepath=logfile_path,
            loglevel=config.get(sub_cmd, "log_level", vars=args)
        )
    elif sub_cmd == "merge":
        logfile_path = Path(args["PATH"]) / "merge.log"
        setup_logger(
            output_filepath=logfile_path,
            loglevel=config.get(sub_cmd, "log_level", vars=args)
        )
    elif sub_cmd == "test-notification":
        logfile_path = getcwd()
        setup_logger(
            output_filepath=logfile_path,
            loglevel=config.get(sub_cmd, "log_level", vars=args),
            log_to_file=False
        )
        if notif_h.disabled:
            print("Emails are currenly disabled by configuration.")
            return
        notif_h.send_email(
            subject=f"{__file__.split(sep)[-1]} test email.",
            message_text="The current configuration works fine!\nYay.\n"
                         "You will receive notifications if the program " 
                         "encounters an issue while doing its tasks."
        )
        print(
            f"Sent test e-mail to {notif_h.receiver_email} via "
            f"SMTP server {notif_h.smtp_server}:{notif_h.smtp_port}... "
            "Check your inbox!"
            )
        if not notif_h.disabled:
            notif_h.q.join()
        return
    else:
        print("Wrong sub-command. Exiting.")
        return

    error = 0
    try:
        error = args["func"](config, args)
    except Exception as e:
        error = 1
        from sys import exc_info
        exc_type, exc_value, exc_traceback = exc_info()
        logger.exception(e)
        notif_h.send_email(
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
    if not notif_h.disabled:
        notif_h.q.join()
        # notif_h.thread.join()
    logging.shutdown()
    return error


if __name__ == "__main__":
    exit(main())
