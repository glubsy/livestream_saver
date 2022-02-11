from typing import List, Optional, Set, Dict, Union
from queue import LifoQueue
from types import MethodType
from datetime import datetime
from json import dumps
import re
import xml.etree.ElementTree as ET
import logging
log = logging.getLogger(__name__)
from livestream_saver.pytube import PytubeYoutube, PytubeStream
from livestream_saver import extract
from livestream_saver import util
from livestream_saver.constants import *
import pytube
# import yt_dlp


class BaseYoutubeVideo():
    """Interface to get information about a video from Youtube."""
    def __init__(self, url, session) -> None:
        self._url = url
        self._session = session
        self._scheduled_timestamp: Optional[int] = None
        self._start_time: Optional[str] = None
        self._author: Optional[str] = None
        self._publish_date: Optional[datetime] = None
        self._json: Optional[Dict] = {}

    @property
    def title(self) -> str:
        raise NotImplementedError
    
    @property
    def streams(self) -> List:
        raise NotImplementedError

    @property
    def json(self) -> Dict:
        raise NotImplementedError


class PytubeYoutubeVideo(BaseYoutubeVideo):
    """Wrapper around pytube YouTube object. Abstraction layer."""
    def __init__(self, url, session) -> None:
        super().__init__(url, session)
        self._yt = PytubeYoutube(url, parent=self, session=session)
        self.selected_streams: set[pytube.Stream] = set()

    @property
    def watch_url(self):
        return self._yt.watch_url

    @property
    def video_id(self):
        return self._yt.video_id

    @property
    def streams(self) -> List:
        return list(self._yt.streams)
        # return [type('BaseStream', (s,)) for s in self._yt.streams]

    def _pytube_streams(self) -> pytube.StreamQuery:
        streams = self._yt.streams
        # BUG in pytube, livestreams with resolution higher than 1080 do not 
        # return descriptions for their available streams, except in the 
        # DASH MPD manifest! These descriptions seem to re-appear after the 
        # stream has been converted to a VOD though.
        if len(streams) == 0:
            if mpd_streams := self.get_streams_from_mpd():
                log.warning(
                    "Could not find any stream descriptor in the response!"
                    f" Loaded streams from MPD instead.")
                log.debug(f"Streams from MPD: {mpd_streams}.")
                streams = pytube.StreamQuery(mpd_streams)
            else:
                raise Exception("Failed to load stream descriptors!")
        return streams 

    def filter_streams(
        self,
        vcodec: str = "mp4",
        acodec: str = "mp4",
        itags: Optional[str] = None,
        maxq: Optional[str] = None
    ) -> None:
        """Sets the selected_streams property to a Set of streams selected from
        user supplied parameters (itags, or max quality threshold)."""
        log.debug(f"Filtering streams: itag {itags}, maxq {maxq}")

        submitted_itags = util.split_by_plus(itags)

        selected_streams: Set[pytube.Stream] = set()
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
                log.warning(f"Invalid itags {invalid_itags} supplied.")
                wants_video = wants_audio = True

            submitted_itags = tuple(
                itag for itag in submitted_itags if itag not in invalid_itags
            )

        found_by_itags = set()
        itags_not_found = set()
        if submitted_itags:
            for itag in submitted_itags:
                # if available_stream := util.stream_by_itag(itag, self.streams):
                if available_stream := self._pytube_streams().get_by_itag(itag):
                    found_by_itags.add(available_stream)
                else:
                    itags_not_found.add(itag)
                    log.warning(
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
                log.warning(
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
            raise Exception(f"No stream assigned to {self._yt.video_id} object!")

    def _filter_streams(
        self,
        tracktype: str,
        codec: str,
        maxq: Optional[str] = None) -> pytube.Stream:
        """
        tracktype == video or audio
        codec == mp4, mov, webm...
        Coalesce filters depending on user-specified criteria.
        """
        if tracktype == "video":
            custom_filters = self._generate_custom_filter(maxq)
            criteria = "resolution"
        else:
            custom_filters = None
            criteria = "abr"

        q = LifoQueue(maxsize=5)
        log.debug(f"Filtering {tracktype} streams by type: \"{codec}\"")
        streams = self._yt.streams.filter(
            subtype=codec, type=tracktype
        )

        if len(streams) == 0:
            log.debug(
                f"No {tracktype} streams for type: \"{codec}\". "
                "Falling back to filtering without any criterium."
            )
            streams = self._yt.streams.filter(type=tracktype)

        log.debug(f"Pushing onto stack: {streams}")
        q.put(streams)

        # This one will usually be empty for livestreams anyway
        # NOTE the if statement is not really necessary, we could push
        # an empty query, it would not matter much in the end
        if progressive_streams := streams.filter(progressive=True):
            log.debug(
                f"Pushing progressive {tracktype} streams to stack: {progressive_streams}"
            )
            q.put(progressive_streams)

        # Prefer adaptive to progressive, so we do this last in order to
        # put on top of the stack and test it first
        if adaptive_streams := streams.filter(adaptive=True):
            log.debug(
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
            log.critical(
                f"Could not get a specified {tracktype} stream! "
            )
            selected_stream = self._yt.streams.filter(type=tracktype) \
                .order_by(criteria).desc().first()
            log.critical(
                f"Falling back to best quality available: {selected_stream}"
            )
        else:
            log.info(f"Selected {tracktype} stream: {selected_stream}")

        return selected_stream

    def _generate_custom_filter(self, maxq: Optional[str]) -> Optional[List]:
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
                log.warning(
                    f"Max resolution setting \"{maxq}\" is incorrect. "
                    "Defaulting to best video quality available."
                )
        elif isinstance(maxq, int):
            i_maxq = maxq

        custom_filters = None
        if i_maxq is not None:  # int
            def resolution_filter(s: pytube.Stream) -> bool:
                res_int = as_int_re(s.resolution)
                if res_int is None:
                    return False
                return res_int <= i_maxq

            def abitrate_filter(s: pytube.Stream) -> bool:
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

    def get_content_from_mpd(self):
        content = None
        if self.mpd is None:
            mpd = MPD(self)
            try:
                content = mpd.get_content()
            except Exception as e:
                log.critical(e)
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
            log.critical(f"Error loading XML of MPD: {e}")
            return streams

        for _as in root.findall("{*}Period/{*}AdaptationSet"):
            mimeType = _as.attrib.get("mimeType")
            for _rs in _as.findall("{*}Representation"):
                url = _rs.find("{*}BaseURL")
                if url is None:
                    log.debug(f"No BaseURL found for {_rs.attrib}. Skipping.")
                    continue
                # Simulate what pytube does in a very basic way
                # FIXME this can safely be removed in next pytube:
                codec = _rs.get("codecs")
                if not codec or not mimeType:
                    log.debug(f"No codecs key found for {_rs.attrib}. Skipping")
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
    def json(self) -> Dict:
        if self._json:
            return self._json
        if self._yt._vid_info:
            return self._yt._vid_info
        try:
            # json_string = extract.initial_player_response(self.ptyt.watch_html)
            # API request with ANDROID client gives us a pre-signed URL
            json_string = self._session.make_api_request(self._yt.video_id)
            self._json = extract.str_as_json(json_string)
            self._session.is_logged_out(self._json)

            util.remove_useless_keys(self._json)
            if log.isEnabledFor(logging.DEBUG):
                log.debug(
                    "Extracted JSON from html:\n"
                    + dumps(self._json, indent=4, ensure_ascii=False)
                )
        except Exception as e:
            log.debug(f"Error extracting JSON from HTML: {e}")
            self._json = {}

        if not self._json:
            log.critical(
                f"WARNING: invalid JSON for {self._yt.watch_url}: {self._json}")
            
            # self._status &= ~Status.AVAILABLE

        # HACK this function does the same as pytube.YouTube.vid_info() 
        # (except we can use our cookies here) so the json can be cached in the same place,   
        self._vid_info = self._json

        return self._json

    @property
    def title(self) -> str:
        return self._yt.title

    @property
    def author(self) -> Optional[str]:
        """Get the video author.
        :rtype: str
        """
        if self._author:
            return self._author
        self._author = self.json.get("videoDetails", {})\
                                .get("author", "Author?")
        return self._author

    @property
    def start_time(self) -> Optional[str]:
        if self._start_time:
            return self._start_time
        try:
            # String reprensentation in UTC format
            # self._start_time = self._yt.player_response \
            self._start_time = self.json \
                .get("microformat", {}) \
                .get("playerMicroformatRenderer", {}) \
                .get("liveBroadcastDetails", {}) \
                .get("startTimestamp", None)
        except Exception as e:
            log.debug(f"Error getting start_time: {e}")
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
            log.info(f"Found scheduledStartTime: {self._scheduled_timestamp}")
        except Exception as e:
            log.debug(f"Error getting scheduled_timestamp: {e}")
        return self._scheduled_timestamp

    @property
    def thumbnail_url(self):
        return self._yt.thumbnail_url

    @property
    def publish_date(self):
        return self._yt.publish_date

    @property
    def description(self):
        return self._yt.description

    def clear_attr(self, attrs: Union[List, str], value = None) -> None:
        """Reset attributes in the wrapper pytube object."""
        if isinstance(attrs, str):
            attrs = [attrs]
        for attr in attrs:
            if hasattr(self._yt, attr):
                setattr(self._yt, attr, value)

    @property
    def video_streams(self):
        return (s for s in self._pytube_streams() if s.includes_video_track())

    @property
    def audio_streams(self):
        return (s for s in self._pytube_streams() if s.includes_audio_track())


class BaseStream(PytubeStream):
    # For now our streams are a subclass of pytube.Stream
    pass



# class YTDLPYoutubeVideo(BaseYoutubeVideo):
#     def __init__(self, url, session) -> None:
#         self._yt = yt_dlp.YoutubeDL(ydl_opts)




class MPD():
    """Cache the URL to the manifest, but enable fetching it data if needed."""
    def __init__(self, parent: BaseYoutubeVideo, mpd_type: str = "dash") -> None:
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
