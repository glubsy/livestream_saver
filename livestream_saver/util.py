import logging
import re
from json import loads
from pathlib import Path
from urllib.request import Request, urlopen
from http.cookiejar import CookieJar, MozillaCookieJar

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


def parse_cookie_file(cookiefile):
    """Returns a dictionary of key value pairs from the Netscape cookie file."""
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
    if not path:
        return MozillaCookieJar()
    try:
        cookie_path = Path(path).absolute()
        if not cookie_path.exists():
            logger.debug("Cookie file does not exist, defaulting to empty cookie...")
            return None
        logger.debug(f"Found cookie file at {cookie_path}")
        # return _get_cookie_dict(path)
        return _get_cookie_jar(path)
    except Exception as e:
        logger.debug(f"Could not parse cookie, defaulting to empty cookie. Error: {e}")
        return MozillaCookieJar()


def _get_cookie_jar(cookie_path):
    """Necessary for urllib.request."""
    cj = MozillaCookieJar()
    cj.load(cookie_path)
    logger.debug(f"Cookie jar: {cj}")
    return cj


def _get_cookie_dict(path):
    """Basic dictionary from cookie file. Used by Requests module."""
    cookie_content = parse_cookie_file(cookie_path)
    if not cookie_content:
        logger.warning("Empty cookie file!")
    return cookie_content


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
    elif '/' in url_pattern: # /c/NAME
        return url_pattern.split('/c/')[-1]
    else:
        return url_pattern


class YoutubeUrllibSession:
    """
    Keep cookies in memory for reuse or update.
    """
    def __init__(self, cookie_path=None):
        self.cookie_jar = get_cookie(cookie_path) # cookie jar
        # TODO add proxies
        # TODO randomized headers?
        self.headers = {
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 \
    (KHTML, like Gecko) Chrome/88.0.4324.96 Safari/537.36',
        'accept-language': 'en-US,en'
        }

    def make_request(self, url):
        req = Request(url, headers=self.headers)
        self.cookie_jar.add_cookie_header(req)
        return parse_response(req)


def parse_response(req):
    """
    Extract the initial JSON from the HTML in the request response.
    """
    # We could also use youtube-dl --dump-json instead
    with urlopen(req) as res:
        logger.debug(f"GET {req.url}\n\
Status code: {res.status}.\n\
Headers:\n{res.headers}")
        if res.status == 429:
            logger.critical("Error 429. Too many requests? \
Please try again later or get a new IP (also a new cookie?).")
            return None

        try:
            _json = ""
            content_page = str(res.read().decode('utf-8'))
            # logger.debug(f"Raw response:\n{content_page}")
            if "ytInitialPlayerResponse =" in content_page:
                _json = content_page.split("ytInitialPlayerResponse = ")[1]\
                                    .split(";var meta = document.")[0]
            # HACK This a bit wonky, as this has to be tested _after_ the
            # ytInitialPlayerResponse, since it might occur on pages with both
            # substrings. This might need some refactoring.
            elif "var ytInitialData =" in content_page:
                _json = content_page.split("var ytInitialData = ")[1]\
                                    .split(';</script><link rel="canonical')[0]
            else:
                raise "Could not find ytInitialData nor ytInitialPlayerResponse\
in the GET request!"
            # logger.debug(f"JSON after split: {_json}")
            return _json
        except Exception as e:
            logger.critical(f"Failed loading initial data response. {e}.")
            raise e


def get_json_from_string(string):
    # logger.debug(f"get_json_from_string: {string}")
    try:
        j = loads(string)
    except Exception as e:
        logger.critical(f"Error loading json from string: {e}")
        return None
    return j