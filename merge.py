from os import sep, path, remove
from json import load
import argparse
from livestream_saver import merge

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=str,
        help='Path to directory holding vid/aud sub-directories \
in which segments have been downloaded as well as the metadata.txt file.')
    parser.add_argument('-d', '--delete_source', action='store',
        default=False, type=bool,
        help='Delete source files once final merge has been done.')
    parser.add_argument('-o', '--output_dir', action='store',
        default=None, type=str,
        help='Output directory where to write final merged file.')
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    args = parse_args()
    data_path = path.abspath(args.path)
    info = merge.get_metadata_info(data_path)
    written_file = merge.merge(info=info,
                                data_dir=out_path,
                                output_dir=args.output_dir,
                                delete_source=args.delete_source)
