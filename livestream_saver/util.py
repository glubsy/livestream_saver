import re
from os import makedirs
from platform import system
from pathlib import Path
from typing import Optional, Tuple, Iterable
import logging

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)

from pytube.itags import ITAGS


# Youtube channel IDs are 24 characters
YT_CH_HASH_RE = re.compile(r".*(channel\/)?([0-9A-Za-z_-]{24}).*|.*youtube\.com\/c\/(.*)")
# YT_CH_ID_HASH_RE = re.compile(r"^[0-9A-Za-z_-]{24}$")
# YT_CH_NAME_RE = re.compile(r".*youtube\.com\/c\/(.*)")

MAX_NAME_LEN = 255

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


def sanitize_channel_url(url: str) -> str:
    # FIXME needs smarter safeguard
    if "http" not in url and "youtube.com" not in url:
        url = f"https://www.youtube.com/channel/{url}"
    if url.endswith("/"):
        url = url[:-1]
    return url


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


def sanitize_filename(filename: str) -> str:
    """Remove characters in name that are illegal in some file systems, and
    make sure it is not too long, including the extension."""
    extension = ""
    ext_idx = filename.rfind(".")
    if ext_idx > -1:
        extension = filename[ext_idx:]
        if not extension.isascii():
            # There is a risk that we failed to detect an actual extension.
            # Only preserve extension if it is valid ASCII, otherwise ignore it.
            extension = ""

    if extension:
        filename = filename[:-len(extension)]

    filename = "".join(
        c for c in filename if 31 < ord(c) and c not in r'<>:"/\|?*'
    )
    logger.debug(f"filename {filename}, extension {extension}")

    if not filename.isascii():
        name_bytes = filename.encode('utf-8')
        length_bytes = len(name_bytes)
        logger.debug(
            f"Length of problematic filename is {length_bytes} bytes "
            f"{'<' if length_bytes < MAX_NAME_LEN else '>='} {MAX_NAME_LEN}")
        if length_bytes > MAX_NAME_LEN:
            filename = simple_truncate(filename, MAX_NAME_LEN - len(extension))
    else:
        # Coerce filename length to 255 characters which is a common limit.
        filename = filename[:MAX_NAME_LEN - len(extension)]

    logger.debug(f"Sanitized name: {filename + extension} "
              f"({len((filename + extension).encode('utf-8'))} bytes)")
    assert(
        len(
            filename.encode('utf-8') + extension.encode('utf-8')
        ) <= MAX_NAME_LEN
    )
    return filename + extension


def simple_truncate(unistr: str, maxsize: int) -> str:
    # from https://joernhees.de/blog/2010/12/14/how-to-restrict-the-length-of-a-unicode-string/
    import unicodedata
    if not unicodedata.is_normalized("NFC", unistr):
        unistr = unicodedata.normalize("NFC", unistr)
    return str(
        unistr.encode("utf-8")[:maxsize],
        encoding="utf-8", errors='ignore'
    )


def check_available_tracks_from_itags(itags: Tuple[int, ...]) -> tuple[bool, bool, set]:
    """
    Return whether at least one video track and one audio track are provided
    by all the supplied itags combined. Invalid itags are returned if not found
    in known itags.
    """
    has_video = False
    has_audio = False
    invalid = set()
    for itag in itags:
        if itag not in ITAGS.keys():
            invalid.add(itag)
            continue
        if ITAGS[itag][0] is not None: # video track
            has_video = True
        if ITAGS[itag][1] is not None: # audio track
            has_audio = True
    return has_video, has_audio, invalid


def split_by_plus(itags: Optional[str]) -> Optional[Tuple[int, ...]]:
    """Return string of itags separated by +, as a tuple of ints.
    >>> split_by_plus("134+140")
    (134, 140)
    >>> split_by_plus("140")
    (140,)
    >>> split_by_plus("222+140")  #  222 does not exist but still returned
    (222, 140)
    >>> split_by_plus("a+b")
    None
    """
    if not itags:
        return None

    itags = itags.strip()
    if len(itags) == 0:
        return None

    itags = "".join(c for c in itags if c == "+" or 48 <= ord(c) <= 57)

    if len(itags) == 0:
        return None

    splits = itags.split('+')

    # if len(splits) == 0:
    #     return None

    # Remove empty entries if any
    splits = tuple(filter(lambda x: x != '', splits))

    if len(splits) == 0:
        return None

    # def filter_unknown(number):
    #     if number not in ITAGS.keys():
    #         logger.warning(
    #             f"Invalid itag supplied: {number} not found in known itags."
    #         )
    #         return False
    #     return True
    # filtered = tuple(filter(filter_unknown, (int(num) for num in splits)))

    # if len(filtered) > 0:
        # return filtered
        
    as_ints = tuple(int(num) for num in splits)
    return as_ints if as_ints else None


def is_wanted_based_on_metadata(
    data: Iterable[Optional[str]], 
    allow_re: re.Pattern = None,
    block_re: re.Pattern = None
    ) -> bool:
    """Test each RE against each item in data (title, description...)"""
    if allow_re is None and block_re is None:
        return True
    wanted = True
    blocked = False

    if allow_re is not None:
        wanted = False
    if block_re is not None:
        blocked = True

    for item in data:
        if not item:
            continue
        if allow_re and allow_re.search(item):
            wanted = True
        if block_re and block_re.search(item):
            blocked = True
    
    if blocked:
        return False
    return wanted


# Base name for each "event"
event_props = [
    "on_upcoming_detected",
    "on_video_detected",
    "on_download_initiated",
    "on_download_started",
    "on_download_ended",
    "on_merge_done",
]

UA = get_system_ua()
