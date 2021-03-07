from os import sep
import argparse
# import logging
import livestream_saver.monitor
from livestream_saver.util import get_cookie

# logger = logging.getLogger(__name__)

def parse_args():
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
    parser.add_argument('--use_api', action='store', default=False, type=bool,
                    help='Use the official API to make requests, use secrets.json')
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    args = parse_args()

    cookie = get_cookie(args.cookie) if args.cookie else {}

    livestream_saver.monitor.monitor(args, cookie)
