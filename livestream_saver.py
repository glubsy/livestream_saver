#!/bin/env python3
from os import sep, makedirs, path
import argparse
import logging
from livestream_saver.monitor import YoutubeChannel, wait_block
from livestream_saver.download import YoutubeLiveStream
from livestream_saver.merge import merge, get_metadata_info
from livestream_saver.util import YoutubeUrllibSession, get_channel_id

logger = logging.getLogger('livestream_saver')
logger.setLevel(logging.DEBUG)


def parse_args():
    parser = argparse.ArgumentParser(
        description='Monitor a Youtube channel for any active live stream and \
record live streams from the first segment.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    log_levels = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
    subparsers = parser.add_subparsers(dest='sub-command',
                                       help='Required sub-command.',
                                       required=True)

    monitor_parser = subparsers.add_parser('monitor',
        help='Monitor a given Youtube channel for activity.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    monitor_parser.set_defaults(func=_monitor)
    monitor_parser.add_argument('URL', type=str,
        help='The Youtube channel to monitor for live streams. \
Either a full youtube URL, /channel/ID, or /c/name format.'
    )
    monitor_parser.add_argument('--log', action='store', type=str.upper,
        default='INFO', choices=log_levels, help='Log level.'
    )
    monitor_parser.add_argument('-c', '--cookie', action='store',
        default=None, type=str,
        help='Path to Netscape formatted cookie file.'
    )
    monitor_parser.add_argument('-q', '--max_video_quality', action='store',
        default=None, type=int,
        help='Use best available video resolution up to this height in pixels.'
    )
    monitor_parser.add_argument('-o', '--output_dir', action='store',
        default='./', type=str,
        help='Output directory where to save channel data.'
    )
    monitor_parser.add_argument('--channel_name', action='store',
        default=None, type=str,
        help='User-defined name of the channel to monitor.'
    )
    monitor_parser.add_argument('-d', '--delete_source', action='store_true',
        help='Delete source segment files once the final \
merging of them has been done.'
    )
    monitor_parser.add_argument('-k', '--keep_concat', action='store_true',
        help='Keep concatenated intermediary files even if \
merging of streams has been successful. Only useful for troubleshooting.'
    )
    # monitor_parser.add_argument('--interactive', action='store_true',
    #    help='Allow user input to skip the current download.')
    monitor_parser.add_argument('--scan_delay', action='store',
        default=10.0, type=float,
        help='Interval in minutes to scan for channel activity.'
    )

    download_parser = subparsers.add_parser('download',
        help='Download a given live stream by URL.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    download_parser.set_defaults(func=_download)
    download_parser.add_argument('URL', type=str, 
        help='Youtube video stream URL to download.'
    )
    download_parser.add_argument('--log', action='store', type=str.upper,
        default='INFO', choices=log_levels, help='Log level.'
    )
    download_parser.add_argument('-c', '--cookie', action='store',
        default=None, type=str,
        help='Path to Netscape formatted cookie file.'
    )
    download_parser.add_argument('-q', '--max_video_quality', action='store',
        default=None, type=int,
        help='Use best available video resolution up to this height in pixels.'
    )
    download_parser.add_argument('-o', '--output_dir', action='store',
        default='./', type=str,
        help='Output directory where to write downloaded chunks.'
    )
    download_parser.add_argument('-d', '--delete_source', action='store_true',
        help='Delete source files once final merge has been done.'
    )
    download_parser.add_argument('-k', '--keep_concat', action='store_true',
        help='Keep concatenated intermediary files even if merging of \
streams has been successful. Only useful for troubleshooting.'
    )
    download_parser.add_argument('--scan_delay', action='store',
        default=120.0, type=float,
        help='Interval in seconds to scan for status update.'
    )

    merge_parser = subparsers.add_parser('merge',
        help='Merge downloaded segments into one single video file.'
    )
    merge_parser.set_defaults(func=_merge)
    merge_parser.add_argument('PATH', type=str,
        help='Path to directory holding vid/aud sub-directories \
in which segments have been downloaded as well as the metadata.txt file.'
    )
    merge_parser.add_argument('--log', action='store', type=str.upper,
        default='INFO', choices=log_levels, help='Log level.'
    )
    merge_parser.add_argument('-d', '--delete_source', action='store_true',
        help='Delete source files (vid/aud) once final merging of \
streams has been successfully done.'
    )
    merge_parser.add_argument('-k', '--keep_concat', action='store_true',
        help='Keep concatenated intermediary files even if merging of \
streams has been successful. This is only useful for debugging.'
    )
    merge_parser.add_argument('-o', '--output_dir', action='store',
        default=None, type=str,
        help='Output directory where to write final merged file.'
    )

    return parser.parse_args()


def _monitor(args):
    channel_id = get_channel_id(args.URL)

    if not args.channel_name:
        output_dir = args.output_dir + sep + channel_id
    else:
        output_dir = args.output_dir + sep + args.channel_name
    makedirs(output_dir, exist_ok=True)

    setup_logger(output_dir + f'{sep}monitor_{channel_id}.log', args.log)

    # FIXME sanitize url in case it points to a video stream url not a channel
    session = YoutubeUrllibSession(args.cookie)
    ch = YoutubeChannel(args, channel_id, session)
    logger.info(f"Monitoring channel: {ch.info.get('id')}")

    while True:
        live_videos = ch.get_live_videos()
        logger.debug(f"Live videos found for channel {ch.get_name()}: {live_videos}")

        if not live_videos:
            wait_block(min_minutes=args.scan_delay, variance=3.5)
            continue

        target_live = live_videos[0]
        _id = target_live.get('videoId')
        stream = None

        try:
            stream = YoutubeLiveStream(
                url=f"https://www.youtube.com{target_live.get('url')}",
                output_dir=output_dir,
                session=ch.session,
                video_id=_id,
                max_video_quality=args.max_video_quality,
                log_level=args.log
            )
        except ValueError as e:
            logger.critical(e)
            wait_block(min_minutes=args.scan_delay, variance=3.5)
            continue

        logger.info(f"Found live: {_id} \
title: {target_live.get('title')}. Downloading...")

        try:
            stream.download()
        except Exception:
            pass

        if stream.done:
            logger.info(f"Finished downloading {_id}. Merging segments...")
            # TODO in a separate thread?
            # logger.info(f"Merging segments for {_id}...")
            merge(info=stream.video_info,
                  data_dir=stream.output_dir,
                  keep_concat=args.keep_concat,
                  delete_source=args.delete_source)
            # TODO get the updated stream title from the channel page if
            # the stream was recorded
        if stream.error:
            # TODO Send notification to admin here
            logger.info(f"Sending notification... {stream.error}")
        wait_block(min_minutes=args.scan_delay, variance=3.5)


def _download(args):
    setup_logger(args.output_dir + sep + "downloader.log", args.log)

    session = YoutubeUrllibSession(args.cookie)
    try:
        dl = YoutubeLiveStream(
            url=args.URL,\
            output_dir=args.output_dir,\
            session=session,\
            max_video_quality=args.max_video_quality,\
            log_level=args.log
        )
    except ValueError as e:
        logger.critical(e)
        exit(1)

    dl.download(args.scan_delay)

    if dl.done:
        merge(info=dl.video_info,\
              data_dir=dl.output_dir,\
              keep_concat=args.keep_concat,\
              delete_source=args.delete_source)


def _merge(args):
    setup_logger(args.PATH + sep + "downloader.log", args.log)

    data_path = path.abspath(args.PATH)
    info = get_metadata_info(data_path)
    written_file = merge(\
                         info=info,\
                         data_dir=data_path,\
                         output_dir=args.output_dir,\
                         keep_concat=args.keep_concat,\
                         delete_source=args.delete_source)

    if not written_file:
        logger.critical("Something failed. Please report the issue with logs.")


def setup_logger(output_dir, loglevel):
    logfile = logging.FileHandler(\
        filename=output_dir, delay=True)
    logfile.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logfile.setFormatter(formatter)
    logger.addHandler(logfile)

    conhandler = logging.StreamHandler()
    conhandler.setLevel(loglevel)
    conhandler.setFormatter(formatter)
    logger.addHandler(conhandler)


def main():
    args = parse_args()
    # Sanitize log level input
    log_level = getattr(logging, args.log, None)
    if not isinstance(log_level, int):
        raise ValueError(f'Invalid log level: {args.log}')
    args.func(args)


if __name__ == "__main__":
    main()
    logging.shutdown()
