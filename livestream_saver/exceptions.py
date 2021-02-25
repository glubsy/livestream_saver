class NoLoginException(Exception):
    def __init__(self, video_id, reason):
        self.video_id = video_id
        self.reason = reason
        super().__init__(self.error_string)

    @property
    def error_string(self):
        return f"{self.video_id} requires a valid login: {self.reason}"


class UnplayableException(Exception):
    def __init__(self, video_id, reason):
        self.video_id = video_id
        self.reason = reason
        super().__init__(self.error_string)

    @property
    def error_string(self):
        return f"{self.video_id} is unplayable: {self.reason}. \
Perhaps it requires a valid cookie and/or membership, or it is region blocked."


class WaitingException(Exception):
    def __init__(self, video_id, reason, scheduled_start_time=None):
        self.video_id = video_id
        self.reason = reason
        self.scheduled_start_time = scheduled_start_time
        super().__init__(self.error_string)

    @property
    def error_string(self):
        if not self.scheduled_start_time:
            return f"{self.video_id} waiting for stream to start: {self.reason}."
        return f"{self.video_id} waiting for stream to start: {self.reason}. At time: {self.scheduled_start_time}"


class OfflineException(Exception):
    def __init__(self, video_id, reason):
        self.video_id = video_id
        self.reason = reason
        super().__init__(self.error_string)

    @property
    def error_string(self):
        return f"{self.video_id} is offline: {self.reason}. It might be temporary only."


class EmptySegmentException(Exception):
    pass
