from os import sep, path, remove
from json import load
import argparse
from livestream_saver import merge
import logging

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=str,
        help='Path to directory holding vid/aud sub-directories \
in which segments have been downloaded as well as the metadata.txt file.')
    parser.add_argument('-d', '--delete_source', action='store',
        default=False, type=bool,
        help='Delete source files once final merging of stream has been successfully done.')
    parser.add_argument('-o', '--output_dir', action='store',
        default=None, type=str,
        help='Output directory where to write final merged file.')
    parser.add_argument('--log', action='store', default="INFO", type=str,
        help='Log level. [DEBUG, INFO, WARNING, ERROR, CRITICAL]')
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    args = parse_args()

    # Sanitize log level input
    numeric_level = getattr(logging, args.log.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f'Invalid log level: {args.log}')
    logging.basicConfig(level=numeric_level)

    data_path = path.abspath(args.path)
    info = merge.get_metadata_info(data_path)
    written_file = merge.merge(\
                            info=info,\
                            data_dir=data_path,\
                            output_dir=args.output_dir,\
                            delete_source=args.delete_source)
