# from sys import version_info
# from platform import python_version_tuple
import re
import time
from os import sep, makedirs
from pathlib import Path
import json
from random import randint
import logging
from urllib.request import Request, urlopen #, build_opener, HTTPCookieProcessor, HTTPHandler
import http.cookiejar
from http.cookies import SimpleCookie
from typing import Dict, Optional, Union
import time
import hashlib

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
import asyncio

from urllib.request import Request, urlopen #, build_opener, HTTPCookieProcessor, HTTPHandler

from livestream_saver.util import UA
from livestream_saver.cookies import (
    CookieJar, get_netscape_cookie_jar, load_cookies_from_file, cookie_to_morsel
)
from livestream_saver.constants import *

# from http.cookies import BaseCookie, SimpleCookie, Morsel, _CookiePattern
from http import cookies as httpcookies
from http import cookiejar as httpcookiejar

from aiohttp import cookiejar as aiocookiejar
from aiohttp import ClientSession
from aiohttp.typedefs import PathLike
from aiohttp.hdrs import SET_COOKIE
from yarl import URL


class ASession:
    """Wrapper around aiohttp ClientSession."""
    def __init__(self, cookie_path: Optional[Path] = None, notifier = None):
        self.notify_h = notifier
        self.headers = {
            'user-agent': UA, # TODO could use fake-useragent package here for an up-to-date string
            'accept-language': 'en-US,en' # ensure messages in english from the API
        }
        self.cookie_path = cookie_path
        aiojar = CookieJar()
        if cookie_path is not None:
            aiojar._cookie_path = load_cookies_from_file(aiojar, cookie_path)
        self.cookie_jar = aiojar
        
        # logger.debug(f"AIOJAR init len={len(aiojar)}")
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
        self._ytcfg = None
        self._SAPISID: Union[str, bool, None] = None

    async def close(self):
        self.cookie_jar.save()
        await self.session.close()

    async def initialize_consent(self):
        logger.info(f"{WARNING}Initializing consent...{ENDC}")
        async with self.session.get('https://www.youtube.com') as response:
            
            logger.debug(f"{WARNING}Response Headers:{ENDC}")
            for hdr in response.headers.getall(SET_COOKIE, ()):
                logger.debug(hdr)
            logger.debug(f"{WARNING}Response Cookies:{ENDC}")
            for _, morsel in response.cookies.items():
                logger.debug(morsel.output())
            
            # TODO get the current API keys in case they have been changed
            # in order to query the innertube API directly.

                  
            if self.cookie_path:  # user supplied cookies
                self._ytcfg = await self.get_ytcfg(response)

            _cookies = self.session.cookie_jar.filter_cookies(
                URL('https://www.youtube.com')
            )
            logger.debug(
                f"{BLUE}After request, filtered cookies "
                f"{type(_cookies)} are:\n{_cookies}{ENDC}"
            )

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

            # if logger.isEnabledFor(logger.DEBUG):
            #     logger.debug(f"Setting consent cookie: {cookie}")

            # New and simpler way of overwriting the consent cookie:
            for _, c in self.cookie_jar._cookies.items():
                for _, morsel in c.items():
                    if "CONSENT" in morsel.key and "youtube" in morsel["domain"]:
                        c.load(
                            {
                                "CONSENT": 'YES+cb.20210328-17-p0.en+F+%s' % consent_id
                            }
                        )
                # Obsolete? This accomplishes the same as above
                # for key, morsel in c.items():
                #     print(f"Cookie in session cjar: {type(morsel)} {morsel}")
                #     if "CONSENT" in morsel.key:
                #         print(f"found consent in key {morsel.key}, value value {morsel.value}")
                #         morsel._value = "TEST_CONSENT"
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
        
    async def make_request_async(self, url):
        print(f"Resquest async: {url}")
        async with self.session.get(url) as response:
            print(f"Response status: {response.status}")
            return await response.text()
    
    # async def make_request_async_cb(self, url, future):
    #     print(f"running resquest async cb: {url}")
    #     async with self.session.get(url) as response:
    #         print(f"Got response status {response.status}")
    #         future.set_result(await response.text())
    
    def make_request(self, url):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        result = loop.run_until_complete(self.make_request_async(url))
        loop.run_until_complete(loop.shutdown_asyncgens())
        
        # result = self.make_request_async(url)
        # fut = asyncio.Future()
        # task = loop.create_task(self.make_request_async_cb(url, fut))
        # result = asyncio.wait(fut)
        # while not fut.done():
        #     try:
        #         # result = loop.run_until_complete(self.make_request_async(url))
        #         result = fut.result()
        #         print(f"result {result}")
        #         break
        #     except InvalidStateError as e:
        #         print(f"invalid state.. {e}")
        #         time.sleep(1)
        #         continue
        #     except KeyboardInterrupt:
        #         task.cancel()
        #         break
        # # finally:
        # #     loop.close()
        # result = fut.result()
        logger.debug(f"{WARNING}Request result:{ENDC} {result[:150]}")
        return result

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

        if logged_out and self.cookie_path:
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

        if self._ytcfg:
            if IdToken := self._ytcfg.get('IdToken'):
                headers["X-Youtube-Identity-Token"] = IdToken
            if DelegatedSessionId := self._ytcfg.get('DelegatedSessionId'):
                headers["X-Goog-PageId"] = DelegatedSessionId
            if VisitorData := self._ytcfg.get('VisitorData'):
                headers["X-Goog-Visitor-Id"] = VisitorData
            if SessionIndex := self._ytcfg.get('SessionIndex'):
                headers["X-Goog-AuthUser"] = SessionIndex
        
        logger.debug(f"Making API request with headers:{headers}")

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

        # self.session.cookie_jar.add_cookie_header(req)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"POST Request {req.full_url}")
            logger.debug(f"POST Request headers: {req.header_items()}")

        return self.get_html(req)

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

    async def get_ytcfg(self, data) -> Dict:
        if not isinstance(data, str):
            # FIXME we assume this is an object with file-like interface
            content_html = None
            try:
                content = await data.read()
                content_html = str(content.decode('utf-8'))
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

        if len(self.session.cookie_jar) == 0:
            return None

        if self._SAPISID is None:
            cookies = {}
            keys = ("SAPISID", "__Secure-3PAPISID")
            for cookie in self.session.cookie_jar:
                print(f"TYPE {cookie} {type(cookie)}")
                if "youtube.com" in cookie.get("domain", ""):
                    for k in keys:
                        if k in cookie.key and cookie.value:
                            cookies[k] = cookie
                            break
            if len(cookies.values()) > 0:
                # Value should be the same for both of them
                self._SAPISID = tuple(cookies.values())[-1].value
                logger.info("Extracted SAPISID cookie.")
                # We still require SAPISID to be present anyway
                if not cookies.get("SAPISID"):
                    _cookies = {}
                    m = httpcookies.Morsel()
                    m.set('SAPISID', self._SAPISID, self._SAPISID)
                    m.update({
                        "domain": '.youtube.com',
                        "path": '/',
                        # FIXME expires is not properly parsed and is ignored?
                        "expires": str(round(time.time()) + 3600),
                        "secure": str(True),
                    })
                    _cookies['SAPISID'] = m
                    self.session.cookie_jar.update_cookies(_cookies)
                    logger.info(f"Copied __Secure-3PAPISID to missing SAPISID.")
            else:
                self._SAPISID = False
        if not self._SAPISID:
            return None
        
        # SAPISIDHASH algorithm from https://stackoverflow.com/a/32065323
        time_now = round(time.time())
        sapisidhash = hashlib.sha1(
            f'{time_now} {self._SAPISID} {origin}'.encode('utf-8')).hexdigest()
        return f'SAPISIDHASH {time_now}_{sapisidhash}'




# Obsolete implementation
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
        
        logger.debug(f"Making API request with headers:{headers}")

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

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"POST Request {req.full_url}")
            logger.debug(f"POST Request headers: {req.header_items()}")

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
            logger.debug(f"REQUEST {req.full_url} -> response url: {res.url}")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"Response Status code: {res.status}.\n"
                    f"Response headers:\n{res.headers}")

            self.update_cookies(req, res)

            if res.status == 429:
                raise Exception(
                    "Error 429. Too many requests? Please try again later "
                    "or get a new IP (also a new cookie?).")

            try:
                content_page = str(res.read().decode('utf-8'))
                return content_page
            except Exception as e:
                logger.critical(f"Failed to load {req.full_url}: {e}")
                raise e
