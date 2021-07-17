#!/bin/env python3
from os import sep, makedirs, path, getcwd
import argparse
import logging
from pathlib import Path
from configparser import ConfigParser
from livestream_saver.monitor import YoutubeChannel, wait_block
from livestream_saver.download import YoutubeLiveStream
from livestream_saver.merge import merge, get_metadata_info
from livestream_saver.util import get_channel_id
from livestream_saver.request import YoutubeUrllibSession
# from livestream_saver.smtp import MailHandler

logger = logging.getLogger('livestream_saver')
logger.setLevel(logging.DEBUG)

# MAIL = MailHandler()

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
    monitor_parser.add_argument('-q', '--max-video-quality', action='store',
        default=argparse.SUPPRESS, type=int,
        help='Use best available video resolution up to this height in pixels.'\
             ' Example: "360" for maximum height 360p. Get the highest available'
             ' resolution by default.'
    )
    monitor_parser.add_argument('-o', '--output-dir', action='store',
        default=argparse.SUPPRESS, type=str,
        help='Output directory where to save channel data.'\
            f' (Default: {config.get("monitor", "output_dir")})'
    )
    monitor_parser.add_argument('--channel-name', action='store',
        default=argparse.SUPPRESS, type=str,
        help='User-defined name of the channel to monitor. Will fallback to \
channel ID deduced from the URL otherwise.'
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
    return vars(parser.parse_args())


def _get_target_params(config, args):
    """Return tuple (URL, channel_name, scan_delay) taken from arguments + config."""
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
    log_enabled(config, args, "monitor")

    URL, channel_name, scan_delay = _get_target_params(config, args)

    channel_id = get_channel_id(URL, "youtube")

    # We need to setup output dir before instanciating downloads
    # because we use it to store our logs
    output_dir = config.get("monitor", "output_dir", vars=args)
    if channel_name is not None:
        output_dir = output_dir + sep + channel_name
    else:
        output_dir = output_dir + sep + channel_id

    makedirs(output_dir, exist_ok=True)

    setup_logger(
        output_dir + f'{sep}monitor_{channel_id}.log',
        config.get("monitor", "log_level", vars=args)
    )

    session = YoutubeUrllibSession(
        config.get("monitor", "cookie", vars=args, fallback=None)
    )

    # FIXME this is dumb
    if "http" not in URL and "youtube.com" not in URL:
        URL = f"https://www.youtube.com/channel/{URL}"

    ch = YoutubeChannel(URL, channel_id, session)

    logger.info(f"Monitoring channel: {ch.info.get('id')}")

    while True:
        live_videos = ch.get_live_videos()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"Live videos found for channel {ch.get_name()}: {live_videos}"
            )

        if not live_videos:
            wait_block(min_minutes=scan_delay, variance=3.5)
            continue

        target_live = live_videos[0]
        _id = target_live.get('videoId')
        stream = None

        try:
            stream = YoutubeLiveStream(
                url=f"https://www.youtube.com{target_live.get('url')}", # /watch?v=...
                output_dir=output_dir,
                session=ch.session,
                video_id=_id,
                max_video_quality=config.getint(
                    "monitor", "max_video_quality", vars=args, fallback=None
                ),
                log_level=config.get("monitor", "log_level", vars=args)
            )
        except ValueError as e:
            # May be thrown by constructor
            logger.critical(e)
            wait_block(min_minutes=scan_delay, variance=3.5)
            continue

        logger.info(f"Found live: {_id} title: {target_live.get('title')}."
                    " Downloading...")

        try:
            stream.download()
        except Exception as e:
            logger.exception(f"Got error in stream download but continuing...\n {e}")
            pass

        if stream.done:
            logger.info(f"Finished downloading {_id}.")
            if not config.getboolean("monitor", "no_merge", vars=args):
                logger.info("Merging segments...")
                # TODO in a separate thread?
                merge(
                    info=stream.video_info,
                    data_dir=stream.output_dir,
                    keep_concat=config.getboolean(
                        "monitor", "keep_concat", vars=args
                    ),
                    delete_source=config.getboolean(
                        "monitor", "delete_source", vars=args
                    )
                )
                # TODO get the updated stream title from the channel page if
                # the stream was recorded correctly?
        if stream.error:
            # TODO Send notification or email to admin here
            # MAIL.send("Error downloading stream", stream.error)
            logger.critical("Error during stream download! Resuming monitoring...")
            pass

        wait_block(min_minutes=scan_delay, variance=3.5)


def download_mode(config, args):
    log_enabled(config, args, "download")

    setup_logger(
        config.get("download", "output_dir", vars=args) + sep + "download.log",
        config.get("download", "log_level")
    )

    session = YoutubeUrllibSession(
        config.get("download", "cookie", vars=args, fallback=None)
    )
    try:
        dl = YoutubeLiveStream(
            url=args.get("URL"),
            output_dir=config.get(
                "download", "output_dir", vars=args, fallback=getcwd()
            ),
            session=session,
            max_video_quality=config.getint(
                "download", "max_video_quality", vars=args, fallback=None
            ),
            log_level=config.get("download", "log_level", vars=args)
        )
    except ValueError as e:
        logger.critical(e)
        exit(1)

    dl.download(config.getfloat("download", "scan_delay", vars=args))

    if dl.done and not config.getboolean("download", "no_merge", vars=args):
        logger.info("Merging segments...")
        merge(
            info=dl.video_info,
            data_dir=dl.output_dir,
            keep_concat=config.getboolean("download", "keep_concat", vars=args),
            delete_source=config.getboolean("download", "delete_source", vars=args)
        )


def merge_mode(config, args):
    log_enabled(config, args, "merge")

    setup_logger(
        args["PATH"] + sep + "merge.log",
        config.get("merge", "log_level", vars=args)
    )

    data_path = path.abspath(args["PATH"])
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


def log_enabled(config, args, mode_str):
    """Sanitize log level input value, return False if disabled by user."""
    if level := config.get(mode_str, "log_level", vars=args):
        # if level == "NONE":
        #     return False
        log_level = getattr(logging, level, None)
        if not isinstance(log_level, int):
            raise ValueError(f'Invalid log-level for {mode_str} mode: {level}')
    return True


def setup_logger(output_dir, loglevel, log_to_file=True):
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
            filename=output_dir, delay=True
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
        "cookie": "",

        "delete_source": "False",
        "keep_concat": "False",
        "no_merge": "False",

        "email_notifications": "False",
        "smtp_server": "",
        "smtp_port": "",
        "email_address": "",
    }

    config = ConfigParser(
        CONFIG_DEFAULTS,
        # interpolation=None
    )
    # Set defaults for each section
    config.add_section("monitor")
    config.set("monitor", "scan_delay", "15.0")  # minutes
    config.add_section("download")
    config.set("download", "scan_delay", "2.0")  # minutes
    config.add_section("merge")
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
    print(f"parse_args() -> {args}")

    config = parse_config(config, args)
    print(f"parse_config() -> {[opt for sect in config.sections() for opt in config.options(sect)]}")

    args["func"](config, args)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # mail.send("We crashed", f"Crash traceback: {e}")
        raise
    logging.shutdown()
