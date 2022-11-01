from pathlib import Path
from time import sleep
from random import uniform
import logging
from typing import Optional, Any, List, Dict
from livestream_saver import extract
from livestream_saver.exceptions import TabNotFound
from livestream_saver.hooks import HookCommand
from livestream_saver.notifier import WebHookFactory

logger = logging.getLogger(__name__)


class YoutubeChannel:
    def __init__(self, URL, channel_id, session, notifier,
        output_dir: Path = Path(), hooks={}
    ):
        self.session = session
        self.url = URL
        self._id = channel_id
        self.channel_name = "N/A"

        self._public_videos = None
        self._upcoming_videos = None
        self._public_streams = None
        self._community_videos = None
        self._membership_videos = None

        # Keep only one html page in memory at a time
        self._cached_html = None
        self._cached_html_tab = None

        # Keep only one json in memory at a time
        self._cached_json = None
        # Shows the last type of json (Home tab, Community tab, etc.)
        # We could also check the "selected" field in the json to detect what
        # tab was last retrieved.
        self._cached_json_tab = None

        self._endpoints = None

        self.notifier = notifier
        self.hooks = hooks
        self._hooked_videos = []
        self.output_dir = output_dir
        self.log = logger

    def load_endpoints(self) -> None:
        """
        Load params values to navigate through the innertube API.
        This essentially gets the values from the Home tab json.
        """
        if not self._cached_json:
            self.get_home_json()
        self._endpoints = get_endpoints_from_json(self._cached_json)

    @property
    def id(self) -> str:
        if self._cached_json is None:
            cached_json = self.get_home_json(update=True)
        _id = extract.get_browseId_from_json(cached_json)
        if self._id != _id:
            self.log.warning(f"Replacing channel id \"{self._id}\" with \"{_id}\".")
        self._id = _id
        return _id

    def get_channel_name(self) -> Optional[str]:
        """
        Get the name of the channel from the home JSON (once retrieved).
        """
        # FIXME this method pre-fetches the json if called before
        # TODO handle channel names which are not IDs
        # => get "videos" tab html page and grab externalId value from it?
        if not self._cached_json:
            self.get_home_json()

        if self._cached_json:
            self.channel_name = self._cached_json.get('metadata', {})\
                .get('channelMetadataRenderer', {})\
                .get('title')
        return self.channel_name

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
        This is probably similar to the /featured tab?
        """
        if update or self._cached_json_tab != "Home":
            try:
                self._cached_json = extract.initial_player_response(
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
            # self._cached_json = extract.initial_player_response(
            #     self.membership_videos_html)
            self._cached_json = self.get_tab_json_from_api(tab_name)
            self._cached_json_tab = tab_name
        return self._cached_json

    def get_public_videos(self, update=False) -> List[Dict]:
        """
        Return the currently listed videos from the Videos tab (VOD).
        Not super useful right now, but could be in the future.
        """
        public_videos = []
        if update or self._public_videos is None:
            public_videos = get_videos_from_tab(
                get_tabs_from_json(
                    self.get_json_and_cache("Videos", update=True)),
                'Videos'
            )

        # Occurs on the first time
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
            known_ids = [v["videoId"] for v in self._public_videos]
            new_pub_videos = [
                v for v in public_videos if v["videoId"] not in known_ids
            ]
            if new_pub_videos:
                logger.info(
                    "Newly added public video: {}\n{}".format(
                        len(new_pub_videos),
                        format_list_output(new_pub_videos)
                    )
                )
                for vid in new_pub_videos:
                    if vid.get("upcoming"):
                        self.trigger_hook('on_upcoming_detected', vid)
                    if vid.get("isLiveNow") or vid.get("isLive"):
                        continue
                    # This should only trigger for VOD (non-live) videos
                    self.trigger_hook('on_video_detected', vid)
        self._public_videos = public_videos
        return self._public_videos

    def get_upcoming_videos(self, update=False) -> List[Dict]:
        """
        Supposed to return upcoming Live Streams (or Premieres?),
        but the site will redirect to public videos if there is none found.

        This is probably about to become useless since it is made redundant by
        the fact that upcoming streams are also listed in the /streams tab, so
        getting those specifically could be done by filtering all videos
        in the filter_videos() method.
        This is not a filter method that returns all upcoming videos from all
        tabs combined!
        """
        # TODO make this method into a filter over videos from all tabs combined?
        upcoming_videos = []
        if update or self._upcoming_videos is None:
            try:
                # We can force update this one since it does not depend on any
                # other cached data
                upcoming_videos = get_videos_from_tab(
                    get_tabs_from_json(
                        self.get_json_and_cache("Upcoming", update=True)),
                    "Videos"
                )
            except Exception as e:
                self.log.debug(
                    f"Failed to get upcoming videos from Videos tab: {e}")
                return upcoming_videos

        # Make sure we only list upcoming videos and not public VODs due to redirect
        upcoming_videos_filtered = []
        for vid in upcoming_videos:
            if not vid.get('upcoming'):
                continue
            upcoming_videos_filtered.append(vid)
        upcoming_videos = upcoming_videos_filtered

        # Occurs on the first time
        if self._upcoming_videos is None:
            logger.info(
                "Currently listed public upcoming videos: {}\n{}".format(
                    len(upcoming_videos),
                    format_list_output(upcoming_videos)
                )
            )
            for vid in upcoming_videos:
                # These checks are important to avoid the youtube bug that
                # return VODs if there is no upcoming video at all.
                # FIXME perhaps API calls might help filter these better,
                # otherwise we could keep track of public videoIds and
                # ignore them.
                # if vid.get('upcoming') and vid.get('isLive'):
                self.trigger_hook('on_upcoming_detected', vid)
        else:
            known_ids = [v["videoId"] for v in self._upcoming_videos]
            new_upcoming_videos = [
                v for v in upcoming_videos if v["videoId"] not in known_ids
            ]
            if new_upcoming_videos:
                logger.info(
                    "Newly added upcoming videos: {}\n{}".format(
                        len(new_upcoming_videos),
                        format_list_output(new_upcoming_videos)
                    )
                )
                for vid in new_upcoming_videos:
                    # These checks are important to avoid the youtube bug that
                    # return VOD here as well.
                    if vid.get('upcoming'): # and vid.get('isLive'):
                        self.trigger_hook('on_upcoming_detected', vid)
        self._upcoming_videos = upcoming_videos
        return self._upcoming_videos

    def get_public_streams(self, update=False) -> List[Dict]:
        """
        Return the currently listed videos from the Live tab.
        """
        public_streams = []
        if update or self._public_streams is None:
            public_streams = get_videos_from_tab(
                get_tabs_from_json(
                    self.get_json_and_cache("Live", update=True)),
                "Live"
            )

        # Occurs on the first time
        if self._public_streams is None:
            logger.info(
                "Currently listed public streams: {}\n{}".format(
                    len(public_streams),
                    format_list_output(public_streams)
                )
            )
            for vid in public_streams:
                if vid.get("upcoming"):
                    self.trigger_hook('on_upcoming_detected', vid)
        else:
            known_ids = [v["videoId"] for v in self._public_streams]
            new_pub_videos = [
                v for v in public_streams if v["videoId"] not in known_ids
            ]
            if new_pub_videos:
                logger.info(
                    "Newly added public streams: {}\n{}".format(
                        len(new_pub_videos),
                        format_list_output(new_pub_videos)
                    )
                )
                for vid in new_pub_videos:
                    if vid.get("upcoming"):
                        self.trigger_hook('on_upcoming_detected', vid)
                    if vid.get("isLiveNow") or vid.get("isLive"):
                        continue
                    # This should only trigger for VOD (non-live) videos
                    self.trigger_hook('on_video_detected', vid)
        self._public_streams = public_streams
        return self._public_streams

    def get_community_videos(self, update=False) -> List[Dict]:
        community_videos = []
        if update or self._community_videos is None:
            community_videos = get_videos_from_tab(
                get_tabs_from_json(
                    self.get_json_and_cache("Community", update=True)),
                "Community"
            )

        # Occurs on the first time
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
            known_ids = [v["videoId"] for v in self._community_videos]
            new_comm_videos = [
                v for v in community_videos if v["videoId"] not in known_ids
            ]
            if new_comm_videos:
                logger.info(
                    "Newly added community video: {}\n{}".format(
                        len(new_comm_videos),
                        format_list_output(new_comm_videos)
                    )
                )
                for vid in new_comm_videos:
                    if vid.get("upcoming"):
                        self.trigger_hook('on_upcoming_detected', vid)
                    if vid.get("isLiveNow") or vid.get("isLive"):
                        continue
                    # Although rare, this should trigger for VODs only
                    self.trigger_hook('on_video_detected', vid)
        self._community_videos = community_videos
        return self._community_videos

    def get_membership_videos(self, update=False) -> List[Dict]:
        membership_videos = []
        if update or self._membership_videos is None:
            _json = self.get_json_and_cache("Membership", update=True)
            self.session.is_logged_out(_json)
            membership_videos = get_videos_from_tab(
                get_tabs_from_json(_json), "Membership")

        # Occurs on the first time
        if self._membership_videos is None:
            logger.info(
                "Currently listed membership videos: {}\n{}".format(
                    len(membership_videos),
                    format_list_output(membership_videos)
                )
            )
            for vid in membership_videos:
                if vid.get("upcoming"):
                    self.trigger_hook('on_upcoming_detected', vid)
        else:
            known_ids = [v["videoId"] for v in self._membership_videos]
            new_membership_videos = [
                v for v in membership_videos if v["videoId"] not in known_ids
            ]
            if new_membership_videos:
                logger.info(
                    "Newly added membership video: {}\n{}".format(
                        len(new_membership_videos),
                        format_list_output(new_membership_videos)
                    )
                )
                for vid in new_membership_videos:
                    if vid.get("upcoming"):
                        self.trigger_hook('on_upcoming_detected', vid)
                    if vid.get("isLiveNow") or vid.get("isLive"):
                        continue
                    # Although rare, this should trigger for VODs only
                    self.trigger_hook('on_video_detected', vid)
        self._membership_videos = membership_videos
        return self._membership_videos

    def trigger_hook(self, hook_name: str, vid: Dict):
        hook_cmd: Optional[HookCommand] = self.hooks.get(hook_name, None)
        webhookfactory: Optional[WebHookFactory] = self.notifier.get_webhook(hook_name)

        if hook_cmd is not None or webhookfactory is not None:
            self.get_metadata_dict(vid)
            self.log.debug(f"Fetched metadata for vid: {vid}")

            if hook_cmd and not self.is_hooked_video(vid.get("videoId", None)):
                hook_cmd.spawn_subprocess(vid)

            if webhookfactory:
                if webhook := webhookfactory.get(vid):
                    self.notifier.q.put(webhook)

    def get_metadata_dict(self, vid: Dict) -> Dict:
        """
        Update vid with various matadata fetched from the API.
        """
        # TODO make vid a full-fledged class!
        url = vid.get("url")
        vid.update({
            "url": f"https://www.youtube.com{url}" if url is not None else None,
            "cookie_path": self.session.cookie_path,
            "logger": self.log,
            "output_dir": self.output_dir
            }
        )
        description = vid.get("description", "")
        if not description:
            json_d = self.fetch_video_metadata(vid)
            if not json_d:
                return vid
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
        return vid

    def fetch_video_metadata(self, vid: Optional[Dict]) -> Optional[Dict]:
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
                return extract.initial_player_response(html_page)
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

    def filter_videos(self, filter_type: str = 'isLiveNow', update=True) -> List:
        """Returns a list of videos that are live, from all channel tabs combined.
        Usually there is only one live video active at a time.
        """
        live_videos = []
        missing_endpoint = []
        try:
            for vid in self.get_community_videos(update=update):
                if vid.get(filter_type):
                    live_videos.append(vid)
        except TabNotFound as e:
            # self.log.debug(f"No Community tab available for this channel: {e}")
            missing_endpoint.append("Community")

        try:
            for vid in self.get_membership_videos(update=update):
                if vid.get(filter_type):
                    live_videos.append(vid)
        except TabNotFound as e:
            # self.log.debug(f"No membership tab available for this channel: {e}")
            # This tab might also be missing if logged in user is simply not a member
            # TODO use this in conjunction with cookies to warn user if logged out?
            pass

        public_videos = []
        try:
            public_videos = self.get_public_videos(update=update)
        except TabNotFound as e:
            # Some channels do no have a Videos tab (only Live tab).
            # self.log.debug(f"No Videos tab available for this channel: {e}")
            missing_endpoint.append("Videos")

        public_streams = []
        try:
            public_streams = self.get_public_streams(update=update)
        except TabNotFound as e:
            # self.log.debug(f"No Live tab available for this channel: {e}")
            missing_endpoint.append("Live")

        if missing_endpoint:
            self.log.debug(
                f"Reloading endpoints because \"{', '.join(missing_endpoint)}\""
                " tab data was missing...")
            self.load_endpoints()

        # No need to check for "upcoming_videos" because live videos should
        # appear in the public videos list.
        for vid in public_videos + public_streams:
            if vid.get(filter_type):
                live_videos.append(vid)
        return live_videos

    def get_tab_json_from_api(self, tab_name: str) -> Optional[Dict]:
        """
        Return the parsed JSON response (as dict) for a specific endpoint,
        which shouldd be dereferenced by its tab name in most cases
        (ie. Home, Videos, Live, Community, Membership)
        """
        endpoint = self._endpoints.get(tab_name)
        if not endpoint:
            if tab_name == "Upcoming":
                raise TabNotFound("No Upcoming endpoint found{}".format(
                    ", but Live tab found"
                    if "Live" in self._endpoints.keys() else "")
                )
            else:
                raise TabNotFound(f"No endpoint found for tab named {tab_name}")

        browseEndpoint = endpoint.get("browseEndpoint")
        canonicalBaseUrl = browseEndpoint.get("canonicalBaseUrl", "")
        webCommandMetadata = endpoint.get("commandMetadata", {})\
                                        .get("webCommandMetadata", {})
        apiUrl = webCommandMetadata.get("apiUrl")
        url = webCommandMetadata.get("url")
        self.log.info(f"Getting videos from the {tab_name} tab data...")

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
            client="web_linux"
        )


def _get_content_from_grid_renderer(contents: List, tabtype: str) -> List[Dict]:
    assert tabtype in ("Videos", "Live")
    videos = []
    for content in contents:
        if __item := content.get('richItemRenderer', {}).get('content', {}):
            if tabtype == "Videos" or tabtype == "Live":
                # gridVideoRenderer might be obsolete
                griditems = __item.get('gridVideoRenderer', {}).get('items', [])
                for griditem in griditems:
                    vid_metadata = get_video_from_post(
                        griditem.get('gridVideoRenderer')
                    )
                    if vid_metadata.get('videoId'):
                        videos.append(vid_metadata)
                # New structure
                if vid_metadata := get_video_from_post(
                    __item.get("videoRenderer")):
                    if vid_metadata.get("videoId"):
                        videos.append(vid_metadata)
    return videos

def _get_content_from_list_renderer(contents: List, tabtype: str) -> List[Dict]:
    videos = []
    for content in contents:
        for __item in content.get('itemSectionRenderer', {}).get('contents', []):
            # These tabs appear to share the same architecture
            if tabtype == "Community" or tabtype == "Membership":
                post = __item.get('backstagePostThreadRenderer', {}).get('post', {})
                if post:
                    if backstageAttachment := post\
                        .get('backstagePostRenderer', {})\
                        .get('backstageAttachment', {}):
                        if videoRenderer := backstageAttachment\
                            .get('videoRenderer', {}):
                            vid_metadata = get_video_from_post(videoRenderer)
                            if vid_metadata.get('videoId'):
                                # some posts don't have attached videos
                                videos.append(vid_metadata)
                # In some cases the video is directly listed as its own item:
                if videoRenderer := __item.get('videoRenderer', {}):
                    vid_metadata = get_video_from_post(videoRenderer)
                    if vid_metadata.get('videoId'):
                        videos.append(vid_metadata)
            elif tabtype == "Videos":
                griditems = __item.get('gridRenderer', {}).get('items', [])
                for griditem in griditems:
                    vid_metadata = get_video_from_post(
                        griditem.get('gridVideoRenderer')
                    )
                    if vid_metadata.get('videoId'):
                        videos.append(vid_metadata)
            # elif tabtype == "Live":
    return videos


def get_videos_from_tab(tabs, tabtype) -> List[Dict]:
    """
    Returns videos attached to posts in available "tab" section in JSON response.
    tabtype is either "Videos" "Community", "Membership", "Home" etc.
    """
    # NOTE the format depends on the client (user-agent) used to make the request
    # FIXME
    for tab in tabs:
        if tab.get('tabRenderer', {}).get('title') != tabtype:
            continue

        if richGridRenderer := tab.get('tabRenderer')\
                      .get('content', {})\
                      .get('richGridRenderer'):
            return _get_content_from_grid_renderer(
                richGridRenderer.get('contents', []), tabtype)

        # Fallback: this is the previous way of rendering tabs, keeping it
        # just in case they are still used somewhere
        if sectionListRenderer := tab.get('tabRenderer')\
                      .get('content', {})\
                      .get('sectionListRenderer'):
            return _get_content_from_list_renderer(
                sectionListRenderer.get('contents', []), tabtype)

        raise Exception(f"No valid content renderer found for \"{tabtype=}\".")
    return []

def get_video_from_post(attachment: Dict) -> Dict[str, Any]:
    """Get video entry and attach various metadata found alongside it."""
    if not attachment:
        return {}
    video_post = {}
    video_post['videoId'] = attachment.get('videoId')
    video_post['thumbnail'] = attachment.get('thumbnail', {})

    badges = attachment.get('badges', [])
    if len(badges) and isinstance(badges[0], dict):
        label = badges[0].get("metadataBadgeRenderer", {}).get("label")
        if label and "Members only" in label:
            video_post['members-only'] = label

    for _item in attachment.get('title', {}).get('runs', []):
        if _item.get('text'): # assumes list with only one item
            video_post['title'] = _item.get('text')
    video_post['url'] = attachment.get('navigationEndpoint', {})\
                                    .get('commandMetadata', {})\
                                    .get('webCommandMetadata', {})\
                                    .get('url')
    if eventData := attachment.get('upcomingEventData', {}):
        # we can safely assume it is "upcoming"
        video_post['upcoming'] = True
        video_post['startTime'] = eventData.get('startTime')

    # Attempt to attach "live" and "upcoming" status from the response
    for _item in attachment.get('thumbnailOverlays', []):
        if status_renderer := _item.get('thumbnailOverlayTimeStatusRenderer', {}):
            if style := status_renderer.get('style'):
                # This seems to be a decent indicator that it is currently LIVE
                if style == 'LIVE':
                    video_post['isLiveNow'] = True
                # This might be redundant with upcomingEventData key
                elif style == 'UPCOMING':
                    video_post['upcoming'] = True
            if text := status_renderer.get('text'):
                if runs := text.get('runs', []):
                    if len(runs) > 0 and runs[0].get('text') == 'LIVE':
                        # This indicates that it should be live in the future
                        video_post['isLive'] = True
            break
    # Another way to check if it is currently LIVE
    for _item in attachment.get('badges', []):
        if badge_renderer := _item.get('metadataBadgeRenderer', {}):
            if badge_renderer.get('label') == "LIVE NOW":
                video_post['isLiveNow'] = True

    return video_post


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


def wait_block(min_minutes=15.0, variance=3.5):
    """
    Sleep (blocking) for a specified amount of minutes,
    with variance to avoid being detected as a robot.
    :param min_minutes float Minimum number of minutes to wait.
    :param variance float Maximum number of minutes added.
    """
    min_seconds = min_minutes * 60
    max_seconds = min_seconds + (variance * 60)
    wait_time_sec = uniform(min_seconds, max_seconds)
    wait_time_min = wait_time_sec / 60
    logger.info(f"Sleeping for {wait_time_min:.2f} minutes ({wait_time_sec:.2f} seconds)...")
    sleep(wait_time_sec)


def format_list_output(vid_list: List[Dict]) -> str:
    strs = []
    for vid in vid_list:
        strs.append(
            f"{vid.get('videoId')} - {vid.get('title')}"
            f"{' (LiveStream)' if vid.get('isLive') else ''}"
            f"{' LIVE NOW!' if vid.get('isLiveNow') else ''}"
            f"{' (Upcoming)' if vid.get('upcoming') else ''}")
    return "\n".join(strs)


def get_endpoints_from_json(json: Dict) -> Dict:
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
