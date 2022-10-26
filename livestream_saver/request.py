import logging
# from sys import version_info
# from platform import python_version_tuple
import re
import json
from random import randint
from urllib.request import Request, urlopen #, build_opener, HTTPCookieProcessor, HTTPHandler
import http.cookiejar
from http.cookies import SimpleCookie
from typing import Dict, Optional, Union
import time
import hashlib

from livestream_saver.util import UA
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
            'user-agent': UA, # TODO could use fake-useragent package here for an up-to-date string
            'accept-language': 'en-US,en' # ensure messages in english from the API
        }
        self._initialize_consent()
        self._logged_in = False
        self.notify_h = notifier
        self.ytcfg = None
        self._SAPISID: Union[str, bool, None] = None

    def get_ytcfg(self, data) -> Dict:
        if not isinstance(data, str):
            # FIXME we assume this is an object with file-like interface
            content_html = None
            try:
                content_html = str(data.read().decode('utf-8'))
            except Exception as e:
                logger.critical(f"Failed to load html for ytcfg: {e}")
            return self.get_ytcfg_from_html(content_html)

        return self.get_ytcfg_from_html(data)
    
    @staticmethod
    def get_ytcfg_from_html(html) -> Dict:
        # TODO only keep the keys we care about (that thing is huge)
        if result := re.search(r"ytcfg\.set\((\{.*\})\);", html):
            # Assuming the first result is the one we're looking for
            objstr = result.group(1)
            try:
                return json.loads(objstr)
            except Exception as e:
                logger.error(f"Error loading ytcfg as json: {e}.")
        return {}

    def _generate_sapisidhash_header(
        self, 
        origin: Optional[str] = 'https://www.youtube.com') -> Optional[str]:
        if not origin:
            return None

        if len(self.cookie_jar) == 0:
            return None

        if self._SAPISID is None:
            cookies = {}
            keys = ("SAPISID", "__Secure-3PAPISID")
            for cookie in self.cookie_jar:
                if "youtube.com" in cookie.domain:
                    for k in keys:
                        if k in cookie.name and cookie.value:
                            cookies[k] = cookie
                            break
            if len(cookies.values()) > 0:
                # Value should be the same for both of them
                self._SAPISID = tuple(cookies.values())[-1].value
                logger.info("Extracted SAPISID cookie")
                # We still require SAPISID to be present anyway
                if not cookies.get("SAPISID"):
                    domain = '.youtube.com'
                    cookie = http.cookiejar.Cookie(
                        0, # version
                        'SAPISID', # name
                        self._SAPISID, # value
                        None, # port
                        False, # port_specified
                        domain, # domain
                        True, # domain_specified
                        domain.startswith('.'), # domain_initial_dot
                        '/', # path
                        True, # path_specified
                        True, # secure
                        round(time.time()) + 3600, # expires
                        False, # discard
                        None, # comment
                        None, # comment_url
                        {} # rest
                    )
                    self.cookie_jar.set_cookie(cookie)
                    logger.debug(f"Copied __Secure-3PAPISID to missing SAPISID.")
            else:
                self._SAPISID = False
        if not self._SAPISID:
            return None
        
        # SAPISIDHASH algorithm from https://stackoverflow.com/a/32065323
        time_now = round(time.time())
        sapisidhash = hashlib.sha1(
            f'{time_now} {self._SAPISID} {origin}'.encode('utf-8')).hexdigest()
        return f'SAPISIDHASH {time_now}_{sapisidhash}'

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
      
        if self.user_supplied_cookies:
            self.ytcfg = self.get_ytcfg(res)

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
        return self.get_html(req)

    def make_api_request(self, video_id) -> str:
        """Make an innertube API call. Return response as string."""
        # Try to circumvent throttling with this workaround for now since
        # pytube is either broken or simply not up to date
        # as per https://code.videolan.org/videolan/vlc/-/issues/26174#note_286445
        headers = self.headers.copy()
        headers.update(
            {
                'Content-Type': 'application/json',
                'Origin': 'https://www.youtube.com',
                'X-YouTube-Client-Name': '3',
                'X-YouTube-Client-Version': '16.20',
                # 'Accept': 'text/plain'
            }
        )
        if auth := self._generate_sapisidhash_header():
            headers.update(
                {
                    'X-Origin': "https://www.youtube.com",
                    'Authorization': auth
                }
            )

        if self.ytcfg:
            if IdToken := self.ytcfg.get('IdToken'):
                headers["X-Youtube-Identity-Token"] = IdToken
            if DelegatedSessionId := self.ytcfg.get('DelegatedSessionId'):
                headers["X-Goog-PageId"] = DelegatedSessionId
            if VisitorData := self.ytcfg.get('VisitorData'):
                headers["X-Goog-Visitor-Id"] = VisitorData
            if SessionIndex := self.ytcfg.get('SessionIndex'):
                headers["X-Goog-AuthUser"] = SessionIndex
        
        logger.debug(f"Making API request... {headers=}")

        data = {
            "context": {
                "client": {
                    "clientName": "ANDROID",
                    "clientVersion": "16.20",
                    "hl": "en"
                }
            },
            "videoId": video_id,
        }

        req = Request(
            "https://www.youtube.com/youtubei/v1/player?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
            headers=headers,
            data=json.dumps(data).encode(),
            method="POST"
        )

        self.cookie_jar.add_cookie_header(req)

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

        # logger.debug(
        #         f"CookieJar after extract_cookies(): {self.cookie_jar}")

    def get_html(self, req: Request) -> str:
        """
        Return the HTML page, or throw exception. Update cookies if needed.
        """
        # TODO get the DASH manifest (MPD) and parse that xml file instead
        # We could also use youtube-dl --dump-json instead
        with urlopen(req) as res:
            status = res.status
            
            if status >= 204:
                logger.debug(f"Request {req.full_url} -> response url: {res.url}")
                logger.debug(f"POST Request headers were {req.header_items()}")
                logger.debug(
                    f"Response {status=}.\n"
                    f"Response headers:\n{res.headers}")

            self.update_cookies(req, res)

            if status == 429:
                raise Exception(
                    "Error 429. Too many requests? Please try again later "
                    "or get a new IP (also a new cookie?).")

            try:
                content_page = str(res.read().decode('utf-8'))
                return content_page
            except Exception as e:
                logger.critical(f"Failed to load {req.full_url}: {e}")
                raise e
