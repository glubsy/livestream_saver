from os import sep
import argparse
import logging
from livestream_saver.monitor import YoutubeRequestSession, YoutubeChannel, wait_block
from livestream_saver.download import YoutubeLiveStream
from livestream_saver.merge import merge
import livestream_saver.util

logger = logging.getLogger("livestream_saver")
logger.setLevel(logging.DEBUG)

def monitor(args, cookie):
    # Sanitize log level input
    log_level = getattr(logging, args.log.upper(), None)
    if not isinstance(log_level, int):
        raise ValueError(f'Invalid log level: {args.log}')

    # File output
    channel_id = livestream_saver.util.get_channel_id(args.url)
    logfile = logging.FileHandler(
        filename=args.output_dir + f"{sep}monitor_{channel_id}.log", delay=True)
    # FIXME by default logs debug into file
    logfile.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logfile.setFormatter(formatter)
    logger.addHandler(logfile)

    # Console output
    conhandler = logging.StreamHandler()
    conhandler.setLevel(log_level)
    conhandler.setFormatter(formatter)
    logger.addHandler(conhandler)

    session = YoutubeRequestSession(cookie)
    ch = YoutubeChannel(args, channel_id, session)
    logger.info(f"Monitoring channel: {ch.info.get('id')}")

    while True:
        live_videos = ch.get_live_videos()
        logger.debug(f"Live videos found for channel {ch.get_name()}: {live_videos}")

        if not live_videos:
            wait_block(min_minutes=0, variance=0.5)
            continue

        target_live = live_videos[0]
        _id = target_live.get('videoId')
        stream = YoutubeLiveStream(
            url=f"https://www.youtube.com{target_live.get('url')}",
            output_dir=args.output_dir,
            cookie=cookie,
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
        wait_block(min_minutes=0, variance=0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('url', type=str,
        help='Youtube Channel URL to monitor for live streams.')
    parser.add_argument('-c', '--cookie', action='store', default="./cookie.txt", type=str,
                    help='Path to cookie file.')
    parser.add_argument('-q', '--max_video_quality', action='store', default=None, type=int,
                    help='Use best available video resolution up to this height in pixels.')
    parser.add_argument('-o', '--output_dir', action='store', default="./", type=str,
                    help='Output directory where to write downloaded chunks.')
    parser.add_argument('-d', '--delete_source', action='store', default=False, type=bool,
                    help='Delete source files once final merge has been done.')
    parser.add_argument('--interactive', action='store', default=False, type=bool,
                    help='Allow user input to skip the current download.')
    parser.add_argument('--log', action='store', default="INFO", type=str,
        help='Log level. [DEBUG, INFO, WARNING, ERROR, CRITICAL]')
    args = parser.parse_args()

    cookie = livestream_saver.util.get_cookie(args.cookie) if args.cookie else {}

    monitor(args, cookie)
    logging.shutdown()
