from typing import Optional
from datetime import datetime


class MissingVideoId(Exception):
    pass


class VideoStatusException(Exception):
    def __init__(self, video_id, reason):
        self.video_id = video_id
        self.reason = reason
        super().__init__(self.error_string)

    @property
    def error_string(self):
        return f"{self.video_id} status error: {self.reason}"


class NoLoginException(VideoStatusException):
    @property
    def error_string(self):
        return f"{self.video_id} requires a valid login: {self.reason}"


class UnplayableException(VideoStatusException):
    @property
    def error_string(self):
        return (
            f"{self.video_id} is unplayable: {self.reason}. "
            "Perhaps it requires a valid cookie and/or membership, "
            "or it is region blocked."
        )


class WaitingException(VideoStatusException):
    def __init__(
        self,
        video_id: str,
        reason: str,
        scheduled_start_time: Optional[float] = None
    ):
        self.video_id = video_id
        self.scheduled_start_time = scheduled_start_time
        super().__init__(self.error_string, reason=reason)

    @property
    def error_string(self):
        msg = f"{self.video_id} waiting for stream to start: {self.reason}."
        if self.scheduled_start_time:
            msg += (
                " Scheduled time: "
                f"{datetime.fromtimestamp(self.scheduled_start_time)}"
            )
        return msg


class OfflineException(VideoStatusException):
    @property
    def error_string(self):
        return (
            f"{self.video_id} is offline: {self.reason}. "
            "It might be temporary only."
        )


class OutdatedAppException(VideoStatusException):
    """
    This seems to be returned by the innertube API if the advertised at random.
    Might be due to the client version we advertise, or some unknown signature
    we are not transmitting properly.
    """
    @property
    def error_string(self):
        return f"Advertised client is deemed outdated: {self.reason}"


class EmptySegmentException(Exception):
    pass

class ForbiddenSegmentException(Exception):
    pass

class TabNotFound(Exception):
    pass