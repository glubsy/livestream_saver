import logging
import re
from datetime import datetime
from json import loads
from typing import Dict, Optional

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def get_base_url_from_itag(_json: Dict, itag: int) -> str:
    """Get the URL corresponding to the specified itag from the json."""
    url = None
    for _dict in _json['streamingData']['adaptiveFormats']:
        if _dict.get('itag', None) == itag:
            url = _dict.get('url', None)
            break
    if url is None:
        raise Exception(f"Failed getting url key for itag {itag}.")
    return url


def get_video_id(url: str) -> str:
    # Argument format:
    # https://youtu.be/njrI8ZDQ7ho or https://youtube.com/?v=njrI8ZDQ7ho
    video_id = ""
    if "?v=" in url:
        video_id = url.split("v=")[1]
    elif "youtu.be" in url:
        video_id = url.split('/')[-1]
    
    if "&pp=" in video_id:
        video_id = video_id.split("&pp=")[0]

    if len(video_id) != 11:
        raise ValueError(f"Invalid video ID length for \
\"{video_id}\": {len(video_id)}. Expected 11.")
    return video_id


def get_video_id_re(url_pattern: str) -> str:
    """
    Naive way to get the video ID from the canonical URL.
    """
    import re
    pattern = r"(?:v=|\/)([0-9A-Za-z_-]{11}).*"
    regex = re.compile(pattern)
    results = regex.search(url_pattern)
    if not results:
        logger.warning(f"Error while looking for {url_pattern}.")
        raise Exception
    logger.info(f"matched regex search: {url_pattern}.")
    return results.group(1)


def initial_player_response(html: str=None) -> str:
    if not html:
        raise ValueError(f"Invalid html: {html}")

    _json: str = ""

    # logger.debug(f"Raw response:\n{content_page}")
    if "ytInitialPlayerResponse =" in html:
        _json = html.split("ytInitialPlayerResponse = ")[1]\
                            .split(";var meta = document.")[0]
    # HACK This a bit wonky, as this has to be tested _after_ the
    # ytInitialPlayerResponse, as in some pages both are present.
    # Might need some refactoring.
    elif "var ytInitialData =" in html:
        _json = html.split("var ytInitialData = ")[1]\
                            .split(';</script><link rel="canonical')[0]
    else:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"JSON after split:\n{_json}")
        raise Exception("Could not find ytInitialData nor \
ytInitialPlayerResponse in the GET request!")
    return _json


def str_as_json(string: str) -> Dict:
    """Return :param string str as a python json object."""
    try:
        j = loads(string)
    except Exception as e:
        logger.critical(f"Error loading JSON from string: {e}")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"get_json_from_string: {string}")
        raise
    return j


# from pytube.extract
def publish_date(watch_html: Optional[str]=None):
    """Extract publish date
    :param str watch_html:
        The html contents of the watch page.
    :rtype: str
    :returns:
        Publish date of the video.
    """
    if not watch_html:
        return None
    try:
        regex = re.compile(r"(?<=itemprop=\"datePublished\" content=\")\d{4}-\d{2}-\d{2}")
        if match := regex.search(watch_html):
            return datetime.strptime(match.group(0), '%Y-%m-%d')
    except Exception as e:
        logger.debug(f"Error looking for publish date: {e}")
    return None
