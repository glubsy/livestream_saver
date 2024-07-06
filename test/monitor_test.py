import unittest
from unittest.mock import patch, Mock
from pathlib import Path
from json import load

# from urllib.request import urlopen
from urllib.error import URLError


from livestream_saver.channel import YoutubeChannel, VideoPost, DedupedVideoList
from livestream_saver.request import YoutubeUrllibSession
from livestream_saver.exceptions import MissingVideoId
from livestream_saver.notifier import NotificationDispatcher
from livestream_saver.livestream_saver import monitor_mode


# API_RESPONSE_SAMPLE = None
# with open(Path() / "data/viewed_live.json", 'r') as f:
#     API_RESPONSE_SAMPLE = load(f)


class BaseTestVideoPost(unittest.TestCase):
    def setUp(self) -> None:
        self.data = {
            "videoId": "test_id",
            "navigationEndpoint": {
                "commandMetadata": {
                    "webCommandMetadata": {
                        "url": "test_url"
                    }
                }
            }
        }
        return super().setUp()

    def test_initialise_from_post(self):
        video = VideoPost.from_post(self.data, channel=None)
        self.assertEqual(video.videoId, "test_id")

    def test_update_data(self):
        video = VideoPost.from_post(self.data, channel=None)
        self.assertEqual(video.url, "test_url")
        video.url = "new_test_url"
        self.assertEqual(video.url, "new_test_url")

    def test_getter(self):
        video = VideoPost.from_post(self.data, channel=None)
        self.assertEqual(video.get('url'), "test_url")
        self.assertEqual(video['url'], "test_url")

    def test_repr(self):
        video = VideoPost.from_post(self.data, channel=None)
        self.assertEqual(repr(video), "test_id")

    def test_missing_video_id(self):
        del self.data["videoId"]
        with self.assertRaises(MissingVideoId):
            VideoPost.from_post(self.data, channel=None)

    def test_invalid_type_passed(self):
        with self.assertRaises(TypeError):
            VideoPost.from_post(None, None)

    def test_video_post_can_update_values(self):
        video = VideoPost.from_post(self.data, channel=None)
        self.assertFalse(video["isLive"])
        video["isLive"] = True
        self.assertTrue(video["isLive"])

    def test_video_post_cannot_create_new_field(self):
        video = VideoPost.from_post(self.data, channel=None)
        with self.assertRaises(AttributeError):
            video["new_field"] = "test"


class TestDedupedVideoList(unittest.TestCase):
    # Cheating here: expected objects should be VideoPost, not Dict
    data_1 = {"videoId": "one"}
    data_2 = {"videoId": "two"}

    def test_added_dupe(self):
        l = DedupedVideoList()
        l.append(self.data_1)
        l.append(self.data_2)
        l.append(self.data_1)
        l.append(self.data_1)
        self.assertEqual(l, [self.data_1, self.data_2])
        self.assertIn(self.data_1, l)
        self.assertIn(self.data_2, l)
        self.assertEqual(len(l), 2)
        self.assertEqual(len(l.seen_ids), 2)
        self.assertEqual(len(l.duplicates), 1)
        self.assertIn(self.data_1["videoId"], l.duplicates)
        self.assertIn(self.data_1, l)
        self.assertIn(self.data_2, l)


class TestGetVideosFromTabs(unittest.TestCase):
    def setUp(self) -> None:
        self.ch = YoutubeChannel(
            URL="",
            channel_id="",
            session=YoutubeUrllibSession(),
            notifier=NotificationDispatcher()
        )
        self.video_post = VideoPost.from_post(
            post={
                "videoId": "test_id",
                "isLive": True,
                "isLiveNow": True,
                "navigationEndpoint": {
                    "commandMetadata": {
                        "webCommandMetadata": {
                            "url": "test_url"
                        }
                    }
                }
            }, 
            channel=self.ch
        )

    @patch("livestream_saver.channel.YoutubeChannel.get_videos_from_tab")
    @patch("livestream_saver.channel.YoutubeChannel.load_endpoints")
    @patch("configparser.ConfigParser")
    @patch("urllib.request.urlopen")
    @patch("livestream_saver.channel.YoutubeChannel.get_json_and_cache")
    def test_duplicate_video_ids_are_filtered(
        self,
        get_json_and_cache: Mock,
        urlopen: Mock, 
        configparser: Mock, 
        load_endpoints: Mock,
        get_videos_from_tab: Mock
    ):
        """
        A given video Id may be found on multiple tabs. The filter_videos method
        should only return one unique Id per video post found.
        """
        get_videos_from_tab.return_value = [self.video_post,]
        videos = self.ch.filter_videos('isLiveNow')
        self.assertTrue(get_videos_from_tab.call_count > 1)

        # Only one Id should be returned, despite being present on all tas
        self.assertEqual(len(videos), 1)


    # @patch("configparser.ConfigParser")
    # @patch("urllib.request.urlopen")
    # def test_update_status_live_started(self, urlopen, configparser):
    #     """
    #     Status for a video that is live should be updated to status.OK 
    #     or something
    #     """
    #     raise NotImplementedError


class TestDownload(unittest.TestCase):
    def setUp(self) -> None:
        # TODO a lof of methods to patch here; we might need 
        # some data fixtures for this 
        self.ch = YoutubeChannel(
            URL="",
            channel_id="",
            session=YoutubeUrllibSession(),
            notifier=NotificationDispatcher()
        )

    @patch("livestream_saver.request.YoutubeUrllibSession.initialize_consent")
    @patch("configparser.ConfigParser")
    @patch("urllib.request.urlopen")
    def test_lost_connection_should_recover(
        self,
        request_mock: Mock, 
        configparser: Mock,
        initialize_consent: Mock
    ):
        request_mock.side_effect = URLError("Temporary failure in name resolution")
        monitor_mode(
            configparser,
            args={
                "URL": "",
                "channel_id": "",
                "scan_delay": 0.0,
                "output_dir": "",
                "hooks": "",
            }
        )
        raise NotImplementedError
