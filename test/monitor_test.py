import unittest
from unittest.mock import patch
from pathlib import Path
from json import load

# from urllib.request import urlopen
from urllib.error import URLError


from livestream_saver.monitor import YoutubeChannel, VideoPost, DedupedVideoList
from livestream_saver.request import YoutubeUrllibSession
from livestream_saver.notifier import NotificationDispatcher
from livestream_saver.livestream_saver import monitor_mode


# API_RESPONSE_SAMPLE = None
# with open(Path() / "data/viewed_live.json", 'r') as f:
#     API_RESPONSE_SAMPLE = load(f)


class BaseTestVideoPost(unittest.TestCase):
    data = {
        "videoId": "test_id", 
        "navigationEndpoint": {
            "commandMetadata": {
                "webCommandMetadata": {
                    "url": "test_url"
                }
            }
        }
    }

    def test_initialise_from_post(self):
        video = VideoPost.from_post(self.data)
        self.assertEqual(video.videoId, "test_id")
    
    def test_update_data(self):
        video = VideoPost.from_post(self.data)
        self.assertEqual(video.url, "test_url")
        video.url = "new_test_url"
        self.assertEqual(video.url, "new_test_url")

    def test_getter(self):
        video = VideoPost.from_post(self.data)
        self.assertEqual(video.get('url'), "test_url")
        self.assertEqual(video['url'], "test_url")

    def test_repr(self):
        video = VideoPost.from_post(self.data)
        self.assertEqual(repr(video), "test_id")


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


class TestMonitor(unittest.TestCase):
    def setUp(self) -> None:
        self.patcher = patch("livestream_saver.request.YoutubeUrllibSession")
        self.patcher.start()
        session = YoutubeUrllibSession()

        self.ch = YoutubeChannel(
            URL="",
            channel_id="",
            session=session,
            notifier=NotificationDispatcher() 
        )

    def tearDown(self) -> None:
        self.patcher.stop()
    
    @patch("configparser.ConfigParser")
    @patch("urllib.request.urlopen")
    def test_update_status_live_started(self, urlopen, configparser):
        """
        Status should be updated to status.OK or something
        """
        raise NotImplementedError



class TestDownload(unittest.TestCase):
    def setUp(self) -> None:
        self.patcher = patch("livestream_saver.request.YoutubeUrllibSession")
        self.patcher.start()
        session = YoutubeUrllibSession()

        self.ch = YoutubeChannel(
            URL="",
            channel_id="",
            session=session,
            notifier=NotificationDispatcher() 
        )

    def tearDown(self) -> None:
        self.patcher.stop()
    
    def tearDown(self) -> None:
        return super().tearDown()

    @patch("configparser.ConfigParser")
    @patch("urllib.request.urlopen")
    def test_lost_connection_should_recover(self, request_mock, config):
        request_mock.side_effect = URLError("Temporary failure in name resolution")
        monitor_mode(
            config, 
            {
                "URL": "",
                "channel_id": "",
                "scan_delay": 0.0,
                "output_dir": "",
                "hooks": "",
            }
        )
        raise NotImplementedError
