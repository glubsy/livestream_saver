# Download Youtube livestreams from the first chunk

This was originally a [gist](https://gist.github.com/glubsy/6e9b3061e074f528ea7153647f9fe615), forked from this [script by @cheadrian](https://gist.github.com/cheadrian/b661fb68a6a87ea64069e641cef68c3e).

## youtube_stream_download.py

While it works fine, it is very basic. You need to manually feed it the audio and video stream URI by editing it everytime.
There is no parsing of cookies nor Youtube page at all for now.

## youtube_stream_pre_merge.py

This script renames the downloaded chunks so that a [decent merge script](https://github.com/mrwnwttk/youtube_stream_capture/blob/main/merge.py) can properly merge them together (thanks to ffmpeg).
It creates symbolic links to files located in `stream_capture_IDHASH/aud` and `stream_capture_IDHASH/vid` into one directory called `segments_IDHASH`.
You can remove this directory after

## youtube_stream_join.sh

This script is currently obsolete, but could be fixed by calling ffmpeg the same way as the above decent script. I can't be bothered to rewrite this for now.

## Example:

1. Open the Youtube live stream in your browser.
2. Get the video and audio links from the developer tools (CTRL+I).
3. Copy them in `vid_link=` and `audio_link=` variables.
4. ```$> python youtube_stream_download.py ```.
5. Once chunks have been downloaded in `stream_capture_IDHASH`, call 
```$> python youtube_stream_pre_merge.py```.
6. Call [youtube_stream_capture/merge.py](https://github.com/mrwnwttk/youtube_stream_capture/blob/main/merge.py) like this ```$> merge.py https://www.youtube.com/watch?v=IDHASH```.
7. If everything worked, you have the final video file generated. You can remove the `segments_IDHASH` directory containing the symbolic links, as well as the source chunks.

### TODO:

* Use netscape cookies to parse livestream page and grab the URIs automatically.
* Write a script to scrape community tab for new live streams.
