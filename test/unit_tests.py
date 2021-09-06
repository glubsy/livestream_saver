import pytest
from livestream_saver.util import split_by_plus, sanitize_filename
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


# # TODO split for each test case
# def test_user_supplied_itags():
#     tests = (
#         ("222+333", (333,)), # 222 is not a known itag
#         ("18", (18,)), # 222 is not a known itag
#         ("242+333", (242, 333)),
#         ("242", (242, )),
#         ("242+333+401", (242, 333, 401)),
#         ("242p+401p", (242, 401)),
#         ("", None),
#         (" ", None),
#         ("_", None),
#         ("_ _ _ a b", None),
#         ("_ _ _ a b+aaaa", None),
#         ("_ _ _ a b+aa+aa+", None),
#         ("++++++", None),
#         ("5p", (5,)),
#         ("5", (5,)),
#         ("222444", ()),
#         ("22p", (22,)),
#         (None, None),
#     )
#     for test in tests:
#         print(f"testing input: {test}")
#         assert split_by_plus(test[0]) == test[1]

@pytest.mark.parametrize(
    ("test_input", "expected"),
    [
        ("222+333", (222, 333)), # 222 is not a known itag
        ("222", (222,)),
        ("242+333", (242, 333)),
        ("242", (242, )),
        ("242+333+401", (242, 333, 401)),
        ("242p+401p", (242, 401)),
        ("", None),
        (" ", None),
        ("_", None),
        ("_ _ _ a b", None),
        ("_ _ _ a b+aaaa", None),
        ("_ _ _ a b+aa+aa+", None),
        ("++++++", None),
        ("5p", (5,)),
        ("5", (5,)),
        ("222444", (222444,)),
        ("22p", (22,)),
        (None, None),
    ]
)
def test_user_supplied_itags(test_input, expected):
    assert split_by_plus(test_input) == expected
