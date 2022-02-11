import re
# from json import loads
from typing import List
import pytube
import pytube.exceptions
from livestream_saver.extract import str_as_json


class PytubeYoutube(pytube.YouTube):
    """Wrapper to override some methods in order to bypass several restrictions
    due to lacking features in pytube (most notably live stream support)."""
    def __init__(self, *args, **kwargs):
        # Keep a handle to update its status
        self.parent = kwargs["parent"]
        self.session = kwargs.get("session")

        super().__init__(*args)
        # if "www" is omitted, it might force a redirect on YT's side
        # (with &ucbcb=1) and force us to update cookies again. YT is very picky
        # about that. Let's just avoid it.
        self.watch_url = f"https://www.youtube.com/watch?v={self.video_id}"
   
    def check_availability(self):
        """Skip this check to avoid raising pytube exceptions."""
        pass

    @property
    def vid_info(self):
        """Parse the raw vid info and return the parsed result.

        :rtype: Dict[Any, Any]
        """
        if not self.session:
            return super().vid_info()
        if self._vid_info:
            return self._vid_info

        # innertube = InnerTube(use_oauth=self.use_oauth, allow_cache=self.allow_oauth_cache)
        # innertube_response = innertube.player(self.video_id)
        self._vid_info = str_as_json(self.session.make_api_request(self.video_id))

        return self._vid_info

    @property
    def watch_html(self):
        """Override for livestream_saver. We have to make the request ourselves
        in order to pass the cookies."""
        # TODO get the DASH manifest (MPD) instead?
        if not self.session:
            return super().watch_html
        if self._watch_html:
            return self._watch_html
        try:
            self._watch_html = self.session.make_request(url=self.watch_url)
        except Exception as e:
            self.parent.logger.debug(f"Error getting watch_url: {e}")
            self._watch_html = None
        return self._watch_html

    @property
    def embed_html(self):
        """Override for livestream_saver. We have to make the request ourselves
        in order to pass the cookies."""
        if not self.session:
            return super().embed_html
        if self._embed_html:
            return self._embed_html
        self._embed_html = self.session.make_request(url=self.embed_url)
        return self._embed_html

    @property
    def js(self):
        """Override for livestream_saver. We have to make the request ourselves
        in order to pass the cookies."""
        if not self.session:
            return super().js
        if self._js:
            return self._js
        if pytube.__js_url__ != self.js_url:
            self._js = self.session.make_request(url=self.js_url)
            pytube.__js__ = self._js
            pytube.__js_url__ = self.js_url
        else:
            self._js = pytube.__js__
        return self._js


# Temporary backport from pytube 11.0.1
def get_throttling_function_name(js: str) -> str:
    """Extract the name of the function that computes the throttling parameter.

    :param str js:
        The contents of the base.js asset file.
    :rtype: str
    :returns:
        The name of the function used to compute the throttling parameter.
    """
    function_patterns = [
        # https://github.com/yt-dlp/yt-dlp/commit/48416bc4a8f1d5ff07d5977659cb8ece7640dcd8
        # var Bpa = [iha];
        # ...
        # a.C && (b = a.get("n")) && (b = Bpa[0](b), a.set("n", b),
        # Bpa.length || iha("")) }};
        # In the above case, `iha` is the relevant function name
        r'a\.[A-Z]\s*&&\s*\(b\s*=\s*a\.get\("n"\)\)\s*&&\s*\(b\s*=\s*([a-zA-Z0-9$]{3})(\[\d+\])?\(b\)',
    ]
    # print('Finding throttling function name')
    for pattern in function_patterns:
        regex = re.compile(pattern)
        function_match = regex.search(js)
        if function_match:
            print("finished regex search, matched: %s", pattern)
            if len(function_match.groups()) == 1:
                return function_match.group(1)
            idx = function_match.group(2)
            if idx:
                idx = idx.strip("[]")
                array = re.search(
                    r'var {nfunc}\s*=\s*(\[.+?\]);'.format(
                        nfunc=function_match.group(1)), 
                    js
                )
                if array:
                    array = array.group(1).strip("[]").split(",")
                    array = [x.strip() for x in array]
                    return array[int(idx)]

    raise pytube.RegexMatchError(
        caller="get_throttling_function_name", pattern="multiple"
    )
pytube.cipher.get_throttling_function_name = get_throttling_function_name


# Another temporary backport to fix https://github.com/pytube/pytube/issues/1163
def throttling_array_split(js_array):
    results = []
    curr_substring = js_array[1:]

    comma_regex = re.compile(r",")
    func_regex = re.compile(r"function\([^)]*\)")

    while len(curr_substring) > 0:
        if curr_substring.startswith('function') and func_regex.search(curr_substring) is not None:
            # Handle functions separately. These can contain commas
            match = func_regex.search(curr_substring)

            match_start, match_end = match.span()

            function_text = pytube.parser.find_object_from_startpoint(curr_substring, match.span()[1])
            full_function_def = curr_substring[:match_end + len(function_text)]
            results.append(full_function_def)
            curr_substring = curr_substring[len(full_function_def) + 1:]
        else:
            match = comma_regex.search(curr_substring)

            # Try-catch to capture end of array
            try:
                match_start, match_end = match.span()
            except AttributeError:
                match_start = len(curr_substring) - 1
                match_end = match_start + 1


            curr_el = curr_substring[:match_start]
            results.append(curr_el)
            curr_substring = curr_substring[match_end:]

    return results
pytube.cipher.throttling_array_split = throttling_array_split


# Another temporary hotfix https://github.com/pytube/pytube/issues/1199
def patched__init__(self, js: str):
    self.transform_plan: List[str] = pytube.cipher.get_transform_plan(js)
    var_regex = re.compile(r"^\$*\w+\W")
    var_match = var_regex.search(self.transform_plan[0])
    if not var_match:
        raise pytube.exceptions.RegexMatchError(
            caller="__init__", pattern=var_regex.pattern
        )
    var = var_match.group(0)[:-1]
    self.transform_map = pytube.cipher.get_transform_map(js, var)
    self.js_func_patterns = [
        r"\w+\.(\w+)\(\w,(\d+)\)",
        r"\w+\[(\"\w+\")\]\(\w,(\d+)\)"
    ]

    self.throttling_plan = pytube.cipher.get_throttling_plan(js)
    self.throttling_array = pytube.cipher.get_throttling_function_array(js)

    self.calculated_n = None

pytube.cipher.Cipher.__init__ = patched__init__



class PytubeStream(pytube.Stream):
    # Used for inheritance if we want a base class.
    pass