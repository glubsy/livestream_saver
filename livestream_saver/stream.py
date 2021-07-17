from typing import Dict, Sequence
# from pytube import extract


class Stream:
    def __init__(self, stream: Dict) -> None:
        self.url = stream
        self.itag = int(stream["itag"])
        self.mime_type, self.codec = None, None
    
    def extract_mime_type(self):
        raise NotImplementedError


class StreamQuery(Sequence):
    pass