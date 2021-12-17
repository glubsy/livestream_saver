from email.message import Message
import unittest
import pytest
from pathlib import Path
import time
from random import uniform
from io import BytesIO
import unittest.mock
import json
import urllib.request
import datetime
from livestream_saver.request import Session
from livestream_saver.constants import *
import livestream_saver.download
from livestream_saver.download import Status
import livestream_saver.request
import pytube.innertube
import pytube.request
import urllib.error
from random import uniform
import http.client
import logging 


TESTDIRFILES = Path(__file__).parent
FIXTURESDIR = TESTDIRFILES / "fixtures"
TIMEOUT = 5 # timeout in seconds before we simulate stream made private (and forbidden segments)

# This works well too https://stackoverflow.com/questions/13654663

@pytest.fixture()
def slow_server(monkeypatch):
    """Used to simulate a server that is heavily throttled, while using a
    blocking urlopen function."""

    start_time = datetime.datetime.now()
    req_num = 0
    def url_get(url):
        # Keep the last two parts of the url to include the stream format directory
        splits = url.split("/")[:2:-1]
        splits.reverse()
        filename = "/".join(splits)
        filepath = FIXTURESDIR / "stream_capture__We0NmSfwqU" / filename
        now = datetime.datetime.now()
        print(f"Serv {now} Requested {url}")

        # After some time, we simulate stream being made private early
        timedout: bool = (now - start_time).total_seconds() >= TIMEOUT
        if timedout:
            print(f"Serv {RED}TIMEOUT. WE REFUSE SERVICE NOW.{ENDC}")
            raise urllib.error.HTTPError(
                url, 403, 
                f"END forbidden seg {filename}. Stream is now private.", 
                Message(), None
            )

        nonlocal req_num
        req_num += 1

        if not filepath.exists():
            raise urllib.error.HTTPError(
                url, 404, f"FAKE Not found {filename}.", Message(), None)

        if "35" in filename: # our fixture stops at 35
            # Override for fake debug message
            def __repr__(self):
                return f"FAKE incomplete read of seg {filename}"
            http.client.IncompleteRead.__repr__ = __repr__
            raise http.client.IncompleteRead(
                partial=bytes("testdata".encode())
            )

        # Every 5 file req_num will generate an error
        if req_num % 5 == 0:
            # Very common errors, are often temporary, requires retry
            raise urllib.error.HTTPError(
                url, 503, f"FAKE Service Unavailable chunk {req_num}", Message(), None)

        if req_num == 8:
            # Forbidden usually means end of stream
            raise urllib.error.HTTPError(
                url, 403, f"FAKE Forbidden req_num {req_num -1}", Message(), None)

        def throttled(fpath):
            # Assume format is "http://127.0.0.1:9999/aud/000000001_audio.ts"
            # since we usually serve with python -m http.server 9999 --directory FIXTUREDIR
            with open(fpath, 'rb') as file_to_serve:
                block = file_to_serve.read(1024)
                while block:
                    # time.sleep(uniform(0.001, 0.010))
                    time.sleep(uniform(0.001, 0.002))
                    yield block
                    block = file_to_serve.read(1024)

        gen = throttled(filepath)
        data = bytes()
        curr_time = datetime.datetime.now()
        flen = filepath.stat().st_size
        for chunk in gen:
            data += bytes(chunk)
            progress = (len(data) * 100) / flen
            now = datetime.datetime.now()
            # Limit printing to every second, and if there is a new chunk
            if now.second != curr_time.second:
                print(f"Serv {filename}: {progress:.2f}%")
                curr_time = now

        # class DummyFile():
        #     def read(self):
        #         # part of html of a normal response
        #         return data

        # mock_response = mock.MagicMock()
        # mock_response.read.return_value = data
        # return mock_response
        # return DummyFile()
        return BytesIO(data)

    # This is bad because all request will be affected
    monkeypatch.setattr(urllib.request, 'urlopen', url_get)


def generate_fake_html(*args, **kwargs):
    print("Making fake html...")
    with open(FIXTURESDIR / "mqH26pGUouA.html", "rb") as f:
        return f.read().decode('utf-8')

def generate_fake_json(*args, **kwargs):
    print("Making fake json...")
    with open(FIXTURESDIR / "mqH26pGUouA.json", "rb") as f:
        data = f.read().decode('utf-8')
        return json.loads(data)

@pytest.fixture
def fake_html():
    return generate_fake_html("")

# @pytest.fixture(autouse=True)
# def patch_html(monkeypatchs):
#     monkeypatch.setattr(PytubeYoutube, "watch_html", read_fake_html)

# @mock.patch('livestream_saver.request.Session.make_request')
@pytest.fixture
def fake_broadcast(tmp_path, monkeypatch, cipher_signature, default_session):
    monkeypatch.setattr(
        livestream_saver.download.PytubeYoutube, "watch_html", generate_fake_html)
    monkeypatch.setattr(
        pytube.innertube.InnerTube, "_call_api", generate_fake_json)

    # def side_effect_func(*args, **kwargs):
    #     if "https://www.youtube.com/watch?v=XXXXXXXXXXX" in args[0]:
    #         return generate_fake_html()

    # m = MagicMock(side_effect=side_effect_func)

    # monkeypatch.setattr(
    #     pytube.request,
    #     "_execute_request",
    #     m
    # )

    broadcast = livestream_saver.download.YoutubeLiveBroadcast(
        url="https://www.youtube.com/watch?v=XXXXXXXXXXX",
        output_dir=tmp_path,
        session=default_session,
    )
    # Force DEBUG level since we already have handlers on the logger
    broadcast.logger.setLevel(logging.DEBUG)
    broadcast.ptyt = cipher_signature
    broadcast.ptyt._watch_html = generate_fake_html()
    # We already changed the private member, so this is redundant:
    # monkeypatch.delattr(
    #     livestream_saver.download.PytubeYoutube,
    #     "watch_html"
    # )

    # Initially available, but not anymore after timeout
    start_time = datetime.datetime.now()
    def status(self, update=False) -> Status:
        now = datetime.datetime.now()
        timedout: bool = (now - start_time).total_seconds() >= TIMEOUT
        if timedout:
            self._status = Status.OFFLINE
        print(f"{BLUE}Status is currently {self._status}{ENDC}")
        return self._status

    broadcast._status = Status.AVAILABLE
    monkeypatch.setattr(
        livestream_saver.download.YoutubeLiveBroadcast,
        'status',
        status)

    return broadcast

# @mock.patch("pytube.request.urlopen")
@unittest.mock.patch("livestream_saver.request.Session.make_request")
def test_download(request, fake_html, fake_broadcast, slow_server):

    request.return_value = fake_html
    # response = mock.Mock()
    # response.read.return_value = fake_html
    # mock_urlopen.return_value = response

    expected = fake_html[:100]
    result = getattr(fake_broadcast.ptyt, "watch_html")()[:100]
    assert result == expected
    assert fake_broadcast.ptyt._watch_html[:100] == expected
    # assert fake_broadcast.ptyt.title == "🔴CALLING SCAMMERS LIVESTREAM #scambaiting #scambait #scambaiter"
    # Make sure no outside request made from LS side
    request.assert_not_called()

    fake_broadcast.filter_streams()

    # TODO place this into a fixture 
    def mock_missing_segments(path):
        if "140" in path.name:
            return 14, [3,4,5,6,9]
        else:
            return 8, [1,2,4,7]

    with unittest.mock.patch(
        "livestream_saver.download.get_latest_valid_segment"
    ) as patched:
        patched.side_effect = mock_missing_segments
        
        for stream in fake_broadcast.selected_streams:
            print(f"filtered stream: {stream}")
            livestream_saver.download.collect_missing_segments(
                fake_broadcast.output_dir, stream
            )
        patched.assert_not_called()
        # Second time now that the directories have been created (and are empty)
        for stream in fake_broadcast.selected_streams:
            livestream_saver.download.collect_missing_segments(
                fake_broadcast.output_dir, stream
            )
        patched.assert_called()

        # test missing segments
        for stream in fake_broadcast.selected_streams:
            if stream.itag == 140:
                assert stream.missing_segs == [3,4,5,6,9]
                # We should be missing segments 10 to 13 in the end here
                assert stream.start_seg == 14
                stream.url = "http://127.0.0.1:9999/f140/"
            else:
                assert stream.missing_segs == [1,2,4,7]
                assert stream.start_seg == 8
                stream.url = "http://127.0.0.1:9999/f399/"

        livestream_saver.download.Segment.geturl = \
            lambda self: self.base_url + f"{self.num:0{10}}" + (
                "_a.ts" if "f140" in self.base_url else "_v.ts"
            )
        livestream_saver.download.Segment.url = property(livestream_saver.download.Segment.geturl)

        fake_broadcast.download()
