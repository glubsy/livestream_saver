from os import sep, path
from json import load
import argparse
from livestream_saver import merge

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=str,
        help='Path to directory holding vid/aud sub-directories \
in which segments have been downloaded as well as the metadata.txt file.')
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    args = parse_args()
    path = path.abspath(args.path)
    info = merge.get_metadata_info(path)

    print(f"Wrote file \"{merge.merge(info, path)}\"")