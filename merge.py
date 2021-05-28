#!/bin/env python3
from os import sep, path
import argparse
from livestream_saver import merge
import logging

logger = logging.getLogger("livestream_saver")
logger.setLevel(logging.DEBUG)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=str,
        help='Path to directory holding vid/aud sub-directories \
in which segments have been downloaded as well as the metadata.txt file.')
    parser.add_argument('-d', '--delete_source', action='store_true',
        help='Delete source files (vid/aud) once final merging of \
streams has been successfully done.')
    parser.add_argument('-k', '--keep_concat', action='store_true',
        help='Keep concatenated intermediary files even if merging of \
streams has been successful. This is only useful for debugging.')
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
    log_level = getattr(logging, args.log.upper(), None)
    if not isinstance(log_level, int):
        raise ValueError(f'Invalid log level: {args.log}')

    # File output
    logfile = logging.FileHandler(\
        filename=args.path + sep +  "download.log", delay=True)
    logfile.setLevel(logging.DEBUG)
    formatter = logging.Formatter(\
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logfile.setFormatter(formatter)
    logger.addHandler(logfile)

    # Console output
    conhandler = logging.StreamHandler()
    conhandler.setLevel(log_level)
    conhandler.setFormatter(formatter)
    logger.addHandler(conhandler)

    data_path = path.abspath(args.path)
    info = merge.get_metadata_info(data_path)
    written_file = merge.merge(\
                            info=info,\
                            data_dir=data_path,\
                            output_dir=args.output_dir,\
                            keep_concat=args.keep_concat,\
                            delete_source=args.delete_source)

    if not written_file:
        logger.critical("Something failed. Please report the issue with logs.")
