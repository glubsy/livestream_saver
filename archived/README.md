# Archived scripts.

These obsolete scripts may still be useful as long as they work. They may still be useful in case of emergency (ie. getting a fatal error in a pinch). The main program above was based on these originally. They are very limited, and should still mostly work, apart from the Youtube throttling problem which requires advanced pre-processing of the download URLs such as computing token signature (thanks Youtube for breaking our things on purpose!).

This was originally a [gist](https://gist.github.com/glubsy/6e9b3061e074f528ea7153647f9fe615), forked from this [script by @cheadrian](https://gist.github.com/cheadrian/b661fb68a6a87ea64069e641cef68c3e). The script is called `manual_download.py`.

## manual_download.py

While it should work fine, it is very basic and not very convenient to use.
For this you have to be logged into Youtube, grab the links from your web browser's developer tools, and manually replace the `vid_link` and `sound_link` variables every time.
After the script has downloaded all the segments, use `pre_merge.py`.

## pre_merge.py

This script is a simple compatibility bridge for the [merge script](https://github.com/mrwnwttk/youtube_stream_capture/blob/main/merge.py) located in the `youtube_stream_capture`. It simply emulates renaming the downloaded segments by creating symbolic links to the files located in `stream_capture_IDHASH/aud` and `stream_capture_IDHASH/vid` into one directory called `segments_IDHASH` and with appropriate filenames.

### Example:

1. Open a Youtube live stream in your browser
2. Get the video and audio links from the developer tools (CTRL+I)
3. Copy them in `vid_link=` and `audio_link=` variables
4. ```$> python manual_download.py```
5. Once chunks have been downloaded in `stream_capture_IDHASH`, call
```$> python pre_merge.py```
6. Call [youtube_stream_capture/merge.py](https://github.com/mrwnwttk/youtube_stream_capture/blob/main/merge.py) like this ```$> merge.py https://www.youtube.com/watch?v=IDHASH```
7. If everything worked, you have the final video file generated. You can remove the `segments_IDHASH` directory containing the symbolic links, as well as the source chunks

## join_segments.sh

This script is currently obsolete, but could be fixed by calling ffmpeg the same way as the above decent script. I can't be bothered to rewrite this for now.
