import pytest
from pathlib import Path
import logging
# logging.getLogger("livestream_saver").setLevel(logging.DEBUG)
# logging.getLogger("livestream_saver.request").setLevel(logging.DEBUG)

from livestream_saver.request import CookieJar, ASession
from livestream_saver.constants import *
from livestream_saver import extract
from livestream_saver.monitor import get_tabs_from_json, get_videos_from_tab


# log = logging.getLogger(__name__)
log = logging.getLogger()
log.setLevel(logging.DEBUG)
# Example usage: https://stackoverflow.com/questions/4673373/logging-within-pytest-tests
# pytest -vv -s --maxfail=10 -o log_cli=true test/request_test.py

COOKIE_PATH = "~/Cookies/firefox_cookies.txt"
PICKLE_PATH = "~/Cookies/firefox_cookies.pickle"

@pytest.fixture()
async def session_cjar():
    asession = ASession(cookie_path=Path(COOKIE_PATH))
    # asession = ASession()
    yield asession
    await asession.session.close()
    Path(PICKLE_PATH).expanduser().unlink(missing_ok=True)

@pytest.fixture()
async def session_empty_cjar():
    asession = ASession()
    yield asession
    await asession.session.close()


@pytest.mark.asyncio
async def test_load_cookies(session_cjar):
    s = session_cjar.session

    assert s.cookie_jar._cookie_path == \
        Path("~/Cookies/firefox_cookies.pickle").expanduser()

    log.debug("c in s.cookie_jar:")
    for c in s.cookie_jar:
        log.debug(c)
    # FIXME Something might be off here...
    # assert len(session.session.cookie_jar) == len(session.meta_cookie_jar)
    # session.meta_cookie_jar.clear()
    # assert len(session.meta_cookie_jar) == 0
    # for c in session.session.cookie_jar:
    # import pprint
    # pp = pprint.PrettyPrinter(indent=4, compact=True, depth=150, width=500)
    # pp.pprint(session.session.cookie_jar._cookies)
    # print(f"_cookies in a_cookie_jar: {session.session.cookie_jar._cookies}")
    


    # s.cookie_jar.save()

    await session_cjar.initialize_consent()

    for c in s.cookie_jar:
        # if "CONSENT" in str(c):
        log.debug(f"{OKGREEN}From cookie jar:{ENDC} {c}{OKBLUE} type {type(c)}{ENDC}")

    # for c in session.meta_cookie_jar:
    #     print(c)

    # DEBUG WORKS
    html = await session_cjar.make_request("https://www.youtube.com/channel/UCoSrY_IQQVpmIRZ9Xf-y93g/community")
    json = extract.str_as_json(extract.initial_player_response(html))
    tabs = get_tabs_from_json(json)
    videos = get_videos_from_tab(tabs, 'Community')
    print(f"videos: {videos}")
    assert len(videos) > 0

    session_cjar.session.cookie_jar.save()
    assert Path(PICKLE_PATH).expanduser().exists()


@pytest.mark.asyncio
async def test_no_preloaded_cookies(session_empty_cjar):
    s = session_empty_cjar.session

    log.debug("c in s.cookie_jar:")
    for c in s.cookie_jar:
        log.debug(c)
    # assert len(session.session.cookie_jar) == len(session.meta_cookie_jar)
    # session.meta_cookie_jar.clear()
    # assert len(session.meta_cookie_jar) == 0
    # for c in session.session.cookie_jar:
    # import pprint
    # pp = pprint.PrettyPrinter(indent=4, compact=True, depth=150, width=500)
    # pp.pprint(session.session.cookie_jar._cookies)
    # print(f"_cookies in a_cookie_jar: {session.session.cookie_jar._cookies}")

    # s.cookie_jar.save()

    await session_empty_cjar.initialize_consent()

    for c in s.cookie_jar:
        # if "CONSENT" in str(c):
        log.debug(f"{OKGREEN}Cookie in cookie jar:{ENDC} {c} {OKBLUE}type {type(c)}{ENDC}")

    # for c in session.meta_cookie_jar:
    #     print(c)

    # DEBUG WORKS
    html = await session_empty_cjar.make_request("https://www.youtube.com/channel/UCoSrY_IQQVpmIRZ9Xf-y93g/community")
    json = extract.str_as_json(extract.initial_player_response(html))
    tabs = get_tabs_from_json(json)
    videos = get_videos_from_tab(tabs, 'Community')
    print(f"videos: {videos}")
    assert len(videos) > 0

    session_empty_cjar.session.cookie_jar.save()
    assert Path(PICKLE_PATH).expanduser().exists()
