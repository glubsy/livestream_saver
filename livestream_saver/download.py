#!/usr/bin/env python
from os import path, makedirs, listdir
from sys import stderr
from platform import system
import logging
from datetime import date, datetime
from time import time, sleep
from json import dumps, dump
from contextlib import closing
from enum import Flag, auto
import asyncio
from typing import Optional, Dict, Tuple, Union, List, Set
from types import MethodType
from pathlib import Path
from queue import LifoQueue
import re
import urllib.request
import urllib.error
from http.client import IncompleteRead
import xml.etree.ElementTree as ET

import pytube
import pytube.exceptions
# import aiohttp

from livestream_saver import exceptions
from livestream_saver import extract
from livestream_saver import util
from livestream_saver.constants import *
# import livestream_saver
from livestream_saver.request import ASession as Session
from livestream_saver.notifier import NotificationDispatcher
from livestream_saver.hooks import is_wanted_based_on_metadata

SYSTEM = system()
ISPOSIX = SYSTEM == 'Linux' or SYSTEM == 'Darwin'
ISWINDOWS = SYSTEM == 'Windows'
COPY_BUFSIZE = 1024 * 1024 if ISWINDOWS else 64 * 1024


class Status(Flag):
    OFFLINE = auto()
    AVAILABLE = auto()
    LIVE = auto()
    VIEWED_LIVE = auto()
    WAITING = auto()
    OK = AVAILABLE | LIVE | VIEWED_LIVE

# logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


class PytubeYoutube(pytube.YouTube):
    """Wrapper to override some methods in order to bypass several restrictions
    due to lacking features in pytube (most notably live stream support)."""
    def __init__(self, *args, **kwargs):
        # Keep a handle to update its status
        self.parent: Optional[YoutubeLiveBroadcast] = kwargs.get("parent")
        self.session: Optional[Session] = kwargs.get("session")
        super().__init__(*args)
        # if "www" is omitted, it might force a redirect on YT's side
        # (with &ucbcb=1) and force us to update cookies again. YT is very picky
        # about that. Let's just avoid it.
        self.watch_url = f"https://www.youtube.com/watch?v={self.video_id}"


# Temporary backport from pytube 11.0.1
def get_throttling_function_name(js: str) -> str:
    """Extract the name of the function that computes the throttling parameter.

    :param str js:
        The contents of the base.js asset file.
    :rtype: str
    :returns:
        The name of the function used to compute the throttling parameter.
    """
    function_patterns = [
        # https://github.com/yt-dlp/yt-dlp/commit/48416bc4a8f1d5ff07d5977659cb8ece7640dcd8
        # var Bpa = [iha];
        # ...
        # a.C && (b = a.get("n")) && (b = Bpa[0](b), a.set("n", b),
        # Bpa.length || iha("")) }};
        # In the above case, `iha` is the relevant function name
        r'a\.[A-Z]\s*&&\s*\(b\s*=\s*a\.get\("n"\)\)\s*&&\s*\(b\s*=\s*([a-zA-Z0-9$]{3})(\[\d+\])?\(b\)',
    ]
    # print('Finding throttling function name')
    for pattern in function_patterns:
        regex = re.compile(pattern)
        function_match = regex.search(js)
        if function_match:
            print("finished regex search, matched: %s", pattern)
            if len(function_match.groups()) == 1:
                return function_match.group(1)
            idx = function_match.group(2)
            if idx:
                idx = idx.strip("[]")
                array = re.search(
                    r'var {nfunc}\s*=\s*(\[.+?\]);'.format(
                        nfunc=function_match.group(1)), 
                    js
                )
                if array:
                    array = array.group(1).strip("[]").split(",")
                    array = [x.strip() for x in array]
                    return array[int(idx)]

    raise pytube.RegexMatchError(
        caller="get_throttling_function_name", pattern="multiple"
    )
pytube.cipher.get_throttling_function_name = get_throttling_function_name

# Another temporary backport to fix https://github.com/pytube/pytube/issues/1163
def throttling_array_split(js_array):
    results = []
    curr_substring = js_array[1:]

    comma_regex = re.compile(r",")
    func_regex = re.compile(r"function\([^)]*\)")

    while len(curr_substring) > 0:
        if curr_substring.startswith('function') and func_regex.search(curr_substring) is not None:
            # Handle functions separately. These can contain commas
            match = func_regex.search(curr_substring)

            match_start, match_end = match.span()

            function_text = pytube.parser.find_object_from_startpoint(curr_substring, match.span()[1])
            full_function_def = curr_substring[:match_end + len(function_text)]
            results.append(full_function_def)
            curr_substring = curr_substring[len(full_function_def) + 1:]
        else:
            match = comma_regex.search(curr_substring)

            # Try-catch to capture end of array
            try:
                match_start, match_end = match.span()
            except AttributeError:
                match_start = len(curr_substring) - 1
                match_end = match_start + 1


            curr_el = curr_substring[:match_start]
            results.append(curr_el)
            curr_substring = curr_substring[match_end:]

    return results
pytube.cipher.throttling_array_split = throttling_array_split


# Another temporary hotfix https://github.com/pytube/pytube/issues/1199
def patched__init__(self, js: str):
    self.transform_plan: List[str] = pytube.cipher.get_transform_plan(js)
    var_regex = re.compile(r"^\$*\w+\W")
    var_match = var_regex.search(self.transform_plan[0])
    if not var_match:
        raise RegexMatchError(
            caller="__init__", pattern=var_regex.pattern
        )
    var = var_match.group(0)[:-1]
    self.transform_map = pytube.cipher.get_transform_map(js, var)
    self.js_func_patterns = [
        r"\w+\.(\w+)\(\w,(\d+)\)",
        r"\w+\[(\"\w+\")\]\(\w,(\d+)\)"
    ]

    self.throttling_plan = pytube.cipher.get_throttling_plan(js)
    self.throttling_array = pytube.cipher.get_throttling_function_array(js)

    self.calculated_n = None

pytube.cipher.Cipher.__init__ = patched__init__


class BaseURL(str):
    """Wrapper class to handle incrementing segment number in various URL formats."""
    def __new__(cls, content):
        return str.__new__(cls,
            content[:-1] if content.endswith("/") else content)

    def add_seg(self, seg_num: int):
        raise NotImplementedError()


class ParamURL(BaseURL):
    """Old-school url with parameters."""
    def add_seg(self, seg_num: int) -> str:
        return self + f"&sq={seg_num}"


class PathURL(BaseURL):
    """URL made with lots of "/" for them fancy new APIs."""
    def add_seg(self, seg_num: int) -> str:
        return self + f"/sq/{seg_num}"


class YoutubeLiveStream():
    def __init__(
        self,
        url: str,
        output_dir: Path,
        session: YoutubeUrllibSession,
        notifier: NotificationDispatcher,
        video_id: Optional[str] = None,
        max_video_quality: Optional[str] = None,
        hooks: Dict = {},
        skip_download = False,
        filters: Dict[str, re.Pattern] = {},
        log_level = logging.INFO
    ) -> None:

        self.session = session
        self.url = url
        self.max_video_quality = max_video_quality
        self.video_id = video_id if video_id is not None \
                                 else extract.get_video_id(url)

        self._js: Optional[str] = None  # js fetched by js_url
        self._js_url: Optional[str] = None  # the url to the js, parsed from watch html

        self._watch_html: Optional[str] = None
        self._embed_html: Optional[str] = None

        self._json: Optional[Dict] = {}

        self.hooks = hooks
        self.skip_download = skip_download

        # NOTE if "www" is omitted, it might force a redirect on YT's side
        # (with &ucbcb=1) and force us to update cookies again. YT is very picky
        # about that. Let's just avoid it.
        self.watch_url = f"https://www.youtube.com/watch?v={self.video_id}"
        self.embed_url = f"https://www.youtube.com/embed/{self.video_id}"

        self._author: Optional[str] = None
        self._title: Optional[str] = None
        self._publish_date: Optional[datetime] = None
        self.video_itag = None
        self.audio_itag = None

        self._player_config_args: Optional[Dict] = None
        self._player_response: Optional[Dict] = None
        self._fmt_streams: Optional[List[pytube.Stream]] = None

        self._chosen_itags: Dict = {}

        self._download_date: Optional[str] = None
        self._scheduled_timestamp = None
        self._start_time: Optional[str] = None

        self._age_restricted: Optional[bool] = None

        self.video_base_url = None
        self.audio_base_url = None
        self.seg = 0
        self.seg_attempt = 0
        self.status = Status.OFFLINE
        self.done = False
        self.error = None
        self.mpd = None

        self.output_dir = output_dir
        if not self.output_dir.exists():
            util.create_output_dir(
                output_dir=output_dir, video_id=None
            )

        # self.output_dir = output_dir \
        #     if output_dir.exists() \
        #     else util.create_output_dir(
        #         output_dir=output_dir, video_id=None
        #     )

        self.logger = self.setup_logger(self.output_dir, log_level)
        self.notifier = notifier

        self.video_outpath = self.output_dir / 'vid'
        self.audio_outpath = self.output_dir / 'aud'

        self.allow_regex: Optional[re.Pattern] = filters.get("allow_regex")
        self.block_regex: Optional[re.Pattern] = filters.get("block_regex")

    def setup_logger(self, output_path, log_level):
        if isinstance(log_level, str):
            log_level = str.upper(log_level)

        # We need to make an independent logger (with no parent) in order to
        # avoid using the parent logger's handlers, although we are writing
        # to the same file.
        logger = logging.getLogger("download" + "." + self.video_id)

        if self.skip_download:
            # Increase level filter because we don't care as much
            logger.setLevel(logging.WARNING)
            return logger

        if logger.hasHandlers():
            logger.debug(
                f"Logger {logger} already had handlers!"
            )
            return logger

        logger.setLevel(logging.DEBUG)
        # File output
        logfile = logging.FileHandler(
            filename=output_path / "download.log", delay=True, encoding='utf-8'
        )
        logfile.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
        )
        logfile.setFormatter(formatter)
        logger.addHandler(logfile)

        # Console output
        conhandler = logging.StreamHandler()
        conhandler.setLevel(log_level)
        conhandler.setFormatter(formatter)

        def dumb_filter(record):
            # if "Downloading segment" in record.msg:
            # Only filter logRecords that came from our function
            if record.funcName == "print_progress":
                return False
            return True

        confilter = logging.Filter()
        confilter.filter = dumb_filter
        conhandler.addFilter(confilter)
        logger.addHandler(conhandler)
        return logger

    def get_first_segment(self, paths) -> int:
        """
        Determine the first segment number from which we should download.
        If some files are found in paths, get the last segment numbers from each
        and return the lowest number of the two.
        """
        # The sequence number to start downloading from (acually starts at 0).
        seg = 0

        # Get the latest downloaded segment number,
        # unless one directory holds an earlier segment than the other.
        # video_last_segment = max([int(f[:f.index('.')]) for f in listdir(paths[0])])
        # audio_last_segment = max([int(f[:f.index('.')]) for f in listdir(paths[1])])
        # seg = min(video_last_segment, audio_last_segment)
        seg = min([
                max([int(f[:f.index('.')].split('_')[0])
                for f in listdir(p)], default=1)
                for p in paths
            ])

        # Step back one file just in case the latest segment got only partially
        # downloaded (we want to overwrite it to avoid a corrupted segment)
        if seg > 0:
            self.logger.warning(f"An output directory already existed. \
We assume a failed download attempt. Last segment available was {seg}.")
            seg -= 1
        return seg

    def is_live(self) -> None:
        if not self.json:
            return

        isLive = self.json.get('videoDetails', {}).get('isLive')
        if isLive is not None and isLive is True:
            self.status |= Status.LIVE
        else:
            self.status &= ~Status.LIVE

        # Is this actually being streamed live?
        val = None
        for _dict in self.json.get('responseContext', {}).get('serviceTrackingParams', []):
            param = _dict.get('params', [])
            for key in param:
                if key.get('key') == 'is_viewed_live':
                    val = key.get('value')
                    break
        if val and val == "True":
            self.status |= Status.VIEWED_LIVE
        else:
            self.status &= ~Status.VIEWED_LIVE
        self.logger.debug(f"is_live() status {self.status}")

    def check_availability(self):
        """Skip this check to avoid raising pytube exceptions."""
        pass

    @property
    def watch_html(self):
        """Override for livestream_saver. We have to make the request ourselves
        in order to pass the cookies."""
        # TODO get the DASH manifest (MPD) instead?
        if not self.session:
            return super().watch_html
        if self._watch_html:
            return self._watch_html
        try:
            self._watch_html = self.session.make_request(url=self.watch_url)
        except Exception as e:
            logging.debug(f"Error getting the watch_html: {e}")
            self._watch_html = None

        return self._watch_html

    @property
    def embed_html(self):
        """Override for livestream_saver. We have to make the request ourselves
        in order to pass the cookies."""
        if not self.session:
            return super().embed_html
        if self._embed_html:
            return self._embed_html
        self._embed_html = self.session.make_request(url=self.embed_url)
        return self._embed_html


    @property
    def json(self):
        if self._json:
            return self._json
        try:
            # json_string = extract.initial_player_response(self.watch_html)
            # API request with ANDROID client gives us a pre-signed URL
            json_string = self.session.make_api_request(self.video_id)
            self._json = extract.str_as_json(json_string)
            self.session.is_logged_out(self._json)

            remove_useless_keys(self._json)
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(
                    "Extracted JSON from html:\n"
                    + dumps(self._json, indent=4, ensure_ascii=False)
                )
        except Exception as e:
            self.logger.debug(f"Error extracting JSON from HTML: {e}")
            self._json = {}

        if not self._json:
            self.logger.critical(
                f"WARNING: invalid JSON for {self.watch_url}: {self._json}"
            )
            self.status &= ~Status.AVAILABLE

        return self._json

    @property
    def publish_date(self):
        """Get the publish date.

        :rtype: datetime
        """
        if self._publish_date:
            return self._publish_date
        self._publish_date = extract.publish_date(self.watch_html)
        return self._publish_date

    @publish_date.setter
    def publish_date(self, value):
        """Sets the publish date."""
        self._publish_date = value

    def get_content_from_mpd(self):
        content = None
        if self.mpd is None:
            mpd = MPD(self)
            try:
                content = mpd.get_content()
            except Exception as e:
                self.logger.critical(e)
                return None
            self.mpd = mpd
        else:
            content = self.mpd.get_content(update=True)
        return content

    def get_streams_from_xml(self, data) -> List[pytube.Stream]:
        """Parse MPD Dash manifest and return basic Stream objects from data found."""
        # We could use the python-mpegdash package but that would be overkill
        # https://www.brendanlong.com/the-structure-of-an-mpeg-dash-mpd.html
        # https://www.brendanlong.com/common-informative-metadata-in-mpeg-dash.html
        if not data:
            return []
        streams = []
        try:
            root = ET.fromstring(data)
            # Strip namespaces for easier access (not necessary, let's use wildcards)
            # it = ET.iterparse(StringIO(data))
            # for _, el in it:
            #     _, _, el.tag = el.tag.rpartition('}') # strip ns
            # root = it.root
        except Exception as e:
            self.logger.critical(f"Error loading XML of MPD: {e}")
            return streams

        for _as in root.findall("{*}Period/{*}AdaptationSet"):
            mimeType = _as.attrib.get("mimeType")
            for _rs in _as.findall("{*}Representation"):
                url = _rs.find("{*}BaseURL")
                if url is None:
                    self.logger.debug(f"No BaseURL found for {_rs.attrib}. Skipping.")
                    continue
                # Simulate what pytube does in a very basic way
                # FIXME this can safely be removed in next pytube:
                codec = _rs.get("codecs")
                if not codec or not mimeType:
                    self.logger.debug(f"No codecs key found for {_rs.attrib}. Skipping")
                    continue
                streams.append(pytube.Stream(
                        stream = {
                            "url": PathURL(url.text),
                            "mimeType": mimeType,
                            "type": mimeType + "; codecs=\"" + codec + "\"",  # FIXME removed in next pytube
                            "itag": _rs.get('id'),
                            "is_otf": False,  # not used
                            "bitrate": None, # not used,
                            "content_length": None, # FIXME removed in next pytube
                            "fps": _rs.get("frameRate") # FIXME removed in next pytube
                        },
                        monostate = {},
                        player_config_args = {} # FIXME removed in next pytube
                    )
                )
        return streams

    def get_streams_from_mpd(self) -> List[pytube.Stream]:
        content = self.get_content_from_mpd()
        # TODO for now we only care about the XML DASH MPD, but not the HLS m3u8.
        return self.get_streams_from_xml(content)

    @property
    def streams(self):
        """Interface to query both adaptive (DASH) and progressive streams.

        :rtype: :class:`StreamQuery <StreamQuery>`.
        """
        # self.update_status()
        query = pytube.StreamQuery(self.fmt_streams)
        # BUG in pytube, livestreams with resolution higher than 1080 do not 
        # return descriptions for their available streams, except in the 
        # DASH MPD manifest! These descriptions seem to re-appear after the 
        # stream has been converted to a VOD though.
        if len(query) == 0:
            if mpd_streams := self.get_streams_from_mpd():
                self.logger.warning(
                    "Could not find any stream descriptor in the response!"
                    f" Loaded streams from MPD instead.")
                self.logger.debug(f"Streams from MPD: {mpd_streams}.")
                query = pytube.StreamQuery(mpd_streams)
            else:
                raise Exception("Failed to load stream descriptors!")
        return query

    @property
    def age_restricted(self):
        if self._age_restricted:
            return self._age_restricted
        self._age_restricted = pytube.extract.is_age_restricted(self.watch_html)
        return self._age_restricted

    @property
    def js_url(self):
        if self._js_url:
            return self._js_url

        if self.age_restricted:
            self._js_url = pytube.extract.js_url(self.embed_html)
        else:
            self._js_url = pytube.extract.js_url(self.watch_html)

        return self._js_url

    @property
    def js(self):
        """Override for livestream_saver. We have to make the request ourselves
        in order to pass the cookies."""
        if not self.session:
            return super().js
        if self._js:
            return self._js
        if pytube.__js_url__ != self.js_url:
            self._js = self.session.make_request(url=self.js_url)
            pytube.__js__ = self._js
            pytube.__js_url__ = self.js_url
        else:
            self._js = pytube.__js__

        return self._js


    # @property
    # def fmt_streams(self):
    #     """Returns a list of streams if they have been initialized.

    #     If the streams have not been initialized, finds all relevant
    #     streams and initializes them.
    #     """
    #     # For Livestream_saver, we should skip this check or risk hitting a
    #     # pytube exception. Overriding it might be simpler...
    #     # self.check_availability()
    #     if self._fmt_streams:
    #         return self._fmt_streams

    #     self._fmt_streams = []

    #     stream_manifest = pytube.extract.apply_descrambler(self.streaming_data)

    #     # If the cached js doesn't work, try fetching a new js file
    #     # https://github.com/pytube/pytube/issues/1054
    #     try:
    #         pytube.extract.apply_signature(stream_manifest, self.vid_info, self.js)
    #     except pytube.exceptions.ExtractError:
    #         # To force an update to the js file, we clear the cache and retry
    #         self._js = None
    #         self._js_url = None
    #         pytube.__js__ = None
    #         pytube.__js_url__ = None
    #         pytube.extract.apply_signature(stream_manifest, self.vid_info, self.js)

    #     # build instances of :class:`Stream <Stream>`
    #     # Initialize stream objects
    #     for stream in stream_manifest:
    #         video = Stream(
    #             stream=stream,
    #             monostate=self.stream_monostate,
    #         )
    #         self._fmt_streams.append(video)

    #     self.stream_monostate.title = self.title
    #     self.stream_monostate.duration = self.length

    #     return self._fmt_streams

    # @property
    # def title(self) -> str:
    #     """Override for livestream_saver to avoid check_availabilty."""
    #     if self._title:
    #         return self._title
    #     try:
    #         # FIXME decode unicode escape sequences if any
    #         self._title = self.vid_info['videoDetails']['title']
    #     except KeyError as e:
    #         self.logger.debug(f"KeyError in {self.video_id}.title: {e}")
    #         # Check_availability will raise the correct exception in most cases
    #         #  if it doesn't, ask for a report. - pytube
    #         # self.check_availability()
    #         # Yeah no. We'll do it ourselves, thank you.
    #         self.parent.update_status()
    #         raise pytube.exceptions.PytubeError(
    #             (
    #                 f'Exception while accessing title of {self.watch_url}. '
    #                 'Please file a bug report at https://github.com/pytube/pytube'
    #             )
    #         )
    #     return self._title

    # @property
    # def streams(self):
    #     """Override for livestream_saver to avoid check_availability.
    #     """
    #     # self.check_availability()
    #     return pytube.StreamQuery(self.fmt_streams)


class YoutubeLiveBroadcast():
    def __init__(
        self,
        url: str,
        output_dir: Path,
        session: Session,
        notifier: NotificationDispatcher,
        video_id: Optional[str] = None,
        filter_args: Dict = {},
        hooks: dict = {},
        skip_download = False,
        regex_filters = {},
        log_level = logging.INFO
    ) -> None:

        self.session: Session = session
        self.url = url
        self.wanted_itags: Optional[Tuple] = None
        self.filter_args = filter_args
        self.video_id = video_id if video_id is not None \
                                 else extract.get_video_id(url)
        self._json: Dict = {}
        self.ptyt = PytubeYoutube(url, session=session, parent=self)
        self.download_start_triggered = False
        self.hooks = hooks
        self.skip_download = skip_download
        self.selected_streams: set[pytube.Stream] = set()
        self.video_stream = None
        self.video_itag = None
        self.audio_stream = None
        self.audio_itag = None
        self._scheduled_timestamp = None
        self._start_time: Optional[str] = None
        self.seg = 0
        self._status = Status.OFFLINE
        self._has_started = False
        self._has_ended = False
        self.done = False
        self.error = None

        # Create output dir first in order to store log in it
        self.output_dir = output_dir
        if not self.output_dir.exists():
            util.create_output_dir(
                output_dir=output_dir, video_id=None
            )

        # global logger
        self.logger = setup_logger(self.output_dir, log_level, self.video_id)

        # self.video_outpath = self.output_dir / 'vid'
        # self.audio_outpath = self.output_dir / 'aud'

        self.allow_regex: Optional[re.Pattern] = regex_filters.get("allow_regex")
        self.block_regex: Optional[re.Pattern] = regex_filters.get("block_regex")

    @property
    def streams(self) -> StreamQuery:
        return self.ptyt.streams

    def print_available_streams(self, logger: logging.Logger = None) -> None:
        if logger is None:
            logger = self.logger
        if len(self.streams) == 0:
            raise Exception("No stream available.")
        for s in self.streams:
            logger.info(
                "Available {}".format(s.__repr__().replace(' ', '\t'))
            )

    def filter_streams(
        self,
        vcodec: str = "mp4",
        acodec: str = "mp4",
        itags: Optional[str] = None,
        maxq: Optional[str] = None
    ) -> None:
        """Sets the selected_streams property to a Set of streams selected from
        user supplied parameters (itags, or max quality threshold)."""
        self.logger.debug(f"Filtering streams: itag {itags}, maxq {maxq}")

        submitted_itags = util.split_by_plus(itags)

        selected_streams: Set[Stream] = set()
        # If an itag is supposed to provide a video track, we assume
        # the user wants a video track. Same goes for audio.
        wants_video = wants_audio = True
        invalid_itags = None

        if submitted_itags is not None:
            wants_video, wants_audio, invalid_itags = \
                util.check_available_tracks_from_itags(submitted_itags)
            if invalid_itags:
                # However, if we discarded an itag, we cannot be sure of what
                # the user really wanted. We assume they wanted both.
                self.logger.warning(f"Invalid itags {invalid_itags} supplied.")
                wants_video = wants_audio = True

            submitted_itags = tuple(
                itag for itag in submitted_itags if itag not in invalid_itags
            )

        found_by_itags = set()
        itags_not_found = set()
        if submitted_itags:
            for itag in submitted_itags:
                # if available_stream := util.stream_by_itag(itag, self.streams):
                if available_stream := self.streams.get_by_itag(itag):
                    found_by_itags.add(available_stream)
                else:
                    itags_not_found.add(itag)
                    self.logger.warning(
                        f"itag {itag} could not be found among available streams.")

            # This is exactly what the user wanted
            # FIXME fail if we have specified 2 progressive streams?
            if found_by_itags and len(found_by_itags) == len(submitted_itags) \
            and not invalid_itags:
                self.selected_streams = found_by_itags
                return
            elif found_by_itags:
                selected_streams = found_by_itags

            if len(found_by_itags) == 0:
                self.logger.warning(
                    "Could not find any of the specified itags "
                    f"\"{submitted_itags}\" among the available streams."
                )

        missing_audio = True
        missing_video = True

        for stream in selected_streams:
            self.selected_streams.add(stream)
            # At least one stream should be enough since it has both video/audio
            if stream.is_progressive:
                self.selected_streams = selected_streams
                return
            if stream.includes_audio_track:
                missing_audio = False
            if stream.includes_video_track:
                missing_video = False

        if missing_video and wants_video:
            video_stream = self._filter_streams(
                tracktype="video", codec=vcodec, maxq=maxq
            )
            self.selected_streams.add(video_stream)

        if missing_audio and wants_audio:
            for stream in self.selected_streams:
                if stream.is_progressive:
                    # We already have an audio track
                    return

            # No quality limit for now on audio
            audio_stream = self._filter_streams(
                tracktype="audio", codec=acodec, maxq=None
            )
            self.selected_streams.add(audio_stream)

        if not self.selected_streams:
            raise Exception(f"No stream assigned to {self.video_id} object!")
        else:
            # We emulate an initializator because no idea how to subclass
            # pytube.Stream other than with a monkeypatch
            # TODO: see https://stackoverflow.com/questions/100003/what-are-metaclasses-in-python?rq=1
            for stream in self.selected_streams:
                stream.parent = self
                stream.logger = self.logger
                stream.missing_segs = []
                stream.start_seg = 0
                stream.current_seg = 0
                # stream.async_download = async_download
                stream.async_download = MethodType(async_download, stream)
                stream.fname_suffix = stream.type[1:] if stream.is_adaptive else "a+v"
                stream.dir_suffix = "f" + str(stream.itag)

    def _filter_streams(
        self,
        tracktype: str,
        codec: str,
        maxq: Optional[str] = None) -> Stream:
        """
        tracktype == video or audio
        codec == mp4, mov, webm...
        Coalesce filters depending on user-specified criteria.
        """
        if tracktype == "video":
            custom_filters = self.generate_custom_filter(maxq)
            criteria = "resolution"
        else:
            custom_filters = None
            criteria = "abr"

        q = LifoQueue(maxsize=5)
        self.logger.debug(f"Filtering {tracktype} streams by type: \"{codec}\"")
        streams = self.streams.filter(
            subtype=codec, type=tracktype
        )

        if len(streams) == 0:
            self.logger.debug(
                f"No {tracktype} streams for type: \"{codec}\". "
                "Falling back to filtering without any criterium."
            )
            streams = self.streams.filter(type=tracktype)

        self.logger.debug(f"Pushing onto stack: {streams}")
        q.put(streams)

        # This one will usually be empty for livestreams anyway
        # NOTE the if statement is not really necessary, we could push
        # an empty query, it would not matter much in the end
        if progressive_streams := streams.filter(progressive=True):
            self.logger.debug(
                f"Pushing progressive {tracktype} streams to stack: {progressive_streams}"
            )
            q.put(progressive_streams)

        # Prefer adaptive to progressive, so we do this last in order to
        # put on top of the stack and test it first
        if adaptive_streams := streams.filter(adaptive=True):
            self.logger.debug(
                f"Pushing adaptive {tracktype} streams to stack: {adaptive_streams}"
            )
            q.put(adaptive_streams)

        selected_stream = None
        while not selected_stream:
            query = q.get()
            # Filter anything above our maximum desired resolution
            query = query.filter(custom_filter_functions=custom_filters) \
                .order_by(criteria).desc()
            selected_stream = query.first()
            if q.empty():
                break

        if not selected_stream:
            self.logger.critical(
                f"Could not get a specified {tracktype} stream! "
            )
            selected_stream = self.streams.filter(type=tracktype) \
                .order_by(criteria).desc().first()
            self.logger.critical(
                f"Falling back to best quality available: {selected_stream}"
            )
        else:
            self.logger.info(f"Selected {tracktype} stream: {selected_stream}")

        return selected_stream

    def generate_custom_filter(self, maxq: Optional[str]) -> Optional[List]:
        """Generate a list of (currently one) callback functions to use in
        pytube.StreamQuery to filter streams up to a specified maximum
        resolution, or average bitrate (although we don't use audio bitrate)."""
        if maxq is None:
            return None

        def as_int_re(res_or_abr: str) -> Optional[int]:
            if res_or_abr is None:
                return None
            as_int = None
            # looks for "1080p" or "48kbps", either a resolution or abr
            if match := re.search(r"(\d{3,4})(p)?|(\d{2,4})(kpbs)?", res_or_abr):
                as_int = int(match.group(1))
            return as_int

        i_maxq = None
        if maxq is not None:
            i_maxq = as_int_re(maxq)
            # if match := re.search(r"(\d{3,4})(p)?|(\d{2,4}(kpbs)?", maxq):
            #     maxq = int(match.group(1))
            if i_maxq is None:
                self.logger.warning(
                    f"Max resolution setting \"{maxq}\" is incorrect. "
                    "Defaulting to best video quality available."
                )
        elif isinstance(maxq, int):
            i_maxq = maxq

        custom_filters = None
        if i_maxq is not None:  # int
            def resolution_filter(s: Stream) -> bool:
                res_int = as_int_re(s.resolution)
                if res_int is None:
                    return False
                return res_int <= i_maxq

            def abitrate_filter(s: Stream) -> bool:
                res_int = as_int_re(s.abr)
                if res_int is None:
                    return False
                return res_int <= i_maxq

            # FIXME currently we don't use audio track filtering and we take
            # the highest abr available.
            if "kpbs" in maxq:
                custom_filters = [abitrate_filter]
            else:
                custom_filters = [resolution_filter]
        return custom_filters

    @property
    def title(self):
        return self.ptyt.title

    @property
    def decription(self):
        return self.player_response.get("videoDetails", {}).get("shortDescription")

    # # NOT USED
    # def populate_info(self):
    #     if not self.json:
    #         return

    #     self.video_title = self.json.get('videoDetails', {}).get('title')
    #     self.author =  self.json.get('videoDetails', {}).get('author')

    #     if not self.thumbnail_url:
    #         tlist = self.json.get('videoDetails', {}).get('thumbnail', {}).get('thumbnails', [])
    #         if tlist:
    #             # Grab the last one, probably always highest resolution
    #             # FIXME grab the best by comparing width/height key-values.
    #             self.thumbnail_url = tlist[-1].get('url')

    #     # self.scheduled_time = self.get_scheduled_time(self.json.get('playabilityStatus', {}))

    #     if self.logger.isEnabledFor(logging.DEBUG):
    #         self.logger.debug(f"Video ID: {self.video_id}")
    #         self.logger.debug(f"Video title: {self.title}")
    #         self.logger.debug(f"Video author: {self.author}")


    @property
    def thumbnail_url(self) -> str:
        """Get the best thumbnail url image.

        :rtype: str
        """
        # The last item seems to have the maximum size
        best_thumbnail = (
            self.ptyt.player_response.get("videoDetails", {})
            .get("thumbnail", {})
            .get("thumbnails", [{}])[-1]\
            .get('url')
        )
        if best_thumbnail:
            return best_thumbnail
        return f"https://img.youtube.com/vi/{self.video_id}/maxresdefault.jpg"

    @property
    def start_time(self):
        if self._start_time:
            return self._start_time
        try:
            # String reprensentation in UTC format
            self._start_time = self.ptyt.player_response \
                .get("microformat", {}) \
                .get("playerMicroformatRenderer", {}) \
                .get("liveBroadcastDetails", {}) \
                .get("startTimestamp", None)
        except Exception as e:
            self.logger.debug(f"Error getting start_time: {e}")
        return self._start_time

    def author(self) -> str:
        """Get the video author.
        :rtype: str
        """
        if self._author:
            return self._author
        self._author = self.player_response.get(
            "videoDetails", {}).get("author", "Author?")
        return self._author

    @author.setter
    def author(self, value):
        """Set the video author."""
        self._author = value

    def download_thumbnail(self):
        # TODO write more thumbnail files in case the first one somehow
        #  got updated.
        thumbnail_path = self.output_dir / 'thumbnail'
        if self.thumbnail_url and not path.exists(thumbnail_path):
            try:
                with closing(urlopen(self.thumbnail_url)) as in_stream:
                    self.write_to_file(in_stream, thumbnail_path)
            except Exception as e:
                self.logger.warning(f"Error writing thumbnails: {e}")

    def update_metadata(self):
        if self.video_itag:
            if info := pytube.itags.ITAGS.get(self.video_itag):
                self.video_resolution = info[0]
        if self.audio_itag:
            if info := pytube.itags.ITAGS.get(self.audio_itag):
                self.audio_bitrate = info[0]

        self.download_thumbnail()

        # TODO get the description once the stream has started

        metadata_file = self.output_dir / 'metadata.json'
        if path.exists(metadata_file):
            # FIXME this avoids writing this file more than once for now.
            # No further updates.
            return
        with open(metadata_file, 'w', encoding='utf8') as fp:
            dump(obj=self.video_info, fp=fp, indent=4, ensure_ascii=False)

    @property
    def video_streams(self):
        return (s for s in self.streams if s.includes_video_track())

    @property
    def audio_streams(self):
        return (s for s in self.streams if s.includes_audio_track())

    # was is_live()
    def live_status(self, force_update=False) -> None:
        if force_update:
            self.ptyt._watch_html = None
            self._json = {}

        if not self.json:
            return

        isLive = self.json.get('videoDetails', {}).get('isLive')
        if isLive is not None and isLive is True:
            self._status |= Status.LIVE
        else:
            self._status &= ~Status.LIVE

        # Is this actually being streamed live?
        val = None
        for _dict in self.ptyt.json.get('responseContext', {}) \
        .get('serviceTrackingParams', []):
            param = _dict.get('params', [])
            for key in param:
                if key.get('key') == 'is_viewed_live':
                    val = key.get('value')
                    break
        if val and val == "True":
            self._status |= Status.VIEWED_LIVE
        else:
            self._status &= ~Status.VIEWED_LIVE

        self.logger.debug(f"is_live() status is now {self._status}")


    def status(self, update=False) -> Status:
        """Check if the stream is still reported as being 'live' and update
        the status property accordingly."""
        if update:
            self.ptyt._watch_html = None
            self._json = {}

        if not self.json:
            raise Exception("Missing json data during status check")

        status = self._status

        self.is_live()
        if not self.skip_download:
            self.logger.info("Stream seems to be viewed live. Good.") \
        if self.status & Status.VIEWED_LIVE else \
        self.logger.warning(
            "Stream is not being viewed live. This might not work!"
        )

        # Check if video is indeed available through its reported status.
        playabilityStatus = self.json.get('playabilityStatus', {})
        status = playabilityStatus.get('status')

        if status == 'LIVE_STREAM_OFFLINE':
            self._status |= Status.OFFLINE

            scheduled_time = self.scheduled_timestamp
            if scheduled_time is not None:
                self._status |= Status.WAITING

                # self._scheduled_timestamp = scheduled_time
                reason = playabilityStatus.get('reason', 'No reason found.')

                self.logger.info(f"Scheduled start time: {scheduled_time} \
({datetime.utcfromtimestamp(scheduled_time)} UTC). We wait...")
                # FIXME use local time zone for more accurate display of time
                # for example: https://dateutil.readthedocs.io/
                self.logger.warning(f"{reason}")

                raise exceptions.WaitingException(
                    self.video_id, reason, scheduled_time
                )

            elif (Status.LIVE | Status.VIEWED_LIVE) not in self._status:
                raise exceptions.WaitingException(
                    self.video_id,
                    playabilityStatus.get('reason', 'No reason found.')
                )

            raise exceptions.OfflineException(
                self.video_id,
                playabilityStatus.get('reason', 'No reason found.')
            )

        elif status == 'LOGIN_REQUIRED':
            raise exceptions.NoLoginException(
                self.video_id,
                playabilityStatus.get('reason', 'No reason found.')
            )

        elif status == 'UNPLAYABLE':
            raise exceptions.UnplayableException(
                self.video_id,
                playabilityStatus.get('reason', 'No reason found.')
            )

        elif status != 'OK':
            subreason = playabilityStatus.get('errorScreen', {})\
                                         .get('playerErrorMessageRenderer', {})\
                                         .get('subreason', {})\
                                         .get('simpleText', \
                                              'No subreason found in JSON.')
            self.logger.warning(
                f"Livestream {self.video_id} playability status is: {status}"
                f"{playabilityStatus.get('reason', 'No reason found')}. "
                f"Sub-reason: {subreason}"
            )
            self._status &= ~Status.AVAILABLE
            # return
        else: # status == 'OK'
            self._status |= Status.AVAILABLE
            self._status &= ~Status.OFFLINE
            self._status &= ~Status.WAITING

        self.logger.info(f"Stream status {self._status}")

        return self._status

    @property
    def json(self) -> dict:
        """Return the extracted json from html and update some states in the
        process."""
        if self._json:
            return self._json
        try:
            json_string = extract.initial_player_response(self.ptyt.watch_html)
            self._json = extract.str_as_json(json_string)
            self.session.is_logged_out(self._json)

            remove_useless_keys(self._json)
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(
                    "Extracted JSON from html:\n"
                    + dumps(self._json, indent=4, ensure_ascii=False)
                )
        except Exception as e:
            self.logger.debug(f"Error extracting JSON from HTML: {e}")
            self._json = {}

        if not self._json:
            self.logger.critical(
                f"WARNING: invalid JSON for {self.ptyt.watch_url}: {self._json}"
            )
            self._status &= ~Status.AVAILABLE

        return self._json

    @property
    def start_time(self) -> Optional[str]:
        if self._start_time:
            return self._start_time
        try:
            # String reprensentation in UTC format
            self._start_time = self.json \
                .get("microformat", {}) \
                .get("playerMicroformatRenderer", {}) \
                .get("liveBroadcastDetails", {}) \
                .get("startTimestamp", None)
            self.logger.info(f"Found start time: {self._start_time}")
        except Exception as e:
            self.logger.debug(f"Error getting start_time: {e}")
        return self._start_time

    @property
    def scheduled_timestamp(self) -> Optional[int]:
        if self._scheduled_timestamp:
            return self._scheduled_timestamp
        try:
            timestamp = self.json.get("playabilityStatus", {}) \
                .get('liveStreamability', {})\
                .get('liveStreamabilityRenderer', {}) \
                .get('offlineSlate', {}) \
                .get('liveStreamOfflineSlateRenderer', {}) \
                .get('scheduledStartTime', None) # unix timestamp
            if timestamp is not None:
                self._scheduled_timestamp = int(timestamp)
            else:
                self._scheduled_timestamp = None
            self.logger.info(f"Found scheduledStartTime: {self._scheduled_timestamp}")
        except Exception as e:
            self.logger.debug(f"Error getting scheduled_timestamp: {e}")
        return self._scheduled_timestamp

    def download_thumbnail(self) -> None:
        # TODO write more thumbnail files in case the first one somehow
        # got updated, by renaming, then placing in place.
        thumbnail_path = self.output_dir / ('thumbnail_' + self.video_id)
        if self.ptyt.thumbnail_url and not path.exists(thumbnail_path):
            with closing(urllib.request.urlopen(self.ptyt.thumbnail_url)) as in_stream:
                write_to_file(self.logger, in_stream, thumbnail_path)

    def update_metadata(self) -> None:
        """Fetch various metadata and write them to disk."""
        if self.video_stream:
            if info := itags.ITAGS.get(self.video_stream):
                self.video_resolution = info[0]
        if self.audio_stream:
            if info := itags.ITAGS.get(self.audio_stream):
                self.audio_bitrate = info[0]

        self.download_thumbnail()

        # TODO get the description once the stream has started

        metadata_file = self.output_dir / 'metadata.json'
        if path.exists(metadata_file):
            # FIXME this avoids writing this file more than once for now.
            # No further updates.
            return
        with open(metadata_file, 'w', encoding='utf8') as fp:
            dump(obj=self.video_info, fp=fp, indent=4, ensure_ascii=False)

    @property
    def video_info(self):
        """Return current metadata for writing to disk as JSON."""
        info = {
            "id": self.video_id,
            "title": self.ptyt.title,
            "author": self.ptyt.author,
            "publish_date": str(self.ptyt.publish_date),
            "start_time": self.start_time,
            "download_date": date.fromtimestamp(time()).__str__(),
            "video_streams": [],
            "audio_streams": [],
            "description": self.ptyt.description,
            "download_time": datetime.now().strftime("%d%m%Y_%H-%M-%S"),
        }
        if self.scheduled_timestamp is not None:
            info["scheduled_time"] = datetime.utcfromtimestamp(
                self.scheduled_timestamp
            ).__str__()

        for stream in self.video_streams:
            s_info = {}
            s_info["itag"] = stream.itag
            s_info["resolution"] = stream.resolution
            info["video_streams"].append(s_info)
        for stream in self.audio_streams:
            s_info = {}
            s_info["itag"] = stream.itag
            s_info["audio_bitrate"] = stream.abr
            info["audio_streams"].append(s_info)
        return info

    # Obsolete?
    def update_status(self):
        self.logger.debug("update_status()...")
        # force update
        self.status(update=True)  # was is_live()

        self.logger.info("Stream seems to be viewed live. Good.") \
        if self._status & Status.VIEWED_LIVE \
        else self.logger.warning(
            "Stream is not being viewed live. This might not work!"
        )

        # Check if video is indeed available through its reported status.
        playabilityStatus = self.json.get('playabilityStatus', {})
        status = playabilityStatus.get('status')

        if status == 'LIVE_STREAM_OFFLINE':
            self._status |= Status.OFFLINE

            scheduled_time = self.scheduled_timestamp
            if scheduled_time is not None:
                self._status |= Status.WAITING

                # self._scheduled_timestamp = scheduled_time
                reason = playabilityStatus.get('reason', 'No reason found.')

                self.logger.info(f"Scheduled start time: {scheduled_time} \
({datetime.utcfromtimestamp(scheduled_time)} UTC). We wait...")
                # FIXME use local time zone for more accurate display of time
                # for example: https://dateutil.readthedocs.io/
                self.logger.warning(f"{reason}")

                raise exceptions.WaitingException(
                    self.video_id, reason, scheduled_time
                )

            elif (Status.LIVE | Status.VIEWED_LIVE) not in self._status:
                raise exceptions.WaitingException(
                    self.video_id,
                    playabilityStatus.get('reason', 'No reason found.')
                )

            raise exceptions.OfflineException(
                self.video_id,
                playabilityStatus.get('reason', 'No reason found.')
            )

        elif status == 'LOGIN_REQUIRED':
            raise exceptions.NoLoginException(
                self.video_id,
                playabilityStatus.get('reason', 'No reason found.')
            )

        elif status == 'UNPLAYABLE':
            raise exceptions.UnplayableException(
                self.video_id,
                playabilityStatus.get('reason', 'No reason found.')
            )

        elif status != 'OK':
            subreason = playabilityStatus.get('errorScreen', {})\
                                         .get('playerErrorMessageRenderer', {})\
                                         .get('subreason', {})\
                                         .get('simpleText', \
                                              'No subreason found in JSON.')
            self.logger.warning(
                f"Livestream {self.video_id} playability status is: {status}"
                f"{playabilityStatus.get('reason', 'No reason found')}. "
                f"Sub-reason: {subreason}"
            )
            self._status &= ~Status.AVAILABLE
            # return
        else: # status == 'OK'
            self._status |= Status.AVAILABLE
            self._status &= ~Status.OFFLINE
            self._status &= ~Status.WAITING

        if not self.skip_download:
            self.logger.info(f"Stream status {self._status}")


    # TODO get itag by quality first, and then update the itag download url
    # if needed by selecting by itag (the itag we have chosen by best quality)
    # TODO If a progressive track has a better audio quality track:
    # use progressive stream's audio track only?
    # This probably won't work with the DASH video stream, so we'll have
    # to download the progressive (video+audio) track only.
    def update_base_urls(self):
        video_stream = self.video_streams[0]
        audio_stream = self.audio_streams[0]
        self.logger.info(f"Selected Video {video_stream}")
        self.logger.info(f"Selected Audio {audio_stream}")

        if (
            (self.video_stream is not None and self.video_stream != video_stream)
            or
            (self.audio_stream is not None and self.audio_stream != audio_stream)
        ):
            # Probably should fail if we suddenly get a different format than the
            # one we had before to avoid problems during merging.
            self.logger.critical(
                "Got a different format after refresh of download URL!\n"
                f"Previous video: {self.video_stream}. New: {video_stream}.\n"
                f"Previous audio: {self.audio_stream}. New: {audio_stream}"
            )
            raise Exception("Format mismatch after update of download URL.")

        self.video_stream = video_stream
        self.audio_stream = audio_stream

        self.logger.debug(
            f"Initial video base url: {getattr(self.video_stream, 'url', None)}"
        )
        self.logger.debug(
            f"Initial audio base url: {getattr(self.audio_stream, 'url', None)}"
        )

    # OBSOLETE
    def update_download_urls(self, force = False):
        previous_video_base_url = self.video_base_url
        previous_audio_base_url = self.audio_base_url
        if force:
            self._watch_html = None
            self._json = None
            self._player_config_args = None
            self._player_response = None
            self._js = None
            self._fmt_streams = None
            self.logger.info("Forcing update of download URLs.")

        video_quality, audio_quality = self.get_best_streams(
            maxq=self.max_video_quality, log=not force)

        if not force:
            # Most likely first time
            self.logger.debug(
                f"Selected video itag: {video_quality} / "
                f"Selected audio itag: {audio_quality}")
        elif not video_quality and not audio_quality:
            if previous_audio_base_url is None and previous_video_base_url is None:
                raise Exception(f"No stream URL found for {self.video_id}")
            self.logger.critical(f"No stream URL found for {self.video_id}")

        if ((self.video_itag is not None
        and self.video_itag.itag != video_quality.itag)
        or
        (self.audio_itag is not None
        and self.audio_itag.itag != audio_quality.itag)):
            # Probably should fail if we suddenly get a different format than the
            # one we had before to avoid problems during merging.
            self.logger.critical(
                "Got a different format after refresh of download URL!\n"
                f"Previous video: {self.video_stream}. New: {video_stream}.\n"
                f"Previous audio: {self.audio_stream}. New: {audio_stream}"
            )
            raise Exception("Format mismatch after update of download URL.")

        self.video_stream = video_stream
        self.audio_stream = audio_stream

        if not force:
            self.logger.debug(f"Video base url: {self.video_base_url}")
            self.logger.debug(f"Audio base url: {self.audio_base_url}")
        else:
            if previous_video_base_url != self.video_base_url:
                self.logger.debug(
                    f"Audio base URL got changed from {previous_video_base_url}"
                    f" to {self.video_base_url}.")
            if previous_audio_base_url != self.audio_base_url:
                self.logger.debug(
                    f"Audio base URL got changed from {previous_audio_base_url}"
                    f" to {self.audio_base_url}.")

    def get_currently_broadcast_segment(self):
        # TODO parse mpd manifest to get the currently broadcast segment
        return 10

    def download(self, wait_delay: float = 2.0):
        """High level entry point to download selected streams separately.
        Can be called from synchronous code.
        """
        self.filter_streams(**self.filter_args)
        self.logger.info(f"Selected streams: {self.selected_streams}")

        # In case we forgot to fetch them first, get the default
        if not self.selected_streams:
            self.logger.warning(
                "Missing selected streams, getting best available by default.")
            self.filter_streams()
            if not self.selected_streams:
                raise Exception("No stream found")

        current_seg = self.get_currently_broadcast_segment()

        for stream in self.selected_streams:
            collect_missing_segments(self.output_dir, stream)
            stream.current_seg = current_seg

        # DEBUG if not using a loop already:
        # asyncio.run(self.async_download())

        loop = asyncio.get_event_loop()
        dltask = loop.create_task(self.async_download())
        try:
            loop.run_until_complete(dltask)
        except KeyboardInterrupt:
            loop.stop()
        finally:
            # Clean up async generators
            loop.run_until_complete(loop.shutdown_asyncgens())

    async def async_download(self):
        """Run the download threads."""
        loop = asyncio.get_running_loop()
        # Used to signal offline state detection to both stream downloads
        offline_event = asyncio.Event()

        for stream in self.selected_streams:
            stream.offline_event = offline_event
            sub_dir: Path = self.output_dir / ("f" + str(stream.itag))
            sub_dir.mkdir(exist_ok=True)
            stream.sub_dir = sub_dir

        tasks = [
            loop.create_task(stream.async_download(loop))
            for stream in self.selected_streams
        ]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            print(e)
            if tasks:
                print(f"Cancelling remaining tasks {tasks}")
                for task in tasks:
                    task.cancel()
            else:
                print("Generic Exception in task.")
            raise

        # for task in tasks:
        #     task.cancel()
        print(f"Results: {results}")

        # loop.run_forever()
        # try:
        #     result = await aw
        # except KeyboardInterrupt:
        #     loop.stop()
        # finally:
        #     loop.close()

        # loop.run_until_complete(ftasks)
        # loop.close()
        # async def gather_with_concurrency(max_conc, *tasks):
        #     semaphore = asyncio.Semaphore(max_conc)

        #     async def sem_task(task):
        #         async with semaphore:
        #             return await task
        #     return await asyncio.gather(*(sem_task(task) for task in tasks))

        # tasks = []
        # for stream in self.selected_streams:
        #     for segment in stream.missing_segs:
        #         tasks.append(
        #             asyncio.create_task(
        #                 stream.download_segment(
        #                     self=stream, logger=self.logger, segment=segment))
        #         )

        # q = Queue()
        # for stream in self.selected_streams:
        #     # stream.download_segments()

        #     task = asyncio.create_task(
        #         stream.download_segments(self=stream, logger=self.logger))
        #     q.put(task)


        # return await gather_with_concurrency(3, *tasks)
        # loop = asyncio.get_event_loop()

        # stream_ended_event = asyncio.Event()

        # fnums = ["①", "②", "③", "④", "⑤", "⑥"]
        # num_idx = 0
        # tasks = []
        # missingqs = []

        # for stream in self.selected_streams:

        #     missingq = asyncio.Queue()
        #     for miss in stream.missing_segs:
        #         missingq.put_nowait(miss)
        #         # missingqs.append(missingq)
        #     print(f"Queue for stream {stream.itag} {fnums[num_idx]} : {missingq}")


        #     # Missing segments, will keep adding as needed
        #     task = loop.create_task(
        #         worker_missing(
        #             url=stream.url,
        #             name=fnums[num_idx],
        #             queue=missingq,
        #             loop=loop
        #         )
        #     )
        #     # tasks.append(task)
        #     missingqs.append(task)
        #     num_idx += 1

        #     # Catching up from the last we've got
        #     if current_seg >= 20:
        #         task = loop.create_task(
        #             worker(
        #                 url=stream.url,
        #                 start=stream.start_seg,
        #                 upto=current_seg,
        #                 name=fnums[num_idx],
        #                 queue=missingq,
        #                 loop=loop
        #             )
        #         )
        #         tasks.append(task)
        #         num_idx += 1

        #     task = loop.create_task(
        #         worker(
        #             url=stream.url,
        #             start=current_seg,
        #             upto=-1,
        #             name=fnums[num_idx],
        #             queue=missingq,
        #             loop=loop
        #         )
        #     )
        #     tasks.append(task)
        #     num_idx += 1

        # Missing segments always done first, because usually closer to beginning
        # Then from

        # task1 = loop.create_task(long_task(url="http://127.0.0.1:9999/133/", name="①", loop=loop))
        # task2 = loop.create_task(long_task(url="http://127.0.0.1:9999/140/", name="②", loop=loop))
        # task3 = loop.create_task(long_task(url="http://127.0.0.1:9999/133/", name="③", loop=loop))
        # task4 = loop.create_task(long_task(url="http://127.0.0.1:9999/140/", name="④", loop=loop))
        # task5 = loop.create_task(long_task(url="http://127.0.0.1:9999/133/", name="⑤", loop=loop))
        # task6 = loop.create_task(long_task(url="http://127.0.0.1:9999/140/", name="⑥", loop=loop))
        # task1 = asyncio.create_task(worker(loop, url="http://127.0.0.1:9999/vid/"))
        # task2 = asyncio.create_task(worker(loop, url="http://127.0.0.1:9999/aud/"))
        # loop.run_until_complete(task5)
        # loop.run_until_complete(task6)
        # tasks = asyncio.gather(task1, task2, task3, task4, task5, task6, loop=loop)

        # for q in missingqs:
        #     _ = q.join()

        # ftasks = asyncio.gather(*tasks, loop=loop, return_exceptions=True)
        # fqs = asyncio.gather(*missingqs, loop=loop, return_exceptions=True)
        # for task in tasks:
            # task.cancel()

        # loop.run_forever()
        # loop.run_until_complete(ftasks)


        # TODO If finished or fatal exception (not live?), end everything? or
        # try to still finish (missing segs) download anyway while we know it's over


        # loop.run_until_complete(tasks)

    def has_started(self) -> bool:
        self.status()
        if self._status & Status.OFFLINE:
            self._has_started = True
        return self._has_started

    def has_ended(self) -> bool:
        return self._has_started and self._has_ended

    # Obsolete
    def download_sync(self, wait_delay: float = 2.0):
        self.seg = get_latest_segment((self.video_outpath, self.audio_outpath))

        if self.seg > 0:
            self.logger.warning(
                "An output directory already existed. We assume a previously "
                "failed download attempt."
            )
        self.logger.info(f'Will start downloading from segment number {self.seg}.')

        # Disable download if regex submitted by user and they match
        self.logger.debug(
            f"Checking metadata items {(self.title, self.description)} against"
            f" {self.allow_regex} and {self.block_regex}\n")
        if not is_wanted_based_on_metadata(
            (self.title, self.description),
            self.allow_regex, self.block_regex
        ):
            self.skip_download = True
            self.logger.warning(
                f"Will skip download of {self.video_id} {self.title} "
                "because a regex filter matched.")

        if self.skip_download:
            # Longer delay in minutes between updates since we don't download
            # we don't care about accuracy that much. Random value.
            wait_delay *= 14.7
        else:
            # If one of the directories exists, assume we are resuming a previously
            # failed download attempt.
            dir_existed = False
            for path in (self.video_outpath, self.audio_outpath):
                try:
                    makedirs(path, 0o766)
                except FileExistsError:
                    dir_existed = True

            if dir_existed:
                self.seg = self.get_first_segment((self.video_outpath, self.audio_outpath))
            else:
                self.seg = 0
            self.logger.info(f'Will start downloading from segment number {self.seg}.')

        attempt = 0
        need_status_update = True
        self.trigger_hooks("on_download_initiated")

        self.seg_attempt = 0
        while not self.done and not self.error:
            try:
                # self.update_status()
                self.status(need_status_update)
                need_status_update = False
                self.logger.debug(f"Status is {self._status}.")

                if not self._status == Status.OK:
                    self.logger.critical(
                        f"Could not download \"{self.url}\": "
                        "stream unavailable or not a livestream.")
                    return

            except exceptions.WaitingException as e:
                need_status_update = True
                self.logger.warning(
                    f"Status is {self._status}. "
                    f"Waiting for {wait_delay} minutes...")
                sleep(wait_delay * 60)
                continue
            except exceptions.OfflineException as e:
                self.logger.critical(f"{e}")
                raise e
            except Exception as e:
                self.logger.critical(f"{e}")
                raise e

            if not self.skip_download:
                self.update_download_urls()
                self.update_metadata()

            self.trigger_hooks('on_download_started')

            if self.skip_download:
                # We rely on the exception above to signal when the stream has ended
                self.logger.debug(
                    f"Not downloading because \"skip-download\" option is active."
                    f" Waiting for {wait_delay} minutes..."
                )
                sleep(wait_delay * 60)
                continue

            while True:
                attempt = 0
                try:
                    self.download_segments()
                except (exceptions.EmptySegmentException,
                        exceptions.ForbiddenSegmentException,
                        IncompleteRead,
                        ValueError,
                        ConnectionError  # ConnectionResetError - Connection reset by peer
                    ) as e:
                    self.logger.info(e)
                    # force update
                    self._watch_html = None
                    self._json = {}
                    self._player_config_args = None
                    self._js = None
                    self.json
                    self.live_status(force_update=True)
                    if Status.LIVE | Status.VIEWED_LIVE in self.status:

                        if self.seg_attempt >= 15:
                            self.logger.critical(
                                f"Too many attempts on segment {self.seg}. "
                                "Skipping it.")
                            self.seg += 1
                            self.seg_attempt = 0
                            continue

                        self.logger.warning(
                            "It seems the stream has not really ended. "
                            f"Retrying in 5 secs... (attempt {self.seg_attempt}/15)")
                        self.seg_attempt += 1
                        sleep(5)
                        try:
                            # no force because cache is already updated here
                            self.update_download_urls(force=False)
                        except Exception as e:
                            self.error = f"{e}"
                            break
                        continue
                    self.logger.warning(f"The stream is not live anymore. Done.")
                    self.done = True
                    break
                except Exception as e:
                    self.logger.exception(f"Unhandled exception. Aborting.")
                    self.error = f"{e}"
                    break
        if self.done:
            self.logger.info(f"Finished downloading {self.video_id}.")
            self.trigger_hooks("on_download_ended")
        if self.error:
            self.logger.critical(
                f"Some kind of error occured during download? {self.error}"
            )

    def download_seg(self, baseurl, seg, type):
        segment_url: str = baseurl.add_seg(seg)

        # To have zero-padded filenames (not compatible with
        # merge.py from https://github.com/mrwnwttk/youtube_stream_capture
        # as it doesn't expect any zero padding )
        if type == "video":
            segment_filename = f'{self.video_outpath}{sep}{self.seg:0{10}}_video.ts'
        else:
            segment_filename = f'{self.audio_outpath}{sep}{self.seg:0{10}}_audio.ts'

        with closing(urlopen(segment_url)) as in_stream:
            headers = in_stream.headers
            status = in_stream.status
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(f"Seg {self.seg} {type} URL: {segment_url}")
                self.logger.debug(f"Seg status: {status}")
                self.logger.debug(f"Seg headers:\n{headers}")

            if not self.write_to_file(in_stream, segment_filename):
                if status == 204 and headers.get('X-Segment-Lmt', "0") == "0":
                    raise exceptions.EmptySegmentException(\
                        f"Segment {self.seg} (video) is empty, stream might have ended...")
                return False
        return True

    # Obsolete
    def download_segments(self):
        if not self.video_stream or not self.video_stream.url:
            raise Exception(
                f"Missing video stream: {self.video_stream}, "
                f"url {getattr(self.video_stream, 'url', 'Missing URL')}"
            )
        if not self.audio_stream or not self.audio_stream.url:
            raise Exception(
                f"Missing audio stream: {self.audio_stream}, "
                f"url {getattr(self.audio_stream, 'url', 'Missing URL')}"
            )

        last_check_time = datetime.now()
        wait_sec = 3
        max_attempt = 10
        attempt = 0
        while True:
            try:
                self.print_progress(self.seg)

                # Update base URLs after 5 minutes, but only check time every 10 segs
                if self.seg % 10 == 0:
                    now = datetime.now()
                    if (now - last_check_time).total_seconds() > 5 * 60:
                        last_check_time = now
                        self.update_download_urls(force=True)

                if not self.download_seg(self.video_base_url, self.seg, "video") \
                or not self.download_seg(self.audio_base_url, self.seg, "audio"):
                    attempt += 1
                    if attempt <= max_attempt:
                        self.logger.warning(
                            f"Waiting for {wait_sec} seconds before retrying... "
                            f"(attempt {attempt}/{max_attempt})")
                        sleep(wait_sec)
                        # FIXME perhaps update the base urls here to avoid
                        # hitting the same (unresponsive?) CDN server again?
                        continue
                    else:
                        self.logger.warning(
                            f"Skipping segment {self.seg} due to too many attempts.")
                attempt = 0
                self.seg_attempt = 0
                self.seg += 1

            except urllib.error.URLError as e:
                self.logger.critical(f'{type(e)}: {e}')
                if e.reason == 'Forbidden':
                    # Usually this means the stream has ended and parts
                    # are now unavailable.
                    raise exceptions.ForbiddenSegmentException(e.reason)
                if attempt > max_attempt:
                    raise e
                attempt += 1
                self.logger.warning(
                    f"Waiting for {wait_sec} seconds before retrying... "
                    f"(attempt {attempt}/{max_attempt})")
                sleep(wait_sec)
                continue
            except (IncompleteRead, ValueError) as e:
                # This is most likely signaling the end of the stream
                self.logger.exception(e)
                raise e
            except IOError as e:
                self.logger.exception(e)
                raise e

    def print_progress(self, seg: int) -> None:
        # TODO display rotating wheel in interactive mode
        fullmsg = f"Downloading segment {seg}..."
        if ISWINDOWS:
            prev_len = getattr(self, '_report_progress_prev_line_length', 0)
            if prev_len > len(fullmsg):
                fullmsg += ' ' * (prev_len - len(fullmsg))
            self._report_progress_prev_line_length = len(fullmsg)
            clear_line = '\r'
        else:
            clear_line = ('\r\x1b[K' if stderr.isatty() else '\r')

        print(clear_line + fullmsg, end='')
        self.logger.info(fullmsg)

    def print_available_streams(self, stream_list):
        if not self.logger.isEnabledFor(logging.INFO):
            return
        for s in stream_list:
            self.logger.info(
                "Available {}".format(s.__repr__().replace(' ', '\t'))
            )

    @property
    def player_config_args(self):
        if self._player_config_args:
            return self._player_config_args

        # FIXME this is redundant with json property
        self._player_config_args = {}
        # self._player_config_args["player_response"] = self.json["responseContext"]
        self._player_config_args["player_response"] = self.json

        if 'streamingData' not in self._player_config_args["player_response"]:
            self.logger.critical("Missing streamingData key in json!")
            # TODO add fallback strategy with get_ytplayer_config()?

        return self._player_config_args

    @property
    def fmt_streams(self):
        """Returns a list of streams if they have been initialized.

        If the streams have not been initialized, finds all relevant
        streams and initializes them.
        """
        # self.check_availability()
        if self._fmt_streams:
            return self._fmt_streams

        self._fmt_streams = []
        stream_maps = ["url_encoded_fmt_stream_map"]

        # unscramble the progressive and adaptive stream manifests.
        for fmt in stream_maps:
            # if not self.age_restricted and fmt in self.vid_info:
            #     extract.apply_descrambler(self.vid_info, fmt)
            pytube.extract.apply_descrambler(self.player_config_args, fmt)

            pytube.extract.apply_signature(self.player_config_args, fmt, self.js)

            # build instances of :class:`Stream <Stream>`
            # Initialize stream objects
            stream_manifest = self.player_config_args[fmt]
            for stream in stream_manifest:
                # Add method to increment segment:
                stream["url"] = ParamURL(stream["url"])
                video = pytube.Stream(
                    stream=stream,
                    player_config_args=self.player_config_args,
                    monostate={},  # FIXME This is a bit dangerous but we don't use it anyway
                )
                self._fmt_streams.append(video)

        return self._fmt_streams

    def get_best_streams(self, maxq=None, log=True, codec="mp4", fps="60"):
        """Return a tuple of pytube.Stream objects, first one for video
        second one for audio.
        If only progressive streams are available, the second item in tuple
        will be None.
        :param str maxq:
        :param str codec: mp4, webm
        :param str fps: 30, 60"""
        video_stream = None
        audio_stream = None

        def as_int(res_or_abr: str) -> Optional[int]:
            if res_or_abr is None:
                return None
            as_int = None
            # looks for "1080p" or "48kbps", either a resolution or abr
            if match := re.search(r"(\d{3,4})(p)?|(\d{2,4})(kpbs)?", res_or_abr):
                as_int = int(match.group(1))
            return as_int

        if maxq is not None and not isinstance(maxq, int):
            maxq = as_int(maxq)
            # if match := re.search(r"(\d{3,4})(p)?|(\d{2,4}(kpbs)?", maxq):
            #     maxq = int(match.group(1))
            if maxq is None:
                self.logger.warning(
                    f"Max quality setting \"{maxq}\" is incorrect. "
                    "Defaulting to best video quality available."
                )

        custom_filters = None
        if maxq is not None and isinstance(maxq, int):
            def filter_maxq(s):
                res_int = as_int(s.resolution)
                if res_int is None:
                    return False
                return res_int <= maxq
            custom_filters = [filter_maxq]

        avail_streams = self.streams
        if log:
            self.print_available_streams(avail_streams)
        video_streams = avail_streams.filter(file_extension=codec,
            custom_filter_functions=custom_filters
            ) \
            .order_by('resolution') \
            .desc()

        video_stream = video_streams.first()
        if log:
            self.logger.info(f"Selected video {video_stream}")

        audio_streams = avail_streams.filter(
            only_audio=True
            ) \
            .order_by('abr') \
            .desc()
        audio_stream = audio_streams.first()
        if log:
            self.logger.info(f"selected audio {audio_stream}")

        # FIXME need a fallback in case we didn't have an audio stream
        # TODO need a strategy if progressive has better audio quality:
        # use progressive stream's audio track only? Would that work with the
        # DASH stream video?
        if len(audio_streams) == 0:
            self.streams.filter(
                progressive=False,
                file_extension=codec
                ) \
                .order_by('abr') \
                .desc() \
                .first()

        return (video_stream, audio_stream)


    # # TODO close but UNFINISHED, superceded by pytube. OBSOLETE
    # def get_best_quality(self, datatype, maxq=None, codec="mp4", fps="60"):
    #     # Select the best possible quality, with maxq (str) as the highest possible
    #     label = 'qualityLabel' if datatype == 'video' else 'audioQuality'
    #     streamingData = self.json.get('streamingData', {})
    #     adaptiveFormats = streamingData.get('adaptiveFormats', {})

    #     if not streamingData or not adaptiveFormats:
    #         raise Exception(f"Could not get {datatype} quality format. \
    # Missing streamingData or adaptiveFormats.")

    #     available_stream_by_itags = []
    #     for stream in adaptiveFormats:
    #         if stream.get(label, None) is not None:
    #             available_stream_by_itags.append(stream)
    #             self.print_found_quality(stream, datatype)

    #     if maxq is not None and isinstance(maxq, str):
    #         if match := re.search(r"(\d{3,4})p?", maxq):
    #             maxq = int(match.group(1))
    #         else:
    #             self.logger.warning(
    #                 f"Max quality setting \"{maxq}\" is incorrect."
    #                 " Defaulting to best video quality available."
    #             )
    #             maxq = None

    #     ranked_profiles = []
    #     for stream in available_stream_by_itags:
    #         i_itag = int(stream.get("itag"))
    #         itag_profile = pytube.itags.get_format_profile(i_itag)
    #         itag_profile["itag"] = i_itag

    #         # Filter None values, we don't know what bitrate they represent.
    #         if datatype == "audio" and itag_profile.get("abr"):
    #             ranked_profiles.append(itag_profile)
    #             # strip kpbs for sorting. Not really necessary anymore since
    #             # None values are filtered already.
    #             # audio_streams[-1]["abr"] = abr.split("kpbs")[0]
    #         elif datatype == "video" and (res := itag_profile.get("resolution")):
    #             if maxq:
    #                 res_int = int(res.split("p")[0])
    #                 if res_int > maxq:
    #                     continue
    #             ranked_profiles.append(itag_profile)

    #     if datatype == "audio":
    #         ranked_profiles.sort(key=lambda s: s.get("abr"))
    #     else:
    #         ranked_profiles.sort(key=lambda s: s.get("resolution"))

    #     # Add back information from the json for further ranking
    #     # because pytube doesn't keep track of those
    #     if datatype == "video":
    #         for avail in adaptiveFormats:
    #             itag = avail.get("itag")
    #             for profile in ranked_profiles:
    #                 if profile.get("itag") == itag:
    #                     # fps: 60/30
    #                     profile["fps"] = avail.get("fps", "")
    #                     # mimeType: video/mp4; codecs="avc1.42c00b"
    #                     # mimeType: video/webm; codecs="vp9"
    #                     profile["mimeType"] = avail.get("mimeType", "").split(";")[0]
    #                     continue
    #         ranked_profiles.sort(key=lambda s: s.get("fps"))

    #     # select mp4 or webm depending on "mimeType" container type
    #     ranked_profiles.sort(key=lambda s: s.get("mimeType"))

    #     filters = []


    #     best_itag = ranked_streams[0].get('itag')

    #     chosen_itag = None
    #     chosen_quality_labels = ""
    #     for i in ranked_streams:
    #         if i in available_itags:
    #             chosen_itag = i
    #             for s in adaptiveFormats:
    #                 if chosen_itag == s.get('itag'):
    #                     if datatype == "video":
    #                         chosen_quality_labels = f"{d.get('qualityLabel')} \
    # type: {d.get('mimeType')} bitrate: {d.get('bitrate')} codec: {d.get('codecs')}"
    #                     else:
    #                         chosen_quality_labels = f"{d.get('audioQuality')} \
    # type: {d.get('mimeType')} bitrate: {d.get('bitrate')} codec: {d.get('codecs')}"
    #             break

    #     self.logger.warning(f"Chosen {datatype} quality: \
    # itag {chosen_itag}; height: {chosen_quality_labels}")

    #     if chosen_itag is None:
    #         raise Exception(f"Failed to get chosen quality from adaptiveFormats.")
    #     return chosen_itag


    def write_to_file(self, fsrc, fdst, length=0):
        """Copy data from file-like object fsrc to file-like object fdst.
        If no bytes are read from fsrc, do not create fdst and return False.
        Return True when file has been created and data has been written."""
        # Localize variable access to minimize overhead.
        if not length:
            length = COPY_BUFSIZE
        fsrc_read = fsrc.read

        try:
            buf = fsrc_read(length)
        except Exception as e:
            # FIXME handle these errors better, for now we just ignore and move on:
            # ValueError: invalid literal for int() with base 16: b''
            # http.client.IncompleteRead: IncompleteRead
            self.logger.exception(e)
            buf = None

        if not buf:
            return False
        with open(fdst, 'wb') as out_file:
            fdst_write = out_file.write
            while buf:
                fdst_write(buf)
                buf = fsrc_read(length)
        return True

    def get_metadata_dict(self) -> Dict:
        # TODO add more data, refresh those that got stale
        thumbnails = self.player_response.get("videoDetails", {})\
            .get("thumbnail", {})

        return {
                "url": self.url,
                "videoId": self.video_id,
                "cookie_path": self.session.cookie_path,
                "logger": self.logger,
                "output_dir": self.output_dir,
                "title": self.title,
                "description": self.ptyt.description,
                "author": self.author,
                "isLive": Status.LIVE | Status.VIEWED_LIVE in self.status,
                # We'll expect to get an array of thumbnails here
                "thumbnail": thumbnails
            }

    def trigger_hooks(self, event: str):
        hook_cmd = self.hooks.get(event, None)
        webhookfactory = self.notifier.get_webhook(event)

        if hook_cmd is not None or webhookfactory is not None:
            # TODO if an event needs to refresh some data, update metadata here
            args = self.get_metadata_dict()

            if hook_cmd:
                hook_cmd.spawn_subprocess(args)

            if webhookfactory:
                if webhook := webhookfactory.get(args):
                    self.notifier.q.put(webhook)


class MPD():
    """Cache the URL to the manifest, but enable fetching it data if needed."""
    def __init__(self, parent: YoutubeLiveStream, mpd_type: str = "dash") -> None:
        self.parent = parent
        self.url = None
        self.content = None
        # self.expires: Optional[float] = None
        self.mpd_type = mpd_type  # dash or hls

    def update_url(self) -> Optional[str]:
        mpd_type = "dashManifestUrl" if self.mpd_type == "dash" else "hlsManifestUrl"

        json = self.parent.json

        if streamingData := json.get("streamingData", {}):
            if ManifestUrl := streamingData.get(mpd_type):
                self.url = ManifestUrl
            else:
                raise Exception(
                    f"No URL found for MPD manifest of {self.parent.video_id}.")
        else:
            raise Exception("No streamingData in json. Cannot load MPD.")

    def get_content(self, update=False):
        if self.content is not None and not update:
            return self.content

        if not self.url:
            self.update_url()

        try:
            self.content = self.parent.session.make_request(self.url)
        except:
            self.content = None
        return self.content


async def worker_retries(
    stream: Stream,
    name: str,
    queue,
    loop: asyncio.AbstractEventLoop,
    events: dict[str, asyncio.Event]
) -> None:
    # The retry task should never throw and exception to trigger a status update.
    # It should as as a simple consumer thread for the queue.
    offline_event = events["offline"]
    forbidden_event = events["forbidden"]
    while not (offline_event.is_set() and forbidden_event.is_set()):
        seg = await queue.get()
        print(f"Got from queue {name} : {seg}. {queue}")
        try:
            res = await loop.run_in_executor(
                None, seg.download_sync, name, 3.0, 3
            ) # res = await seg.download_async(name)
        except exceptions.ForbiddenSegmentException:
            # TODO determine who can set the forbidden event?
            print("Forbidden in retry, setting forbidden event!")
            forbidden_event.set()
            raise
        except (IncompleteRead, ValueError) as e:
            # We treat this as a signal that the stream may have ended
            stream.logger.exception(e)
            forbidden_event.set()
        except IOError as e:
            stream.logger.exception(e)
            raise
        except Exception as e:
            # TODO count number of errors and attach that to Segment
            # TODO use PriorityQueue to push back segments with higher number
            # of errors to the back of the queue
            print(f"Exception in retry: {seg}: {e}")
            print("Giving up after 1 try (DEBUG)!")
            forbidden_event.set()
            # TODO raise forbidden error after 5 tries?

        queue.task_done()
    print("RETRY loop is done!")


# pytube.Stream
async def async_download(
    self: Stream,
    loop: asyncio.AbstractEventLoop,
    ):
    basecolor = BLUE if self.type == "video" else YELLOW
    fnums = [
        f"{basecolor}{self.itag}{ENDC} {BROWN}①{ENDC}",
        f"{basecolor}{self.itag}{ENDC} {CYAN}②{ENDC}",
        f"{basecolor}{self.itag}{ENDC} {PURPLE}③{ENDC}",
    ]

    # Keep track of missing segments
    missingq = asyncio.Queue()
    self.missingq = missingq
    for miss in self.missing_segs:
        missingq.put_nowait(Segment(miss, self))
    # Clear missing_segs as it is now an unused list of ints
    del self.missing_segs

    # Signals when the server refuses all further requests. We need one per stream.
    self.forbidden_event = asyncio.Event()
    events = {
        "forbidden": self.forbidden_event,
        "offline": self.offline_event
    }

    num_idx = 0
    tasks = []
    self.parent.logger.debug(f"Queue for stream {fnums[num_idx]} : {missingq}")

    # Missing segments, and will keep adding more as needed (due to errors)
    retry_name = fnums[num_idx]
    retry_task = loop.create_task(
        worker_retries(
            stream=self,
            name=retry_name,
            queue=missingq,
            loop=loop,
            events=events
        ),
        name=retry_name
    )
    num_idx += 1

    # Catching up from the most recent segment we've got on disk
    dl_h_catchup = None
    if self.start_seg < self.current_seg and self.current_seg >= 10:
        dl_h_catchup = DownloadHandler(
            stream=self,
            start_seg=self.start_seg,
            upto_seg=self.current_seg,
            name=fnums[num_idx],
            queue=missingq,
            loop=loop,
            events=events
        )
        tasks.append(loop.create_task(dl_h_catchup.worker(), name=dl_h_catchup.name))
    dl_h_catchup_name = fnums[num_idx]
    num_idx += 1

    # Start from the currently broadcast segment
    dl_h = DownloadHandler(
        stream=self,
        start_seg=self.current_seg,
        upto_seg=-1,
        name=fnums[num_idx],
        queue=missingq,
        loop=loop,
        events=events
    )
    tasks.append(loop.create_task(dl_h.worker(), name=dl_h.name))
    num_idx += 1

    # try:
    #     fl = await asyncio.gather(
    #         retry_task, *tasks, return_exceptions=False)
    # except Exception as e:
    #     print(f"raised {e}")

    while True:
        await asyncio.sleep(1)
        print("slept 1")
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_EXCEPTION)
            print(f"Tasks returned! {BLUE}Done: {done}{ENDC} {BROWN}Pending: {pending}{ENDC}")
            got_exception = False
            tasks = []
            for task in done:
                if task.exception():
                    got_exception = True
                    print(f"{RED}Exception in done task:{ENDC} {task.exception()}")
                    # DEBUG
                    if "END" in str(task.exception()):
                        print(f"Exception signals end of stream - {PURPLE}Updating status...{ENDC}")
                        status = self.parent.status(update=True)
                        print(f"{YELLOW}status is {status}{ENDC}")
                        if status & Status.OFFLINE:
                            print("FAKE Status is offline. Setting offline event.")
                            self.offline_event.set()
                            # We should keep trying to get queued segments just in case we can still get them
                            # await missingq.join()
                    else:
                        break
                    # Recreate the tasks because exceptions are not fatal
                    if task.get_name() == dl_h.name:
                        tasks.append(loop.create_task(dl_h.worker(), name=dl_h.name))
                    elif task.get_name() == dl_h_catchup_name and dl_h_catchup is not None:
                        tasks.append(loop.create_task(dl_h_catchup.worker(), name=dl_h_catchup_name))

            if not got_exception:
                print(f"{RED}No exception anymore, {self.itag} is done!{ENDC}")
                break

            for task in pending:
                print(f"Adding back pending task {task}")
                tasks.append(task)

            if len(tasks) == 0:
                print("No more tasks active. Breaking")
                break

            # if dl_h_catchup is not None:
            #     tasks.append(loop.create_task(dl_h_catchup.worker(), name=dl_h_catchup.name))
            # tasks.append(loop.create_task(dl_h.worker(), name=dl_h.name))
            print(f"{GREEN}Resuming tasks {tasks}{ENDC}")

            # exceptions = []
            # for d in done:
            #     if d.exception():
            #         exceptions.append(d.exception())
            # for p in pending:
            #     if p.exception():
            #         exceptions.append(p.exception())

            # if not exceptions:
            #     print("No exception found. Stopping loop.")
            #     break
            # print(f"Got exception: {exceptions}. Continuing...")
            # future_list.cancel()
            # await asyncio.sleep(1)
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_EXCEPTION)
        except Exception as e:
            print(f"{RED}Exception catch! {str(e)}{ENDC}")
            if "END" in str(e):
                print(f"Exception signals end of stream - {PURPLE}Update status?{ENDC}")
                self.offline_event.set()
                await missingq.join()


    # print(f"Joining retry task's missing queue {missingq} for {self.itag}")
    if retry_task.done():
        if retry_task.exception():
            print(f"Exception in {retry_task}")
            retry_task.cancel()
    else:
        await missingq.join()
        print(f"Joined retry task queue {missingq} for {self.itag}")
        retry_task.cancel()
        print(f"Canceled retry task done {retry_task} for {self.itag}")

    for pending_task in pending:
        pending_task.cancel()

    await asyncio.gather(retry_task, *tasks, return_exceptions=True)

    # try:
    # loop.run_forever()
    # except KeyboardInterrupt:
    #     loop.stop()
    #     loop.close()
    # concurrent.futures.as_completed(tasks)
    return f"{self.itag} is done downloading"


class Segment():
    def __init__(self, num: int, stream: Stream) -> None:
        self.num: int = num
        # Reference stream for any future URL updates
        self.stream = stream
        self.retries: int = 0
        self.error = None

    @property
    def base_url(self) -> str:
        return self.stream.url

    # @property
    def geturl(self) -> str:
        return "{}&sq={}".format(self.base_url, self.num)

    def __repr__(self) -> str:
        return str(self.num)

    def __add__(self, o):
        return Segment(
            num=self.num + o,
            stream=self.stream
        )

    # def __iadd__(self, o):
    #     if isinstance(o, Segment):
    #        self.num += o.num
    #     else:  # assuming it's an int
    #         self.num += o
    #     return self

    @property
    def filename(self):
        return self.stream.sub_dir / f'{self.num:0{10}}.ts'

    def download_sync(self, name, wait_sec: float = 3.0, max_attempt: int = 3):
        print(f"{datetime.now()} {self.geturl()} ↘ {name}")

        attempt = 0
        while True:
            try:
                with closing(urllib.request.urlopen(self.geturl())) as in_stream:
                    if not self.write_to_file_sync(in_stream):
                        if in_stream.status == 204 \
                        and in_stream.headers.get('X-Segment-Lmt', "0") == "0":
                            raise exceptions.EmptySegmentException(
                                f"Segment {self} is empty (itag "
                                f"{self.stream.itag}, {self.stream.subtype}) "
                                "stream might have ended...")
                        break
                print(f"{datetime.now()} {self.geturl()} ☻ {name}")
                return
            except urllib.error.URLError as e:
                self.stream.logger.critical(f'{type(e)}: {e} for seg {self.num} {self.stream.itag}')
                # FIXME use a regex here or make all lower case?
                if 'Forbidden'.lower() in str(e.reason).lower():
                    # Usually this means the stream has ended and parts
                    # are now unavailable. The status should be error 403.
                    raise exceptions.ForbiddenSegmentException(e.reason)
                if attempt >= max_attempt:
                    raise
                attempt += 1
                self.stream.logger.critical(
                    f"Waiting for {wait_sec} seconds before retrying... "
                    f"(attempt {attempt}/{max_attempt})")
                sleep(wait_sec)
                continue
            except (IncompleteRead, ValueError) as e:
                # We treat this as a signal that the stream may have ended
                self.stream.logger.exception(e)
                raise
            except IOError as e:
                self.stream.logger.exception(e)
                raise
        raise exceptions.FailedSegmentDownload(
            f"Failed to write segment {self} "
            f"(itag {self.stream.itag}, {self.stream.subtype})"
        )

    async def download_async(self, name):
        # TODO finish implement this to actually download+write async
        with closing(urllib.request.urlopen(self.geturl())) as in_stream:
            if not await self.write_to_file_async(in_stream):
                return

    async def write_to_file_async(self, data):
        length = COPY_BUFSIZE
        fsrc_read = data.read
        try:
            buf = await fsrc_read(length)
        except Exception as e:
            # FIXME handle these errors better, for now we just ignore and move on:
            # ValueError: invalid literal for int() with base 16: b''
            # http.client.IncompleteRead: IncompleteRead
            self.stream.logger.exception(e)
            buf = None

        if not buf:
            return False
        # FIXME move this in global namespace
        import aiofiles
        async with aiofiles.open(self.filename, 'wb') as out_file:
            fdst_write = out_file.write
            while buf:
                await fdst_write(buf)
                buf = fsrc_read(length)
        return True

    def write_to_file_sync(self, data):
        length = COPY_BUFSIZE
        fsrc_read = data.read
        try:
            buf = fsrc_read(length)
        except Exception as e:
            # FIXME handle these errors better, for now we just ignore and move on:
            # ValueError: invalid literal for int() with base 16: b''
            # http.client.IncompleteRead: IncompleteRead
            self.stream.logger.exception(e)
            buf = None

        if not buf:
            return False
        with open(self.filename, 'wb') as out_file:
            fdst_write = out_file.write
            while buf:
                fdst_write(buf)
                buf = fsrc_read(length)
        return True

    url = property(geturl)


class DownloadHandler():
    """Keep track of downloaded segments and errors."""
    def __init__(
        self, stream: Stream,
        start_seg: int, upto_seg: int,
        name: str, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
        events: dict[str, asyncio.Event]) -> None:
        self.stream = stream
        self.start_seg = start_seg
        self.upto_seg = upto_seg
        self.name = name
        self.queue = queue
        self.loop = loop
        self.offline_event = events["offline"]
        self.forbidden_event = events["forbidden"]
        self.current_seg = 0

    async def worker(self):
        seg = Segment(self.start_seg, self.stream)
        self.stream.logger.warning(f"Worker {self.name} downloading segment {seg.num}...")
        name = self.name
        upto_seg = self.upto_seg
        stream = self.stream
        queue = self.queue
        loop = self.loop
        offline_event = self.offline_event
        forbidden_event = self.forbidden_event
        while not (forbidden_event.is_set() and offline_event.is_set()):
            try:
                fut = await loop.run_in_executor(
                    None, seg.download_sync, name, 3.0, 1
                ) # await seg.download_async(name)
            except exceptions.ForbiddenSegmentException:
                print(
                    f"{RED}DEBUG ouch forbidden {seg.num} {stream.itag}?"
                    f"Failing immediately!{ENDC}")
                forbidden_event.set()
                raise
            except Exception as e:
                # fut.set_exception(e)
                # DEBUG
                if "404" in str(e):
                    break
                stream.logger.debug(
                    f"{name} Error getting {seg}: {e}. Putting to queue."
                )
                # Place this segment number into queue to retry later
                await queue.put(seg) # queue.put_nowait(seg)

            # This should create a new object here, otherwise this change will
            # affect the item already placed in the queue.
            seg += 1
            # Update in case we need to resume from this segment later after throwing
            self.start_seg = seg.num
            if upto_seg > 0 and seg.num >= upto_seg:
                stream.logger.debug(f"{name} Reached upto_seg {upto_seg}.")
                break
        stream.logger.debug(f"{name} has finished.")
        # await queue.join()
        return


def write_to_file(logger, fsrc, fdst: Path, length: int = 0) -> bool:
    """Copy data from file-like object fsrc to file-like object fdst.
    If no bytes are read from fsrc, do not create fdst and return False.
    Return True when file has been created and data has been written."""
    # Localize variable access to minimize overhead.
    if not length:
        length = COPY_BUFSIZE
    fsrc_read = fsrc.read

    try:
        buf = fsrc_read(length)
    except Exception as e:
        # FIXME handle these errors better, for now we just ignore and move on:
        # ValueError: invalid literal for int() with base 16: b''
        # http.client.IncompleteRead: IncompleteRead
        logger.exception(e)
        buf = None

    if not buf:
        return False
    with open(fdst, 'wb') as out_file:
        fdst_write = out_file.write
        while buf:
            fdst_write(buf)
            buf = fsrc_read(length)
    return True


def remove_useless_keys(_dict: dict) -> None:
    """Update _dict in place by removing keys we probably won't use to declutter
    logs a big."""
    for keyname in ['heartbeatParams', 'playerAds', 'adPlacements',
    'playbackTracking', 'annotations', 'playerConfig', 'storyboards',
    'trackingParams', 'attestation', 'messages', 'frameworkUpdates', 'captions']:
        try:
            _dict.pop(keyname)
        except KeyError:
            continue
    # remove this annoying long list, although this could be useful to check
    # for restricted region...
    try:
        _dict.get('microformat', {})\
             .get('playerMicroformatRenderer', {})\
             .pop('availableCountries')
    except KeyError:
        pass


def setup_logger(
    output_path: Path, log_level: Union[int, str], video_id: str
) -> logging.Logger:
    if isinstance(log_level, str):
        log_level = str.upper(log_level)

    # We need to make an independent logger - with no parent (other than root)- 
    # in order to avoid using the parent logger's handlers, although we are writing
    # to the same file.
    logger = logging.getLogger("download" + "." + video_id)

    if logger.hasHandlers():
        logger.debug(
            f"Logger {logger} already had handlers!"
        )
        return logger

    logger.setLevel(logging.DEBUG)
    # File output
    logfile = logging.FileHandler(
        filename=output_path / "download.log", delay=True, encoding='utf-8'
    )
    logfile.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
    )
    logfile.setFormatter(formatter)
    logger.addHandler(logfile)

    # Console output
    conhandler = logging.StreamHandler()
    conhandler.setLevel(log_level)
    conhandler.setFormatter(formatter)

    def dumb_filter(record):
        # if "Downloading segment" in record.msg:
        # Only filter logRecords that came from our function
        if record.funcName == "print_progress":
            return False
        return True

    confilter = logging.Filter()
    confilter.filter = dumb_filter
    conhandler.addFilter(confilter)
    logger.addHandler(conhandler)
    return logger


def collect_missing_segments(output_dir: Path, stream: Stream):
    """Update stream objects properties in selected streams with output
    directory, starting segment, and mising segments if any."""
    stream_output_dir = output_dir / stream.dir_suffix
    if stream_output_dir.exists():
        stream.start_seg, stream.missing_segs = \
            get_latest_valid_segment(stream_output_dir)
    else:
        makedirs(stream_output_dir, 0o766)


def get_latest_valid_segment(path: Union[str, Path]
) -> tuple[int, list[int]]:
    """
    Return the latest segment number already downloaded, and a
    list of missing segments (with inferior numbers) if any.
    """
    # FIXME only read files that match our filename pattern
    # in case foreign files are in there too
    top_seg = 0
    missing: list[int] = []

    if isinstance(path, str):
        path = Path(path)
    if not path.exists():
        return top_seg, missing

    def as_int(fname: str) -> int:
        # This assumes file name format 00000001_audio+video.ts
        return int(fname.split('_')[0])

    num_list = [as_int(f) for f in listdir(path)]
    num_list.sort()

    if not num_list:
        return top_seg, missing

    top_seg = num_list[-1]

    if num_list and num_list[-1] != len(num_list):
        def find_missing(lst):
            return [x for x in range(0, lst[-1])
                                    if x not in lst]

        missing = find_missing(num_list)

    # Step back one file just in case the latest segment got only partially
    # downloaded (we want to overwrite it to avoid a corrupted segment)
    if top_seg > 0:
        top_seg -= 1
    return top_seg, missing


# def get_latest_segment(paths: Tuple) -> int:
#     """
#     Create each path in paths. If one already existed, return the last
#     segment already downloaded, otherwise return 1.
#     :param paths: tuple of pathlib.Path
#     """
#     # If one of the directories exists, assume we are resuming a previously
#     # failed download attempt.
#     dir_existed = False
#     for path in paths:
#         try:
#             makedirs(path, 0o766)
#         except FileExistsError:
#             dir_existed = True

#     # The sequence number to start downloading from (acually starts at 0).
#     seg = 0

#     if dir_existed:
#         # Get the latest downloaded segment number,
#         # unless one directory holds an earlier segment than the other.
#         # video_last_segment = max([int(f[:f.index('.')]) for f in listdir(paths[0])])
#         # audio_last_segment = max([int(f[:f.index('.')]) for f in listdir(paths[1])])
#         # seg = min(video_last_segment, audio_last_segment)
#         seg = min([
#                 max([int(f[:f.index('.')].split('_')[0])
#                 for f in listdir(p)], default=1)
#                 for p in paths
#             ])

#         # Step back one file just in case the latest segment got only partially
#         # downloaded (we want to overwrite it to avoid a corrupted segment)
#         if seg > 0:
#             seg -= 1
#     return seg
