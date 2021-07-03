* Download Youtube livestreams from the very beginning to the end.
* Monitor a given channel for upcoming livestreams and download them automatically when they become active.

Cookies (in Netscape format) are needed to access membership-only videos as well as age-restricted videos (if you sold your soul to Youtube and your account is "verified").

# Monitoring a channel

Monitor a given Youtube channel for any upcoming livestream by requesting the channel's *videos* and *community* tabs every few minutes. 
It should automatically download a livestream as soon as it is detected in one of these requests.

Basic usage: `python livestream_saver.py monitor --cookie /path/to/cookie.txt CHANNEL_URL`

```
> python3 livestream_saver.py monitor --help

usage: livestream_saver.py monitor [-h] [--log {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [-c COOKIE] [-q MAX_VIDEO_QUALITY] 
[-o OUTPUT_DIR] [--channel_name CHANNEL_NAME] [-d] [-k] [--scan_delay SCAN_DELAY] URL

positional arguments:
  URL                   The Youtube channel to monitor for live streams. Either a full youtube URL, /channel/ID, or /c/name format.

optional arguments:
  -h, --help            show this help message and exit
  --log {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Log level. (default: INFO)
  -c COOKIE, --cookie COOKIE
                        Path to Netscape formatted cookie file. (default: None)
  -q MAX_VIDEO_QUALITY, --max_video_quality MAX_VIDEO_QUALITY
                        Use best available video resolution up to this height in pixels. (default: None)
  -o OUTPUT_DIR, --output_dir OUTPUT_DIR
                        Output directory where to save channel data. (default: ./)
  --channel_name CHANNEL_NAME
                        User-defined name of the channel to monitor. (default: None)
  -d, --delete_source   Delete source segment files once the final merging of them has been done. (default: False)
  -k, --keep_concat     Keep concatenated intermediary files even if merging of streams has been successful. Only useful for troubleshooting. (default: False)
  --scan_delay SCAN_DELAY
                        Interval in minutes to scan for channel activity. (default: 10.0)
```

# Downloading a live stream

Downloads an active Youtube livestream specified by its URL.

Basic usage: `python livestream_saver.py download --cookie /path/to/cookie.txt VIDEO_STREAM_URL`

```
> python3 livestream_saver.py download --help

usage: livestream_saver.py download [-h] [--log {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [-c COOKIE] [-q MAX_VIDEO_QUALITY] 
[-o OUTPUT_DIR] [-d] [-k] [--scan_delay SCAN_DELAY] URL

positional arguments:
  URL                   Youtube video stream URL to download.

optional arguments:
  -h, --help            show this help message and exit
  --log {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Log level. (default: INFO)
  -c COOKIE, --cookie COOKIE
                        Path to Netscape formatted cookie file. (default: None)
  -q MAX_VIDEO_QUALITY, --max_video_quality MAX_VIDEO_QUALITY
                        Use best available video resolution up to this height in pixels. (default: None)
  -o OUTPUT_DIR, --output_dir OUTPUT_DIR
                        Output directory where to write downloaded chunks. (default: ./)
  -d, --delete_source   Delete source files once final merge has been done. (default: False)
  -k, --keep_concat     Keep concatenated intermediary files even if merging of streams has been successful. Only useful for troubleshooting. (default: False)
  --scan_delay SCAN_DELAY
                        Interval in seconds to scan for status update. (default: 120.0)
```

# Merging segments

The *download* sub-command above should automatically merge the downloaded segments once the live stream has ended. If it failed for whatever reason, this sub-command can be invoked on the directory path to the downloaded segments. That directory should be named something like "segments_{VIDEO_ID}".

Basic usage: `python livestream_saver.py merge /path/to/segments_{VIDEO_ID}`

```
> python3 livestream_saver.py merge --help

usage: livestream_saver.py merge [-h] [--log {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [-d] [-k] [-o OUTPUT_DIR] PATH

positional arguments:
  PATH                  Path to directory holding vid/aud sub-directories in which segments have been downloaded as well as the metadata.txt file.

optional arguments:
  -h, --help            show this help message and exit
  --log {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Log level.
  -d, --delete_source   Delete source files (vid/aud) once final merging of streams has been successfully done.
  -k, --keep_concat     Keep concatenated intermediary files even if merging of streams has been successful. This is only useful for debugging.
  -o OUTPUT_DIR, --output_dir OUTPUT_DIR
                        Output directory where to write final merged file.
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

## Notes:

This is beta software. It should work, but in case it doesn't, feel free to report issues. Or better yet, fix them yourself and submit a merge request.

# TODO

* Monitor Twitch channels.
* Make sure age-restricted videos are not blocked by the new Youtube Cookies consent page.
* Send e-mail alerts in case of fatal error. 
