#!/usr/bin/env python
from os import sep, path, makedirs, listdir
from sys import stderr
from platform import system
import logging
from datetime import date, datetime
from time import time, sleep
from json import dumps, dump, loads
from contextlib import closing
from enum import Flag, auto
from typing import Optional, Dict, Tuple, Union
from pathlib import Path
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
        self.parent: YoutubeLiveStream = kwargs["parent"]
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


class YoutubeLiveStream():
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
        self.max_video_quality: Optional[str] = max_video_quality
        self.video_id = video_id if video_id is not None \
                                 else extract.get_video_id(url)

        self._json: Dict = {}

        self.ptyt = PytubeYoutube(url, parent=self)

        self.video_itag = None
        self.audio_itag = None

        self._scheduled_timestamp = None
        self._start_time: Optional[str] = None

        self.video_base_url = None
        self.audio_base_url = None
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

    def get_first_segment(self, paths):
        """
        Create each path in paths. If one already existed, return the last
        segment already downloaded, otherwise return 1.
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
                self.logger.warning(f"An output directory already existed. \
We assume a failed download attempt. Last segment available was {seg}.")
                seg -= 1
        return seg


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
    def video_info(self):
        """Return current metadata for writing to disk as JSON."""
        info = {
            "id": self.video_id,
            "title": self.ptyt.title,
            "author": self.ptyt.author,
            "publish_date": str(self.ptyt.publish_date),
            "start_time": self.start_time,
            "download_date": date.fromtimestamp(time()).__str__(),
            "video_itag": self.video_itag,
            "audio_itag": self.audio_itag,
            "description": self.ptyt.description,
        }
        if self.scheduled_timestamp is not None:
            info["scheduled_time"] = datetime.utcfromtimestamp(
                self.scheduled_timestamp
            ).__str__()

        if self.video_itag:
            info["video_itag"] = self.video_itag.itag
            info["video_resolution"] = self.video_itag.resolution
        if self.audio_itag:
            info["audio_itag"] = self.audio_itag.itag
            info["audio_bitrate"] = self.audio_itag.abr
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
            self.logger.warning(f"Livestream {self.video_id} \
playability status is: {status} \
{playabilityStatus.get('reason', 'No reason found')}. Sub-reason: {subreason}")
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
        video_quality, audio_quality = self.get_best_streams(
            maxq=self.max_video_quality
        )

        self.logger.debug(
            f"Selected video itag {video_quality} / "
            f"Selected audio itag:{audio_quality}"
        )

        if ((self.video_itag is not None
        and self.video_itag != video_quality)
        or
        (self.audio_itag is not None
        and self.audio_itag != audio_quality)):
            # Probably should fail if we suddenly get a different format than the
            # one we had before to avoid problems during merging.
            self.logger.critical(
                "Got a different format after refresh of download URL!\n"
                f"Previous video itag: {self.video_itag}. New: {video_quality}.\n"
                f"Previous audio itag: {self.audio_itag}. New: {audio_quality}"
            )
            raise Exception("Format mismatch after update of base URL.")

        self.video_itag = video_quality
        self.audio_itag = audio_quality

        # self.video_base_url = ls_extract.get_base_url_from_itag(self.json, video_quality)
        # self.audio_base_url = ls_extract.get_base_url_from_itag(self.json, audio_quality)
        self.video_base_url = self.video_itag.url
        self.audio_base_url = self.audio_itag.url

        self.logger.debug(f"Video base url: {self.video_base_url}")
        self.logger.debug(f"Audio base url: {self.audio_base_url}")

    def download(self, wait_delay=2.0):
        self.seg = self.get_first_segment((self.video_outpath, self.audio_outpath))
        self.logger.info(f'Will start downloading from segment number {self.seg}.')

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
                    # force update
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
        if not self.video_base_url or not self.audio_base_url:
            raise Exception("Missing video or audio base url!")

        wait_sec = 60
        attempt = 0
        while True:
            try:
                video_segment_url = f'{self.video_base_url}&sq={self.seg}'
                audio_segment_url = f'{self.audio_base_url}&sq={self.seg}'
                self.print_progress(self.seg)

                # To have zero-padded filenames (not compatible with
                # merge.py from https://github.com/mrwnwttk/youtube_stream_capture
                # as it doesn't expect any zero padding )
                video_segment_filename = f'{self.video_outpath}{sep}{self.seg:0{10}}_video.ts'
                audio_segment_filename = f'{self.audio_outpath}{sep}{self.seg:0{10}}_audio.ts'

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
                        self.logger.debug(f"Seg {self.seg} URL: {video_segment_url}")
                        self.logger.debug(f"Seg status: {status}")
                        self.logger.debug(f"Seg headers:\n{headers}")

                    if not self.write_to_file(in_stream, video_segment_filename):
                        if status == 204 and headers.get('X-Segment-Lmt', "0") == "0":
                            raise exceptions.EmptySegmentException(\
                                f"Segment {self.seg} is empty, stream might have ended...")
                        self.logger.warning(f"Waiting for {wait_sec} seconds before retrying...")
                        sleep(wait_sec)
                        continue

                # urllib.request.urlretrieve(audio_segment_url, audio_segment_filename)
                with closing(urlopen(audio_segment_url)) as in_stream:
                    self.write_to_file(in_stream, audio_segment_filename)

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

    def print_available_streams(self, stream_list):
        if not self.logger.isEnabledFor(logging.INFO):
            return
        for s in stream_list:
            self.logger.info(
                "Available {}".format(s.__repr__().replace(' ', '\t'))
            )

    def get_best_streams(
        self,
        maxq: Union[str, None] = None,
        codec="mp4",
        fps="60") -> Tuple:
        """Return a tuple of pytube.Stream objects, first one for video
        second one for audio.
        If only progressive streams are available, the second item in tuple
        will be None.
        :param str maxq:
        :param str codec: mp4, webm
        :param str fps: 30, 60"""
        # FIXME needs improved selection criteria
        video_stream = None
        audio_stream = None

        def re_as_int(res_or_abr: str) -> Optional[int]:
            if res_or_abr is None:
                return None
            as_int = None
            # looks for "1080p" or "48kbps", either a resolution or abr
            if match := re.search(r"(\d{3,4})(p)?|(\d{2,4})(kpbs)?", res_or_abr):
                as_int = int(match.group(1))
            return as_int

        i_maxq = None
        if maxq is not None:
            i_maxq = re_as_int(maxq)
            # if match := re.search(r"(\d{3,4})(p)?|(\d{2,4}(kpbs)?", maxq):
            #     maxq = int(match.group(1))
            if i_maxq is None:
                self.logger.warning(
                    f"Max quality setting \"{maxq}\" is incorrect. "
                    "Defaulting to best video quality available."
                )
        elif isinstance(maxq, int):
            i_maxq = maxq

        custom_filters = None
        if i_maxq is not None:  # int
            def filter_maxq(s):
                res_int = re_as_int(s.resolution)
                if res_int is None:
                    return False
                return res_int <= i_maxq
            custom_filters = [filter_maxq]

        avail_streams = self.ptyt.streams
        self.print_available_streams(avail_streams)
        video_streams = avail_streams.filter(
            file_extension=codec,
            custom_filter_functions=custom_filters
        ).order_by('resolution').desc()

        video_stream = video_streams.first()
        self.logger.info(f"Selected Video {video_stream}")

        audio_streams = avail_streams.filter(
            only_audio=True
        ).order_by('abr').desc()

        audio_stream = audio_streams.first()
        self.logger.info(f"Selected Audio {audio_stream}")

        # FIXME need a fallback in case we didn't have an audio stream
        # TODO need a strategy if progressive has better audio quality:
        # use progressive stream's audio track only? Would that work with the
        # DASH stream video?
        if len(audio_streams) == 0:
            self.ptyt.streams.filter(
                progressive=False,
                file_extension=codec
            ).order_by('abr') \
             .desc() \
             .first()

        return (video_stream, audio_stream)

    def write_to_file(self, fsrc, fdst, length: int = 0) -> bool:
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
