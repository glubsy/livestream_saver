"""Canonical imports for live-stream state objects.

We keep the concrete implementation in ``download.py`` for now because that
module still owns the downloader helpers and low-level segment logic. This
module is the safe import boundary for the stateful live-video classes so we
can continue extracting implementation here without creating circular imports.
"""

from livestream_saver.download import LiveVideo, YoutubeLiveStream

__all__ = ["LiveVideo", "YoutubeLiveStream"]
