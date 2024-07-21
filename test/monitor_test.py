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
        video = VideoPost.from_post(self.data, channel_name="channel_name")
        self.assertEqual(video.videoId, "test_id")

    def test_update_data(self):
        video = VideoPost.from_post(self.data, channel_name="channel_name")
        self.assertEqual(video.url, "test_url")
        video.url = "new_test_url"
        self.assertEqual(video.url, "new_test_url")

    def test_getter(self):
        video = VideoPost.from_post(self.data, channel_name="channel_name")
        self.assertEqual(video.get('url'), "test_url")
        self.assertEqual(video['url'], "test_url")

    def test_repr(self):
        video = VideoPost.from_post(self.data, channel_name="channel_name")
        self.assertEqual(repr(video), "test_id")

    def test_missing_video_id(self):
        del self.data["videoId"]
        with self.assertRaises(MissingVideoId):
            VideoPost.from_post(self.data, channel_name="channel_name")

    def test_invalid_type_passed(self):
        with self.assertRaises(TypeError):
            VideoPost.from_post(None, None) # type: ignore

    def test_video_post_can_update_values(self):
        video = VideoPost.from_post(self.data, channel_name="channel_name")
        self.assertFalse(video["isLive"])
        video["isLive"] = True
        self.assertTrue(video["isLive"])

    def test_video_post_cannot_create_new_field(self):
        video = VideoPost.from_post(self.data, channel_name="channel_name")
        with self.assertRaises(AttributeError):
            video["new_field"] = "test"


class TestDedupedVideoList(unittest.TestCase):
    # Cheating here: expected objects should be VideoPost, not Dict
    data_1 = {"videoId": "one"}
    data_2 = {"videoId": "two"}

    def test_added_dupes_are_filtered(self):
        deduped_l = DedupedVideoList()
        video_post_1 = VideoPost.from_post(self.data_1, channel_name="channel_name")
        video_post_2 = VideoPost.from_post(self.data_2, channel_name="channel_name")

        deduped_l.append(video_post_1)
        deduped_l.append(video_post_2)
        deduped_l.append(video_post_2)
        deduped_l.append(video_post_2)

        self.assertIn(self.data_1["videoId"], deduped_l.seen_ids)
        self.assertIn(self.data_2["videoId"], deduped_l.seen_ids)
        self.assertIn(video_post_1, deduped_l)
        self.assertIn(video_post_2, deduped_l)
        self.assertEqual(len(deduped_l), 2)
        self.assertEqual(len(deduped_l.seen_ids), 2)
        self.assertEqual(len(deduped_l.duplicates), 1)
        self.assertIn(self.data_2["videoId"], deduped_l.duplicates)


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
            channel_name="channel name"
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

    @patch("livestream_saver.channel.YoutubeChannel.load_endpoints")
    @patch("configparser.ConfigParser")
    @patch("urllib.request.urlopen")
    @patch("livestream_saver.channel.YoutubeChannel.get_json_and_cache")
    def test_get_changes(
        self,
        get_json_and_cache: Mock,
        urlopen: Mock,
        configparser: Mock,
        load_endpoints: Mock,
    ):
        """
        Ensure that only new video Ids are returned by YoutubeChannel.get_changes
        """
        previously = []
        newly      = [self.video_post]
        new, removed = self.ch.get_changes(
            videos=newly,
            previous=previously
        )
        self.assertEqual(new, [self.video_post])
        self.assertEqual(removed, [])

        new_video = VideoPost.from_post(
            post={
                "videoId": "test_id2",
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
            channel_name="channel name"
        )

        previously.append(self.video_post)
        previously.append(new_video)
        new, removed = self.ch.get_changes(
            videos=newly,
            previous=previously
        )
        self.assertEqual(new, [])
        self.assertEqual(removed, [new_video])

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

    @patch("livestream_saver.request.YoutubeUrllibSession._initialize_consent")
    @patch("configparser.ConfigParser")
    @patch("urllib.request.urlopen")
    def test_lost_connection_should_recover(
        self,
        request_mock: Mock,
        configparser: Mock,
        _initialize_consent: Mock
    ):
        request_mock.side_effect = URLError("Temporary failure in name resolution")
        raise NotImplementedError
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
