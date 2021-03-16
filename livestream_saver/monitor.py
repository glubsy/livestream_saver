from os import sep
from time import sleep
from random import randint, uniform
import logging
import requests
import json
import livestream_saver.download
import livestream_saver.exceptions
import livestream_saver.util
import livestream_saver.merge

logger = logging.getLogger(__name__)


class YoutubeRequestSession:
    def __init__(self, cookie):
        self.cookie = cookie

    def do_request(self, url):
        headers = {
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 \
    (KHTML, like Gecko) Chrome/88.0.4324.96 Safari/537.36',
        'accept-language': 'en-US,en'
        }
        return requests.get(url, headers=headers, cookies=self.cookie)


class YoutubeChannel:
    def __init__(self, args, channel_id, session):
        self.info = {}
        self.url = args.url
        self.session = session
        self.info['id'] = channel_id
        self.community_json = None
        self.videos_json = None

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

    def get_live_videos(self):
        """
        High level method.
        Returns a list of videos that are live, from various channel tabs.
        """
        community_videos = self.get_community_videos()
        logger.debug(f"community videos {community_videos}")
        public_videos = self.get_public_videos()
        logger.debug(f"public videos {public_videos}")
        live_videos = []
        for vid in community_videos:
            if vid.get('isLive'):
                live_videos.append(vid)
        for vid in public_videos:
            if vid.get('isLive'):
                live_videos.append(vid)
        return live_videos

    def get_community_videos(self):
        """
        Returns list of dict with urls to videos attached to community posts.
        """
        self.community_json = get_json(self.session.do_request(self.url + '/community'))
        tabs = get_tabs_from_json(self.community_json)
        return get_videos_from_tab(tabs, 'Community')

    def get_public_videos(self):
        """
        Returns list of videos from "videos" or "featured" tabs.
        """
        self.videos_json = self.get_public_livestreams('current')
        # logger.debug(f"{self.videos_json}")
        tabs = get_tabs_from_json(self.videos_json)
        return get_videos_from_tab(tabs, 'Videos')

    def get_public_livestreams(self, filtertype):
        if filtertype == 'upcoming':
            # https://www.youtube.com/c/kamikokana/videos\?view\=2\&live_view\=502
            # https://www.youtube.com/channel/UCoSrY_IQQVpmIRZ9Xf-y93g/videos?view=2&live_view=502
            # This video tab filtered list, returns public upcoming livestreams (with scheduled times)
            return get_json(self.session.do_request(self.url + '/videos?view=2&live_view=502'))
        if filtertype == 'current':
            # NOTE: active livestreams are also displays in /featured tab:
            # https://www.youtube.com/c/kamikokana/videos?view=2&live_view=501
            return get_json(self.session.do_request(self.url + '/videos?view=2&live_view=501'))
        if filtertype == 'featured':
            # NOTE "featured" tab is ONLY reliable for CURRENT live streams
            return get_json(self.session.do_request(self.url + '/featured'))
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


def get_json(req):
    """
    Extract the initial JSON from the HTML in the request response.
    """
    # We could also use youtube-dl --dump-json instead
    content_page = req.text\
                    .split("var ytInitialData = ")[1]\
                    .split(';</script><link rel="canonical')[0]
    try:
        j = json.loads(content_page)
    except Exception as e:
        logger.critical(f"Exception while loading json: {e}")
        return {}
    return j


def wait_block(min_minutes=15.0, variance=3.5):
    """Wait for float: minutes, with up to float: variance minutes."""
    min_seconds = min_minutes * 60
    max_seconds = min_seconds + (variance * 60)
    wait_time = uniform(min_seconds, max_seconds)
    logger.debug(f"Sleeping for {wait_time:.2f} seconds...")
    sleep(wait_time)
