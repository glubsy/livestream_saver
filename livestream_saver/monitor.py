from time import sleep
from random import uniform
import logging

logger = logging.getLogger(__name__)


class YoutubeChannel:
    def __init__(self, args, channel_id, session):
        self.info = {}
        self.session = session
        self.url = self.sanitize_url(args.url)
        self.info['id'] = channel_id
        self.community_json = None
        self.videos_json = None
        self.community_videos = None
        self.public_videos = None

    def get_name(self):
        """
        Get the name of the channel from the community JSON (once retrieved).
        """
        # TODO handle channel names which are not IDs
        # => get "videos" tab html page and grab externalId value from it?
        if self.community_json:
            return self.community_json\
            .get('metadata', {})\
            .get('channelMetadataRenderer', {})\
            .get('title')

    def sanitize_url(self, url):
        """Make sure url passed to constructor is valid"""
        # FIXME needs smarter checks
        if "http" not in url and "youtube.com" not in url:
            return f"https://www.youtube.com/channel/{url}"
        return url

    def get_live_videos(self):
        """
        High level method.
        Returns a list of videos that are live, from various channel tabs.
        """
        community_videos = self.update_community_videos()
        if self.community_videos is None:
            # Log only the very first time
            logger.debug(f"Community videos: {community_videos}")
        else:
            new_comm_videos = [v for v in community_videos if v not in self.community_videos]
            if new_comm_videos:
                logger.warning(f"Newly added community video: {new_comm_videos}")
        self.community_videos = community_videos

        public_videos = self.get_public_videos()
        if self.public_videos is None:
            logger.debug(f"Public videos: {public_videos}")
        else:
            new_pub_videos = [v for v in public_videos if v not in self.public_videos]
            if new_pub_videos:
                logger.warning(f"Newly added public video: {new_pub_videos}")
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
        """
        Returns list of dict with urls to videos attached to community posts.
        """
        try:
            self.community_json = self.session.make_request(self.url + '/community')
        except:
            self.community_json = {}
        # logger.debug(f"community videos JSON:\n{self.community_json}")
        if not self.community_json:
            return []
        tabs = get_tabs_from_json(self.community_json)
        return get_videos_from_tab(tabs, 'Community')

    def get_public_videos(self):
        """
        Returns list of videos from "videos" or "featured" tabs.
        """
        try:
            self.videos_json = self.get_public_livestreams('current')
        except:
            self.videos_json = None
        # logger.debug(f"public videos JSON:\n{self.videos_json}")
        if not self.videos_json:
            return []
        tabs = get_tabs_from_json(self.videos_json)
        return get_videos_from_tab(tabs, 'Videos')

    def get_public_livestreams(self, filtertype):
        if filtertype == 'upcoming':
            # https://www.youtube.com/c/kamikokana/videos\?view\=2\&live_view\=502
            # https://www.youtube.com/channel/UCoSrY_IQQVpmIRZ9Xf-y93g/videos?view=2&live_view=502
            # This video tab filtered list, returns public upcoming livestreams (with scheduled times)
            return self.session.make_request(self.url + '/videos?view=2&live_view=502')
        if filtertype == 'current':
            # NOTE: active livestreams are also displays in /featured tab:
            # https://www.youtube.com/c/kamikokana/videos?view=2&live_view=501
            return self.session.make_request(self.url + '/videos?view=2&live_view=501')
        if filtertype == 'featured':
            # NOTE "featured" tab is ONLY reliable for CURRENT live streams
            return self.session.make_request(self.url + '/featured')
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
    return _json.get('contents', {})\
                .get('twoColumnBrowseResultsRenderer', {})\
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
    """Wait for float: minutes, with up to float: variance minutes."""
    min_seconds = min_minutes * 60
    max_seconds = min_seconds + (variance * 60)
    wait_time_sec = uniform(min_seconds, max_seconds)
    wait_time_min = wait_time_sec / 60
    logger.info(f"Sleeping for {wait_time_min:.2f} minutes ({wait_time_sec:.2f} seconds)...")
    sleep(wait_time_sec)
