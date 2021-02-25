import logging
import argparse

from livestream_saver.download import *
from livestream_saver.util import *

logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('url', type=str, help='Youtube URL to download.')
    parser.add_argument('-y', '--youtube-dl', action='store', default=False, type=bool,
                    help='Use youtube-dl to get the download urls.')
    parser.add_argument('-c', '--cookie', action='store', default="./cookie.txt", type=str,
                    help='Path to cookie file.')
    parser.add_argument('-q', '--max_video_quality', action='store', default=None, type=int,
                    help='Use best available video resolution up to this height in pixels.')
    parser.add_argument('-o', '--output_dir', action='store', default="./", type=str,
                    help='Output directory where to write downloaded chunks.')
    parser.add_argument('-d', '--debug', action='store', default=True, type=bool,
                    help='Debug mode, verbose.')
    args = parser.parse_args()
    logger.debug(f"Arguments: url={args.url} cookie={args.cookie}")
    return args


if __name__ == "__main__":
    args = parse_args()

    logfile = logging.FileHandler(filename=args.output_dir + os.sep + "livestream_saver.log", delay=True)
    logfile.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logfile.setFormatter(formatter)
    logger.addHandler(logfile)

    conhandler = logging.StreamHandler()
    conhandler.setLevel(logging.DEBUG)
    conhandler.setFormatter(formatter)
    logger.addHandler(conhandler)

    cookie = get_cookie(args.cookie) if args.cookie else {}

    dl = YoutubeLiveStream(args.url, args.output_dir, args.max_video_quality, cookie)
    dl.download()

    if dl.done:
        logger.info(f"Finished downloading {dl.video_id}.") 

    # TODO
    # merge_parts() # make symlinks in /tmp?
    # delete_parts()



# Use Youtube-DL to get the json with the bootstrap links?
# Get the DASH manifest?
# TODO?
# use youtube-dl to get urls from page
# or use the initial json
# or use pytube library
