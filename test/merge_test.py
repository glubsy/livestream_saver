from livestream_saver.merge import sanitize_filename, get_filetype
from pathlib import Path
from unittest import TestCase


class TestSanitizeFilename(TestCase):
    def test_sanitize_filename(self):
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


class TestGetFiletype(TestCase):
    def test_get_filetype(self):
        assert get_filetype(
            Path(__file__).parent / "samples" / "img.png") == "png"
        assert get_filetype(
            Path(__file__).parent / "samples" / "img.jpg") == "jpg"
        assert get_filetype(
            Path(__file__).parent / "samples" / "img.webp") == "webp"
