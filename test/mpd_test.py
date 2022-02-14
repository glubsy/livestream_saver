from unittest.mock import MagicMock
from livestream_saver.youtube import MPD


def test_segments(regular_mpd, innertube_response):
    # FIXME not the same video_id for mpd and json reponse, oh well
    mock_ytv = MagicMock()
    mock_ytv.json.return_value = innertube_response
    mock_ytv.session.make_request = MagicMock(return_value=regular_mpd)
    # mock_ytv.session.make_request.return_value = regular_mpd
    
    mpd = MPD(parent=mock_ytv)
    streams = mpd.streams
    mock_ytv.session.make_request.assert_called_with(mpd.url)
    assert streams is not None
    assert len(streams) == 8

    earliest_seg, latest_seg = mpd.segments
    assert earliest_seg == 4953
    assert latest_seg == 4955

    # TODO test when fetching mpd data again with different values
    # see if the old values are invalidated