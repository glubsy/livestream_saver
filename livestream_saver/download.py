#!/usr/bin/env python
from os import path, makedirs, listdir
from sys import stderr
from platform import system
import logging
from datetime import date, datetime
from time import time, sleep
from json import dumps, dump, loads
from contextlib import closing
from enum import Flag, auto
from typing import Optional, Dict, Tuple, Union, List, Set
from pathlib import Path
from queue import LifoQueue
import re
from urllib.request import urlopen
import urllib.error
from http.client import IncompleteRead

import pytube
import pytube.cipher
from pytube import YouTube
import pytube.exceptions

from livestream_saver import exceptions
from livestream_saver import extract
from livestream_saver import util
from livestream_saver.request import YoutubeUrllibSession

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


class PytubeYoutube(YouTube):
    """Wrapper to override some methods in order to bypass several restrictions
    due to lacking features in pytube (most notably live stream support)."""
    def __init__(self, *args, **kwargs):
        # Keep a handle to update its status
        self.parent: YoutubeLiveBroadcast = kwargs["parent"]
        super().__init__(*args)
        # NOTE if "www" is omitted, it might force a redirect on YT's side
        # (with &ucbcb=1) and force us to update cookies again. YT is very picky
        # about that. Let's just avoid it.
        self.watch_url = f"https://www.youtube.com/watch?v={self.video_id}"

    def check_availability(self):
        """Skip this check to avoid raising pytube exceptions."""
        pass

    @property
    def watch_html(self):
        """Override for livestream_saver. We have to make the request ourselves
        in order to pass the cookies."""
        # TODO get the DASH manifest (MPD) instead?
        if self._watch_html:
            return self._watch_html
        try:
            self._watch_html = self.parent.session.make_request(url=self.watch_url)
        except Exception as e:
            self.parent.logger.debug(f"Error getting watch_url: {e}")
            self._watch_html = None

        return self._watch_html

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
        session: YoutubeUrllibSession,
        video_id: Optional[str] = None,
        max_video_quality: Optional[str] = None,
        log_level = logging.INFO) -> None:

        self.session = session
        self.url = url
        self.wanted_itags: Optional[Tuple] = None
        self.max_video_quality: Optional[str] = max_video_quality
        self.video_id = video_id if video_id is not None \
                                 else extract.get_video_id(url)

        self._json: Dict = {}

        self.ptyt = PytubeYoutube(url, parent=self)

        self.selected_streams: set[pytube.Stream] = set()
        self.video_stream = None
        self.video_itag = None
        self.audio_stream = None
        self.audio_itag = None

        self._scheduled_timestamp = None
        self._start_time: Optional[str] = None

        self.seg = 0
        self._status = Status.OFFLINE
        self.done = False
        self.error = None

        # Create output dir first in order to store log in it
        self.output_dir = output_dir
        if not self.output_dir.exists:
            util.create_output_dir(
                output_dir=output_dir, video_id=None
            )

        self.logger = setup_logger(self.output_dir, log_level, self.video_id)

        self.video_outpath = self.output_dir / 'vid'
        self.audio_outpath = self.output_dir / 'aud'

    @property
    def streams(self) -> pytube.StreamQuery:
        return self.ptyt.streams

    def print_available_streams(self, logger: logging.Logger = None) -> None:
        if logger is None:
            logger = self.logger
        for s in self.streams:
            logger.info(
                "Available {}".format(s.__repr__().replace(' ', '\t'))
            )

    def filter_streams(
        self,
        vcodec: str,
        acodec: str,
        itags: Optional[str] = None,
        maxq: Optional[str] = None,
    ) -> None:
        """Sets selected_streams property to a set of streams selected from
        user supplied parameters (itags, or max quality threshold)."""
        self.logger.debug(f"Filtering streams: itag {itags}, maxq {maxq}")

        submitted_itags = util.split_by_plus(itags)

        selected_streams = set()
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

    def _filter_streams(
        self,
        tracktype: str,
        codec: str,
        maxq: Optional[str] = None
    ) -> pytube.Stream:
        """
        tracktype == video or audio
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
            subtype=codec,
            type=tracktype
        )

        if len(streams) == 0:
            self.logger.debug(
                f"No {tracktype} streams for type: \"{codec}\". "
                "Falling back to removing selection criterium."
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
            def resolution_filter(s):
                res_int = as_int_re(s.resolution)
                if res_int is None:
                    return False
                return res_int <= i_maxq

            def abitrate_filter(s):
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
    def video_streams(self):
        return (s for s in self.streams if s.includes_video_track())

    @property
    def audio_streams(self):
        return (s for s in self.streams if s.includes_audio_track())

    # was is_live()
    def status(self, update=False) -> Status:
        """Check if the stream is still reported as being 'live' and update
        the status property accordingly."""
        if update:
            self.ptyt._watch_html = None
            self._json = {}
            # self._player_config_args = None

        if not self.json:
            raise Exception("Missing json data during status check")

        status = self._status

        isLive = self.json.get('videoDetails', {}).get('isLive')
        if isLive is not None and isLive is True:
            status |= Status.LIVE
        else:
            status &= ~Status.LIVE

        # Is this actually being streamed live?
        val = None
        for _dict in self.json.get('responseContext', {}) \
        .get('serviceTrackingParams', []):
            param = _dict.get('params', [])
            for key in param:
                if key.get('key') == 'is_viewed_live':
                    val = key.get('value')
                    break
        if val and val == "True":
            status |= Status.VIEWED_LIVE
        else:
            status &= ~Status.VIEWED_LIVE
        self._status = status
        self.logger.debug(f"is_live() status is now {status}")
        return status

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
        thumbnail_path = self.output_dir / 'thumbnail'
        if self.ptyt.thumbnail_url and not path.exists(thumbnail_path):
            with closing(urlopen(self.ptyt.thumbnail_url)) as in_stream:
                self.write_to_file(in_stream, thumbnail_path)

    def update_metadata(self) -> None:
        """Fetch various metadata and write them to disk."""
        if self.video_stream:
            if info := pytube.itags.ITAGS.get(self.video_stream):
                self.video_resolution = info[0]
        if self.audio_stream:
            if info := pytube.itags.ITAGS.get(self.audio_stream):
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

    def update_status(self):
        self.logger.debug("update_status()...")
        # force update
        self.status(update=True)

        self.logger.info("Stream seems to be viewed live. Good.") \
        if self._status & Status.VIEWED_LIVE else \
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


    # TODO get itag by quality first, and then update the itag download url
    # if needed by selecting by itag (the itag we have chosen by best quality)
    def update_download_urls(self):
        video_stream = self.get_best_stream(tracktype="video")

        try:
            audio_stream = self.get_best_stream(tracktype="audio")
        except Exception as e:
            # FIXME need a fallback in case we didn't have an audio stream
            # but that probably would rarely happen (if ever!).
            # In this case, we'll fall back to progressive streams, at the
            # expense of poorer video resolution.
            video_stream = None
            audio_stream = self.streams.filter(
                progressive=True,
                # file_extension="mp4"
            ).order_by('abr').desc().first()
            self.logger.critical(
                "No DASH audio stream found! Falling back to progressive stream..."
            )

        # TODO If a progressive track has a better audio quality track:
        # use progressive stream's audio track only?
        # This probably won't work with the DASH video stream, so we'll have
        # to download the progressive (video+audio) track only.

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

    def download(self, wait_delay: float = 2.0):
        self.seg = get_first_segment((self.video_outpath, self.audio_outpath))
        if self.seg > 0:
            self.logger.warning(
                "An output directory already existed. We assume a previously "
                "failed download attempt."
            )
        self.logger.info(f'Will start downloading from segment number {self.seg}.')

        # TODO get the current segment from the url, then start downloading
        # from that segment on (like ytdl), but also download from the start
        # in parallel until that segment we got from the server

        attempt = 0
        while not self.done and not self.error:
            try:
                self.update_status()
                self.logger.debug(f"Status is {self._status}.")

                if not self._status == Status.OK:
                    self.logger.critical(
                        f"Could not download \"{self.url}\": "
                        "stream unavailable or not a livestream.")
                    return

            except exceptions.WaitingException as e:
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

            self.update_download_urls()
            self.update_metadata()

            while True:
                try:
                    self.do_download()
                except (exceptions.EmptySegmentException,
                        exceptions.ForbiddenSegmentException,
                        IncompleteRead,
                        ValueError) as e:
                    self.logger.info(e)
                    # Force a status update
                    status = self.status(update=True)
                    if Status.LIVE | Status.VIEWED_LIVE in status:

                        if attempt >= 15:
                            self.logger.critical(
                                f"Too many attempts on segment {self.seg}. "
                                "Skipping it.")
                            self.seg += 1
                            attempt = 0
                            continue

                        self.logger.warning(
                            "It seems the stream has not really ended. "
                            f"Retrying in 10 secs... (attempt {attempt}/15)")
                        attempt += 1
                        sleep(10)
                        try:
                            self.update_download_urls()
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
        if self.error:
            self.logger.critical(
                f"Some kind of error occured during download? {self.error}"
            )

    def do_download(self):
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

        wait_sec = 60
        attempt = 0
        while True:
            try:
                video_segment_url = f'{self.video_stream.url}&sq={self.seg}'
                audio_segment_url = f'{self.audio_stream.url}&sq={self.seg}'
                self.print_progress(self.seg)

                # To have zero-padded filenames (not compatible with
                # merge.py from https://github.com/mrwnwttk/youtube_stream_capture
                # as it doesn't expect any zero padding )
                video_segment_filename = self.video_outpath / f'{self.seg:0{10}}_video.ts'
                audio_segment_filename = self.audio_outpath / f'{self.seg:0{10}}_audio.ts'

                # urllib.request.urlretrieve(video_segment_url, video_segment_filename)
                # # Assume stream has ended if last segment is empty
                # if stat(video_segment_filename).st_size == 0:
                #     unlink(video_segment_filename)
                #     break

                # TODO pass proper user-agent headers to server (construct Request)
                with closing(urlopen(video_segment_url)) as in_stream:
                    headers = in_stream.headers
                    status = in_stream.status
                    if self.logger.isEnabledFor(logging.DEBUG):
                        self.logger.debug(f"Seg {self.seg} (video) URL: {video_segment_url}")
                        self.logger.debug(f"Seg status: {status}")
                        self.logger.debug(f"Seg headers:\n{headers}")

                    if not self.write_to_file(in_stream, video_segment_filename):
                        if status == 204 and headers.get('X-Segment-Lmt', "0") == "0":
                            raise exceptions.EmptySegmentException(\
                                f"Segment {self.seg} (video) is empty, stream might have ended...")
                        self.logger.warning(f"Waiting for {wait_sec} seconds before retrying...")
                        sleep(wait_sec)
                        # FIXME perhaps update the base urls here to avoid
                        # hitting the same (unresponsive?) CDN server again?
                        continue

                # urllib.request.urlretrieve(audio_segment_url, audio_segment_filename)
                with closing(urlopen(audio_segment_url)) as in_stream:
                    headers = in_stream.headers
                    status = in_stream.status
                    if self.logger.isEnabledFor(logging.DEBUG):
                        self.logger.debug(f"Seg {self.seg} (audio) URL: {audio_segment_url}")
                        self.logger.debug(f"Seg status: {status}")
                        self.logger.debug(f"Seg headers:\n{headers}")

                    if not self.write_to_file(in_stream, audio_segment_filename):
                        if status == 204 and headers.get('X-Segment-Lmt', "0") == "0":
                            raise exceptions.EmptySegmentException(\
                                f"Segment {self.seg} (audio) is empty, stream might have ended...")
                        self.logger.warning(f"Waiting for {wait_sec} seconds before retrying...")
                        sleep(wait_sec)
                        # FIXME perhaps update the base urls here to avoid
                        # hitting the same (unresponsive?) CDN server again?
                        continue

                attempt = 0
                self.seg += 1

            except urllib.error.URLError as e:
                self.logger.critical(f'{type(e)}: {e}')
                if e.reason == 'Forbidden':
                    # Usually this means the stream has ended and parts
                    # are now unavailable.
                    raise exceptions.ForbiddenSegmentException(e.reason)
                if attempt >= 30:
                    raise e
                attempt += 1
                self.logger.warning(
                    f"Waiting for {wait_sec} seconds before retrying... "
                    f"(attempt {attempt}/30)")
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

    def write_to_file(self, fsrc, fdst: Path, length: int = 0) -> bool:
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

    # We need to make an independent logger (with no parent) in order to
    # avoid using the parent logger's handlers, although we are writing
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


def get_first_segment(paths: Tuple) -> int:
    """
    Create each path in paths. If one already existed, return the last
    segment already downloaded, otherwise return 1.
    :param paths: tuple of pathlib.Path
    """
    # If one of the directories exists, assume we are resuming a previously
    # failed download attempt.
    dir_existed = False
    for path in paths:
        try:
            makedirs(path, 0o766)
        except FileExistsError:
            dir_existed = True

    # The sequence number to start downloading from (acually starts at 0).
    seg = 0

    if dir_existed:
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
            seg -= 1
    return seg
