Download Youtube livestreams from the first chunk

This was originally a [gist](https://gist.github.com/glubsy/6e9b3061e074f528ea7153647f9fe615), forked from this [script by @cheadrian](https://gist.github.com/cheadrian/b661fb68a6a87ea64069e641cef68c3e). That script is still made available for manual download as `manual_download.py`.

Each module can be used independently of the others if needed.

# Modules

## yt_livestream_downloader.py

Downloads a specific Youtube livestream URL.

* Usage: 
`yt_livestream_downloader.py --cookie /path/to/cookie.txt YOUTUBE_VIDEOSTREAM_URL`

## yt_livestream_monitor.py

[TODO]

Monitor a specific channel for upcoming livestreams. Calls yt_livestream_downloader.py when one is detected.

* Usage:
`yt_livestream_monitor.py --cookie /path/to/cookie.txt YOUTUBE_CHANNEL_URL`


# Archived:

## manual_download.py

While it should work fine, it is very basic and not very convenient to use.
For this you have to be logged into Youtube, grab the links from your web browser's developer tools, and manually replace the `vid_link` and `sound_link` variables every time.
After the script has downloaded all the chunks, use `youtube_stream_pre_merge.py`.

## youtube_stream_pre_merge.py

This script renames the downloaded chunks so that a [decent merge script](https://github.com/mrwnwttk/youtube_stream_capture/blob/main/merge.py) can properly merge them together (thanks to ffmpeg).
It creates symbolic links to files located in `stream_capture_IDHASH/aud` and `stream_capture_IDHASH/vid` into one directory called `segments_IDHASH`.
You can remove this directory after it has built the final video file.

## Example:

1. Open the Youtube live stream in your browser
2. Get the video and audio links from the developer tools (CTRL+I)
3. Copy them in `vid_link=` and `audio_link=` variables
4. ```$> python youtube_stream_download.py ```
5. Once chunks have been downloaded in `stream_capture_IDHASH`, call 
```$> python youtube_stream_pre_merge.py```
6. Call [youtube_stream_capture/merge.py](https://github.com/mrwnwttk/youtube_stream_capture/blob/main/merge.py) like this ```$> merge.py https://www.youtube.com/watch?v=IDHASH```
7. If everything worked, you have the final video file generated. You can remove the `segments_IDHASH` directory containing the symbolic links, as well as the source chunks


## youtube_stream_join.sh

This script is currently obsolete, but could be fixed by calling ffmpeg the same way as the above decent script. I can't be bothered to rewrite this for now.
