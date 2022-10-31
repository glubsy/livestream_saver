import logging
import re
from datetime import datetime
from typing import Dict, Optional
from livestream_saver.util import str_as_json

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def get_browseId_from_json(json: Dict) -> Optional[str]:
    """
    Return the browseId for this channel. Useful if we only know the vanity url.
    Eg. https://www.youtube.com/c/dokuganP -> UC_HrzgYmapddmGSfn2UMHTA
    """
    serviceTrackingParams = json.get("responseContext", {}).get("serviceTrackingParams", [])
    for service in serviceTrackingParams:
        if params := service.get("params", []):
            for param in params:
                if param.get("key") == "browse_id":
                    return param.get("value")


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


def initial_player_response(html: Optional[str] = None) -> str:
    if not html:
        raise ValueError(f"Invalid html: {html}")

    # logger.debug(f"Raw response:\n{content_page}")
    if "ytInitialPlayerResponse =" in html:
        _json = html.split("ytInitialPlayerResponse = ")[1]\
                            .split(";var meta = document.createElement('meta');")[0]
    # HACK This a bit wonky, as this has to be tested _after_ the
    # ytInitialPlayerResponse, as in some pages both are present.
    # Might need some refactoring.
    elif "var ytInitialData =" in html:
        _json = html.split("var ytInitialData = ")[1]\
                            .split(';</script><script nonce="')[0]
    else:
        logger.critical(f"Failed to extract JSON. HTML content:\n{_json}")
        raise Exception(
            "Could not find ytInitialData nor ytInitialPlayerResponse in HTML."
        )
    return str_as_json(_json)


# from pytube.extract, with some modifications
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
