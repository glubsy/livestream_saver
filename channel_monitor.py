#!/bin/env python3
from os import sep, makedirs
import argparse
import logging
from livestream_saver.monitor import YoutubeChannel, wait_block
from livestream_saver.download import YoutubeLiveStream
from livestream_saver.merge import merge
from livestream_saver.util import YoutubeUrllibSession, get_channel_id

logger = logging.getLogger("livestream_saver")
logger.setLevel(logging.DEBUG)

def monitor(args):
    # Sanitize log level input
    log_level = getattr(logging, args.log.upper(), None)
    if not isinstance(log_level, int):
        raise ValueError(f'Invalid log level: {args.log}')

    channel_id = get_channel_id(args.url)
    if not args.channel_name:
        output_dir = args.output_dir + sep + channel_id
    else:
        output_dir = args.output_dir + sep + args.channel_name
    makedirs(output_dir, exist_ok=True)

    # Logging file output
    logfile = logging.FileHandler(
        filename=output_dir + f"{sep}monitor_{channel_id}.log", delay=True)
    # FIXME by default level DEBUG into file
    logfile.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logfile.setFormatter(formatter)
    logger.addHandler(logfile)

    # Console output
    conhandler = logging.StreamHandler()
    conhandler.setLevel(log_level)
    conhandler.setFormatter(formatter)
    logger.addHandler(conhandler)

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
        stream = YoutubeLiveStream(
            url=f"https://www.youtube.com{target_live.get('url')}",
            output_dir=output_dir,
            session=ch.session,
            video_id=_id,
            max_video_quality=args.max_video_quality,
            log_level=log_level
        )
        logger.info(f"Found live: {_id} \
title: {target_live.get('title')}. Downloading...")
        stream.download()
        if stream.done:
            logger.info(f"Finished downloading {_id}. Merging segments...")
            # TODO in a separate thread?
            # logger.info(f"Merging segments for {_id}...")
            merge(info=stream.video_info, data_dir=stream.output_dir, delete_source=args.delete_source)
            # TODO get the updated stream title from the channel page if the stream was recorded
        if stream.error:
            # TODO Send notification to admin here
            logger.info(f"Sending notification... {stream.error}")
        wait_block(min_minutes=args.scan_delay, variance=3.5)


def setup_ouput_path(path):
    """Create directory for each channel, which in turn holds the merged videos."""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('url', type=str,
        help='Youtube Channel to monitor for live streams. \
Either a full youtube URL or /channel/hash format.')
    parser.add_argument('-c', '--cookie', action='store',
                        default=None, type=str,
                        help='Path to Netscape formatted cookie file.')
    parser.add_argument('-q', '--max_video_quality', action='store',
                        default=None, type=int,
                        help='Use best available video resolution up to this height in pixels.')
    parser.add_argument('-o', '--output_dir', action='store',
                        default="./", type=str,
                        help='Output directory where to save channel data.')
    parser.add_argument('--channel_name', action='store',
                        default=None, type=str,
                        help='User-defined name of the channel to monitor.')
    parser.add_argument('-d', '--delete_source', action='store_true',
                        help='Delete source segment files once the final \
merging of them has been done.')
    # parser.add_argument('--interactive', action='store_true',
    #                     help='Allow user input to skip the current download.')
    parser.add_argument('--scan_delay', action='store',
                        default=10.0, type=float,
                        help='Interval in minutes to scan for activity (default 10.0).')
    parser.add_argument('--log', action='store',
                        default="INFO", type=str,
                        help='Log level. [DEBUG, INFO, WARNING, ERROR, CRITICAL]')
    args = parser.parse_args()
    monitor(args)
    logging.shutdown()
