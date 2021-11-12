from time import sleep
from random import uniform
import logging
from typing import Optional
from livestream_saver import extract

logger = logging.getLogger(__name__)


class YoutubeChannel:
    def __init__(self, URL, channel_id, session):
        self.session = session
        self.url = URL
        self.id = channel_id
        self.videos_json = None
        self.community_videos = None
        self.public_videos = None

        self._community_videos_html = None
        self._public_videos_html = None
        self._community_json = None
        self._public_json = None

    def get_channel_name(self):
        """Get the name of the channel from the community JSON (once retrieved).
        """
        # FIXME this method pre-fetches the json if called before 
        # TODO handle channel names which are not IDs
        # => get "videos" tab html page and grab externalId value from it?
        _json = self.public_json
        if not _json:
            _json = self.community_json

        if _json:
            return _json.get('metadata', {})\
                        .get('channelMetadataRenderer', {})\
                        .get('title')
        return "Unknown"

    @property
    def community_videos_html(self):
        if self._community_videos_html:
            return self._community_videos_html

        self._community_videos_html = self.session.make_request(
            self.url + '/community'
        )
        return self._community_videos_html

    @property
    def public_videos_html(self):
        if self._public_videos_html:
            return self._public_videos_html

        self._public_videos_html = self.get_public_livestreams('current')
        return self._public_videos_html

    @property
    def community_json(self) -> dict:
        if self._community_json:
            return self._community_json

        self._community_json = extract.str_as_json(
            extract.initial_player_response(
                self.community_videos_html
            )
        )
        return self._community_json

    @property
    def public_json(self):
        if self._public_json:
            return self._public_json

        self._public_json = extract.str_as_json(
            extract.initial_player_response(
                self.public_videos_html
            )
        )
        return self._public_json

    def get_live_videos(self):
        """High level method.
        Returns a list of videos that are live, from various channel tabs.
        """
        community_videos = self.update_community_videos()

        if self.community_videos is None:
            # Log only the very first time
            logger.info(
                "Currently listed community videos:\n{}".format(
                    format_list_output(community_videos)
                )
            )
        else:
            new_comm_videos = [
                v for v in community_videos if v not in self.community_videos
            ]
            if new_comm_videos:
                logger.info(
                    "Newly added community video:\n{}".format(
                        format_list_output(new_comm_videos)
                    )
                )
        self.community_videos = community_videos

        public_videos = self.update_public_videos()

        if self.public_videos is None:
            # Log only the very first time
            logger.info(
                "Currently listed public videos:\n{}".format(
                    format_list_output(public_videos)
                )
            )
        else:
            new_pub_videos = [
                v for v in public_videos if v not in self.public_videos
            ]
            if new_pub_videos:
                logger.info(
                    "Newly added public video: {}".format(
                        format_list_output(new_pub_videos)
                    )
                )
        self.public_videos = public_videos

        live_videos = []
        for vid in community_videos:
            if vid.get('isLive'):
                live_videos.append(vid)
        for vid in public_videos:
            if vid.get('isLive'):
                live_videos.append(vid)
        return live_videos

    def update_community_videos(self):
        """Returns list of dict with urls to videos attached to community posts.
        """
        self._community_json = self._community_videos_html = None # force update
        
        community_json = {}
        try:
            community_json = self.community_json
            self.session.is_logged_out(community_json)
        except Exception as e:
            # TODO send email here?
            logger.critical(f"Got an invalid community JSON: {e}")
        
        tabs = get_tabs_from_json(community_json)
        return get_videos_from_tab(tabs, 'Community')

    def update_public_videos(self):
        """Returns list of videos from "videos" or "featured" tabs."""
        self._public_json = self._public_videos_html = None # force update
        
        public_json = {}
        try:
            public_json = self.public_json
        except Exception as e:
            # TODO send email here?
            logger.critical(f"Got an invalid public JSON: {e}")
        
        tabs = get_tabs_from_json(public_json)
        return get_videos_from_tab(tabs, 'Videos')

    def get_public_livestreams(self, filtertype):
        if filtertype == 'upcoming':
            # https://www.youtube.com/c/kamikokana/videos\?view\=2\&live_view\=502
            # https://www.youtube.com/channel/UCoSrY_IQQVpmIRZ9Xf-y93g/videos?view=2&live_view=502
            # This video tab filtered list, returns public upcoming livestreams (with scheduled times)
            return self.session.make_request(
                self.url + '/videos?view=2&live_view=502'
            )
        if filtertype == 'current':
            # NOTE: active livestreams are also displays in /featured tab:
            # https://www.youtube.com/c/kamikokana/videos?view=2&live_view=501
            return self.session.make_request(
                self.url + '/videos?view=2&live_view=501'
            )
        if filtertype == 'featured':
            # NOTE "featured" tab is ONLY reliable for CURRENT live streams
            return self.session.make_request(
                self.url + '/featured'
            )
        # NOTE "/live" virtual tab is a redirect to the current live broadcast


def get_videos_from_tab(tabs, tabtype):
    """
    Returns videos attached to posts in available "tab" section in JSON response.
    tabtype is either "Videos" "Community", "Home" etc.
    """
    videos = []
    for tab in tabs:
        if tab.get('tabRenderer', {}).get('title') != tabtype:
            continue
        sectionList_contents = tab.get('tabRenderer')\
                      .get('content', {})\
                      .get('sectionListRenderer', {})\
                      .get('contents', [])
        for _item in sectionList_contents:
            for __item in _item.get('itemSectionRenderer', {}).get('contents', []):
                if tabtype == "Community":
                    post = __item.get('backstagePostThreadRenderer', {})\
                                 .get('post', {})
                    if post:
                        video_attachement = get_video_from_post(
                            post.get('backstagePostRenderer', {})\
                                .get('backstageAttachment', {})\
                                .get('videoRenderer', {})
                            )
                        if video_attachement.get('videoId'):
                            # some posts don't have attached videos
                            videos.append(video_attachement)
                elif tabtype == "Videos":
                    griditems = __item.get('gridRenderer', {})\
                                      .get('items', [])
                    for griditem in griditems:
                        video_attachement = get_video_from_post(
                            griditem.get('gridVideoRenderer')
                        )
                        if video_attachement.get('videoId'):
                            videos.append(video_attachement)
    return videos


def get_video_from_post(attachment):
    if not attachment:
        return {}
    video_post = {}
    video_post['videoId'] = attachment.get('videoId')
    for _item in attachment.get('title', {}).get('runs', []):
        if _item.get('text'): # assumes list with only one item
            video_post['title'] = _item.get('text')
    video_post['url'] = attachment.get('navigationEndpoint', {})\
                                    .get('commandMetadata', {})\
                                    .get('webCommandMetadata', {})\
                                    .get('url')
    for _item in attachment.get('thumbnailOverlays', []):
        if _item.get('thumbnailOverlayTimeStatusRenderer'):
            video_post['isLive'] = _item.get('thumbnailOverlayTimeStatusRenderer').get('style') == 'LIVE'
            # NOTE there is also "UPCOMING" style, which includes
            # "upcomingEventData" section, with startTime timestamp
            break
    return video_post


def get_tabs_from_json(_json):
    if not _json:
        return _json
    return _json.get('contents', {}) \
                .get('twoColumnBrowseResultsRenderer', {}) \
                .get('tabs', [])


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


def format_list_output(vid_list):
    strs = []
    for vid in vid_list:
        strs.append(f"{vid.get('videoId')} - {vid.get('title')}")
    return "\n".join(strs)
