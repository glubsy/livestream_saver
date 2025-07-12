from typing import Optional, Any, List, Dict
from pathlib import Path
import logging
from dataclasses import dataclass, field

from livestream_saver.exceptions import TabNotFound, MissingVideoId
from livestream_saver.hooks import HookCommand
from livestream_saver.notifier import WebHookFactory, NotificationDispatcher
from livestream_saver.request import YoutubeUrllibSession
from livestream_saver.extract import get_browseId_from_json, initial_player_response

logger = logging.getLogger(__name__)



# Only raise a warning if that many Ids are missing from one returned list
# of Id to another (between two requests). It is expected that video Ids
# disappear from the front page as more Ids are added. It is only relevant to
# us if we are suddenly missing a lof of entries, as it may indicate that we
# have been logged out.
MISSING_THRESHOLD = 3


@dataclass(slots=True)
class VideoPost:
    videoId: str = field(init=True)
    title: str = field(init=False, default="No title")
    url: str = field(init=False)
    thumbnail: dict = field(init=False, default_factory=dict)
    isLiveNow: bool = field(init=False, default=False)
    isLive: bool = field(init=False, default=False)
    members_only: bool = field(init=False, default=False)
    upcoming: bool = field(init=False, default=False)
    startTime: Optional[str] = field(init=False, default=None)
    download_metadata: dict = field(init=False, default_factory=dict)
    channel_name: str = field(init=False)

    def __repr__(self) -> str:
        return self.videoId

    def __hash__(self) -> int:
        return hash(self.videoId)

    def get(self, value, default=None) -> Any:
        return getattr(self, value, default)

    def __getitem__(self, value, default=None) -> Any:
        return self.get(value, default)

    def __setitem__(self, key, value) -> None:
        setattr(self, key, value)

    @staticmethod
    def from_post(
        post: Dict, channel_name: str
    ) -> "VideoPost":
        if not isinstance(post, dict):
            raise TypeError(f"Expected type dict, got {type(post)}")

        if not (videoId := post.get('videoId')):
            raise MissingVideoId("Missing videoId in video post")

        video = VideoPost(videoId=videoId)
        video.thumbnail = post.get('thumbnail', {})
        video.isLiveNow = post.get('isLiveNow', False)
        video.isLive = post.get('isLive', False)

        badges = post.get('badges', [])
        if len(badges) and isinstance(badges[0], dict):
            label = badges[0].get("metadataBadgeRenderer", {}).get("label")
            if label and "Members only" in label:
                video.members_only = label

        for _item in post.get('title', {}).get('runs', []):
            if _item.get('text'): # assumes list with only one item
                video.title = _item.get('text')

        video.url = post.get('navigationEndpoint', {})\
                .get('commandMetadata', {})\
                .get('webCommandMetadata', {})\
                .get('url')

        if eventData := post.get('upcomingEventData', {}):
            # we can safely assume it is "upcoming"
            video.upcoming = True
            video.startTime = eventData.get('startTime')

        # Attempt to attach "live" and "upcoming" status from the response
        for _item in post.get('thumbnailOverlays', []):
            if status_renderer := _item.get('thumbnailOverlayTimeStatusRenderer', {}):
                if style := status_renderer.get('style'):
                    # This seems to be a decent indicator that it is currently LIVE
                    if style == 'LIVE':
                        video.isLiveNow = True
                    # This might be redundant with upcomingEventData key
                    elif style == 'UPCOMING':
                        video.upcoming = True
                if text := status_renderer.get('text'):
                    if runs := text.get('runs', []):
                        if len(runs) > 0 and runs[0].get('text') == 'LIVE':
                            # This indicates that it should be live in the future
                            video.isLive = True
                break
        # Another way to check if it is currently LIVE
        for _item in post.get('badges', []):
            if badge_renderer := _item.get('metadataBadgeRenderer', {}):
                if badge_renderer.get('label') == "LIVE NOW":
                    video.isLiveNow = True

        video.channel_name = channel_name
        return video




class YoutubeChannel:
    def __init__(
        self,
        URL: str,
        channel_id: str,
        session: YoutubeUrllibSession,
        notifier: NotificationDispatcher,
        output_dir: Optional[Path] = None,
        hooks: Optional[Dict] = None,
        name: Optional[str] = None
    ):
        self.session: YoutubeUrllibSession = session
        self.url = URL
        self._id = channel_id
        self._name: Optional[str] = name

        self._home_videos: List[VideoPost] = None
        self._public_videos: List[VideoPost] = None
        self._upcoming_videos: List[VideoPost] = None
        self._public_streams: List[VideoPost] = None
        self._community_videos: List[VideoPost] = None
        self._membership_videos: List[VideoPost] = None

        # Keep only one html page in memory at a time
        self._cached_html = None
        self._cached_html_tab = None

        # Keep only one json in memory at a time
        self._cached_json: Optional[Dict] = None

        # Shows the last type of json (Home tab, Community tab, etc.)
        # TODO We could also check the "selected" field in the json to detect
        # which tab was last retrieved.
        self._cached_json_tab: Optional[str] = None

        # These values define values to pass to the API in order to navigate
        # around the innertube API.
        self._endpoints: Optional[Dict] = None

        self.notifier: NotificationDispatcher = notifier
        self.hooks = hooks if hooks is not None else {}
        self._hooked_videos = []
        if not output_dir:
            output_dir = Path().cwd()
        self.output_dir = output_dir
        self.log = logger

    @property
    def cached_json(self) -> Dict:
        """
        The current tab data as JSON currently kept in memory.
        Initially, this gets data from the "Home" tab json.
        """
        if self._cached_json is None:
            self._cached_json = self.get_home_json(update=True)
        return self._cached_json

    def load_endpoints(self, reset=False) -> Dict[str, Any]:
        """
        Load various values from whatever cached json is in memory currently.
        If reset is True, the Home tab will be fetched and cached in memory.
        This relies on the cached_json property to return the Home tab JSON
        if none other are in memory.
        """
        if reset:
            self._cached_json = None
        self._endpoints = get_endpoints_from_json(self.cached_json)
        return self._endpoints

    @property
    def endpoints(self) -> Dict[str, Any]:
        if self._endpoints is None:
            self._endpoints = self.load_endpoints()
        return self._endpoints

    @property
    def id(self) -> str:
        _id = get_browseId_from_json(self.cached_json)

        if self._id != _id:
            self.log.warning(f"Replacing channel id \"{self._id}\" with \"{_id}\".")

        self._id = _id
        return _id

    @property
    def name(self) -> str:
        if self._name is None:
            self._name = self._get_channel_name()
        return self._name

    def _get_channel_name(self) -> str:
        """
        Get the name of the channel from the home JSON (once retrieved).
        """
        # FIXME this method pre-fetches the json if called before
        # TODO handle channel names which are not IDs
        # => get "videos" tab html page and grab externalId value from it?
        return self.cached_json.get('metadata', {})\
                .get('channelMetadataRenderer', {})\
                .get('title', 'Unknown channel name')

    def get_public_videos_html(self, update=False) -> str:
        # NOTE active livestreams are also displayed in /featured tab:
        # https://www.youtube.com/c/kamikokana/videos?view=2&live_view=501
        # NOTE this also seems to be equivalent to /streams
        if update or self._cached_html_tab != "public":
            self._cached_html = self.session.make_request(
                self.url + '/videos?view=2&live_view=501')
            self._cached_html_tab = "public"
        return self._cached_html

    def get_upcoming_videos_html(self, update=False) -> str:
        # https://www.youtube.com/c/kamikokana/videos\?view\=2\&live_view\=502
        # https://www.youtube.com/channel/UCoSrY_IQQVpmIRZ9Xf-y93g/videos?view=2&live_view=502
        # This video tab filtered list, returns public upcoming livestreams (with scheduled times)
        # BUG it seems there is a redirect to the public videos if there is
        # no scheduled upcoming live stream listed on the page.
        if update or self._cached_html_tab != "upcoming":
            self._cached_html = self.session.make_request(
                self.url + '/videos?view=2&live_view=502')
            self._cached_html_tab = "upcoming"
        return self._cached_html

    def get_featured_html(self, update=False) -> str:
        # NOTE "/live" virtual tab is a redirect to the current live broadcast
        # NOTE "featured" tab is ONLY reliable to get active live streams
        return self.session.make_request(self.url + '/featured')

    def get_community_videos_html(self, update=False) -> str:
        if update or self._cached_html_tab != "community":
            self._cached_html = self.session.make_request(
                self.url + '/community')
            self._cached_html_tab = "community"
        return self._cached_html

    def get_membership_videos_html(self, update=False) -> str:
        if update or self._cached_html_tab != "membership":
            self._cached_html = self.session.make_request(
                self.url + '/membership')
            self._cached_html_tab = "membership"
        return self._cached_html

    def get_home_json(self, update=False) -> str:
        """
        Fetch and cache the Home tab's json, grabbed from an initial request
        that returned the HTML page.
        This is probably similar to the /featured tab.
        This does not make an API request, only a simple GET.
        """
        if update or self._cached_json_tab != "Home":
            try:
                self._cached_json = initial_player_response(
                    self.session.make_request(self.url))
                # Probably similar to doing:
                # self.session.make_request(self.url + '/featured')
                self._cached_json_tab = "Home"
                # TODO we could check if we are logged in here
            except Exception as e:
                self.log.warning(f"Failed to get Home json from initial html: {e}")
                raise e
        return self._cached_json

    def get_json_and_cache(self, tab_name: str, update=False) -> Dict:
        """
        Return the parsed JSON response for a specified tab. The cache is
        overwritten on each request.
        Args:
            tab_name: either of "Videos", "Community", "Upcoming", etc.
            update: to force updating, otherwise will use the cached data
            unless it has been overwritten by a request for another tab prior.
        """
        if update or self._cached_json_tab != tab_name:
            # In the past we got it from the HTML page:
            # self._cached_json = initial_player_response(
            #     self.membership_videos_html)
            self._cached_json = self.get_tab_json_from_api(tab_name)
            self._cached_json_tab = tab_name
        return self._cached_json

    def get_home_videos(self) -> List[VideoPost]:
        """
        Return the currently listed videos from the Home tab.
        Note that Upcoming videos might both get listed in Home and the Live
        tab (for livestreams) and possibly the Videos tab (for premieres?).
        Note also that active Livestreams seem to be listed only in this tab,
        and not in the Live tab anymore.
        """
        home_videos = self.get_videos_from_tab(
            tabtype="Home",
            tabs=get_tabs_from_json(
                self.get_json_and_cache("Home", update=True)
            )
        )
        # Only after first request, print the full list of Ids retrieved
        if self._home_videos is None:
            logger.info(
                "Currently listed Featured videos: {}\n{}".format(
                    len(home_videos),
                    format_list_output(home_videos)
                )
            )
        else:
            new_videos, _ = self.get_changes(
                videos=home_videos,
                previous=self._home_videos
            )
            # TODO warn of removed live VODs (requires keeping memory of all
            # Ids in the channel by using Shelve or Sqlite)
            if new_videos:
                self.warn_of_new(
                    new_videos=new_videos,
                    name="featured")
        self._home_videos = home_videos
        return home_videos

    def get_public_videos(self) -> List[VideoPost]:
        """
        Return the currently listed videos from the Videos tab (VOD).
        Not super useful right now, but could be in the future if we ever wanted
        to scrape VOD, or record premiering videos as they are streamed.
        """
        public_videos = self.get_videos_from_tab(
            tabtype="Videos",
            tabs=get_tabs_from_json(
                self.get_json_and_cache("Videos", update=True)),
        )
        # Only after first request, print the full list of Ids retrieved
        if self._public_videos is None:
            logger.info(
                "Currently listed public videos: {}\n{}".format(
                    len(public_videos),
                    format_list_output(public_videos)
                )
            )
            for vid in public_videos:
                if vid.get("upcoming"):
                    self.trigger_hook('on_upcoming_detected', vid)
        else:
            new_videos, _ = self.get_changes(
                videos=public_videos,
                previous=self._public_videos
            )
            # TODO warn of removed live VODs (requires keeping memory of all
            # Ids in the channel by using Shelve or Sqlite)
            if new_videos:
                self.warn_of_new(
                    new_videos=new_videos,
                    name="public")
        self._public_videos = public_videos
        return public_videos

    def get_public_streams(self) -> List[VideoPost]:
        """
        Return the currently listed videos from the Live tab.
        """
        public_streams = self.get_videos_from_tab(
            tabtype="Live",
            tabs=get_tabs_from_json(
                self.get_json_and_cache("Live", update=True)),
        )
        # Only after first request, print the full list of Ids retrieved
        if self._public_streams is None:
            logger.info(
                "Currently listed public Live streams: {}\n{}".format(
                    len(public_streams),
                    format_list_output(public_streams)
                )
            )
            for vid in public_streams:
                if vid.get("upcoming"):
                    self.trigger_hook('on_upcoming_detected', vid)
        else:
            new_videos, _ = self.get_changes(
                videos=public_streams,
                previous=self._public_streams
            )
            # TODO warn of removed live VODs (requires keeping memory of all
            # Ids in the channel by using Shelve or Sqlite)
            if new_videos:
                self.warn_of_new(
                    new_videos=new_videos,
                    name="public Live stream")
        self._public_streams = public_streams
        return public_streams

    def get_community_videos(self) -> List[VideoPost]:
        community_videos = self.get_videos_from_tab(
            tabtype="Community",
            tabs=get_tabs_from_json(
                self.get_json_and_cache("Community", update=True)),
        )
        # Only after first request, print the full list of Ids retrieved
        if self._community_videos is None:
            logger.info(
                "Currently listed community videos: {}\n{}".format(
                    len(community_videos),
                    format_list_output(community_videos)
                )
            )
            for vid in community_videos:
                if vid.get("upcoming"):
                    self.trigger_hook('on_upcoming_detected', vid)
        else:
            new_videos, removed_videos = self.get_changes(
                videos=community_videos,
                previous=self._community_videos
            )
            if len(removed_videos) >= MISSING_THRESHOLD:
                self.warn_of_removed(
                    videos=community_videos,
                    removed_videos=removed_videos,
                    name="community")
            if new_videos:
                self.warn_of_new(
                    new_videos=new_videos,
                    name="community")
        self._community_videos = community_videos
        return community_videos

    def get_membership_videos(self) -> List[VideoPost]:
        _json = self.get_json_and_cache("Membership", update=True)
        self.session.is_logged_out(_json)
        membership_videos = self.get_videos_from_tab(
            tabtype="Membership",
            tabs=get_tabs_from_json(_json)
        )
        # Only after first request, print the full list of Ids retrieved
        if self._membership_videos is None:
            logger.info(
                "Currently listed membership videos: {}\n{}".format(
                    len(membership_videos),
                    format_list_output(membership_videos)
                )
            )
            for vid in membership_videos:
                if vid.upcoming:
                    self.trigger_hook('on_upcoming_detected', vid)
        else:
            new_videos, removed_videos = self.get_changes(
                videos=membership_videos,
                previous=self._membership_videos
            )
            if len(removed_videos) >= MISSING_THRESHOLD:
                self.warn_of_removed(
                    videos=membership_videos,
                    removed_videos=removed_videos,
                    name="membership")
            if new_videos:
                self.warn_of_new(
                    new_videos=new_videos,
                    name="membership")
        self._membership_videos = membership_videos
        return membership_videos

    def get_changes(
        self,
        videos: List[VideoPost],
        previous: List[VideoPost],
    ) -> tuple[list[VideoPost], list[VideoPost]]:
        """
        Return the changes in the list of video Ids compared to its previous state.
        """
        previous_ids = set(v["videoId"] for v in previous)
        new_videos = [
            v for v in videos if v["videoId"] not in previous_ids
        ]
        collected_ids = set(v["videoId"] for v in videos)
        removed_videos = list(filter(
            lambda x: x["videoId"] not in collected_ids, previous)
        )
        return new_videos, removed_videos

    def warn_of_removed(
        self,
        videos: List[VideoPost],
        removed_videos: List[VideoPost],
        name: str
    ) -> None:
        message = (
            f"Some video Ids are now missing from the {name} tab. "
            "This may indicate that we got logged out. "
            f"Out of the {len(videos)} returned Ids, we are now missing:\n"
            + "\n".join(
                f"{v['videoId']}: {v.get('title')}" for v in removed_videos
            )
        )
        logger.warning(message)
        self.notifier.send_email(
            subject="Channel monitor: tab is missing video Ids",
            message_text=(message)
        )

    def warn_of_new(
        self,
        new_videos: List[VideoPost],
        name: str
    ) -> None:
        logger.info(
            f"Newly added {name} video: {len(new_videos)}\n"
            f"{format_list_output(new_videos)}")

        for vid in new_videos:
            if vid.upcoming:
                self.trigger_hook('on_upcoming_detected', vid)
            if vid.isLiveNow or vid.isLive:
                continue
            # This should only trigger for VOD (non-live) videos
            self.trigger_hook('on_video_detected', vid)


    def trigger_hook(self, hook_name: str, vid: VideoPost):
        hook_cmd: Optional[HookCommand] = self.hooks.get(hook_name, None)
        webhookfactory: Optional[WebHookFactory] = self.notifier.get_webhook(hook_name)

        if hook_cmd is not None or webhookfactory is not None:
            self.update_metadata(vid)
            self.log.debug(f"Fetched metadata for vid: {vid}")

            if hook_cmd and not self.is_hooked_video(vid.get("videoId", None)):
                hook_cmd.spawn_subprocess(vid)

            if webhookfactory:
                if webhook := webhookfactory.get(vid):
                    self.notifier.q.put(webhook)

    def update_metadata(self, vid: VideoPost) -> None:
        """
        Update a VideoPost object with various matadata fetched from the API.
        """
        url = vid.get("url")
        vid.download_metadata.update(
            {
                "url": f"https://www.youtube.com{url}" if url is not None else None,
                "cookiefile_path": self.session.cookiefile_path,
                "logger": self.log,
                "output_dir": self.output_dir
            }
        )
        description = vid.get("description", "")
        if not description:
            json_d = self.fetch_video_metadata(vid)
            if not json_d:
                return

            self.log.debug(
                f"Got metadata JSON for videoId \"{vid.get('videoId', '')}\".")
            # if self.logger.isEnabledFor(logging.DEBUG):
            #     import pprint
            #     pprint.pprint(json_d, indent=4)

            vid["description"] = json_d.get('videoDetails', {})\
                                        .get("shortDescription", "")
            vid["author"] = json_d.get('videoDetails', {})\
                                    .get("author", "Author?")
            if isLive := json_d.get('videoDetails', {})\
                                    .get('isLiveContent', False):
                # This should overwrite the same value.
                vid["isLive"] = isLive
            # "This live event will begin in 3 hours."
            vid["liveStatus"] = json_d.get('playabilityStatus', {})\
                                        .get('reason')
            if liveStreamOfflineSlateRenderer := json_d\
                .get('playabilityStatus', {})\
                .get('liveStreamability', {})\
                .get('liveStreamabilityRenderer', {})\
                .get('offlineSlate', {})\
                .get('liveStreamOfflineSlateRenderer', {}):
                if mainTextruns := liveStreamOfflineSlateRenderer\
                    .get('mainText', {})\
                    .get('runs', []):
                    shortRemainingTime = ""
                    for text in mainTextruns:
                        # "Live in " + "3 hours"
                        shortRemainingTime += text.get('text', "")
                    vid["shortRemainingTime"] = shortRemainingTime

                if subtitleTextRuns := liveStreamOfflineSlateRenderer\
                    .get('subtitleText', {})\
                    .get('runs', []):
                    if localScheduledTime := subtitleTextRuns[0].get('text'):
                        # December 22, 11:00 AM GMT+9
                        vid["localScheduledTime"] = localScheduledTime

                if scheduledStartTime := liveStreamOfflineSlateRenderer\
                    .get('scheduledStartTime'):
                    # Timestamp, will overwrite
                    vid["startTime"] = scheduledStartTime
            # logger.debug(f"JSON fetched for video {vid}:\n{json_d}")

    def fetch_video_metadata(self, vid: Optional[VideoPost]) -> Optional[Dict]:
        """
        Fetch more details about a particular video Id.
        This is necessary for videos that we only know the Id of, but need the
        description to match some regex rules in order to trigger hooks.
        """
        if not vid:
            return None
        videoId = vid.get("videoId")
        if not videoId:
            return None

        logger.debug(f"Fetching extra info from API for {videoId=} ...")
        try:
            return self.session.make_api_request(
                endpoint="https://www.youtube.com/youtubei/v1/player",
                payload={
                    "videoId": videoId
                }
            )
        except Exception as e:
            logger.warning(f"Error fetching metadata for {videoId=}: {e}")

        # Fallback: fetch from regular HTML page
        if url := vid.get("url"):
            logger.warning(
                f"Fetching {videoId=} info from HTML page because it failed through the API...")
            try:
                html_page = self.session.make_request(url)
                return initial_player_response(html_page)
            except Exception as e:
                logger.error(f"Error fetching metadata for video {videoId}: {e}")

    def is_hooked_video(self, videoId: Optional[str]):
        """Keep track of the last few videos for which we have triggered a hook
        command already, in a circular buffer to avoid growing infinitely and
        triggering again for the same video."""
        if not videoId:
            # ignore if missing entry, avoid calling hook
            return True
        if videoId in self._hooked_videos:
            return True
        self._hooked_videos.append(videoId)
        # Limit the buffer conserve memory
        if len(self._hooked_videos) >= 40:
            self._hooked_videos.pop(0)

    def filter_videos(
        self,
        filter_type: str = 'isLiveNow'
    ) -> List[VideoPost]:
        """
        Return a list of videos that are live, from all channel tabs combined.
        There may be more than one active broadcast.
        """
        # Only collect videos for which the field has a value
        filtered_videos = DedupedVideoList()
        missing_endpoints = []

        # We should call this first since we probably have the Home tab in memory
        # as the first cached json data
        try:
            for home_vid in self.get_home_videos():
                if home_vid.get(filter_type):
                    filtered_videos.append(home_vid)
        except TabNotFound as e:
            # Doubt this would ever happen
            missing_endpoints.append("Home")

        try:
            for comm_vid in self.get_community_videos():
                if comm_vid.get(filter_type):
                    filtered_videos.append(comm_vid)
        except TabNotFound:
            # self.log.debug(f"No Community tab available for this channel: {e}")
            missing_endpoints.append("Community")

        try:
            for memb_vid in self.get_membership_videos():
                if memb_vid.get(filter_type):
                    filtered_videos.append(memb_vid)
        except TabNotFound:
            # self.log.debug(f"No membership tab available for this channel: {e}")
            # This tab might also be missing if logged in user is simply not a member
            if self.session.was_logged_in:
                self.log.warning(
                    f"Missing expected Membership tab: We might be logged out!")

        # TODO this should be removed if filter is isLiveNow since Youtube does not
        # list livestreams in the Videos tab anymore. Keep for now just in case.
        public_videos = []
        try:
            public_videos = self.get_public_videos()
        except TabNotFound as e:
            # Some channels do no have a Videos tab (only Live tab).
            # self.log.debug(f"No Videos tab available for this channel: {e}")
            missing_endpoints.append("Videos")

        public_streams = []
        try:
            public_streams = self.get_public_streams()
        except TabNotFound as e:
            # self.log.debug(f"No Live tab available for this channel: {e}")
            missing_endpoints.append("Live")

        if missing_endpoints:
            self.log.debug(
                f"Reloading endpoints because \"{', '.join(missing_endpoints)}\""
                " tab data was missing, hoping it will appear at some point...")
            self.load_endpoints()

        # No need to check for "upcoming_videos" because live videos should
        # appear in the public videos list.
        for video in public_videos + public_streams:
            if video.get(filter_type):
                filtered_videos.append(video)
        return list(filtered_videos)

    def get_tab_json_from_api(self, tab_name: str) -> Optional[Dict]:
        """
        Return the parsed JSON response (as dict) for a specific endpoint,
        which shouldd be dereferenced by its tab name in most cases
        (ie. Home, Videos, Live, Community, Membership)
        """
        endpoint = self.endpoints.get(tab_name)
        if not endpoint:
            if tab_name == "Upcoming":
                raise TabNotFound("No Upcoming endpoint found{}".format(
                    ", but Live tab found"
                    if "Live" in self.endpoints.keys() else "")
                )
            else:
                raise TabNotFound(f"No endpoint found for tab named {tab_name}")

        browseEndpoint = endpoint.get("browseEndpoint")
        canonicalBaseUrl = browseEndpoint.get("canonicalBaseUrl", "")
        webCommandMetadata = endpoint.get("commandMetadata", {})\
                                        .get("webCommandMetadata", {})
        apiUrl = webCommandMetadata.get("apiUrl")
        url = webCommandMetadata.get("url")
        self.log.debug(f"Getting videos from the {tab_name} tab data...")

        return self.session.make_api_request(
            endpoint=f"https://www.youtube.com{apiUrl}",
            custom_headers={
                "referer": f"https://www.youtube.com{canonicalBaseUrl}/videos"
            },
            payload={
                "context": {
                    "client": {
                        "mainAppWebInfo": {
                            "graftUrl": url,
                        }
                    }
                },
                "browseId": browseEndpoint.get("browseId"),
                "params": browseEndpoint.get("params")
            },
            client="web"
        )

    def get_videos_from_tab(self, tabtype: str, tabs: List) -> List[VideoPost]:
        """
        Return videos attached to posts in available "tab" section in JSON response.
        tabtype is either "Videos" "Community", "Membership", "Home" etc.
        """
        # The format depends on the client (user-agent) used to make the
        # request, so either grid_renderer or list_renderer will be used.
        for tab in tabs:
            if tab.get('tabRenderer', {}).get('title') != tabtype:
                continue

            if richGridRenderer := tab.get('tabRenderer', {})\
                        .get('content', {})\
                        .get('richGridRenderer'):
                return _get_content_from_grid_renderer(
                    tabtype,
                    richGridRenderer.get('contents', []),
                    self)

            # This is the way the Home tab renders
            if sectionListRenderer := tab.get('tabRenderer', {})\
                        .get('content', {})\
                        .get('sectionListRenderer'):
                return _get_content_from_list_renderer(
                    tabtype,
                    sectionListRenderer.get('contents', []),
                    self)

            raise Exception(f"No valid content renderer found for \"{tabtype=}\".")
        return []


class DedupedVideoList(list):
    def __init__(self) -> None:
        self.seen_ids = set()
        self.duplicates = set()  # seen more than once

    def append(self, video: VideoPost) -> None:
        videoId = video.get("videoId")

        if videoId in self.seen_ids:
            self.duplicates.add(videoId)
        else:
            self.seen_ids.add(videoId)
            super().append(video)


def _get_content_from_grid_renderer(
    tabtype: str,
    contents: List,
    channel: YoutubeChannel
) -> List[VideoPost]:
    """
    Parse gridVideoRenderer for video posts.
    """
    assert tabtype in ("Videos", "Live")
    videos = DedupedVideoList()
    for content in contents:
        if __item := content.get('richItemRenderer', {}).get('content', {}):
            if tabtype == "Videos" or tabtype == "Live":
                # gridVideoRenderer might be obsolete
                griditems = __item.get('gridVideoRenderer', {}).get('items', [])
                for griditem in griditems:
                    if data :=  griditem.get('gridVideoRenderer'):
                        try:
                            vid_metadata = VideoPost.from_post(data, channel.name)
                        except Exception as e:
                            logger.debug(e)
                        else:
                            videos.append(vid_metadata)

                # New structure
                if data := __item.get("videoRenderer"):
                    try:
                        vid_metadata = VideoPost.from_post(data, channel.name)
                    except Exception as e:
                        logger.debug(e)
                    else:
                        videos.append(vid_metadata)
    if videos.duplicates:
        logger.debug(
            f"Filtered duplicate video Ids from tab {tabtype}: {videos.duplicates}")
    return list(videos)


def _get_content_from_list_renderer(
    tabtype: str,
    contents: List,
    channel: YoutubeChannel
) -> List[VideoPost]:
    """
    Parse sectionListRenderer for video posts.
    """
    videos = DedupedVideoList()
    for content in contents:
        for __item in content.get('itemSectionRenderer', {}).get('contents', []):

            # These two tab types appear to share the same architecture
            if tabtype in ("Community", "Membership"):
                post = __item.get('backstagePostThreadRenderer', {}).get('post', {})
                if post:
                    if backstageAttachment := post\
                        .get('backstagePostRenderer', {})\
                        .get('backstageAttachment', {}):
                        if videoRenderer := backstageAttachment\
                            .get('videoRenderer', {}):
                            # some posts don't have attached videos
                            try:
                                vid_metadata = VideoPost.from_post(
                                    videoRenderer, channel.name)
                            except Exception as e:
                                logger.debug(e)
                            else:
                                videos.append(vid_metadata)
                # In some cases the video is directly listed as its own item:
                elif videoRenderer := __item.get('videoRenderer', {}):
                    try:
                        vid_metadata = VideoPost.from_post(
                            videoRenderer, channel.name)
                    except Exception as e:
                        logger.debug(e)
                    else:
                        videos.append(vid_metadata)

            elif tabtype == "Videos":
                griditems = __item.get('gridRenderer', {}).get('items', [])
                for griditem in griditems:
                    try:
                        vid_metadata = VideoPost.from_post(
                            griditem.get('gridVideoRenderer'), channel.name)
                    except Exception as e:
                        logger.debug(e)
                    else:
                        videos.append(vid_metadata)

            elif tabtype == "Home":
                cfcRenderer = __item.get(
                    'channelFeaturedContentRenderer', {}).get('items', [])
                for cfcItem in cfcRenderer:
                    if videoRenderer := cfcItem.get('videoRenderer'):
                        try:
                            vid_metadata = VideoPost.from_post(
                                videoRenderer, channel.name)
                        except Exception as e:
                            logger.debug(e)
                        else:
                            videos.append(vid_metadata)

            # elif tabtype == "Live":

    if videos.duplicates:
        logger.debug(
            f"Filtered duplicate video Ids from tab {tabtype}: {videos.duplicates}")

    return list(videos)




def get_tabs_from_json(_json) -> Optional[List]:
    if not _json:
        return _json
    if tabs := _json.get('contents', {}) \
                .get('twoColumnBrowseResultsRenderer', {}) \
                .get('tabs', []):
        return tabs
    if tabs := _json.get('contents', {}) \
                .get('singleColumnBrowseResultsRenderer', {}) \
                .get('tabs', []):
        return tabs
    return None


def rss_from_id(channel_id):
    # This endpoint doesn't show member streams.
    # It does show public streams, but we don't know it's a stream until we start
    # actually downloading it.
    # WARNING: this seems to be a legacy API, might get deprecated someday!
    return 'https://www.youtube.com/feeds/videos.xml?channel_id=' + channel_id


def rss_from_name(channel_name):
    return 'https://www.youtube.com/feeds/videos.xml?user=' + channel_name


def format_list_output(vid_list: List[VideoPost]) -> str:
    return "\n".join(
        (
            f"{vid.get('videoId')} - {vid.get('title')}"
            f"{' (LiveStream)' if vid.get('isLive') else ''}"
            f"{' LIVE NOW!' if vid.get('isLiveNow') else ''}"
            f"{' (Upcoming)' if vid.get('upcoming') else ''}"
        )
            for vid in vid_list
    )


def get_endpoints_from_json(json: Dict) -> Dict[str, Any]:
    """
    Retrieve the endpoints (browseId+params) to navigate the innertube API.
    Typically the endpoints should look like this:
    {
        "Live": {
            "commandMetadata": {
                "webCommandMetadata": {
                    "url": "/c/channelname/featured",
                    "apiUrl": "/youtubei/v1/browse",
                }
            },
            "browseEndpoint": {
                "browseId": "XXX",
                "params": "XXX",
                "canonicalBaseUrl": "/c/channelname"
            }
        },
        ...
    }
    """
    endpoints = {}
    tabs = json.get("contents", {})\
                .get("twoColumnBrowseResultsRenderer", {})\
                .get("tabs", [])
    for tab in tabs:
        tabRenderer = tab.get("tabRenderer", {})
        title = tabRenderer.get("title")

        if tabRenderer.get("selected"):
            logger.debug(f"Parsing json of selected tab: \"{title}\"...")

        _endpoint = tabRenderer.get("endpoint")
        if not tabRenderer or not title or not _endpoint:
            continue
        endpoints[title] = _endpoint

        # The Home tab may have a filter to "upcoming live streams", if not,
        # then they should appear in the Live tab, among the other VOD
        # We store the value if we have it to do the request separately if needed
        if title == "Home":
            for content in tabRenderer.get("content", {})\
                                      .get("sectionListRenderer", {})\
                                      .get("contents", []):
                if runs := content.get("itemSectionRenderer", {})\
                        .get("shelfRenderer", {})\
                        .get("title", {})\
                        .get("runs"):
                    for run in runs:
                        # We could also store the webCommandMetadata in case we need
                        # to do regular request through URL
                        if run.get("text") == "Upcoming live streams":
                            endpoints["Upcoming"] = run.get("navigationEndpoint")
                            break
    return endpoints
