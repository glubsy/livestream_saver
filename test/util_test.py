import unittest
import re

from livestream_saver.util import none_filtered_out

# Run with pytest -vv -s --maxfail=10 test/unit_tests.py


class test_regex_in_config(unittest.TestCase):
    # TODO test various cases where a section override regex values in config

    def test_is_wanted_based_on_metadata(self):
        title = "serious business title アーカイブなし"
        desc = "non-archived video description"
        allowed = re.compile(".*archive.*|.*アーカイブ.*", re.I|re.M)
        blocked = re.compile(".*serious.*", re.I|re.M)

        assert none_filtered_out(
            (title, desc), allowed, blocked) is False

        assert none_filtered_out(
            (title, desc), allowed, None) is True

        assert none_filtered_out(
            (title, desc), None, None) is True

        assert none_filtered_out(
            (title, desc), None, blocked) is False

        assert none_filtered_out(
            (title, desc), blocked, blocked) is False

        # FIXME do proper parametrization
        assert none_filtered_out(
            (None, None), allowed, blocked) is True
        
        assert none_filtered_out(
            (None, None), None, None) is True
        
        assert none_filtered_out(
            (None, None), None, blocked) is True
        
        assert none_filtered_out(
            (None, None), blocked, blocked) is True