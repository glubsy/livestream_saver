Download Youtube livestreams from the beginning to the end.

Each module can be used independently from the others if needed.

# stream_downloader.py

Downloads a specific Youtube livestream URL.

* Usage:
`python stream_downloader.py --cookie /path/to/cookie.txt {YOUTUBE_VIDEOSTREAM_URL}`

# merge.py

The *stream_downloader.py* script should automatically merge the downloaded segments once the live stream has ended. If for whatever reason it failed, this script can be invoked on the directory path to the downloaded segments. If you used the downloader script, that directory should be named "segments_{VIDEO_ID}".

* Usage:
`python merge.py /path/to/segments_{VIDEO_ID}`

# stream_monitor.py

Monitor a given Youtube channel for any upcoming livestream. Automatically downloads livestreams as soon as they become available.

* Usage:
`python stream_monitor.py --cookie /path/to/cookie.txt {YOUTUBE_CHANNEL_URL}`

**TODO: this script is not yet implemented!**

## Archived:

Archived scripts which may still be useful in case of emergency.