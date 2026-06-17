#!/usr/bin/env python
import json
from typing import Optional, Dict, List, Any
from os import sep, path, makedirs, listdir
from sys import stderr, stdout
from platform import system
import logging
from datetime import date, datetime, timezone
from time import time, sleep
from json import dumps, dump, loads
from contextlib import closing
from enum import Flag, auto
from pathlib import Path
import re
from urllib.request import urlopen, Request
from urllib.parse import urljoin
import urllib.error
from http.client import IncompleteRead
import xml.etree.ElementTree as ET

import yt_dlp
from yt_dlp.utils import DownloadError

import pytube.cipher
import pytube
from copy import deepcopy

from livestream_saver.notifier import NotificationDispatcher
from livestream_saver.request import YoutubeUrllibSession
from livestream_saver.channel import VideoPost
from livestream_saver.util import wait_block, create_output_dir, none_filtered_out
from livestream_saver.extract import publish_date
from livestream_saver.exceptions import (
    WaitingException,
    OfflineException,
    NoLoginException,
    UnplayableException, 
    OutdatedAppException,
    TooManyRequestsException,
    NoVideoFormatsException,
    EmptySegmentException,
    ForbiddenSegmentException,
)

SYSTEM = system()
ISPOSIX = SYSTEM == 'Linux' or SYSTEM == 'Darwin'
ISWINDOWS = SYSTEM == 'Windows'
COPY_BUFSIZE = 1024 * 1024 if ISWINDOWS else 64 * 1024

# logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)

# Temporary backport from pytube 11.0.1
# and also https://github.com/pytube/pytube/issues/1281
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
        r'a\.[a-zA-Z]\s*&&\s*\([a-z]\s*=\s*a\.get\("n"\)\)\s*&&\s*'
        r'\([a-z]\s*=\s*([a-zA-Z0-9$]+)(\[\d+\])?\([a-z]\)'
    ]
    # print('Finding throttling function name')
    for pattern in function_patterns:
        regex = re.compile(pattern)
        function_match = regex.search(js)
        if function_match:
            # print("finished regex search, matched: %s", pattern)
            if len(function_match.groups()) == 1:
                return function_match.group(1)
            idx = function_match.group(2)
            if idx:
                idx = idx.strip("[]")
                array = re.search(
                    r'var {nfunc}\s*=\s*(\[.+?\]);'.format(
                        nfunc=re.escape(function_match.group(1))),
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
    self.transform_plan = pytube.cipher.get_transform_plan(js)
    var_regex = re.compile(r"^\$*\w+\W")
    var_match = var_regex.search(self.transform_plan[0])
    if not var_match:
        raise pytube.RegexMatchError(
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


def patched_get_throttling_plan(js: str):
    """Extract the "throttling plan".

    The "throttling plan" is a list of tuples used for calling functions
    in the c array. The first element of the tuple is the index of the
    function to call, and any remaining elements of the tuple are arguments
    to pass to that function.

    :param str js:
        The contents of the base.js asset file.
    :returns:
        The full function code for computing the throttlign parameter.
    """
    raw_code = pytube.cipher.get_throttling_function_code(js)

    transform_start = r"try{"
    plan_regex = re.compile(transform_start)
    match = plan_regex.search(raw_code)

    if match:
        transform_plan_raw = pytube.cipher.find_object_from_startpoint(
            raw_code, match.span()[1] - 1)
    else:
        transform_plan_raw = raw_code

    # Steps are either c[x](c[y]) or c[x](c[y],c[z])
    step_start = r"c\[(\d+)\]\(c\[(\d+)\](,c(\[(\d+)\]))?\)"
    step_regex = re.compile(step_start)
    matches = step_regex.findall(transform_plan_raw)
    transform_steps = []
    for match in matches:
        if match[4] != '':
            transform_steps.append((match[0],match[1],match[4]))
        else:
            transform_steps.append((match[0],match[1]))

    return transform_steps

# TODO might have to do the same for get_throttling_function_array and get_throttling_function_code
# https://github.com/pytube/pytube/issues/1498
pytube.cipher.get_throttling_plan = patched_get_throttling_plan


class BaseURL(str):
    """Wrapper class to handle incrementing segment number in various URL formats."""
    def __new__(cls, content):
        return str.__new__(cls,
            content[:-1] if content.endswith("/") else content)

    def add_seg(self, seg_num: int):
        raise NotImplementedError()


class ParamURL(BaseURL):
    """Plain url with parameters."""
    def add_seg(self, seg_num: int) -> str:
        return self + f"&sq={seg_num}"


class PathURL(BaseURL):
    """URI for GraphQL API style."""
    def add_seg(self, seg_num: int) -> str:
        return self + f"/sq/{seg_num}"


class YTDLPStream:
    """Small wrapper exposing yt-dlp format dictionaries with stream-like attributes."""

    def __init__(self, stream: Dict[str, Any]) -> None:
        self.stream = stream

    @property
    def itag(self) -> Optional[str]:
        return self.stream.get("format_id") or self.stream.get("itag")

    @property
    def resolution(self) -> Optional[str]:
        if height := self.stream.get("height"):
            return f"{height}p"
        return self.stream.get("resolution")

    @property
    def abr(self) -> Optional[str]:
        abr = self.stream.get("abr")
        if abr is None:
            return None
        if isinstance(abr, (int, float)):
            return f"{int(round(abr))}kbps"
        return str(abr)

    @property
    def mime_type(self) -> Optional[str]:
        ext = self.stream.get("ext")
        if ext:
            return ext
        return self.stream.get("protocol")

    @property
    def file_extension(self) -> Optional[str]:
        return self.stream.get("ext")

    @property
    def url(self) -> Optional[BaseURL]:
        url = self.stream.get("fragment_base_url") or self.stream.get("url")
        if not url:
            return None
        protocol = self.stream.get("protocol") or ""
        if "http_dash_segments" in protocol:
            return PathURL(url)
        if "/sq/" in url:
            base_url = re.sub(r"/sq/\d+/?", "/", url, count=1)
            return PathURL(base_url)
        return ParamURL(url)

    @property
    def progressive(self) -> bool:
        return self.has_video and self.has_audio

    @property
    def has_video(self) -> bool:
        return self.stream.get("vcodec") not in (None, "none")

    @property
    def has_audio(self) -> bool:
        return self.stream.get("acodec") not in (None, "none")

    @property
    def is_dash(self) -> bool:
        protocol = self.stream.get("protocol")
        return protocol in ("http_dash_segments", "dash", "dashy") or bool(
            self.stream.get("fragment_base_url")
        )

    def __repr__(self) -> str:
        return (
            f"<YTDLPStream itag={self.itag} res={self.resolution} "
            f"abr={self.abr} ext={self.file_extension} "
            f"v={self.stream.get('vcodec')} a={self.stream.get('acodec')} "
            f"proto={self.stream.get('protocol')}>"
        )


class YTDLPStreamQuery:
    """Minimal query helper for yt-dlp-backed stream selection."""

    def __init__(self, streams: List[YTDLPStream]) -> None:
        self._streams = streams

    def __iter__(self):
        return iter(self._streams)

    def __len__(self) -> int:
        return len(self._streams)

    def first(self) -> Optional[YTDLPStream]:
        return self._streams[0] if self._streams else None

    def filter(
        self,
        file_extension: Optional[str] = None,
        only_audio: bool = False,
        progressive: Optional[bool] = None,
        custom_filter_functions: Optional[List[Any]] = None
    ) -> "YTDLPStreamQuery":
        streams = self._streams
        if file_extension is not None:
            streams = [s for s in streams if s.file_extension == file_extension]
        if only_audio:
            streams = [s for s in streams if s.has_audio and not s.has_video]
        if progressive is not None:
            streams = [s for s in streams if s.progressive == progressive]
        if custom_filter_functions:
            for func in custom_filter_functions:
                streams = [s for s in streams if func(s)]
        return YTDLPStreamQuery(streams)

    def order_by(self, key: str) -> "YTDLPStreamQuery":
        def as_int(value: Any) -> int:
            if value is None:
                return -1
            if isinstance(value, (int, float)):
                return int(value)
            if match := re.search(r"(\d{2,5})", str(value)):
                return int(match.group(1))
            return -1

        streams = sorted(
            self._streams,
            key=lambda s: as_int(getattr(s, key, None))
        )
        return YTDLPStreamQuery(streams)

    def desc(self) -> "YTDLPStreamQuery":
        return YTDLPStreamQuery(list(reversed(self._streams)))


class Probe:
    """Base helper for fetching live-stream metadata from one backend."""
    def __init__(self, stream: "YoutubeLiveStream") -> None:
        self.stream = stream


class YTDLPProbe(Probe):
    """Probe helper dedicated to yt-dlp format and metadata discovery."""

    def ensure_info(self) -> Optional[Dict[str, Any]]:
        """Ensure yt-dlp probe info is available before reading yt-dlp-backed metadata."""
        if self.stream._ytdlp_info is None:
            return self.get_info()
        return self.stream._ytdlp_info

    def log_available_formats(self, info: Optional[Dict[str, Any]]) -> None:
        """Log a readable summary of the formats returned by yt-dlp."""
        if not info or not self.stream.log.isEnabledFor(logging.INFO):
            return

        formats = info.get("formats")
        if not isinstance(formats, list) or not formats:
            return

        lines = []
        for fmt in formats:
            if not isinstance(fmt, dict):
                continue

            format_id = fmt.get("format_id") or fmt.get("itag") or "?"
            height = fmt.get("height")
            resolution = f"{height}p" if isinstance(height, int) else (fmt.get("resolution") or "?")
            protocol = fmt.get("protocol") or "?"
            vcodec = fmt.get("vcodec") or "none"
            acodec = fmt.get("acodec") or "none"
            ext = fmt.get("ext") or "?"
            note = fmt.get("format_note") or ""
            lines.append(
                f"itag={format_id} res={resolution} ext={ext} "
                f"v={vcodec} a={acodec} proto={protocol}"
                + (f" note={note}" if note else "")
            )

        if not lines:
            return

        self.stream.log.info("yt-dlp reported available formats:")
        for line in lines:
            self.stream.log.info("  %s", line)

    def get_opts(self) -> Dict[str, Any]:
        """Return yt-dlp options for probing all formats without pre-filtering them."""
        opts = deepcopy(self.stream.ytdl_opts) if self.stream.ytdl_opts else {}

        # We want the full formats list here and do our own selection later.
        for key in ("format", "format_sort"):
            opts.pop(key, None)

        # Avoid the current yt-dlp live-stream bug in the probe path by
        # disabling live-from-start when we only use yt-dlp for metadata/format
        # discovery. Keep cookies intact so private/member streams can still be
        # probed when possible.
        if not self.stream.use_ytdl and opts.pop("live_from_start", None):
            self.stream.log.warning(
                "Removed live_from_start from yt-dlp probe options because "
                "the native downloader is active."
            )

        opts["logger"] = self.stream.ytdlp_logger
        return opts

    def get_client(self) -> yt_dlp.YoutubeDL:
        """Return a reusable yt-dlp client configured for probing all formats."""
        if self.stream._ytdlp_probe_client is None:
            self.stream._ytdlp_probe_client = yt_dlp.YoutubeDL(self.get_opts())
        return self.stream._ytdlp_probe_client

    @staticmethod
    def reports_no_formats(exc: Exception) -> bool:
        """Return whether a yt-dlp exception indicates that no formats were found."""
        message = str(exc).lower()
        return "no video formats found" in message or "no formats found" in message

    def get_no_formats_reason(self, exc: Exception) -> str:
        """Build a user-facing explanation for yt-dlp's no-formats error."""
        reason = str(exc)
        if self.stream.use_ytdl and self.stream.ytdl_opts and self.stream.ytdl_opts.get("cookiefile"):
            return (
                f"{reason}. This commonly happens when yt-dlp is used as the downloader "
                "with cookies on live streams. Try disabling cookies for yt-dlp or "
                "commenting out `live_from_start` in the ytdlp_config.json file (recommended)"
            )
        return reason

    def get_info(self, force_update: bool = False) -> Optional[Dict[str, Any]]:
        """Fetch and cache yt-dlp metadata used for format choice and metadata hydration."""
        if self.stream._ytdlp_probe_error is not None:
            return self.stream._ytdlp_info
        if self.stream._ytdlp_info is not None and not force_update:
            return self.stream._ytdlp_info

        try:
            self.stream._ytdlp_info = self.get_client().extract_info(
                self.stream.url, download=False
            )
        except DownloadError as exc:
            if self.reports_too_many_requests(exc):
                self.stream._ytdlp_rate_limited = True
                raise TooManyRequestsException(
                    self.stream.video_id,
                    "yt-dlp reported HTTP 429 Too Many Requests"
                )
            if self.reports_live_end(exc):
                self.mark_live_as_ended()
                self.stream.log.info("yt-dlp reports that the live event has ended.")
                return None
            if self.reports_no_formats(exc):
                reason = self.get_no_formats_reason(exc)
                self.stream._ytdlp_probe_error = reason
                raise NoVideoFormatsException(self.stream.video_id, reason)
            self.stream.log.debug("Error getting metadata from yt-dlp: %s", exc)
            return self.stream._ytdlp_info
        except Exception as exc:
            self.stream.log.debug("Error getting metadata from yt-dlp: %s", exc)
            return self.stream._ytdlp_info

        self.stream._ytdlp_rate_limited = False
        self.stream._ytdlp_probe_error = None
        self.hydrate_metadata(self.stream._ytdlp_info)
        self.log_available_formats(self.stream._ytdlp_info)
        return self.stream._ytdlp_info

    @staticmethod
    def reports_live_end(exc: Exception) -> bool:
        """Return whether a yt-dlp exception means the live event is over."""
        message = str(exc).lower()
        return (
            "this live event has ended" in message
            or "live event has ended" in message
        )

    @staticmethod
    def reports_too_many_requests(exc: Exception) -> bool:
        """Return whether a yt-dlp exception reflects rate limiting."""
        message = str(exc).lower()
        return "429" in message or "too many requests" in message

    def mark_live_as_ended(self) -> None:
        """Update local status flags after yt-dlp reports that the live stream is over."""
        self.stream._ytdlp_live_ended = True
        self.stream.status &= ~Status.LIVE
        self.stream.status &= ~Status.VIEWED_LIVE
        self.stream.status |= Status.OFFLINE

    def hydrate_metadata(self, info: Optional[Dict[str, Any]]) -> None:
        """Populate cached metadata fields from yt-dlp probe results when available."""
        if not info:
            return

        live_status = info.get("live_status")
        if live_status in ("post_live", "was_live", "not_live"):
            self.mark_live_as_ended()
        elif live_status in ("is_live", "is_upcoming"):
            self.stream._ytdlp_live_ended = False

        if not self.stream._title:
            self.stream._title = info.get("fulltitle") or info.get("title")
        if not self.stream._author:
            self.stream._author = info.get("channel") or info.get("uploader")
        if not self.stream._description:
            self.stream._description = info.get("description") or ""
        if not self.stream._thumbnail_url:
            self.stream._thumbnail_url = info.get("thumbnail")
            if not self.stream._thumbnail_url and (thumbs := info.get("thumbnails")):
                self.stream._thumbnail_url = thumbs[-1].get("url")
        if self.stream._publish_date is None:
            if timestamp := info.get("release_timestamp") or info.get("timestamp"):
                self.stream._publish_date = datetime.fromtimestamp(timestamp)
            else:
                for key in ("release_date", "upload_date"):
                    if date_str := info.get(key):
                        try:
                            self.stream._publish_date = datetime.strptime(date_str, "%Y%m%d")
                            break
                        except ValueError:
                            continue
        if self.stream._start_time is None and (timestamp := info.get("release_timestamp") or info.get("timestamp")):
            self.stream._start_time = datetime.fromtimestamp(
                timestamp, timezone.utc
            ).isoformat().replace("+00:00", "Z")
        if self.stream._scheduled_timestamp is None:
            release_ts = info.get("release_timestamp")
            if live_status == "is_upcoming" and release_ts is not None:
                self.stream._scheduled_timestamp = int(release_ts)

    def get_title(self) -> Optional[str]:
        """Return the title from yt-dlp metadata when available."""
        self.ensure_info()
        return self.stream._title or (
            self.stream._ytdlp_info.get("fulltitle") or self.stream._ytdlp_info.get("title")
            if self.stream._ytdlp_info else None
        )

    def get_description(self) -> Optional[str]:
        """Return the description from yt-dlp metadata when available."""
        self.ensure_info()
        return self.stream._description or (
            self.stream._ytdlp_info.get("description")
            if self.stream._ytdlp_info else None
        )

    def get_author(self) -> Optional[str]:
        """Return the channel/uploader name from yt-dlp metadata when available."""
        self.ensure_info()
        return self.stream._author or (
            self.stream._ytdlp_info.get("channel") or self.stream._ytdlp_info.get("uploader")
            if self.stream._ytdlp_info else None
        )

    def get_thumbnail_url(self) -> Optional[str]:
        """Return the preferred thumbnail URL from yt-dlp metadata when available."""
        self.ensure_info()
        if self.stream._thumbnail_url:
            return self.stream._thumbnail_url
        if not self.stream._ytdlp_info:
            return None
        thumbnail = self.stream._ytdlp_info.get("thumbnail")
        if thumbnail:
            return thumbnail
        if thumbs := self.stream._ytdlp_info.get("thumbnails"):
            return thumbs[-1].get("url")
        return None


class PytubeProbe(Probe):
    """Probe helper dedicated to YouTube player API and pytube-backed state."""

    def get_info(self, force_update: bool = False, client: str = "android") -> Dict[str, Any]:
        """Fetch and cache player API metadata used by pytube/native probing logic."""
        if self.stream._json and not force_update:
            return self.stream._json

        json_data: Dict[str, Any] = {}
        try:
            # json_string = extract.initial_player_response(self.watch_html)
            # API request with ANDROID client gives us a pre-signed URL.
            # "android" also seems to partially work around throttling here.
            json_data = self.stream.session.make_api_request(
                endpoint="https://www.youtube.com/youtubei/v1/player",
                payload={
                    "videoId": self.stream.video_id
                },
                client=client
            )
            self.stream.session.is_logged_out(json_data)
            remove_useless_keys(json_data)
        except Exception as exc:
            self.stream.log.debug(f"Error getting metadata JSON: {exc}")
        self.stream._json = json_data
        return self.stream._json

    def get_title(self) -> Optional[str]:
        """Return the title from cached player_response when available."""
        try:
            return self.get_player_response()["videoDetails"]["title"]
        except KeyError as exc:
            self.stream.log.warning(f"Error acessing title from player_response: {exc}")
            return None

    def get_description(self) -> Optional[str]:
        """Return the description from cached player_response when available."""
        try:
            return self.get_player_response()["videoDetails"]["shortDescription"]
        except KeyError as exc:
            self.stream.log.warning(f"Error fetching description from player_response: {exc}")
            return None

    def get_author(self) -> Optional[str]:
        """Return the author from cached player_response when available."""
        try:
            return self.get_player_response()["videoDetails"]["author"]
        except KeyError as exc:
            self.stream.log.warning(f"Error fetching author from player_response: {exc}")
            return None

    def get_player_response(self) -> Dict[str, Any]:
        """Return the cached player_response payload derived from the pytube/API probe."""
        if self.stream._player_response:
            return self.stream._player_response

        player_response = self.stream.player_config_args["player_response"]
        if isinstance(player_response, str):
            self.stream._player_response = loads(player_response)
        else:
            self.stream._player_response = player_response
        return self.stream._player_response

    def get_thumbnail_url(self) -> Optional[str]:
        """Return the best thumbnail URL from cached player_response when available."""
        return (
            self.get_player_response().get("videoDetails", {})
            .get("thumbnail", {})
            .get("thumbnails", [{}])[-1]
            .get("url")
        )

    def get_start_time(self) -> Optional[str]:
        """Return the start timestamp from cached player_response when available."""
        try:
            return self.get_player_response() \
                .get("microformat", {}) \
                .get("playerMicroformatRenderer", {}) \
                .get("liveBroadcastDetails", {}) \
                .get("startTimestamp", None)
        except Exception as exc:
            self.stream.log.debug(f"Error getting start_time: {exc}")
            return None

    def get_scheduled_timestamp(self) -> Optional[int]:
        """Return the scheduled start timestamp from cached player_response when available."""
        try:
            timestamp = self.get_player_response().get("playabilityStatus", {}) \
                .get('liveStreamability', {})\
                .get('liveStreamabilityRenderer', {}) \
                .get('offlineSlate', {}) \
                .get('liveStreamOfflineSlateRenderer', {}) \
                .get('scheduledStartTime', None)
            return int(timestamp) if timestamp is not None else None
        except Exception as exc:
            self.stream.log.debug(f"Error getting scheduled_timestamp: {exc}")
            return None

    def get_thumbnail_metadata(self) -> Dict[str, Any]:
        """Return the thumbnail metadata block from cached player_response when available."""
        try:
            return self.get_player_response().get("videoDetails", {}).get("thumbnail", {})
        except Exception as exc:
            # This might occur if we invalidated the cache but the stream is not
            # live anymore, and "streamingData" key is missing from the json.
            self.stream.log.warning(f"Error getting thumbnail metadata value: {exc}")
            return {}

    def get_player_config_args(self) -> Dict[str, Any]:
        """Return the legacy pytube player config mapping used for descrambling streams."""
        if self.stream._player_config_args:
            return self.stream._player_config_args

        # FIXME this is redundant with "json" property of this class
        self.stream._player_config_args = {}
        # self._player_config_args["player_response"] = self.json["responseContext"]
        self.stream._player_config_args["player_response"] = self.get_info()

        if 'streamingData' not in self.stream._player_config_args["player_response"]:
            self.stream.log.critical("Missing streamingData key in json!")
            self.stream.log.debug(self.stream._player_config_args)
            # TODO add fallback strategy with get_ytplayer_config()?

        return self.stream._player_config_args

    def get_fmt_streams(self) -> List[pytube.Stream]:
        """Return the legacy pytube Stream list built from player config args."""
        if self.stream._fmt_streams:
            return self.stream._fmt_streams

        self.stream._fmt_streams = []
        stream_maps = ["url_encoded_fmt_stream_map"]

        # unscramble the progressive and adaptive stream manifests.
        for fmt in stream_maps:
            # if not self.age_restricted and fmt in self.vid_info:
            #     extract.apply_descrambler(self.vid_info, fmt)
            pytube.extract.apply_descrambler(self.get_player_config_args(), fmt)

            pytube.extract.apply_signature(self.get_player_config_args(), fmt, self.stream.js)

            # build instances of :class:`Stream <Stream>`
            # Initialize stream objects
            stream_manifest = self.get_player_config_args()[fmt]
            for stream in stream_manifest:
                # Add method to increment segment:
                stream["url"] = ParamURL(stream["url"])
                video = pytube.Stream(
                    stream=stream,
                    player_config_args=self.get_player_config_args(),
                    monostate={},  # FIXME This is a bit dangerous but we don't use it anyway
                )
                self.stream._fmt_streams.append(video)

        return self.stream._fmt_streams

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
            self.stream.log.critical(f"Error loading XML of MPD: {e}")
            return streams

        for _as in root.findall("{*}Period/{*}AdaptationSet"):
            mimeType = _as.attrib.get("mimeType")
            for _rs in _as.findall("{*}Representation"):
                url = _rs.find("{*}BaseURL")
                if url is None:
                    self.stream.log.debug(f"No BaseURL found for {_rs.attrib}. Skipping.")
                    continue
                # Simulate what pytube does in a very basic way
                # FIXME this can safely be removed in next pytube:
                codec = _rs.get("codecs")
                if not codec or not mimeType:
                    self.stream.log.debug(f"No codecs key found for {_rs.attrib}. Skipping")
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
        """Return the legacy pytube-style stream list derived from the XML DASH MPD."""
        content = self.stream.get_content_from_mpd()
        # TODO for now we only care about the XML DASH MPD, but not the HLS m3u8.
        return self.get_streams_from_xml(content)


class Downloader:
    """Base strategy object for stream downloading.

    The live stream instance owns all mutable state; downloader subclasses
    coordinate a particular download approach against that state.
    """
    def __init__(self, stream: "YoutubeLiveStream") -> None:
        self.stream = stream

    def get_probe_info(self) -> Optional[Dict[str, Any]]:
        """Fetch yt-dlp probe data and convert rate limiting into stream error state."""
        try:
            return self.stream.get_ytdlp_info()
        except NoVideoFormatsException as exc:
            self.stream.error = str(exc)
            self.stream.log.warning(exc)
            return None
        except TooManyRequestsException as exc:
            self.stream.error = str(exc)
            self.stream.log.warning(exc)
            return None

    def download(self, wait_delay: float = 1.0) -> None:
        raise NotImplementedError()


class YTDLPDownloader(Downloader):
    def get_ytdlp_downloader(self) -> yt_dlp.YoutubeDL:
        """Return a reusable yt-dlp client configured for full downloads."""
        if self.stream._ytdlp_download_client is None:
            ytdl_opts = deepcopy(self.stream.ytdl_opts) if self.stream.ytdl_opts else {}
            ytdl_opts["logger"] = self.stream.ytdlp_logger
            self.stream._ytdlp_download_client = yt_dlp.YoutubeDL(ytdl_opts)
        return self.stream._ytdlp_download_client

    def download(self, wait_delay: float = 1.0) -> None:
        """Delegate the full download to yt-dlp after a successful probe."""
        info = self.get_probe_info()
        if not info:
            return

        self.stream.error = self.get_ytdlp_downloader().download(self.stream.url)


class NativeDownloader(Downloader):
    def download(self, wait_delay: float = 1.0) -> None:
        """Run the native downloader flow for either muxed HLS or DASH segments."""
        info = self.get_probe_info()
        if not info:
            return

        if self.stream.prepare_hls_download(info):
            self.stream.update_metadata()
            self.stream.trigger_hooks('on_download_started')

            if self.stream.skip_download:
                self.stream.done = True
                return

            self.do_download_hls(wait_delay=wait_delay)
            if self.stream.done:
                self.stream.log.info(f"Finished downloading {self.stream.video_id}.")
                self.stream.trigger_hooks("on_download_ended")
            if self.stream.error:
                self.stream.log.critical(
                    f"Some kind of error occured during download? {self.stream.error}")
            return

        # If one of the directories exists, assume we are resuming a previously
        # failed download attempt.
        dir_existed = False
        for path in (self.stream.video_outpath, self.stream.audio_outpath):
            try:
                makedirs(path, 0o770)
            except FileExistsError:
                dir_existed = True

        if dir_existed:
            self.stream.seg = self.stream.get_first_segment(
                (self.stream.video_outpath, self.stream.audio_outpath)
            )
        else:
            self.stream.seg = 0
        self.stream.log.info(
            f"Will start downloading from segment number {self.stream.seg}."
        )

        if self.stream.skip_download:
            # If the user explicitly asked to skip download, but calls this
            # method anyway, we assume it's because they only care about the
            # events being triggered
            self.stream.trigger_hooks("on_download_initiated")

        self.stream.seg_attempt = 0
        while not self.stream.done and not self.stream.error:
            try:
                self.stream.update_status()
                self.stream.log.debug(f"Status is {self.stream.status}.")

                if not self.stream.status == Status.OK:
                    self.stream.log.critical(
                        f"Could not download \"{self.stream.url}\": "
                        "stream unavailable or not a livestream.")
                    return

            except WaitingException as e:
                self.stream.log.warning(
                    f"Status is {self.stream.status}. "
                    f"Waiting for {wait_delay} minutes...")
                sleep(wait_delay * 60)
                continue
            except OutdatedAppException as e:
                self.stream.log.warning("Outdated client error. Retrying shortly...")
                wait_block(2)
                continue
            except OfflineException as e:
                self.stream.log.critical(e)
                raise e
            except Exception as e:
                self.stream.log.critical(e, exc_info=True)
                raise e

            if not self.stream.skip_download:
                self.stream.update_download_urls()
                self.stream.update_metadata()

            # FIXME triggers everytime the download resumes, rename to
            # on_download_resumed or on_stream_resumed?
            self.stream.trigger_hooks('on_download_started')

            if self.stream.skip_download:
                # We rely on the exception above to signal when the stream has ended
                self.stream.log.debug(
                    f"Not downloading because \"skip-download\" option is active."
                    f" Waiting for {wait_delay} minutes..."
                )
                sleep(wait_delay * 60)
                continue

            while True:
                try:
                    self.do_download()
                except (
                    EmptySegmentException,
                    ForbiddenSegmentException,
                    IncompleteRead,
                    ValueError,
                    ConnectionError,
                    urllib.error.HTTPError
                ) as exc:
                    self.stream.log.warning(exc)
                    self.stream._watch_html = None
                    self.stream._json = None
                    self.stream._player_config_args = None
                    self.stream._js = None
                    self.stream.get_info()
                    self.stream.is_live()
                    if Status.LIVE | Status.VIEWED_LIVE in self.stream.status:

                        if self.stream.seg_attempt >= 15:
                            self.stream.log.critical(
                                f"Too many attempts on segment {self.stream.seg}. "
                                "Skipping it.")
                            self.stream.seg += 1
                            self.stream.seg_attempt = 0
                            continue

                        self.stream.log.warning(
                            "It seems the stream has not really ended. "
                            f"Retrying in 5 secs... (attempt {self.stream.seg_attempt}/15)")
                        self.stream.seg_attempt += 1
                        sleep(5)
                        try:
                            self.stream.update_download_urls(force=False)
                        except Exception as update_err:
                            self.stream.error = f"{update_err}"
                            break
                        continue
                    self.stream.log.warning("The stream is not live anymore. Done.")
                    self.stream.done = True
                    break
                except Exception as exc:
                    self.stream.log.exception("Unhandled exception. Aborting.")
                    self.stream.error = f"{exc}"
                    break
        if self.stream.done:
            self.stream.log.info(f"Finished downloading {self.stream.video_id}.")
            self.stream.trigger_hooks("on_download_ended")
        if self.stream.error:
            self.stream.log.critical(
                f"Some kind of error occured during download? {self.stream.error}"
            )

    def do_download_hls(self, wait_delay: float = 0.7) -> None:
        """Poll a muxed HLS playlist and download new segments until the stream ends.

        Transient connection failures retry the same URL first. We only refresh
        playlist URLs when errors suggest the current manifest URL may be stale.
        """
        if not self.stream.video_base_url:
            raise Exception("Missing HLS playlist url!")

        wait_sec = max(1, round(wait_delay))
        retry_wait_sec = max(10, wait_sec)
        max_direct_url_retries = 3
        direct_url_retry_counts: Dict[int, int] = {}

        while not self.stream.done and not self.stream.error:
            try:
                extra_headers = self.stream.video_itag.get("http_headers") \
                    if isinstance(self.stream.video_itag, dict) else None
                segments, end_list = self.stream.get_hls_playlist_segments(
                    self.stream.video_base_url,
                    extra_headers=extra_headers
                )
                first_available_seq = segments[0][0] if segments else None
                synthetic_segments: List[tuple[int, str]] = []
                if first_available_seq is not None and self.stream.seg < first_available_seq:
                    if template_url := self.stream.build_hls_segment_url(
                        segments[0][1], self.stream.seg
                    ):
                        self.stream.log.info(
                            "Playlist starts at segment %s but local resume point is %s. "
                            "Trying direct segment URLs for the gap.",
                            first_available_seq,
                            self.stream.seg,
                        )
                        synthetic_segments = [
                            (seg_num, self.stream.build_hls_segment_url(segments[0][1], seg_num))
                            for seg_num in range(self.stream.seg, first_available_seq)
                        ]
                        synthetic_segments = [
                            (seg_num, seg_url)
                            for seg_num, seg_url in synthetic_segments
                            if seg_url is not None
                        ]
                    else:
                        self.stream.log.warning(
                            "Playlist starts at segment %s but older segment URLs could not "
                            "be reconstructed. Resuming from %s instead of %s.",
                            first_available_seq,
                            first_available_seq,
                            self.stream.seg,
                        )
                        self.stream.seg = first_available_seq

                next_segments = [
                    (seg_num, seg_url)
                    for seg_num, seg_url in segments
                    if seg_num >= self.stream.seg
                ]
                if synthetic_segments:
                    next_segments = synthetic_segments + next_segments

                if not next_segments:
                    if end_list:
                        self.stream.done = True
                        break
                    sleep(wait_sec)
                    continue

                for seg_num, seg_url in next_segments:
                    self.stream.print_progress(seg_num)
                    try:
                        if not self.stream.download_hls_segment(
                            seg_url,
                            seg_num,
                            extra_headers=extra_headers
                        ):
                            break
                    except urllib.error.HTTPError as exc:
                        if first_available_seq is not None and seg_num < first_available_seq:
                            direct_url_retry_counts.pop(seg_num, None)
                            self.stream.log.warning(
                                "Failed to fetch older segment %s directly (%s). "
                                "Resuming from playlist media sequence %s.",
                                seg_num,
                                exc,
                                first_available_seq,
                            )
                            self.stream.seg = first_available_seq
                            break
                        raise
                    except urllib.error.URLError as exc:
                        if first_available_seq is not None and seg_num < first_available_seq:
                            attempts = direct_url_retry_counts.get(seg_num, 0) + 1
                            direct_url_retry_counts[seg_num] = attempts
                            if attempts < max_direct_url_retries:
                                self.stream.log.warning(
                                    "Failed to fetch older segment %s directly (%s). "
                                    "Retrying the same direct URL in %s seconds "
                                    "(attempt %s/%s).",
                                    seg_num,
                                    exc,
                                    retry_wait_sec,
                                    attempts,
                                    max_direct_url_retries,
                                )
                                sleep(retry_wait_sec)
                                break

                            direct_url_retry_counts.pop(seg_num, None)
                            self.stream.log.warning(
                                "Failed to fetch older segment %s directly after %s attempts (%s). "
                                "Refreshing the playlist URL and resuming from media sequence %s.",
                                seg_num,
                                attempts,
                                exc,
                                first_available_seq,
                            )
                            self.stream.refresh_hls_format()
                            self.stream.seg = first_available_seq
                            sleep(retry_wait_sec)
                            break
                        raise
                    direct_url_retry_counts.pop(seg_num, None)
                    self.stream.seg = seg_num + 1

                if end_list and self.stream.seg > next_segments[-1][0]:
                    self.stream.done = True
                    break

            except TooManyRequestsException as exc:
                self.stream.log.warning(exc)
                self.stream.error = str(exc)
                break
            except OfflineException as exc:
                self.stream.log.info(exc)
                self.stream.done = True
                break
            except (
                EmptySegmentException,
                ForbiddenSegmentException,
                urllib.error.HTTPError,
            ) as exc:
                self.stream.log.warning(exc)
                try:
                    self.stream.refresh_hls_format()
                except Exception as refresh_err:
                    if isinstance(refresh_err, TooManyRequestsException):
                        self.stream.log.warning(refresh_err)
                        self.stream.error = str(refresh_err)
                        break
                    if isinstance(refresh_err, OfflineException) or self.stream._ytdlp_live_ended:
                        self.stream.log.info(refresh_err)
                        self.stream.done = True
                        break
                    self.stream.error = f"{refresh_err}"
                    break
                sleep(wait_sec)
            except (
                IncompleteRead,
                ValueError,
                ConnectionError,
                urllib.error.URLError,
            ) as exc:
                self.stream.log.warning(
                    "%s. Retrying the same HLS URL in %s seconds.",
                    exc,
                    retry_wait_sec,
                )
                sleep(retry_wait_sec)
            except Exception as exc:
                self.stream.log.exception("Unhandled exception in HLS download. Aborting.")
                self.stream.error = f"{exc}"
                break

    def do_download(self) -> None:
        """Download separate audio/video DASH segments using the native URL scheme."""
        if not self.stream.video_base_url:
            raise Exception("Missing video url!")
        if not self.stream.audio_base_url:
            raise Exception("Missing audio url!")

        last_check_time = datetime.now()
        wait_sec = 3
        max_attempts = 10
        attempts_left = max_attempts
        while True:
            try:
                self.stream.print_progress(self.stream.seg)

                if self.stream.seg % 10 == 0:
                    now = datetime.now()
                    if (now - last_check_time).total_seconds() > 5 * 60:
                        last_check_time = now
                        self.stream.update_download_urls(force=True)

                if not self.stream.download_seg(self.stream.video_base_url, self.stream.seg, "video") \
                or not self.stream.download_seg(self.stream.audio_base_url, self.stream.seg, "audio"):
                    attempts_left -= 1
                    if attempts_left >= 0:
                        self.stream.log.warning(
                            f"Waiting for {wait_sec} seconds before retrying "
                            f"segment {self.stream.seg} (attempt {max_attempts - attempts_left}/{max_attempts})")
                        sleep(wait_sec)
                        continue
                    else:
                        self.stream.log.warning(
                            f"Skipping segment {self.stream.seg} due to too many attempts.")
                attempts_left = max_attempts
                self.stream.seg_attempt = 0
                self.stream.seg += 1

            except urllib.error.URLError as exc:
                self.stream.log.critical(f'{type(exc)}: {exc}')
                if exc.reason == "Not Found":
                    raise
                if exc.reason == 'Forbidden':
                    raise ForbiddenSegmentException(exc.reason)
                if attempts_left < 0:
                    raise exc
                attempts_left -= 1
                self.stream.log.warning(
                    f"Waiting for {wait_sec} seconds before retrying... "
                    f"(attempt {max_attempts - attempts_left}/{max_attempts})")
                sleep(wait_sec)
                continue
            except (IncompleteRead, ValueError) as exc:
                self.stream.log.exception(exc)
                raise exc
            except IOError as exc:
                self.stream.log.exception(exc)
                raise exc


class YTDLPLogger:
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def debug(self, msg: str) -> None:
        self.logger.debug(msg)

    def info(self, msg: str) -> None:
        self.logger.info(msg)

    def warning(self, msg: str) -> None:
        self.logger.warning(msg)

    def error(self, msg: str) -> None:
        self.logger.error(msg)


class LiveVideo(VideoPost):
    # TODO extend video Post with method to get live data
    pass


class YoutubeLiveStream:
    def __init__(
        self,
        video_id: str,
        session: YoutubeUrllibSession,
        notifier: NotificationDispatcher,
        url: Optional[str] = None,
        max_video_height: Optional[str] = None,
        output_dir: Optional[Path] = None,
        hooks: Dict = {},
        skip_download = False,
        filters: Dict[str, re.Pattern] = {},
        ignore_quality_change: bool = False,
        log_level = logging.INFO,
        initial_metadata: Optional[VideoPost] = None,
        use_ytdl = False,
        ytdl_opts: Optional[Dict] = None
    ) -> None:
        self.session = session
        self.video_id = video_id
        self.url = url if url else f"https://www.youtube.com/watch?v={video_id}"
        self.max_video_height = max_video_height

        self._js: Optional[str] = None  # js fetched by js_url
        self._js_url: Optional[str] = None  # the url to the js, parsed from watch html

        self._watch_html: Optional[str] = None
        self._embed_html: Optional[str] = None

        self._json: Optional[Dict] = {}

        self.hooks = hooks
        self.skip_download = skip_download
        self.ignore_quality_change = ignore_quality_change

        # NOTE if "www" is omitted, it might force a redirect on YT's side
        # (with &ucbcb=1) and force us to update cookies again. YT is very picky
        # about that. Let's just avoid it.
        self.watch_url = f"https://www.youtube.com/watch?v={self.video_id}"
        self.embed_url = f"https://www.youtube.com/embed/{self.video_id}"

        self._author: Optional[str] = None
        self._title: Optional[str] = None
        self._description = ""
        self._thumbnail_url: Optional[str] = None
        self._publish_date: Optional[datetime] = None
        self._initial_metadata = initial_metadata
        self._ytdlp_info: Optional[Dict[str, Any]] = None
        self._ytdlp_probe_client: Optional[yt_dlp.YoutubeDL] = None
        self._ytdlp_download_client: Optional[yt_dlp.YoutubeDL] = None
        self._ytdlp_probe_error: Optional[str] = None
        self._ytdlp_live_ended = False
        self._ytdlp_rate_limited = False

        self.video_itag = None
        self.audio_itag = None

        self._player_config_args: Optional[Dict] = None
        self._player_response: Optional[Dict] = None
        self._fmt_streams: Optional[List[YTDLPStream]] = None

        self._chosen_itags: Dict = {}

        self._download_date: Optional[str] = None
        self._scheduled_timestamp = None
        self._start_time: Optional[str] = None

        self._age_restricted: Optional[bool] = None

        self.video_base_url = None
        self.audio_base_url = None
        self.is_muxed_hls = False
        self.seg = 0
        self.seg_attempt = 0
        self.status = Status.OFFLINE
        self.done = False
        self.error = None
        self.mpd = None

        self.use_ytdl = use_ytdl
        self.ytdl_opts = ytdl_opts
        self.ytdlp_probe = YTDLPProbe(self)
        self.pytube_probe = PytubeProbe(self)
        self.downloader: Downloader = (
            YTDLPDownloader(self) if use_ytdl else NativeDownloader(self)
        )

        if use_ytdl and output_dir is not None:
            if not output_dir.exists():
                self.output_dir = create_output_dir(
                    output_dir=output_dir, video_id=None
                )

        if self.ytdl_opts is not None:
            if paths := self.ytdl_opts.get("paths"):
                if output_dir and str(output_dir) != '.':
                    paths.update({"home": str(output_dir)})
                elif home := paths.get("home"):
                    output_dir = Path(home)
            elif output_dir is not None:
                self.ytdl_opts["paths"] = {"home": str(output_dir.absolute())}
        if not output_dir:
            output_dir = Path()

        self.log = self.setup_logger(output_dir, log_level)
        self.ytdlp_logger = YTDLPLogger(self.log)
        self.notifier = notifier

        self.output_dir = output_dir
        self.video_outpath = self.output_dir / 'vid'
        self.audio_outpath = self.output_dir / 'aud'

        self.allow_regex: Optional[re.Pattern] = filters.get("allow_regex")
        self.block_regex: Optional[re.Pattern] = filters.get("block_regex")

    def get_ytdl_info_opts(self) -> Dict[str, Any]:
        """Return yt-dlp probe options through the dedicated yt-dlp probe helper."""
        return self.ytdlp_probe.get_opts()

    def get_ytdlp_info(self, force_update: bool = False) -> Optional[Dict[str, Any]]:
        """Fetch yt-dlp probe data through the dedicated yt-dlp probe helper."""
        return self.ytdlp_probe.get_info(force_update=force_update)

    @staticmethod
    def ytdlp_reports_live_end(exc: Exception) -> bool:
        """Compatibility wrapper for the dedicated yt-dlp probe helper."""
        return YTDLPProbe.reports_live_end(exc)

    @staticmethod
    def ytdlp_reports_too_many_requests(exc: Exception) -> bool:
        """Compatibility wrapper for the dedicated yt-dlp probe helper."""
        return YTDLPProbe.reports_too_many_requests(exc)

    def mark_live_as_ended_from_ytdlp(self) -> None:
        """Compatibility wrapper for the dedicated yt-dlp probe helper."""
        self.ytdlp_probe.mark_live_as_ended()

    def hydrate_metadata_from_ytdlp_info(self, info: Optional[Dict[str, Any]]) -> None:
        """Compatibility wrapper for the dedicated yt-dlp probe helper."""
        self.ytdlp_probe.hydrate_metadata(info)

    def setup_logger(self, output_path: Path, log_level: str | int):
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
            logger.debug("Logger %s already had handlers!", logger)
            return logger

        logger.setLevel(logging.DEBUG)
        # File output
        logfile = logging.FileHandler(
            filename=output_path / f"download_{self.video_id}.log",
            delay=True, encoding='utf-8'
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

    def __repr__(self) -> str:
        return f"{self.video_id} - {self.author} - {self.title}"

    def is_download_wanted(self) -> bool:
        """
        Test for blocked substrings in metadata.
        """
        if self._title is None and self._initial_metadata is not None:
            self._title = self._initial_metadata.get("title")
        if not self._description and self._initial_metadata is not None:
            self._description = self._initial_metadata.get("description") or ""

        title = None
        description = None
        try:
            title = self.title
            description = self.description
        except Exception as exc:
            # Default to allowing download in that case.
            self.log.error("Failed getting some metadata for regex matching: %s", exc)

        # Fallback to using data from monitoring phase
        if not title and self._initial_metadata is not None:
            self.title = self._initial_metadata.get("title")
        if not description and self._initial_metadata is not None:
            self.description = self._initial_metadata.get("description")

        return none_filtered_out(
            (self.title, self.description),
            self.allow_regex, self.block_regex)

    def pre_download_checks(self) -> bool:
        """
        Make sure the stream has started, and return whether the metadata allow
        for download.
        This method will block if the stream is filtered by regex expressions
        until it goes offline.
        """
        try:
            self.get_ytdlp_info()
        except NoVideoFormatsException as exc:
            self.error = str(exc)
            self.log.warning(exc)
            return False
        except TooManyRequestsException as exc:
            self.error = str(exc)
            self.log.warning(exc)
            return False
        download_wanted = self.is_download_wanted()
        if not download_wanted:
            self.skip_download = True
            self.log.info(
                f"Regex filters prevented download of {self.video_id} - {self.title}")

        # Keep checking metadata for any change in case it becomes wanted for
        # download, until the stream goes offline.
        long_wait = 25.0  # minutes
        while not download_wanted:
            if self._ytdlp_live_ended:
                self.log.info("yt-dlp reports that the stream is no longer live.")
                break
            self.status = Status.OFFLINE
            try:
                self.update_status()
                self.log.debug(f"Status is: {self.status}.")

                if not (Status.LIVE in self.status):
                    self.log.info("Stream is not live anymore.")
                    break
            except WaitingException:
                self.log.info(
                    f"Stream {self.video_id} status is: {self.status}. "
                    f"Waiting {long_wait} minutes...")
            except OfflineException:
                self.log.info(f"Stream {self.video_id} status is now offline.")
                break
            except (
                NoLoginException,
                UnplayableException
            ) as exc:
                self.log.warning(exc)
                if not (Status.LIVE in self.status):
                    self.log.info("Stream is not live anymore.")
                    break
                wait_block(long_wait)
                continue
            except OutdatedAppException as exc:
                self.log.warning("Outdated client error. Retrying shortly...")
                if not (Status.LIVE in self.status):
                    self.log.info("Stream is not live anymore.")
                    break
                wait_block(2)
                continue
            except Exception as exc:
                self.log.error("Error getting status for stream %s: %s", self.video_id, exc)

            if not self.status == Status.OK:
                self.log.info("Stream %s status is not active.", self.video_id)
                break

            self.log.info("Stream %s is still active...", self.video_id)

            # Keep checking in case the metadata changed
            try:
                if not self.get_ytdlp_info(force_update=True) and self._ytdlp_live_ended:
                    self.log.info("yt-dlp reports that the stream is no longer live.")
                    break
            except NoVideoFormatsException as exc:
                self.error = str(exc)
                self.log.warning(exc)
                break
            except TooManyRequestsException as exc:
                self.error = str(exc)
                self.log.warning(exc)
                break
            self.get_metadata()
            download_wanted = self.is_download_wanted()

            if download_wanted:
                self.skip_download = False
                self.log.warning(
                    f"Stream {self.video_id} actually became wanted for download!")
                break

            # Longer delay in minutes between updates since we don't download
            # we don't care about accuracy that much.
            wait_block(long_wait)
            continue
        return download_wanted


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
            self.log.warning(
                "An output directory already existed. "
                "We assume a failed download attempt. "
                f"Last segment available was {seg}.")
            seg -= 1
        return seg

    def is_live(self) -> None:
        if not self.get_info():
            self.log.debug(
                "Got no JSON data, removing \"Available\" flag from status.")
            self.status &= ~Status.AVAILABLE
            return

        # FIXME we could have JSON data but no videoDetails, while the stream
        # is actually still live.

        isLive = self.get_info().get('videoDetails', {}).get('isLive')
        if isLive is True:
            self.status |= Status.LIVE
        else:
            self.status &= ~Status.LIVE

        # Is this actually being streamed live?
        is_viewed_live = None
        for _dict in self.get_info().get('responseContext', {}).get('serviceTrackingParams', []):
            param = _dict.get('params', [])
            for key in param:
                if key.get('key') == 'is_viewed_live':
                    is_viewed_live = key.get('value')
                    break
        if is_viewed_live and is_viewed_live == "True":
            self.status |= Status.VIEWED_LIVE
        else:
            self.status &= ~Status.VIEWED_LIVE
        self.log.debug(f"is_live() status: {self.status}")


    def clear_cache(self) -> None:
        """
        Clear all cached metadata.
        """
        self._json = None
        self._player_config_args = None
        self._player_response = None
        self._watch_html = None

    def get_metadata(self, force_update=False) -> Dict:
        """
        Fetch metadata about the video.
        Mainly used to populate member properties.
        """
        if force_update:
            self.clear_cache()
        return self.get_info()

    @property
    def watch_html(self):
        # TODO get the DASH manifest (MPD) instead?
        if self._watch_html:
            return self._watch_html
        try:
            self._watch_html = self.session.make_request(url=self.watch_url)
        except:
            self._watch_html = None

        return self._watch_html

    @property
    def embed_html(self):
        if self._embed_html:
            return self._embed_html
        self._embed_html = pytube.request.get(url=self.embed_url)
        return self._embed_html

    def get_info(self, force_update=False, client="android") -> Dict[str, Any]:
        """Fetch player API metadata through the dedicated pytube/API probe helper."""
        return self.pytube_probe.get_info(force_update=force_update, client=client)

    @property
    def publish_date(self):
        """Get the publish date.

        :rtype: datetime
        """
        if self._publish_date:
            return self._publish_date
        self._publish_date = publish_date(self.watch_html)
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
                self.log.critical(e)
                return None
            self.mpd = mpd
        else:
            content = self.mpd.get_content(update=True)
        return content

    def get_streams_from_xml(self, data) -> List[YTDLPStream]:
        """Parse MPD XML into yt-dlp-style stream wrappers as a last-resort fallback."""
        if not data:
            return []
        streams: List[YTDLPStream] = []
        try:
            root = ET.fromstring(data)
        except Exception as e:
            self.log.critical(f"Error loading XML of MPD: {e}")
            return streams

        for _as in root.findall("{*}Period/{*}AdaptationSet"):
            mime_type = _as.attrib.get("mimeType", "")
            for _rs in _as.findall("{*}Representation"):
                url = _rs.find("{*}BaseURL")
                if url is None or not url.text:
                    self.log.debug(f"No BaseURL found for {_rs.attrib}. Skipping.")
                    continue
                codec = _rs.get("codecs")
                if not codec or not mime_type:
                    self.log.debug(f"No codecs key found for {_rs.attrib}. Skipping")
                    continue
                vcodec = codec if mime_type.startswith("video/") else "none"
                acodec = codec if mime_type.startswith("audio/") else "none"
                abr = None
                if audio_rate := _rs.get("bandwidth"):
                    try:
                        abr = str(int(audio_rate) // 1000)
                    except ValueError:
                        abr = None
                streams.append(YTDLPStream({
                    "format_id": _rs.get("id"),
                    "url": url.text,
                    "fragment_base_url": url.text,
                    "protocol": "http_dash_segments",
                    "ext": mime_type.rsplit("/", 1)[-1],
                    "vcodec": vcodec,
                    "acodec": acodec,
                    "height": int(_rs.get("height")) if _rs.get("height") else None,
                    "fps": int(_rs.get("frameRate")) if _rs.get("frameRate") else None,
                    "abr": abr,
                }))
        return streams

    def get_streams_from_mpd(self) -> List[YTDLPStream]:
        """Return DASH stream wrappers derived from yt-dlp formats or, failing that, from the MPD XML."""
        if info := self.get_ytdlp_info():
            formats = info.get("formats") or []
            dash_streams = [
                YTDLPStream(fmt) for fmt in formats
                if isinstance(fmt, dict)
                and (
                    fmt.get("protocol") in ("http_dash_segments", "dash", "dashy")
                    or fmt.get("fragment_base_url")
                )
                and fmt.get("url")
            ]
            if dash_streams:
                return dash_streams

        content = self.get_content_from_mpd()
        return self.get_streams_from_xml(content)

    @property
    def streams(self) -> YTDLPStreamQuery:
        """Expose yt-dlp-discovered formats through a small query helper."""
        stream_list = self.fmt_streams

        if not stream_list:
            self.log.info("No stream descriptors found. Getting them from MPD...")
            stream_list = self.get_streams_from_mpd()
        if not stream_list:
            raise Exception("Failed to load stream descriptors!")
        return YTDLPStreamQuery(stream_list)

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
        if self._js:
            return self._js

        # If the js_url doesn't match the cached url, fetch the new js and update
        #  the cache; otherwise, load the cache.
        if pytube.__js_url__ != self.js_url:
            self._js = pytube.request.get(self.js_url)
            pytube.__js__ = self._js
            pytube.__js_url__ = self.js_url
        else:
            self._js = pytube.__js__

        return self._js

    @property
    def player_response(self) -> Optional[Dict]:
        """The player response contains subtitle information and video details."""
        return self.pytube_probe.get_player_response()

    @property
    def title(self) -> Optional[str]:
        if self._title:
            return self._title

        title = self.get_title()

        # Keep the last valid value in cache (if we have one), just in case we
        # would end up overwriting it with nothing.
        if title is not None:
            if self._title != title:
                self.log.debug(
                    f"Title change: \"{self._title}\" -> \"{title}\"")
            self._title = title

        return self._title

    @title.setter
    def title(self, value):
        self._title = value

    def get_title(self) -> Optional[str]:
        """
        Retrieve the best title currently available from the configured probes.
        """
        if self._title:
            return self._title

        # Prefer yt-dlp's metadata first since it is our primary probing path.
        if title := self.ytdlp_probe.get_title():
            return title

        # If yt-dlp already failed, use the title we got during channel scanning
        # rather than forcing the older pytube/player_response path.
        if self._initial_metadata is not None:
            if initial_title := self._initial_metadata.get("title"):
                return initial_title

        # Fall back to player_response when yt-dlp could not answer.
        return self.pytube_probe.get_title()

    @property
    def description(self) -> Optional[str]:
        if self._description:
            return self._description

        desc = self.get_description()

        # Keep the last valid value in cache (if we have one), just in case we
        # would end up overwriting it with nothing.
        if desc is not None:
            if self._description != desc:
                self.log.debug(
                    f"Description change: \"{self._description}\" -> \"{desc}\"")
            self._description = desc
        return self._description

    @description.setter
    def description(self, value) -> None:
        self._description = value

    def get_description(self) -> Optional[str]:
        """
        Retrieve the best description currently available from the configured probes.
        """
        if self._description:
            return self._description

        if description := self.ytdlp_probe.get_description():
            return description

        if self._initial_metadata is not None:
            if initial_description := self._initial_metadata.get("description"):
                return initial_description

        return self.pytube_probe.get_description()

    @property
    def thumbnail_url(self) -> str:
        """
        Get the best thumbnail image URL.
        """
        if self._thumbnail_url:
            return self._thumbnail_url

        thumbnail = self.ytdlp_probe.get_thumbnail_url()
        if thumbnail is None:
            if self._initial_metadata is not None:
                thumbnails = self._initial_metadata.get("thumbnail", {}).get("thumbnails", [])
                if thumbnails:
                    thumbnail = thumbnails[-1].get("url")

        if thumbnail is None:
            # Fall back to player_response when yt-dlp does not expose one.
            thumbnail = self.pytube_probe.get_thumbnail_url()

        if thumbnail:
            self._thumbnail_url = thumbnail
            return thumbnail
        return f"https://img.youtube.com/vi/{self.video_id}/maxresdefault.jpg"

    @property
    def start_time(self):
        if self._start_time:
            return self._start_time
        self._start_time = self.pytube_probe.get_start_time()
        return self._start_time

    @property
    def scheduled_timestamp(self) -> Optional[int]:
        if self._scheduled_timestamp:
            return self._scheduled_timestamp
        self._scheduled_timestamp = self.pytube_probe.get_scheduled_timestamp()
        return self._scheduled_timestamp

    @property
    def author(self) -> str:
        if self._author:
            return self._author

        author = self.ytdlp_probe.get_author()
        if author is None:
            if self._initial_metadata is not None:
                author = self._initial_metadata.get("channel_name") \
                    or self._initial_metadata.get("author")

        if author is None:
            author = self.pytube_probe.get_author()

        # Keep the last valid value in cache (if we have one), just in case we
        # would end up overwriting it with nothing.
        if author is not None:
            if self._author != author:
                self.log.debug(
                    f"Author change: \"{self._author}\" -> \"{author}\"")
            self._author = author
        return self._author

    @author.setter
    def author(self, value):
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
                self.log.warning(f"Error writing thumbnails: {e}")

    @staticmethod
    def _stream_value(stream: Any, key: str) -> Any:
        if stream is None:
            return None
        if isinstance(stream, dict):
            if key == "itag":
                return stream.get("format_id") or stream.get("itag")
            if key == "resolution":
                if height := stream.get("height"):
                    return f"{height}p"
                return stream.get("resolution")
            if key == "abr":
                return stream.get("abr")
            if key == "mime_type":
                return stream.get("ext") or stream.get("protocol")
            return stream.get(key)
        return getattr(stream, key, None)

    def update_metadata(self):
        """
        Write some basic metadata to a file and download thumbnail onto storage
        device.
        """
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
            "title": self.title,
            "author": self.author,
            "publish_date": str(self.publish_date),
            "start_time": self.start_time,
            "download_date": date.fromtimestamp(time()).__str__(),
            "download_time": datetime.now().strftime("%d%m%Y_%H-%M-%S"),
            "video_itag": self.video_itag,
            "audio_itag": self.audio_itag,
            "description": self.description,
        }
        scheduled_timestamp = self._scheduled_timestamp
        if scheduled_timestamp is None and not self.is_muxed_hls:
            scheduled_timestamp = self.scheduled_timestamp
        if scheduled_timestamp is not None:
            info["scheduled_time"] = datetime.fromtimestamp(
                scheduled_timestamp
            ).__str__()

        if self.video_itag:
            info["video_itag"] = self._stream_value(self.video_itag, "itag")
            info["video_resolution"] = self._stream_value(self.video_itag, "resolution")
        if self.audio_itag:
            info["audio_itag"] = self._stream_value(self.audio_itag, "itag")
            info["audio_bitrate"] = self._stream_value(self.audio_itag, "abr")
        return info

    def update_status(self):
        """Refresh live-state flags from the YouTube player API response."""
        self.log.debug("update_status...")
        # Force update
        self.clear_cache()
        _json = self.get_info()

        if not _json:
            self.log.debug("Got no JSON data, removing \"Available\" flag from status.")
            self.status &= ~Status.AVAILABLE
            self.log.debug(dumps(_json, indent=2, ensure_ascii=False))
            return

        self.is_live()
        if not self.skip_download:
            if self.status & Status.VIEWED_LIVE:
                self.log.info("Stream seems to be viewed live. Good.")
            else:
                self.log.warning(
                    "Stream is not being viewed live. This might not work!")

        # Check if video is indeed available through its reported status.
        playabilityStatus = _json.get('playabilityStatus', {})
        status = playabilityStatus.get('status')
        playability_reason = playabilityStatus.get('reason', 'No reason found.')
        subreason = None
        error_reason = None
        if errorScreen := playabilityStatus.get('errorScreen'):
            if playerErrorMessageRenderer := errorScreen.get(
                    'playerErrorMessageRenderer'):
                if _subreason := playerErrorMessageRenderer.get('subreason'):
                    if simpleText := _subreason.get('simpleText'):
                        subreason = simpleText
                    elif subr_runs := _subreason.get('runs'):
                        subreason = ','.join([r.get('text') for r in subr_runs])
                    else:
                        subreason = "No subreason found."
                if _reason := playerErrorMessageRenderer.get('reason'):
                    if runs := _reason.get('runs'):
                        error_reason = ','.join([r.get('text') for r in runs])

        if status == 'LIVE_STREAM_OFFLINE':
            self.status |= Status.OFFLINE

            scheduled_time = self.scheduled_timestamp
            if scheduled_time is not None:
                self.status |= Status.WAITING

                # self._scheduled_timestamp = scheduled_time
                self.log.info(
                    f"Scheduled start time: {scheduled_time}"
                    f"({datetime.fromtimestamp(scheduled_time)} UTC). We wait...")
                # FIXME use local time zone for more accurate display of time
                # for example: https://dateutil.readthedocs.io/

                self.log.warning(f"{playability_reason}")

                raise WaitingException(
                    self.video_id, playability_reason, scheduled_time)

            elif (Status.LIVE | Status.VIEWED_LIVE) not in self.status:
                raise WaitingException(self.video_id, playability_reason)

            raise OfflineException(self.video_id, playability_reason)

        elif status == 'LOGIN_REQUIRED':
            raise NoLoginException(self.video_id, playability_reason)

        elif status == 'UNPLAYABLE':
            raise UnplayableException(self.video_id, playability_reason)

        elif status != 'OK':
            self.log.warning(
                f"Livestream {self.video_id} "
                f"playability status is: {status} "
                f"Reason: {playability_reason}. "
                f"Sub-reason: {subreason}. Error reason: {error_reason}.")
            self.log.debug(dumps(_json, indent=2, ensure_ascii=False))

            if (error_reason and "not available on this app" in error_reason)\
                or (subreason and "Watch on the latest version of YouTube" in subreason):
                # Video might still be available if we retry with different client
                raise OutdatedAppException(self.video_id, error_reason)

            self.status &= ~Status.AVAILABLE

        else:  # status == 'OK'
            self.status |= Status.AVAILABLE
            self.status &= ~Status.OFFLINE
            self.status &= ~Status.WAITING

        if not self.skip_download:
            self.log.info(f"Stream status flags: {self.status}")


    # TODO get itag by quality first, and then update the itag download url
    # if needed by selecting by itag (the itag we have chosen by best quality)
    def update_download_urls(self, force = False):
        """Refresh native DASH download URLs and validate that chosen formats stay compatible."""
        previous_video_base_url = self.video_base_url
        previous_audio_base_url = self.audio_base_url
        if force:
            self._watch_html = None
            self._json = None
            self._player_config_args = None
            self._player_response = None
            self._js = None
            self._fmt_streams = None
            self.log.info("Forcing update of download URLs.")

        video_quality, audio_quality = self.get_best_streams(
            maxq=self.max_video_height, log=not force)

        if not force:
            # Most likely first time
            self.log.debug(
                f"Selected video itag: {video_quality} / "
                f"Selected audio itag: {audio_quality}")
        elif not video_quality and not audio_quality:
            if previous_audio_base_url is None and previous_video_base_url is None:
                raise Exception(f"No stream URL found for {self.video_id}")
            self.log.critical(f"No stream URL found for {self.video_id}")

        if ((self.video_itag is not None
        and self.video_itag.itag != video_quality.itag)
        or
        (self.audio_itag is not None
        and self.audio_itag.itag != audio_quality.itag)):
            # Probably should fail if we suddenly get a different format than the
            # one we had before to avoid problems during merging.
            self.log.critical(
                "Got a different format after refresh of download URL!\n"
                f"Previous video itag: {self.video_itag}. New: {video_quality}.\n"
                f"Previous audio itag: {self.audio_itag}. New: {audio_quality}"
            )

            # If the codec is too different, abort download:
            if not self.ignore_quality_change or \
            ((self.audio_itag.mime_type != audio_quality.mime_type)
            or (self.video_itag.mime_type != video_quality.mime_type)):
                raise Exception("Stream format mismatch after update of base URL.")

        self.video_itag = video_quality
        self.audio_itag = audio_quality

        # self.video_base_url = extract.get_base_url_from_itag(self.json, video_quality)
        # self.audio_base_url = extract.get_base_url_from_itag(self.json, audio_quality)
        self.video_base_url = self.video_itag.url
        self.audio_base_url = self.audio_itag.url

        if not force:
            self.log.debug(f"Video base url: {self.video_base_url}")
            self.log.debug(f"Audio base url: {self.audio_base_url}")
        else:
            if previous_video_base_url != self.video_base_url:
                self.log.debug(
                    f"Audio base URL got changed from {previous_video_base_url}"
                    f" to {self.video_base_url}.")
            if previous_audio_base_url != self.audio_base_url:
                self.log.debug(
                    f"Audio base URL got changed from {previous_audio_base_url}"
                    f" to {self.audio_base_url}.")


    def download(self, wait_delay: float = 1.0):
        """Run the configured downloader strategy for this live stream."""
        self.downloader.download(wait_delay=wait_delay)

    def prepare_hls_download(self, info: Optional[Dict[str, Any]]) -> bool:
        """Initialize muxed-HLS state when yt-dlp exposes a suitable single-track format."""
        if not info:
            return False

        selected = self.get_best_hls_format(info)
        if not selected:
            return False

        self.video_itag = selected
        self.audio_itag = None
        self.video_base_url = selected.get("url") or selected.get("manifest_url")
        self.audio_base_url = None
        self.is_muxed_hls = True

        if not self.video_base_url:
            self.log.warning("Selected HLS format had no playlist URL.")
            self.is_muxed_hls = False
            return False

        dir_existed = False
        try:
            makedirs(self.video_outpath, 0o770)
        except FileExistsError:
            dir_existed = True

        if dir_existed:
            self.seg = self.get_first_segment((self.video_outpath,))
        else:
            self.seg = 0

        self.log.info(
            "Selected muxed HLS itag %s at %s via yt-dlp.",
            self._stream_value(selected, "itag"),
            self._stream_value(selected, "resolution"),
        )
        self.log.info(f"Will start downloading from segment number {self.seg}.")
        return True

    def get_best_hls_format(self, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Select the best muxed HLS format respecting the configured max height."""
        formats = info.get("formats") or []
        if not isinstance(formats, list):
            return None

        maxq = self.max_video_height
        if maxq is not None and not isinstance(maxq, int):
            match = re.search(r"(\d{3,4})", str(maxq))
            maxq = int(match.group(1)) if match else None

        candidates = [
            fmt for fmt in formats
            if isinstance(fmt, dict)
            and fmt.get("protocol") == "m3u8_native"
            and fmt.get("vcodec") not in (None, "none")
            and fmt.get("acodec") not in (None, "none")
            and (fmt.get("url") or fmt.get("manifest_url"))
        ]
        if not candidates:
            return None

        if maxq is not None:
            within_limit = [
                fmt for fmt in candidates
                if isinstance(fmt.get("height"), int) and fmt["height"] <= maxq
            ]
            if within_limit:
                candidates = within_limit

        return sorted(
            candidates,
            key=lambda fmt: (fmt.get("height") or -1, fmt.get("tbr") or -1),
            reverse=True
        )[0]

    def refresh_hls_format(self) -> None:
        """Refresh the selected muxed HLS manifest URL from a fresh yt-dlp probe."""
        info = self.get_ytdlp_info(force_update=True)
        if self._ytdlp_live_ended:
            raise OfflineException(
                self.video_id,
                "yt-dlp reports that the live event has ended"
            )
        if not info:
            raise Exception("Failed to refresh stream information from yt-dlp.")
        selected = self.get_best_hls_format(info)
        if not selected:
            raise Exception("Failed to refresh muxed HLS format from yt-dlp.")

        if self.video_itag and \
        self._stream_value(self.video_itag, "itag") != self._stream_value(selected, "itag"):
            self.log.warning(
                "Muxed HLS itag changed from %s to %s while refreshing.",
                self._stream_value(self.video_itag, "itag"),
                self._stream_value(selected, "itag"),
            )

        self.video_itag = selected
        self.video_base_url = selected.get("url") or selected.get("manifest_url")

    def make_request_obj(
        self,
        url: str,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> Request:
        headers = self.session.headers.copy()
        if extra_headers:
            headers.update(extra_headers)
        req = Request(url, headers=headers)
        self.session.cookie_jar.add_cookie_header(req)
        return req

    def get_hls_playlist_segments(
        self,
        playlist_url: str,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> tuple[List[tuple[int, str]], bool]:
        """Load an HLS media playlist and return numbered segment URLs plus ENDLIST state."""
        req = self.make_request_obj(playlist_url, extra_headers=extra_headers)
        content = self.session.get_response_as_str(req)

        media_sequence = 0
        end_list = False
        segments: List[tuple[int, str]] = []
        seg_num: Optional[int] = None

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                media_sequence = int(line.rsplit(":", 1)[-1])
                seg_num = media_sequence
                continue
            if line == "#EXT-X-ENDLIST":
                end_list = True
                continue
            if line.startswith("#"):
                continue

            if seg_num is None:
                seg_num = media_sequence
            segments.append((seg_num, urljoin(playlist_url, line)))
            seg_num += 1

        return segments, end_list

    def download_hls_segment(
        self,
        segment_url: str,
        seg_num: int,
        stream_type: str = "video",
        extra_headers: Optional[Dict[str, str]] = None
    ) -> bool:
        """Download a single muxed-HLS segment into the native segment staging directory."""
        segment_filename = f'{self.video_outpath}{sep}{seg_num:0{10}}_video.ts'
        req = self.make_request_obj(segment_url, extra_headers=extra_headers)

        with closing(urlopen(req)) as in_stream:
            headers = in_stream.headers
            status = in_stream.status
            if status >= 204:
                self.log.debug(f"Seg {seg_num} {stream_type} URL: {segment_url}")
                self.log.debug(f"Seg status: {status}")
                self.log.debug(f"Seg headers:\n{headers}")

            if not self.write_to_file(in_stream, segment_filename):
                if status == 204 and headers.get('X-Segment-Lmt', "0") == "0":
                    raise EmptySegmentException(
                        f"Segment {seg_num} ({stream_type}) is empty, stream might have ended...")
                return False
        return True

    @staticmethod
    def build_hls_segment_url(segment_url: str, seg_num: int) -> Optional[str]:
        """Rewrite a known HLS segment URL to target another `/sq/<n>/` segment number."""
        updated = re.sub(r"/sq/\d+/", f"/sq/{seg_num}/", segment_url, count=1)
        if updated == segment_url:
            return None
        return updated

    def download_seg(self, baseurl: BaseURL, seg, type):
        """Download a single native DASH media segment to the appropriate staging file."""
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
            if status >= 204:
                self.log.debug(f"Seg {self.seg} {type} URL: {segment_url}")
                self.log.debug(f"Seg status: {status}")
                self.log.debug(f"Seg headers:\n{headers}")

            if not self.write_to_file(in_stream, segment_filename):
                if status == 204 and headers.get('X-Segment-Lmt', "0") == "0":
                    raise EmptySegmentException(\
                        f"Segment {self.seg} (video) is empty, stream might have ended...")
                return False
        return True

    def print_progress(self, seg: int) -> None:
        """Report the currently requested segment to stdout and to debug logs."""
        # TODO display rotating wheel in interactive mode
        fullmsg = f"Downloading segment {seg}..."
        if stdout.isatty():
            if ISWINDOWS:
                prev_len = getattr(self, '_report_progress_prev_line_length', 0)
                if prev_len > len(fullmsg):
                    fullmsg += ' ' * (prev_len - len(fullmsg))
                self._report_progress_prev_line_length = len(fullmsg)
                clear_line = '\r'
            else:
                clear_line = ('\r\x1b[K' if stderr.isatty() else '\r')

            print(clear_line + fullmsg, end='', flush=True)
        else:
            print(fullmsg, flush=True)

        self.log.debug(fullmsg)

    # OBSOLETE
    def print_found_quality(self, item, datatype):
        if datatype == "video":
            keys = ["itag", "qualityLabel", "mimeType", "bitrate", "quality", "fps"]
        else:
            keys = ["itag", "audioQuality", "mimeType", "bitrate", "audioSampleRate"]
        try:
            result = f"Available {datatype} quality: "
            for k in keys:
                result += f"{k}: {item.get(k)}\t"
            self.log.info(result)
        except Exception as e:
            self.log.critical(
                f"Exception while trying to print found {datatype} quality: {e}"
            )

    def print_available_streams(self, stream_list):
        if not self.log.isEnabledFor(logging.INFO):
            return
        for s in stream_list:
            self.log.info(
                "Available {}".format(s.__repr__().replace(' ', '\t'))
            )

    @property
    def player_config_args(self):
        return self.pytube_probe.get_player_config_args()

    @property
    def fmt_streams(self):
        """Return yt-dlp formats wrapped in a small stream-like interface."""
        if self._fmt_streams:
            return self._fmt_streams

        self._fmt_streams = []
        info = self.get_ytdlp_info()
        if not info:
            return self._fmt_streams

        for stream in info.get("formats", []):
            if not isinstance(stream, dict):
                continue
            if not stream.get("url"):
                continue
            self._fmt_streams.append(YTDLPStream(stream))

        return self._fmt_streams

    def get_best_streams(self, maxq=None, log=True, codec="mp4", fps="60"):
        """Return yt-dlp-backed video/audio stream wrappers for native DASH downloading."""
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
                self.log.warning(
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
        video_stream = next(
            (
                s for s in video_streams
                if s.has_video and not s.has_audio and s.is_dash
            ),
            None
        )
        if video_stream is None:
            video_stream = next(
                (s for s in video_streams if s.has_video and s.is_dash),
                video_streams.first()
            )

        if log:
            self.log.info(f"Selected video {video_stream}")

        audio_streams = avail_streams.filter(
            only_audio=True
            ) \
            .order_by('abr') \
            .desc()
        audio_stream = next(
            (s for s in audio_streams if s.is_dash),
            audio_streams.first()
        )

        if log:
            self.log.info(f"selected audio {audio_stream}")

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
            self.log.exception(e)
            buf = None

        if not buf:
            return False
        with open(fdst, 'wb') as out_file:
            fdst_write = out_file.write
            while buf:
                fdst_write(buf)
                buf = fsrc_read(length)
        return True

    def get_metadata_dict(self) -> Dict[str, Any]:
        """
        Get various information about the video.
        """
        # TODO add more data, refresh those that got stale
        thumbnails = self.pytube_probe.get_thumbnail_metadata()

        return {
                "url": self.url,
                "videoId": self.video_id,
                "cookiefile_path": self.session.cookiefile_path,
                "logger": self.log,
                "output_dir": self.output_dir,
                "title": self.title,
                "description": self._description,
                "author": self.author,
                "isLive": Status.LIVE | Status.VIEWED_LIVE in self.status,
                # We'll expect to get an array of thumbnails here
                "thumbnail": thumbnails
            }

    def trigger_hooks(self, event: str):
        hook_cmd = self.hooks.get(event, None)
        webhookfactory = self.notifier.get_webhook(event)

        if hook_cmd is not None or webhookfactory is not None:
            self.log.debug(f"Triggered event hook: {event}")

            # TODO if an event needs to refresh some data, update metadata here
            args = self.get_metadata_dict()

            if hook_cmd:
                hook_cmd.spawn_subprocess(args)

            if webhookfactory:
                if webhook := webhookfactory.get(args):
                    self.notifier.q.put(webhook)


class MPD():
    """Cache the URL to the manifest, but enable fetching if data is needed."""
    def __init__(self, parent: YoutubeLiveStream, mpd_type: str = "dash") -> None:
        self.parent = parent
        self.url = None
        self.content = None
        # self.expires: Optional[float] = None
        self.mpd_type = mpd_type  # dash or hls

    def update_url(self) -> Optional[str]:
        info = self.parent.get_ytdlp_info()
        if info:
            if self.mpd_type == "dash":
                if manifest_url := info.get("manifest_url"):
                    self.url = manifest_url
                    return self.url
                for fmt in info.get("formats", []):
                    if not isinstance(fmt, dict):
                        continue
                    if fmt.get("protocol") in ("http_dash_segments", "dash", "dashy") \
                    and fmt.get("manifest_url"):
                        self.url = fmt["manifest_url"]
                        return self.url
            else:
                if hls_url := info.get("url") or info.get("manifest_url"):
                    self.url = hls_url
                    return self.url

        mpd_type = "dashManifestUrl" if self.mpd_type == "dash" else "hlsManifestUrl"
        json = self.parent.get_info()

        if streamingData := json.get("streamingData", {}):
            if manifest_url := streamingData.get(mpd_type):
                self.url = manifest_url
                return self.url
            raise Exception(
                f"No URL found for MPD manifest of {self.parent.video_id}.")
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


class Status(Flag):
    OFFLINE = auto()
    AVAILABLE = auto()
    LIVE = auto()
    VIEWED_LIVE = auto()
    WAITING = auto()
    OK = AVAILABLE | LIVE | VIEWED_LIVE


def remove_useless_keys(json_response: Dict[str, Any]) -> None:
    """
    Modify dictionary passed in <json_response> in place by removing
    key we will never use to reduce output in log files.
    """
    for keyname in ['heartbeatParams', 'playerAds', 'adPlacements', 'playbackTracking',
    'annotations', 'playerConfig', 'storyboards',
    'trackingParams', 'attestation', 'messages', 'frameworkUpdates']:
        try:
            json_response.pop(keyname)
        except KeyError:
            continue

    # remove this annoying long list
    try:
        json_response.get('microformat', {})\
             .get('playerMicroformatRenderer', {})\
             .pop('availableCountries')
    except KeyError:
        pass
