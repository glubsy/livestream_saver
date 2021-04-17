Download Youtube livestreams from the beginning to the end.
Monitor a given channel for upcoming any upcoming livestream and download them automatically when they are active.

Each module can be used independently from the others if needed.
Cookies (in Netscape format) are needed to access membership-only videos.

## Notes:

This is still beta software. It should work, but in case it doesn't, feel free to report issues. Or better yet, fix them yourself and submit a merge request.

# channel_monitor.py

Monitor a given Youtube channel for any upcoming livestream by requesting the channel's pages every few minutes. Automatically download a livestream as soon as it becomes active.

Basic usage:

`python channel_monitor.py --cookie /path/to/cookie.txt CHANNEL_URL`

```
usage: channel_monitor.py [-h] [-c COOKIE] [-q MAX_VIDEO_QUALITY] [-o OUTPUT_DIR] [--channel_name CHANNEL_NAME] [-d] [--scan_delay SCAN_DELAY] [--log LOG] url

positional arguments:
  url                   Youtube Channel to monitor for live streams. Either a full youtube URL or /channel/hash format.

optional arguments:
  -h, --help            show this help message and exit
  -c COOKIE, --cookie COOKIE
                        Path to Netscape formatted cookie file.
  -q MAX_VIDEO_QUALITY, --max_video_quality MAX_VIDEO_QUALITY
                        Use best available video resolution up to this height in pixels.
  -o OUTPUT_DIR, --output_dir OUTPUT_DIR
                        Output directory where to save channel data.
  --channel_name CHANNEL_NAME
                        User-defined name of the channel to monitor.
  -d, --delete_source   Delete source segment files once the final merging of them has been done.
  --scan_delay SCAN_DELAY
                        Interval in minutes to scan for activity (default 10.0).
  --log LOG             Log level. [DEBUG, INFO, WARNING, ERROR, CRITICAL]

```

# stream_downloader.py

Downloads an active Youtube livestream specified by its URL.

Basic usage:

`python stream_downloader.py --cookie /path/to/cookie.txt VIDEO_STREAM_URL`

```
usage: stream_downloader.py [-h] [-c COOKIE] [-q MAX_VIDEO_QUALITY] [-o OUTPUT_DIR] [-d] [--log {DEBUG,INFO,WARNING,ERROR,CRITICAL}] url

positional arguments:
  url                   Youtube URL to download.

optional arguments:
  -h, --help            show this help message and exit
  -c COOKIE, --cookie COOKIE
                        Path to Netscape formatted cookie file.
  -q MAX_VIDEO_QUALITY, --max_video_quality MAX_VIDEO_QUALITY
                        Use best available video resolution up to this height in pixels.
  -o OUTPUT_DIR, --output_dir OUTPUT_DIR
                        Output directory where to write downloaded chunks.
  -d, --delete_source   Delete source files once final merge has been done.
  --log {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Log level.
```

# merge.py

The *stream_downloader.py* script above should automatically merge the downloaded segments once the live stream has ended. If it failed for whatever reason, this script can be invoked on the directory path to the downloaded segments. That directory should be named "segments_{VIDEO_ID}".

Basic usage:

`python merge.py /path/to/segments_{VIDEO_ID}`

```
usage: merge.py [-h] [-d] [-o OUTPUT_DIR] [--log LOG] path

positional arguments:
  path                  Path to directory holding vid/aud sub-directories in which segments have been downloaded as well as the metadata.txt file.

optional arguments:
  -h, --help            show this help message and exit
  -d, --delete_source   Delete source files once final merging of stream has been successfully done.
  -o OUTPUT_DIR, --output_dir OUTPUT_DIR
                        Output directory where to write final merged file.
  --log LOG             Log level. [DEBUG, INFO, WARNING, ERROR, CRITICAL]
```

# Dependencies

* python3
* ffmpeg

# Archived

The `arvhived` directory contains archived scripts which may still be useful in case of emergency (ie. in case of a fatal error). These are what the programs above were based on originally. They should still work, but are very limited.

# License:

GPLv3