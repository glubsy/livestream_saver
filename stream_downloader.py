from os import sep
import logging
import argparse

import livestream_saver.download
import livestream_saver.merge
from livestream_saver.util import get_cookie

logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('url', type=str, help='Youtube URL to download.')
    # parser.add_argument('-y', '--youtube-dl', action='store', default=False, type=bool,
    #                 help='Use youtube-dl to get the download urls.')
    parser.add_argument('-c', '--cookie', action='store',
        default="./cookie.txt", type=str,
        help='Path to cookie file.')
    parser.add_argument('-q', '--max_video_quality', action='store', 
        default=None, type=int,
        help='Use best available video resolution up to this height in pixels.')
    parser.add_argument('-o', '--output_dir', action='store',
        default="./", type=str,
        help='Output directory where to write downloaded chunks.')
    parser.add_argument('-d', '--delete_source', action='store',
        default=False, type=bool,
        help='Delete source files once final merge has been done.')
    args = parser.parse_args()
    logger.debug(f"Arguments: url={args.url} cookie={args.cookie}")
    return args


if __name__ == "__main__":
    args = parse_args()

    logfile = logging.FileHandler(filename=args.output_dir + sep + "livestream_saver.log", delay=True)
    logfile.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logfile.setFormatter(formatter)
    logger.addHandler(logfile)

    conhandler = logging.StreamHandler()
    conhandler.setLevel(logging.DEBUG)
    conhandler.setFormatter(formatter)
    logger.addHandler(conhandler)

    cookie = get_cookie(args.cookie) if args.cookie else {}

    dl = livestream_saver.download.YoutubeLiveStream(
        url=args.url,\
        output_dir=args.output_dir,\
        max_video_quality=args.max_video_quality,\
        cookie=cookie
    )
    dl.download()

    if dl.done:
        logger.info(f"Finished downloading {dl.video_info.get('id')}.")

    # TODO
    # make sure number of segment match the last numbered segment
    
    # Merge segments into one file
    if dl.done:
        merge(info=dl.video_info, data_dir=dl.output_dir, delete_source=args.delete_source)


    # Use Youtube-DL to get the json with the bootstrap links?
    # Get the DASH manifest?
    # TODO?
    # use youtube-dl to get urls from page
    # or use the initial json
    # or use pytube library
