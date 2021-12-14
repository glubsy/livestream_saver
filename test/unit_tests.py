import pytest
import unittest
import re
from livestream_saver.merge import sanitize_filename
from livestream_saver.hooks import is_wanted_based_on_metadata

# Run with pytest -vv -s --maxfail=10 test/unit_tests.py

def test_sanitize_filename():
    illegal_windows_chars = r'<>:"/\|?*'
    # input, expected
    tests = (
        ("a" * 300, "a" * 255),
        ("a" * 300 + ".mp4", "a" * (255 - len(".mp4")) + ".mp4"),
        ("きょうもapexの練習だー.mkv", "きょうもapexの練習だー.mkv"),
        ("き?ょ*<う?も>a?p?e/x?|の?\\練?習::?だ?ー??.mkv", "きょうもapexの練習だー.mkv"),
        ("きょうもapexの練習だー" * 100 + ".mkv", "きょうもapexの練習だーきょうもapexの練習だーきょうもapexの練習だーきょうもapexの練習だーきょうもapexの練習だーきょうもapexの練習だーきょうもapexの練習だーきょうもapexの練習だーき.mkv"),
    )
    for test in tests:
        res = sanitize_filename(test[0])
        assert test[1] == res
        assert len(res) <= 255
        for char in illegal_windows_chars:
            assert char not in res


class test_regex_in_config(unittest.TestCase):
    # TODO test various cases where a section override regex values in config

    def test_is_wanted_based_on_metadata(self):
        title = "serious business title アーカイブなし"
        desc = "non-archived video description"
        allowed = re.compile(".*archive.*|.*アーカイブ.*", re.I|re.M)
        blocked = re.compile(".*serious.*", re.I|re.M)

        assert is_wanted_based_on_metadata(
            (title, desc), allowed, blocked) is False

        assert is_wanted_based_on_metadata(
            (title, desc), allowed, None) is True

        assert is_wanted_based_on_metadata(
            (title, desc), None, None) is True

        assert is_wanted_based_on_metadata(
            (title, desc), None, blocked) is False

        assert is_wanted_based_on_metadata(
            (title, desc), blocked, blocked) is False
