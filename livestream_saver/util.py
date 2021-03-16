import logging
import re
import pathlib

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


def parse_cookie_file(cookiefile):
    cookies = {}
    with open(cookiefile, 'r') as fp:
        content = fp.read()
        for line in content.split('\n'):
            if line.startswith('#'):
                continue
            if 'youtube' in line:
                elements = line.split('\t')
                cookies[elements[-2]] = elements[-1]
    return cookies


def get_cookie(path):
    try:
        cookie_path = pathlib.Path(path).absolute()
        if not cookie_path.exists():
            logger.debug("Cookie file does not exist, defaulting to empty cookie...")
            return {}

        logger.debug(f"Found cookie at {cookie_path}")
        cookie_content = parse_cookie_file(cookie_path)
        if cookie_content == {}:
            logger.debug("Empty cookie!")
        else:
            return cookie_content
    except Exception as e:
        logger.debug(f"Could not parse cookie, defaulting to empty cookie. Error: {e}")
        return {}

def get_channel_id(url_pattern):
    """
    Naive way to get the channel id from channel canonical URL.
    """
    # FIXME allow for passing only ID hash intead of full url
    if "channel" in url_pattern: # /channel/HASH
        pattern = r".*(channel\/)([0-9A-Za-z_-]{24}).*"
        regex = re.compile(pattern)
        results = regex.search(url_pattern)
        if not results:
            logger.error(f"Error while looking for channel {url_pattern}")
        logger.debug(f"matched regex search: {url_pattern}: {results.group(2)}")
        return results.group(2)
    else: # /c/NAME
        return url_pattern.split('/c/')[-1]

