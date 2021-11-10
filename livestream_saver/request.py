import logging
# from sys import version_info
# from platform import python_version_tuple
import re
from random import randint
from urllib.request import Request, urlopen #, build_opener, HTTPCookieProcessor, HTTPHandler
import http.cookiejar
from http.cookies import SimpleCookie

from livestream_saver.util import get_system_ua
from livestream_saver.cookies import get_cookie

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


class YoutubeUrllibSession:
    """
    Keep cookies in memory for reuse or update.
    """
    def __init__(self, cookie_path=None, notifier=None):
        # Hack to only warn user once after first validity check
        self.user_supplied_cookies = 1 if cookie_path else 0
        self.cookie_path = cookie_path
        self.cookie_jar = get_cookie(cookie_path)
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
