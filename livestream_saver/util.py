import logging
import re
from os import makedirs
from platform import system
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)

# Youtube channel IDs are 24 characters
YT_CH_HASH_RE = re.compile(r".*(channel\/)?([0-9A-Za-z_-]{24}).*|.*youtube\.com\/c\/(.*)")
# YT_CH_ID_HASH_RE = re.compile(r"^[0-9A-Za-z_-]{24}$")
# YT_CH_NAME_RE = re.compile(r".*youtube\.com\/c\/(.*)")


def get_channel_id(str_url, service_name):
    """
    Naive way to get the channel id from channel canonical URL.
    :param pattern str: URL to channel or channel ID directly.
    """
    if service_name == "youtube":
        if match := YT_CH_HASH_RE.search(str_url):
            logger.debug(f"Matched regex: {str_url}: {match.group(1)}")
            return match.group(2) if match.group(2) else match.group(3)

        if "youtube" not in str_url:
            raise Exception("Not a youtube URL.")

        if '/watch' in str_url:
            raise Exception("Not a valid channel URL. Is this a video URL?")
        
        # Apparently this also exists: https://www.youtube.com/recordedamigagames
        if 'youtube.com/' in str_url:
            return str_url.split("/")[-1]

    raise Exception(f"No valid channel ID found in \"{str_url}\".")


def create_output_dir(output_dir: Path, video_id: Optional[str]) -> Path:
    capture_dirpath = output_dir
    if video_id is not None:
        capture_dirname = f"stream_capture_{video_id}"
        capture_dirpath = output_dir / capture_dirname
    logger.debug(f"Creating output_dir: {capture_dirpath}...")
    makedirs(capture_dirpath, 0o766, exist_ok=True)
    return capture_dirpath

def get_system_ua():
    SYSTEM = system()
    if SYSTEM == 'Windows':
        return 'Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:90.0) Gecko/20100101 Firefox/90.0'
    if SYSTEM == 'Darwin':
        return 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:89.0) Gecko/20100101 Firefox/89.0'
    return 'Mozilla/5.0 (X11; Linux x86_64; rv:89.0) Gecko/20100101 Firefox/89.0'

