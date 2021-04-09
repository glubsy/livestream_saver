import logging
import re
from time import sleep
from random import randint
from json import loads
from pathlib import Path
from urllib.request import Request, urlopen
from http.cookiejar import Cookie, MozillaCookieJar
from http.cookies import SimpleCookie

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


def get_cookie(path):
    # CookieJar now instead of a dict
    return _get_cookie_jar(path)


def _get_cookie_jar(cookie_path):
    """Necessary for urllib.request."""
    cj = MozillaCookieJar()
    if not cookie_path:
        return cj
    try:
        cj.load(Path(cookie_path).absolute(), ignore_expires=True)
    except Exception as e:
        logger.error(f"Failed to load cookie file {cookie_path}: {e}. \
Defaulting to empty cookie.")
    # logger.debug(f"Cookie jar: {cj}")

    # TODO Make sure the necessary youtube cookies are there, ie. LOGIN_INFO,
    # APISID, CONSENT, HSID, NID, PREF, SID, SIDCC, SSID, VISITOR_INFO1_LIVE,
    # __Secure-3PAPISID, __Secure-3PSID, __Secure-3PSIDCC, etc.
    # otherwise we risk silently losing data!
    for cookie in cj:
        if "youtube" in cookie.domain and cookie.is_expired:
            logger.warning(f"{cookie} is expired! Might want to renew it.")
    return cj


def _get_cookie_dict(path):
    """Basic dictionary from cookie file. Used by Requests module."""
    cookie_path = Path(path).absolute()
    if not cookie_path.exists():
        logger.error("Cookie file does not exist, defaulting to empty cookie...")
        return {}
    cookie_content = parse_cookie_file(cookie_path)
    if not cookie_content:
        logger.warning("Empty cookie file!")
    return cookie_content


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
        # TODO add proxies, randomized headers?
        self.headers = {
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 \
(KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36',
        'accept-language': 'en-US,en' # ensure messages in english from the API
        }
        self._initialize_consent()

    def _initialize_consent(self):
        """
        Set a consent cookie if not yet present in the cookie jar, and in the
        request headers as a result.
        If a pending consent cookie is there, accept it to avoid the blocking page.
        """
        # Make a first request to get initial cookies, in case none were passed
        # as argument (or failed to load)
        # TODO perhaps this needs to be done once in a while for very long
        # running sessions.
        req = Request('https://www.youtube.com/', headers=self.headers)

        cookies = SimpleCookie(req.get_header('Cookie'))
        if cookies.get('__Secure-3PSID'):
            return
        consent_id = None
        consent = cookies.get('CONSENT')
        if consent:
            if 'YES' in consent.value:
                return
            consent_id = re.search(
                r'PENDING\+(\d+)', consent.value)
        if not consent_id:
            consent_id = randint(100, 999)
        domain = '.youtube.com'
        cookie = Cookie(
                        0, # version
                        'CONSENT', # name
                        'YES+cb.20210328-17-p0.en+F+%s' % consent_id, # value
                        None, # port
                        False, # port_specified
                        domain, # domain
                        True, # domain_specified
                        domain.startswith('.'), # domain_initial_dot
                        '/', # path
                        True, # path_specified
                        False, # secure
                        None, # expires
                        False, # discard
                        None, # comment
                        None, # comment_url
                        {} # rest
                    )

        self.cookie_jar.set_cookie(cookie)

    def make_request(self, url):
        req = Request(url, headers=self.headers)
        self.cookie_jar.add_cookie_header(req)
        logger.debug(f"Request {req.full_url}")
        logger.debug(f"Request headers: {req.header_items()}")
        return self.parse_response(req)

    def parse_response(self,req):
        """
        Extract the initial JSON from the HTML in the request response.
        """
        # We could also use youtube-dl --dump-json instead
        with urlopen(req) as res:
            logger.info(f"GET {res.url}")
            logger.debug(f"Response Status code: {res.status}.\n\
Response headers:\n{res.headers}")
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
                # ytInitialPlayerResponse, as in some pages both are present.
                # Might need some refactoring.
                elif "var ytInitialData =" in content_page:
                    _json = content_page.split("var ytInitialData = ")[1]\
                                        .split(';</script><link rel="canonical')[0]
                else:
                    logger.debug(f"JSON after split:\n{_json}")
                    raise "Could not find ytInitialData nor \
ytInitialPlayerResponse in the GET request!"
                return _json
            except Exception as e:
                logger.critical(f"Failed loading initial data response. {e}.")
                raise e


def get_json_from_string(string):
    try:
        j = loads(string)
    except Exception as e:
        logger.critical(f"Error loading JSON from string: {e}")
        logger.debug(f"get_json_from_string: {string}")
        return None
    return j