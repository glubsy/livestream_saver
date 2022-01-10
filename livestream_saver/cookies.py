import logging
import time
import re
from os import sep, makedirs
from pathlib import Path
import http.cookiejar

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


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
