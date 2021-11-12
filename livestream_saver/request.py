# from sys import version_info
# from platform import python_version_tuple
import re
import time
from os import sep, makedirs
from pathlib import Path
from random import randint
from typing import Optional, Union
import logging
logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)

from urllib.request import Request, urlopen #, build_opener, HTTPCookieProcessor, HTTPHandler

from livestream_saver.util import get_system_ua
from livestream_saver.constants import *

# from http.cookies import BaseCookie, SimpleCookie, Morsel, _CookiePattern
from http import cookies as httpcookies
from http import cookiejar as httpcookiejar

from aiohttp import cookiejar as aiocookiejar
from aiohttp import ClientSession
from aiohttp.typedefs import PathLike
from aiohttp.hdrs import SET_COOKIE
from yarl import URL


def monkeypatch_cookielib():
    """
    The core http library has a bug in cookie string parsing. We have to 
    impromptu patch ("monkey patch") a few things to avoid hitting the bug.
    See: https://bugs.python.org/issue45358
    """
    # Test output of the library in use. We'll patch if it fails our expectations.
    C = httpcookies.SimpleCookie()
    C.load('foo=bar; bar=Low; baz; Priority=High')
    # Cookie should not be discarded completely because of unknown attributes 
    # such as "baz", and the "Priority" attribute should be retained.
    if C['bar'].output() == 'Set-Cookie: bar=Low; Priority=High':
        # Patch has been merged upstream, nothing to fix.
        logger.debug("NOT patching cookie lib bug 45358.")
        return
    del C

    logger.warning(f"Patching cookie lib bug 45358...")

    def __parse_string(self, str, patt=httpcookies._CookiePattern):
        logging.debug(f"{WARNING}__parse_string({str}){ENDC}")
        i = 0                 # Our starting point
        n = len(str)          # Length of string
        parsed_items = []     # Parsed (type, key, value) triples
        morsel_seen = False   # A key=value pair was previously encountered

        TYPE_ATTRIBUTE = 1
        TYPE_KEYVALUE = 2

        # We first parse the whole cookie string and reject it if it's
        # syntactically invalid (this helps avoid some classes of injection
        # attacks).
        while 0 <= i < n:
            # Start looking for a cookie
            match = patt.match(str, i)
            if not match:
                # No more cookies
                break

            key, value = match.group("key"), match.group("val")
            logging.debug(f"matched key={key} value={value}")
            i = match.end(0)
            if key[0] == "$":
                if not morsel_seen:
                    # We ignore attributes which pertain to the cookie
                    # mechanism as a whole, such as "$Version".
                    # See RFC 2965. (Does anyone care?)
                    continue
                parsed_items.append((TYPE_ATTRIBUTE, key[1:], value))
            elif key.lower() in httpcookies.Morsel._reserved:
                if not morsel_seen:
                    # Invalid cookie string
                    return
                if value is None:
                    if key.lower() in httpcookies.Morsel._flags:
                        parsed_items.append((TYPE_ATTRIBUTE, key, True))
                    else:
                        # Invalid cookie string
                        return
                else:
                    parsed_items.append((TYPE_ATTRIBUTE, key, httpcookies._unquote(value)))
            elif value is not None:
                parsed_items.append((TYPE_KEYVALUE, key, self.value_decode(value)))
                morsel_seen = True
            else:
                if morsel_seen:
                    continue
                # Invalid cookie string
                return
        # The cookie string is valid, apply it.
        M = None         # current morsel
        for tp, key, value in parsed_items:
            logging.debug(f"Parsed_items: tp={tp}, key={key}, value={value}")
            if tp == TYPE_ATTRIBUTE:
                assert M is not None
                M[key] = value
            else:
                assert tp == TYPE_KEYVALUE
                rval, cval = value
                self.__set(key, rval, cval)
                M = self[key]

    # For debugging purposes only
    from functools import wraps
    def prefix_function(function, prefunction):
        @wraps(function)
        def run(*args, **kwargs):
            prefunction(*args, **kwargs)
            return function(*args, **kwargs)
        return run

    httpcookies.BaseCookie.load = prefix_function(
        httpcookies.BaseCookie.load, 
        lambda self, rawdata: logging.debug(
            f"Current cookies: {self}\nNow Loading: \"{rawdata}\"")
    )

    # Monkey patch: __parse_string being a dunder method, name mangling is in 
    # effet, see output of dir(http.cookies.BaseCookie) for example
    httpcookies.BaseCookie._BaseCookie__parse_string = __parse_string
    # Name mangling also forces us to do this hack 
    # cf. https://www.geeksforgeeks.org/name-mangling-in-python/
    # cf. https://dbader.org/blog/meaning-of-underscores-in-python
    # cf. https://stackoverflow.com/questions/7456807/python-name-mangling
    httpcookies.SimpleCookie.__set = httpcookies.SimpleCookie._BaseCookie__set

    # Add known and expected cookie attributes from Youtube:
    httpcookies.Morsel._reserved["priority"] = "Priority"
    httpcookies.Morsel._flags.add("sameparty")

monkeypatch_cookielib()


class CookieJar(aiocookiejar.CookieJar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cookies_updated_callback = None
        self._cookie_path: Optional[Path] = None  # path to pickle dump on disk
        # self.meta_cookie_jar = meta_cookie_jar
        # self.update_cookies(meta_cookie_jar._cookies)

    def set_cookies_updated_callback(self, callback):
        self._cookies_updated_callback = callback

    def update_cookies(self, cookies, url=URL()):
        super().update_cookies(cookies, url)
        if cookies and self._cookies_updated_callback:
            self._cookies_updated_callback(list(self))

    def save(self) -> None:
        if self._cookie_path is not None:
            return super().save(self._cookie_path)
    
    # def save(self, file_path: Optional[PathLike] = None) -> None:
    #     if not file_path:
    #         file_path = Path()
    #     if not self.meta_cookie_jar.filename:
    #         file_path = Path(file_path)
    #         return super().save(file_path)

    #     self.meta_cookie_jar.save()


def load_cookies_from_file(jar: CookieJar, cookie_path) -> Optional[Path]:
    """Load cookies and return the path where to save pickled cookies."""
    pickled_cookie_path = Path() / "livestream_saver_cookies.pickle"
    if not cookie_path:
        return pickled_cookie_path
    if not isinstance(cookie_path, Path):
        cookie_path = Path(cookie_path).expanduser()
    else:
        cookie_path = cookie_path.expanduser()
    if cookie_path.is_dir():
        return cookie_path / "livestream_saver_cookies.pickle"
    if not cookie_path.exists():
        return pickled_cookie_path
    
    # We use this jar to load our files safely, maybe we could do it ourselves
    netscape_cookie_jar: Union[
        httpcookiejar.MozillaCookieJar, CompatMozillaCookieJar
    ] = get_netscape_cookie_jar(cookie_path)
    
    if cookie_path.is_file():
        pickled_cookie_path = cookie_path.absolute().with_suffix('.pickle')
        if pickled_cookie_path.exists():
            if pickled_cookie_path.stat().st_mtime > cookie_path.stat().st_mtime:
                logger.info("Loading pickled cookies...")
                jar.load(pickled_cookie_path)
            else:
                pickled_cookie_path.unlink()
                load_from_jar(netscape_cookie_jar, jar)
                # aiojar.save(pickled_cookie_path)
        else:
            load_from_jar(netscape_cookie_jar, jar)
    return pickled_cookie_path


def load_from_jar(from_jar, to_jar):
    """
    Load http.cookiejar.cookies from http.cookiejar.Cookiejar as 
    http.cookies.Morsel objects in order to be compatible with 
    aiohttp.AbstractCookieJar.
    """

    def make_update_d(c, m):
        d = {
            "domain": c.domain,
            "path": c.path,
            "expires": str(c.expires) if c.expires is not None else "",
        }
        if c.secure:  # Not used
            d["secure"] = c.secure
        if c.comment:  # Not used
            d["comment"] = c.comment
        if c.version:  # Not used
            d["version"] = c.version
        return d

    logger.debug(f"Loading cookies from cookiejar: {from_jar.filename}")
    _cookies = {}
    for c in from_jar:
        logger.debug(f"Loaded from_jar cookie: {c.__repr__()} {OKBLUE}type {type(c)}{ENDC}")
        m = httpcookies.Morsel()
        m.set(c.name, c.value, c.value)
        m.update(
            make_update_d(c, m)
        )
        _cookies[c.name] = m

    to_jar.update_cookies(_cookies)


class ASession:
    """Wrapper around aiohttp ClientSession."""
    def __init__(self, cookie_path: Optional[Path] = None, notifier = None):
        self.notify_h = notifier
        self.headers = {
            'user-agent': get_system_ua(), # TODO could use fake-useragent package here for an up-to-date string
            'accept-language': 'en-US,en' # ensure messages in english from the API
        }
    
        aiojar = CookieJar()
        if cookie_path is not None:
            aiojar._cookie_path = load_cookies_from_file(aiojar, cookie_path)
        self.aiojar = aiojar
        
        logger.debug(f"AIOJAR init len={len(aiojar)}")
        # aiojar.set_cookies_updated_callback(...)
        self.session = ClientSession(
            cookie_jar=aiojar,
            headers=self.headers,
            # cookies=netscape_cookie_jar._cookies
        )

        # if user submitted, we know we need to save
        # if cookie_path:  
        #     aiojar.save(pickled_cookie_path)

        self._logged_in = False

    async def close(self):
        self.aiojar.save()
        await self.session.close()


    async def initialize_consent(self):
        logger.info("Initializing consent...")
        async with self.session.get('https://www.youtube.com') as response:
            
            logger.debug(f"{WARNING}HEADERS:{ENDC}")
            for hdr in response.headers.getall(SET_COOKIE, ()):
                logger.debug(hdr)
            logger.debug(f"{WARNING}COOKIES:{ENDC}")
            for _, morsel in response.cookies.items():
                logger.debug(morsel.output())

            _cookies = self.session.cookie_jar.filter_cookies(
                URL('https://www.youtube.com')
            )
            logger.debug(f"{OKBLUE}filter_cookies after request{ENDC}:\n"
                 f"{_cookies}\ntype={type(_cookies)}")

            if _cookies.get('__Secure-3PSID'):
                return
            consent_id = None
            consent = _cookies.get('CONSENT')
            if consent:
                if 'YES' in consent.value:
                    return
                consent_id = re.search(r'PENDING\+(\d+)', consent.value)
            if not consent_id:
                consent_id = randint(100, 999)
            else:
                # FIXME might be best to just force a random number here instead?
                consent_id = consent_id.group(1)

            # domain = '.youtube.com'
            # cookie = http.cookiejar.Cookie(
            #                 0, # version
            #                 'CONSENT', # name
            #                 'YES+cb.20210328-17-p0.en+F+%s' % consent_id, # value
            #                 None, # port
            #                 False, # port_specified
            #                 domain, # domain
            #                 True, # domain_specified
            #                 domain.startswith('.'), # domain_initial_dot
            #                 '/', # path
            #                 True, # path_specified
            #                 False, # secure
            #                 None, # expires
            #                 False, # discard
            #                 None, # comment
            #                 None, # comment_url
            #                 {} # rest
            #             )

            # if logger.isEnabledFor(logging.DEBUG):
            #     logger.debug(f"Setting consent cookie: {cookie}")

            for k, c in self.session.cookie_jar._cookies.items():
                print(f"native cjar items() k={k}, v={c} (type {type(c)})")
                for key, morsel in c.items():
                    if "CONSENT" in morsel.key and "youtube" in morsel["domain"]:
                        c.load({"CONSENT": 'YES+cb.20210328-17-p0.en+F+%s' % consent_id})


                # for key, morsel in c.items():
                #     print(f"Cookie in session cjar: {type(morsel)} {morsel}")
                #     if "CONSENT" in morsel.key:
                #         print(f"found consent in key {morsel.key}, value value {morsel.value}")
                #         morsel._value = "CONSEEEEEENT"
                #         c[key] = morsel.copy()
                #         print(f"updated: {morsel.items()}, value {morsel.value}")

            # print(f"Setting override cookie {cookie} into mcj...")
            # mcj = http.cookiejar.MozillaCookieJar()
            # mcj.set_cookie(cookie)
            # print(f"Updating aiojar with mcj._cookies: {mcj._cookies}")
            # cookies = {}
            # for each in mcj:
            #     m = Morsel()
            #     m["key"] = each.name
            #     m["value"] = each.value
            #     m.update({"domain": each.domain, "path": each.path})
            #     # m.path = each.path
            #     # cookies[each.name] = m
            # print(f"updating with parsed cookies {OKGREEN}{cookies}{ENDC}")
            # self.aiojar.update_cookies(cookies)
            # if self.aiojar.filename:
            #     self.aiojar.save(ignore_expires=True)
        
    async def make_request(self, url):
        async with self.session.get(url) as response:
            return await response.text()


class Session:
    """
    Keep cookies in memory for reuse or update. Synchronized version.
    """
    def __init__(self, cookie_path = None, notifier = None):
        # Hack to only warn user once after first validity check
        self.user_supplied_cookies = 1 if cookie_path else 0
        self.cookie_path = cookie_path
        self.cookie_jar = get_netscape_cookie_jar(cookie_path)
        # TODO add proxies
        self.headers = {
            'user-agent': get_system_ua(), # TODO could use fake-useragent package here for an up-to-date string
            'accept-language': 'en-US,en' # ensure messages in english from the API
        }
        self._initialize_consent()
        self._logged_in = False
        self.notify_h = notifier

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

        _cookies = httpcookies.SimpleCookie(req.get_header('Cookie'))
        logger.debug(f"Initial req cookies after extract: {_cookies}")

        if _cookies.get('__Secure-3PSID'):
            return
        consent_id = None
        consent = _cookies.get('CONSENT')
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
        cookie = httpcookiejar.Cookie(
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
        """Make a request with cookies applied."""
        req = Request(url, headers=self.headers)
        self.cookie_jar.add_cookie_header(req)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Request {req.full_url}")
            logger.debug(f"Request headers: {req.header_items()}")

        return self.get_html(req)

    # TODO Place this in both monitor and download
    def _check_logged_out(self, json_obj):
        logged_out = json_obj.get("responseContext", {}) \
                .get("mainAppWebResponseContext", {}) \
                .get("loggedOut", True)
        return logged_out

    def is_logged_out(self, json_obj):
        """Take a json object and return if we detect logged out status
        only if we have supplied our own cookies, which we ASSUME are meant
        to be logged in."""
        if not json_obj:
            return False
        logged_out = self._check_logged_out(json_obj)

        if logged_out and self.user_supplied_cookies:
            self.user_supplied_cookies = 0
            logger.critical(
                "We are not logged in. Check the validity of your cookies!"
            )

        if logged_out and self._logged_in == True:
            logger.critical(
                "We are not logged in anymore! Are cookies still valid?"
            )
            if self.notify_h:
                self.notify_h.send_email(
                    subject="Not logged in anymore",
                    message_text=f"We are logged out: {json_obj}"
                )
        
        self._logged_in = not logged_out
        return logged_out

    def update_cookies(self, req, res):
        """
        Update cookiejar with whatever Youtube send us in Set-Cookie headers.
        """
        # cookies = SimpleCookie(req.get_header('Cookie'))
        # logger.debug(f"Req header cookie: \"{cookies}\".")

        ret_cookies = self.cookie_jar.make_cookies(res, req)
        # if logger.isEnabledFor(logging.DEBUG):
        #     logger.debug(f"make_cookies(): {ret_cookies}")

        for cook in ret_cookies:
            if cook.name == "SIDCC" and cook.value == "EXPIRED":
                logger.critical("SIDCC expired. Renew your cookies.")
                #TODO send email to admin
                return

        self.cookie_jar.extract_cookies(res, req)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"CookieJar after extract_cookies(): {self.cookie_jar}"
            )

    def get_html(self, req: Request) -> str:
        """
        Return the HTML page, or throw exception. Update cookies if needed.
        """
        # TODO get the DASH manifest (MPD) and parse that xml file instead
        # We could also use youtube-dl --dump-json instead
        with urlopen(req) as res:
            logger.debug(f"REQUEST {req.full_url} -> response url: {res.url}")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"Response Status code: {res.status}.\n"
                    f"Response headers:\n{res.headers}")

            self.update_cookies(req, res)

            if res.status == 429:
                raise Exception(
                    "Error 429. Too many requests? Please try again later "
                    "or get a new IP (also a new cookie?)."
                )

            try:
                content_page = str(res.read().decode('utf-8'))
                return content_page
            except Exception as e:
                logger.critical(f"Failed to load html: {e}")
                raise e


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

class CompatMozillaCookieJar(httpcookiejar.MozillaCookieJar):
    """
    Backport of Python 3.10 version in order to load HTTPOnly cookies too.
    Prior to Python 3.10, http.cookiejar ignored lines starting with "#HttpOnly_".
    """

    def _really_load(self, f, filename, ignore_discard, ignore_expires):
        now = int(time.time())

        if not NETSCAPE_MAGIC_RGX.match(f.readline()):
            raise httpcookiejar.LoadError(
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
                c = httpcookiejar.Cookie(0, name, value,
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
            raise httpcookiejar.LoadError("invalid Netscape format cookies file %r: %r" %
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


def get_netscape_cookie_jar(cookie_path: Union[str, PathLike, None]
    ) -> Union[httpcookiejar.MozillaCookieJar, CompatMozillaCookieJar]:
    """Necessary for urllib.request."""

    # Before Python 3.10, these cookies are ignored which breaks our credentials
    cj = httpcookiejar.MozillaCookieJar() \
        if "HTTPONLY_PREFIX" in dir(httpcookiejar) \
        else CompatMozillaCookieJar()

    if not cookie_path:
        logger.info(f"No cookie path submitted. Using a blank cookie jar.")
        return cj

    cp = Path(cookie_path).expanduser()

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
            # Used to create a new file if needed
            cp_str = cp_str + sep + "livestream_saver_cookies.txt"
        else:  
            # device node or something illegal
            logger.warning(
                f"Submitted cookie path \"{cp}\" is incorrect. "
                "Using blank cookie jar.")
            return cj

        logger.warning(
            f"Cookie file not found. Creating an empty new one in \"{cp_str}\"."
        )
        cj.filename = cp_str  # this has to be an absolute valid path string
        return cj

    new_cp_str = str(cp.absolute().with_suffix('')) + "_updated.txt"
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
            # Session tokens don't appear to be very useful
            if cookie.name.startswith("ST-"):
                cj.clear(".youtube.com", "/", cookie.name)
                continue

            if cookie.is_expired:
                logger.warning(
                    f"{cookie} is expired ({cookie.expires})! Might want to renew it.")
        else: 
            # Remove non-youtube cookies, we don't care about them
            if cj._cookies.get(cookie.domain):
                cj.clear(cookie.domain)

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
