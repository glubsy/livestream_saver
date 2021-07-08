* Download Youtube livestreams from the very beginning to the end.
* Monitor a given channel for upcoming livestreams and download them automatically when they become active.

Cookies (in Netscape format) are needed to access membership-only videos as well as age-restricted videos (if you sold your soul to Youtube and your account is "verified").

The example config file is a only a convenience to override the default values, but it is optional.


# Monitoring a channel

Monitor a given Youtube channel for any upcoming livestream by requesting the channel's *videos* and *community* tabs every few minutes. 
It should automatically download a livestream as soon as it is detected in one of these requests.

Basic usage: `python livestream_saver.py monitor --cookie /path/to/cookie.txt CHANNEL_URL`

```
> python3 livestream_saver.py monitor --help

usage: livestream_saver.py monitor [-h] [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [-c CONF_FILE] [--cookie COOKIE_PATH] [-q MAX_VIDEO_QUALITY] [-o OUTPUT_DIR] [--channel-name CHANNEL_NAME] [-d] [-n] [-k] [--scan-delay SCAN_DELAY] [--email-notifications] [YOUTUBE_CHANNEL_URL]

positional arguments:
  YOUTUBE_CHANNEL_URL   The Youtube channel to monitor for live streams. Either a full youtube URL, /channel/ID, or /c/name format. (default: None)

optional arguments:
  -h, --help            show this help message and exit
  --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Log level. (Default: INFO)
  -c CONF_FILE, --conf-file CONF_FILE
                        Path to config file to use. (Default: $(pwd)/livestream_saver.cfg))
  --cookie COOKIE_PATH  Path to Netscape formatted cookie file.
  -q MAX_VIDEO_QUALITY, --max-video-quality MAX_VIDEO_QUALITY
                        Use best available video resolution up to this height in pixels. Example: "360" for maximum height 360p. Get the highest available resolution by default.
  -o OUTPUT_DIR, --output-dir OUTPUT_DIR
                        Output directory where to save channel data. (Default: $(pwd))
  --channel-name CHANNEL_NAME
                        User-defined name of the channel to monitor. Will fallback to channel ID deduced from the URL otherwise.
  -d, --delete-source   Delete source segment files once the final merging of them has been done. (default: False)
  -n, --no-merge        Do not merge segments after live streams has ended. (default: False)
  -k, --keep-concat     Keep concatenated intermediary files even if merging of streams has been successful. Only useful for troubleshooting. (default: False)
  --scan-delay SCAN_DELAY
                        Interval in minutes to scan for channel activity. (Default: 15.0)
  --email-notifications
                        Enables sending e-mail reports to administrator. (Default: False)
```

# Downloading a live stream

Downloads an active Youtube livestream specified by its URL.

Basic usage: `python livestream_saver.py download --cookie /path/to/cookie.txt VIDEO_STREAM_URL`

```
> python3 livestream_saver.py download --help

usage: livestream_saver.py download [-h] [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [-c CONF_FILE] [--cookie COOKIE_PATH] [-q MAX_VIDEO_QUALITY] [-o OUTPUT_DIR] [-d | -n] [-k] [--scan-delay SCAN_DELAY] [--email-notifications] YOUTUBE_VIDEO_URL

positional arguments:
  YOUTUBE_VIDEO_URL     Youtube video stream URL to download.

optional arguments:
  -h, --help            show this help message and exit
  --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Log level. (Default: INFO)
  -c CONF_FILE, --conf-file CONF_FILE
                        Path to config file to use. (Default: $(pwd)/livestream_saver.cfg)
  --cookie COOKIE_PATH  Path to Netscape formatted cookie file.
  -q MAX_VIDEO_QUALITY, --max-video-quality MAX_VIDEO_QUALITY
                        Use best available video resolution up to this height in pixels. Example: "360" for maximum height 360p. Get the highest available resolution by default.
  -o OUTPUT_DIR, --output-dir OUTPUT_DIR
                        Output directory where to write downloaded chunks. (Default: $(pwd))
  -d, --delete-source   Delete source files once final merge has been done. (default: False)
  -n, --no-merge        Do not merge segments after live streams has ended. (default: False)
  -k, --keep-concat     Keep concatenated intermediary files even if merging of streams has been successful. Only useful for troubleshooting. (default: False)
  --scan-delay SCAN_DELAY
                        Interval in minutes to scan for status update. (Default: 2.0)
  --email-notifications
                        Enable sending e-mail reports to administrator. (Default: False)
```

# Merging segments

The *download* sub-command above should automatically merge the downloaded segments once the live stream has ended. If it failed for whatever reason, this sub-command can be invoked on the directory path to the downloaded segments. That directory should be named something like "segments_{VIDEO_ID}".

Basic usage: `python livestream_saver.py merge /path/to/segments_{VIDEO_ID}`

```
> python3 livestream_saver.py merge --help

usage: livestream_saver.py merge [-h] [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [-c CONF_FILE] [-d] [-k] [-o OUTPUT_DIR] PATH

positional arguments:
  PATH                  Path to directory holding vid/aud sub-directories in which segments have been downloaded as well as the metadata.txt file.

optional arguments:
  -h, --help            show this help message and exit
  --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Log level. (Default: INFO)
  -c CONF_FILE, --conf-file CONF_FILE
                        Path to config file to use. (Default: $(pwd)/livestream_saver.cfg)
  -d, --delete-source   Delete source files (vid/aud) once final merging of streams has been successfully done. (default: False)
  -k, --keep-concat     Keep concatenated intermediary files even if merging of streams has been successful. This is only useful for debugging. (default: False)
  -o OUTPUT_DIR, --output-dir OUTPUT_DIR
                        Output directory where to write final merged file. (default: None)
```

# Dependencies

* python3
* ffmpeg to concatenate segments and merge them into one file 
* [Pillow](https://pillow.readthedocs.io/en/stable/installation.html) (optional) to convert webp thumbnail

Install with `python3 -m pip install --upgrade Pillow` or with your preferred package manager.

# Archived

The `archived` directory contains archived scripts which may still be useful in case of emergency (ie. getting a fatal error in a pinch). These are what the program above was based on originally. They should still work, but are very limited.

# License

GPLv3

# Notes:

This is beta software. It should work, but in case it doesn't, feel free to report issues. Or better yet, fix them yourself and submit a merge request.

# TODO

* Add proxy support.
* Fetch segments in threads to catch up faster.
* Make sure age-restricted videos are not blocked by the new Youtube Cookies consent page.
* Monitor Twitch channels.
* Send e-mail alerts in case of fatal error. 
