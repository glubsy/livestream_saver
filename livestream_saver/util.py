import io
import logging
import time
# from sys import version_info
# from platform import python_version_tuple
import re
from os import sep, makedirs
from random import randint
from platform import system
from json import loads
from pathlib import Path
from urllib.request import Request, urlopen #, build_opener, HTTPCookieProcessor, HTTPHandler
import http.cookiejar
from http.cookies import SimpleCookie

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)

def get_cookie(path):
    # CookieJar now instead of a dict
    return _get_cookie_jar(path)


def _get_cookie_jar(cookie_path):
    """Necessary for urllib.request."""

    # Before Python 3.10, these cookies are ignored which breaks our credentials
    cj = http.cookiejar.MozillaCookieJar() \
        if "HTTPONLY_PREFIX" in dir(http.cookiejar) \
        else CompatMozillaCookieJar()

    if not cookie_path:
        logger.info(f"No cookie path submitted. Using a blank cookie jar.")
        return cj

    cp = Path(cookie_path).absolute()

    if not cp.is_file():  # either file doesn't exist, or it's a directory
        cp_str = str(cp)
        logger.debug(f"Cookie path \"{cp_str}\" is not a file...")

        if not cp.exists():
            # Get base directory, remove bogus filename
            found = cp_str.rfind(sep)
            if found != -1:
                cp_str = cp_str[:found]
            logger.debug(f"Creating directory for cookie: \"{cp_str}\"")
            makedirs(cp_str, exist_ok=True)
            cp_str = str(cp)
        elif cp.is_dir():
            cp_str = cp_str + sep + "livestream_saver_cookies.txt"
        else:  
            # device node or something illegal
            logger.warning(f"Submitted cookie path \"{cp}\" is incorrect. \
Using blank cookie jar.")
            return cj

        logger.warning(
            f"Cookie file not found. Creating an empty new one in \"{cp_str}\"."
        )
        cj.filename = cp_str  # this has to be an absolute valid path string
        return cj

    new_cp_str = str(Path(cookie_path).absolute().with_suffix('')) + "_updated.txt"
    new_cp = Path(new_cp_str)

    try:
        cj.load(new_cp_str if new_cp.exists() else str(cp),
                ignore_expires=True, ignore_discard=True)
    except Exception as e:
        logger.error(f"Failed to load cookie file {cookie_path}: {e}. \
Defaulting to empty cookie.")

    # Avoid overwriting the cookie, only write to a new one.
    cj.filename = new_cp_str

    # TODO Make sure the necessary youtube cookies are there, ie. LOGIN_INFO,
    # APISID, CONSENT, HSID, NID, PREF, SID, SIDCC, SSID, VISITOR_INFO1_LIVE,
    # __Secure-3PAPISID, __Secure-3PSID, __Secure-3PSIDCC, etc.
    for cookie in cj:
        if "youtube.com" in cookie.domain:
            if "CONSENT" in cookie.name:
                if cookie.value is not None and "PENDING" in cookie.value:
                    cj.clear(".youtube.com", "/", "CONSENT")
                    continue
            # Session tokens seem not very useful
            if cookie.name.startswith("ST-"):
                cj.clear(".youtube.com", "/", cookie.name)
                continue

            if cookie.is_expired:
                logger.warning(f"{cookie} is expired ({cookie.expires})! \
Might want to renew it.")

    return cj


# Obsolete
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


# Obsolete
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


# Youtube channel IDs are 24 characters
YT_CH_HASH_RE = re.compile(r".*channel\/([0-9A-Za-z_-]{24}).*")
YT_CHID_HASH_RE = re.compile(r"^[0-9A-Za-z_-]{24}$")


def get_channel_id(str_url):
    """
    Naive way to get the channel id from channel canonical URL.
    :param pattern str: URL to channel or channel ID directly.
    """
    if "channel/" in str_url: # /channel/HASH
        if match := YT_CH_HASH_RE.search(str_url):
            logger.debug(f"Matched regex: {str_url}: {match.group(1)}")
            return match.group(1)
        raise Exception(f"Error while looking for channel HASH in \"{str_url}\"")

    if match := YT_CHID_HASH_RE.search(str_url):
        return str_url

    if '/watch' in str_url:
        raise Exception("Not a valid channel URL. Is this a video URL?")

    if 'youtube.com/c/' in str_url: # /c/NAME
        return str_url.split('/c/')[-1]

    return str_url


def get_system_ua():
    SYSTEM = system()
    if SYSTEM == 'Windows':
        return 'Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:90.0) Gecko/20100101 Firefox/90.0'
    if SYSTEM == 'Darwin':
        return 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:89.0) Gecko/20100101 Firefox/89.0'
    return 'Mozilla/5.0 (X11; Linux x86_64; rv:89.0) Gecko/20100101 Firefox/89.0'


class YoutubeUrllibSession:
    """
    Keep cookies in memory for reuse or update.
    """
    def __init__(self, cookie_path=None):
        self.user_supplied_cookies = True if cookie_path else False
        self.cookie_jar = get_cookie(cookie_path)
        # TODO add proxies
        self.headers = {
        'user-agent': get_system_ua(), # TODO could use fake-useragent package here for an up-to-date string
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
        self.cookie_jar.add_cookie_header(req)

        res = urlopen(req)

        logger.debug(f"Initial req header items: {req.header_items()}")
        logger.debug(f"Initial res headers: {res.headers}")

        # Update our cookies according to the response headers
        # if not len(self.cookie_jar) and self.cookie_jar.make_cookies(res, req):
        self.cookie_jar.extract_cookies(res, req)
        # FIXME a bit hacky, all we need is a dict of the updated cookies in cj for below
        self.cookie_jar.add_cookie_header(req)

        cookies = SimpleCookie(req.get_header('Cookie'))
        logger.debug(f"Initial req cookies after extract: {cookies}")

        if cookies.get('__Secure-3PSID'):
            return
        consent_id = None
        consent = cookies.get('CONSENT')
        if consent:
            if 'YES' in consent.value:
                return
            consent_id = re.search(r'PENDING\+(\d+)', consent.value)
        if not consent_id:
            consent_id = randint(100, 999)
        else:
            # FIXME might be best to just force a random number here instead?
            consent_id = consent_id.group(1)
        domain = '.youtube.com'
        cookie = http.cookiejar.Cookie(
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

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Setting consent cookie: {cookie}")
        self.cookie_jar.set_cookie(cookie)

        if self.cookie_jar.filename:
            self.cookie_jar.save(ignore_expires=True)

    def make_request(self, url):
        req = Request(url, headers=self.headers)
        self.cookie_jar.add_cookie_header(req)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Request {req.full_url}")
            logger.debug(f"Request headers: {req.header_items()}")

        _json = str_as_json(self.parse_response(req))
        self.is_logged_out(_json)
        return _json

    def is_logged_out(self, json_obj):
        """Take a json object and return if we detect logged out status
        only if we have supplied our own cookies, which we ASSUME are meant
        to be logged in."""
        # TODO only warn when the status changed (from logged in to logged out)
        if not json_obj:
            return False
        if json_obj.get("responseContext", {})\
                .get("mainAppWebResponseContext", {})\
                .get("loggedOut")\
        and self.user_supplied_cookies:
            logger.critical("We are not logged in anymore. Update your cookies!")
            # TODO send warning email to user
            return True
        return False

    def update_cookies(self, req, res):
        """
        Update cookiejar with whatever Youtube send us in Set-Cookie headers.
        """
        # cookies = SimpleCookie(req.get_header('Cookie'))
        # logger.debug(f"Req header cookie: \"{cookies}\".")

        ret_cookies = self.cookie_jar.make_cookies(res, req)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"make_cookies(): {ret_cookies}")

        for cook in ret_cookies:
            if cook.name == "SIDCC" and cook.value == "EXPIRED":
                logger.critical("SIDCC expired. Renew your cookies.")
                #TODO send email to admin
                return

        self.cookie_jar.extract_cookies(res, req)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"CookieJar after extract_cookies(): {self.cookie_jar}")

    def parse_response(self, req):
        """
        Extract the initial embedded JSON from the HTML in the request response.
        Return a string of that embedded json or None on failure.
        """
        # TODO get the DASH manifest (MPD) and parse that xml file instead

        # We could also use youtube-dl --dump-json instead
        with urlopen(req) as res:
            logger.info(f"GET {res.url}")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Response Status code: {res.status}.\n\
Response headers:\n{res.headers}")

            self.update_cookies(req, res)

            if res.status == 429:
                logger.critical("Error 429. Too many requests? \
Please try again later or get a new IP (also a new cookie?).")
                return None

            # TODO split this into a separate function
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
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"JSON after split:\n{_json}")
                    raise Exception("Could not find ytInitialData nor \
ytInitialPlayerResponse in the GET request!")
                return _json
            except Exception as e:
                logger.critical(f"Failed loading initial data response. {e}.")
                raise e


def str_as_json(string):
    """Return :param string str as a python json object."""
    try:
        j = loads(string)
    except Exception as e:
        logger.critical(f"Error loading JSON from string: {e}")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"get_json_from_string: {string}")
        raise
    return j


HTTPONLY_ATTR = "HTTPOnly"
HTTPONLY_PREFIX = "#HttpOnly_"
NETSCAPE_MAGIC_RGX = re.compile("#( Netscape)? HTTP Cookie File")
MISSING_FILENAME_TEXT = ("a filename was not supplied (nor was the CookieJar "
                         "instance initialised with one)")
NETSCAPE_HEADER_TEXT =  """\
# Netscape HTTP Cookie File
# http://curl.haxx.se/rfc/cookie_spec.html
# This is a generated file!  Do not edit.

"""

class CompatMozillaCookieJar(http.cookiejar.MozillaCookieJar):
    """
    Backport of Python 3.10 version in order to load HTTPOnly cookies too.
    Prior to Python 3.10, http.cookiejar ignored lines starting with "#HttpOnly_".
    """

    def _really_load(self, f, filename, ignore_discard, ignore_expires):
        now = int(time.time())

        if not NETSCAPE_MAGIC_RGX.match(f.readline()):
            raise http.cookiejar.LoadError(
                "%r does not look like a Netscape format cookies file" %
                filename)

        line = ""
        try:
            while 1:
                line = f.readline()
                rest = {}

                if line == "": break

                # httponly is a cookie flag as defined in rfc6265
                # when encoded in a netscape cookie file,
                # the line is prepended with "#HttpOnly_"
                if line.startswith(HTTPONLY_PREFIX):
                    rest[HTTPONLY_ATTR] = ""
                    line = line[len(HTTPONLY_PREFIX):]

                # last field may be absent, so keep any trailing tab
                if line.endswith("\n"): line = line[:-1]

                # skip comments and blank lines XXX what is $ for?
                if (line.strip().startswith(("#", "$")) or
                    line.strip() == ""):
                    continue

                domain, domain_specified, path, secure, expires, name, value = \
                        line.split("\t")
                secure = (secure == "TRUE")
                domain_specified = (domain_specified == "TRUE")
                if name == "":
                    # cookies.txt regards 'Set-Cookie: foo' as a cookie
                    # with no name, whereas http.cookiejar regards it as a
                    # cookie with no value.
                    name = value
                    value = None

                initial_dot = domain.startswith(".")
                assert domain_specified == initial_dot

                discard = False
                if expires == "":
                    expires = None
                    discard = True

                # assume path_specified is false
                c = http.cookiejar.Cookie(0, name, value,
                           None, False,
                           domain, domain_specified, initial_dot,
                           path, False,
                           secure,
                           expires,
                           discard,
                           None,
                           None,
                           rest)
                if not ignore_discard and c.discard:
                    continue
                if not ignore_expires and c.is_expired(now):
                    continue
                self.set_cookie(c)

        except OSError:
            raise
        except Exception:
            _warn_unhandled_exception()
            raise http.cookiejar.LoadError("invalid Netscape format cookies file %r: %r" %
                                          (filename, line))


    def save(self, filename=None, ignore_discard=False, ignore_expires=False):
        if filename is None:
            if self.filename is not None: filename = self.filename
            else: raise ValueError(MISSING_FILENAME_TEXT)

        with open(filename, "w") as f:
            f.write(NETSCAPE_HEADER_TEXT)
            now = int(time.time())
            for cookie in self:
                domain = cookie.domain
                if not ignore_discard and cookie.discard:
                    continue
                if not ignore_expires and cookie.is_expired(now):
                    continue
                if cookie.secure: secure = "TRUE"
                else: secure = "FALSE"
                if domain.startswith("."): initial_dot = "TRUE"
                else: initial_dot = "FALSE"
                if cookie.expires is not None:
                    expires = str(cookie.expires)
                else:
                    expires = ""
                if cookie.value is None:
                    # cookies.txt regards 'Set-Cookie: foo' as a cookie
                    # with no name, whereas http.cookiejar regards it as a
                    # cookie with no value.
                    name = ""
                    value = cookie.name
                else:
                    name = cookie.name
                    value = cookie.value
                if cookie.has_nonstandard_attr(HTTPONLY_ATTR):
                    domain = HTTPONLY_PREFIX + domain
                f.write(
                    "\t".join([domain, initial_dot, cookie.path,
                               secure, expires, name, value])+
                    "\n")


def _warn_unhandled_exception():
    # There are a few catch-all except: statements in this module, for
    # catching input that's bad in unexpected ways.  Warn if any
    # exceptions are caught there.
    import io, warnings, traceback
    f = io.StringIO()
    traceback.print_exc(None, f)
    msg = f.getvalue()
    warnings.warn("http.cookiejar bug!\n%s" % msg, stacklevel=2)
