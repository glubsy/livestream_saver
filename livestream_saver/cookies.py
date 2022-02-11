import logging
import time
import re
from typing import Union, Optional
from aiohttp.typedefs import PathLike
from os import sep, makedirs
from pathlib import Path
import http.cookiejar
from http import cookiejar as httpcookiejar
from http import cookies as httpcookies
from aiohttp import cookiejar as aiocookiejar
from yarl import URL

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


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
    if morsel := C.get('bar'):
        if morsel.output() == 'Set-Cookie: bar=Low; Priority=High':
            # Patch has been merged upstream, nothing to fix.
            logger.debug("NOT patching cookie lib bug 45358.")
            return
    del C

    logger.info(f"Patching cookie lib bug 45358...")

    def __parse_string(self, str, patt=httpcookies._CookiePattern):
        # logger.debug(f"{WARNING}__parse_string({str}){ENDC}")
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
            # logging.debug(f"__parse_string - matched key={key} value={value}")
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
            # logger.debug(f"__parse_string - Parsed_items: tp={tp}, key={key}, value={value}")
            if tp == TYPE_ATTRIBUTE:
                assert M is not None
                M[key] = value
            else:
                assert tp == TYPE_KEYVALUE
                rval, cval = value
                self.__set(key, rval, cval)
                M = self[key]

    # For debugging purposes only
    # from functools import wraps
    # def prefix_function(function, prefunction):
    #     @wraps(function)
    #     def run(*args, **kwargs):
    #         prefunction(*args, **kwargs)
    #         return function(*args, **kwargs)
    #     return run
    # httpcookies.BaseCookie.load = prefix_function(
    #     httpcookies.BaseCookie.load, 
    #     lambda self, rawdata: logger.debug(
    #         f"Current cookies: {self}\nNow Loading: \"{rawdata}\"")
    # )

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
    """Async cookiejar used for async requests."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cookies_updated_callback = None
        self._cookie_path: Optional[Path] = None  # path to pickle dump on disk
        # Meta cookie jar can be a MozillaCookieJar to ease loading/saving
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
            # Serialize and write as a pickle
            return super().save(self._cookie_path)
    
    # Obsolete: only if a meta cookie jar is used
    # def save(self, file_path: Optional[PathLike] = None) -> None:
    #     if not file_path:
    #         file_path = Path()
    #     if not self.meta_cookie_jar.filename:
    #         file_path = Path(file_path)
    #         return super().save(file_path)
    #     self.meta_cookie_jar.save()


def load_cookies_from_file(
    jar: CookieJar, 
    cookie_path: Optional[PathLike]) -> Optional[Path]:
    """Load cookies and return the path where to save pickled cookies."""
    pickled_cookie_path = Path() / "livestream_saver_cookies.pickle"  # cwd
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
    
    # We use this jar's methods to load our files safely, 
    # but maybe we could do it ourselves in the future
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
    logger.debug(f"Loading cookies from cookiejar path: {from_jar.filename}")
    _cookies = {}
    for c in from_jar:
        # logger.debug(f"load_from_jar {OKBLUE}{type(c)}{ENDC} {c.__repr__()}")
        _cookies[c.name] = cookie_to_morsel(c)

    to_jar.update_cookies(_cookies)


def cookie_to_morsel(from_cookie: http.cookiejar.Cookie):
    """Transforms http.cookiejar.Cookie into http.cookies.Morsel."""
    def make_update_d(c):
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

    m = httpcookies.Morsel()
    m.set(from_cookie.name, from_cookie.value, from_cookie.value)
    m.update(make_update_d(from_cookie))
    return m


def get_cookie(path):
    # CookieJar now instead of a dict
    return _get_cookie_jar(path)


def _get_cookie_jar(cookie_path: str):
    """Necessary for urllib.request."""

    policy = http.cookiejar.DefaultCookiePolicy(
        allowed_domains=(".youtube.com", ".google.com"))

    # Before Python 3.10, these cookies are ignored which breaks our credentials
    cj = http.cookiejar.MozillaCookieJar(policy=policy) \
        if "HTTPONLY_PREFIX" in dir(http.cookiejar) \
        else CompatMozillaCookieJar(policy=policy)

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

            if cookie.is_expired():
                logger.warning(
                    f"{cookie} is expired ({cookie.expires})! "
                     "Might want to renew it.")

    return cj


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



def get_netscape_cookie_jar(
    cookie_path: Union[str, PathLike, None]) -> Union[httpcookiejar.MozillaCookieJar, CompatMozillaCookieJar]:
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
        logger.error(
            f"Failed to load cookie file {cookie_path}: {e}."
            " Defaulting to empty cookie."
        )

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
