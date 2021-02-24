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


class OfflineException(Exception):
    def __init__(self, video_id, reason):
        self.video_id = video_id
        self.reason = reason
        super().__init__(self.error_string)

    @property
    def error_string(self):
        return f"{self.video_id} is offline: {self.reason}. It might be temporary only."



class EmptyChunkException(Exception):
    pass
