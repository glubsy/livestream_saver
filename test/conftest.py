"""Reusable dependency injected testing components."""
import gzip
import json
import os
import pytest
from unittest import mock

from livestream_saver.download import PytubeYoutube
from livestream_saver.request import Session

def load_playback_file(filename):
    """Load a gzip json playback file."""
    cur_fp = os.path.realpath(__file__)
    cur_dir = os.path.dirname(cur_fp)
    fp = os.path.join(cur_dir, "mocks", filename)
    with gzip.open(fp, "rb") as fh:
        content = fh.read().decode("utf-8")
        return json.loads(content)


@pytest.fixture()
def no_cookies_check(monkeypatch):
    def do_nothing(self):
        pass
    monkeypatch.setattr(
        Session, "_initialize_consent", do_nothing)

@pytest.fixture
def default_session(no_cookies_check):
    return Session()

@mock.patch('pytube.request.urlopen')
def load_and_init_from_playback_file(filename, mock_urlopen):
    """Load a gzip json playback file and create YouTube instance."""
    pb = load_playback_file(filename)

    # Mock the responses to YouTube
    mock_url_open_object = mock.Mock()
    mock_url_open_object.read.side_effect = [
        pb['watch_html'].encode('utf-8'),
        pb['js'].encode('utf-8')
    ]
    mock_urlopen.return_value = mock_url_open_object

    # Pytest caches this result, so we can speed up the tests
    #  by causing the object to fetch all the relevant information
    #  it needs. Previously, this was handled by prefetch_init()
    #  and descramble(), but this functionality has since been
    #  deferred
    
    v = PytubeYoutube(pb["url"])
    v.watch_html
    v._vid_info = pb['vid_info']
    v.js
    v.fmt_streams
    return v


@pytest.fixture
def cipher_signature():
    """Youtube instance initialized with video id 2lAe1cqCOXo."""
    filename = "yt-video-2lAe1cqCOXo-html.json.gz"
    return load_and_init_from_playback_file(filename)


@pytest.fixture
def presigned_video():
    """Youtube instance initialized with video id QRS8MkLhQmM."""
    filename = "yt-video-QRS8MkLhQmM-html.json.gz"
    return load_and_init_from_playback_file(filename)


@pytest.fixture
def stream_dict():
    """Youtube instance initialized with video id WXxV9g7lsFE."""
    file_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "mocks",
        "yt-video-WXxV9g7lsFE-html.json.gz",
    )
    with gzip.open(file_path, "rb") as f:
        content = json.loads(f.read().decode("utf-8"))
        return content['watch_html']