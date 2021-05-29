#!/usr/bin/env python3
# With the help of this script you can download parts from the Youtube Video
# that is live streamed, from the start of the stream
# https://gist.github.com/glubsy/6e9b3061e074f528ea7153647f9fe615

import urllib.request
import urllib.error
from os import makedirs, sep, listdir

# Note: you need to be logged in to get the URL, we do not use cookies directly here.
# E.G: "https://r4---sn-gqn-p5ns.googlevideo.com/videoplayback?expire=1603041842& ..... 2.20201016.02.00&sq=..."
# The sound link should contain: &mime=audio in it.
# Here's an example from NASA LIVE:
# VIDEO: https://r5---sn-gqn-p5ns.googlevideo.com/videoplayback?expire=1603165657&ei=eQmOX8TeFtS07gO1xLWwDA&ip=x.x.x.x&id=DDU-rZs-Ic4.1&itag=137&aitags=133%2C134%2C135%2C136%2C137%2C160&source=yt_live_broadcast&requiressl=yes&mh=PU&mm=44%2C29&mn=sn-gqn-p5ns%2Csn-c0q7lnsl&ms=lva%2Crdu&mv=m&mvi=5&pl=20&initcwndbps=1350000&vprv=1&live=1&hang=1&noclen=1&mime=video%2Fmp4&gir=yes&mt=1603143920&fvip=5&keepalive=yes&fexp=23915654&c=WEB&sparams=expire%2Cei%2Cip%2Cid%2Caitags%2Csource%2Crequiressl%2Cvprv%2Clive%2Chang%2Cnoclen%2Cmime%2Cgir&sig=AOq0QJ8wRQIgQMnxy1Yk3HLTpqbOGmjZYH1CXCTNx6u6PgngAVGi4EQCIQDWyaye-u_KGyVQ0HRUsyKVaAzyXbmzDqOGVGpIyP7VtA%3D%3D&lsparams=mh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Cinitcwndbps&lsig=AG3C_xAwRAIgR5QVZh23NcLE2nRpo5IT-axGEfUCJrXKMmJHjXQdkCYCIFLsIFacvPpy98zaNSB0RfXswacyc-Ru3sYeEjTFym43&alr=yes&cpn=LlPCcTsE_3Xao9Xh&cver=2.20201016.02.00&sq=2504043&rn=13&rbuf=21958
# AUDIO: https://r5---sn-gqn-p5ns.googlevideo.com/videoplayback?expire=1603165657&ei=eQmOX8TeFtS07gO1xLWwDA&ip=x.x.x.x&id=DDU-rZs-Ic4.1&itag=140&source=yt_live_broadcast&requiressl=yes&mh=PU&mm=44%2C29&mn=sn-gqn-p5ns%2Csn-c0q7lnsl&ms=lva%2Crdu&mv=m&mvi=5&pl=20&initcwndbps=1350000&vprv=1&live=1&hang=1&noclen=1&mime=audio%2Fmp4&gir=yes&mt=1603143920&fvip=5&keepalive=yes&fexp=23915654&c=WEB&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cvprv%2Clive%2Chang%2Cnoclen%2Cmime%2Cgir&sig=AOq0QJ8wRAIgWFTZLV1G33cKJoitlK7dUgNg1KuXyvC6F9F7Lc6x3gcCIHaGjehjvVAjUd6cqMnTLtBq9pPRfQWXM3bwI1qQYqpx&lsparams=mh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Cinitcwndbps&lsig=AG3C_xAwRAIgR5QVZh23NcLE2nRpo5IT-axGEfUCJrXKMmJHjXQdkCYCIFLsIFacvPpy98zaNSB0RfXswacyc-Ru3sYeEjTFym43&alr=yes&cpn=LlPCcTsE_3Xao9Xh&cver=2.20201016.02.00&sq=2504045&rn=20&rbuf=17971
# Use MPV or VLC to play the parts. ffmpeg to re-encode / re-mux and then concatenate.

# You can copy the entire link here, it will be split automatically below
video_link = ""
# video_link = "VIDEO LINK THE END -> &sq=" # Look for the substring mime=video to make sure
video_link = f'{video_link.split(r"&sq=")[0]}&sq='

# You can copy the entire link here, it will be split automatically below
sound_link = ""
# sound_link = "AUDIO LINK THE END -> &sq= " # Look for the substring mime=audio to make sure
sound_link = f'{sound_link.split(r"&sq=")[0]}&sq='

# On Youtube, each segment can be equivalent to 1 to several seconds of video (depending on latency settings)
# The itag determines the quality. 140 for audio seems best, 135 for video means 480p.
# See https://github.com/pytube/pytube/blob/master/pytube/itags.py for reference.

# The boadcastID follows the videoID. Format is currently: "&id=videoID.broadcastID&itags="
YT_HASH = video_link.split("&id=")[1].split('.')[0]
print(f'Capturing video with Hash ID: {YT_HASH}')

rootpath = f'stream_capture_{YT_HASH}'
vidpath = f'{rootpath}{sep}vid'
audpath = f'{rootpath}{sep}aud'

# The sequence number to start downloading from (starts at 0).
seg = 0

try:
    makedirs(vidpath, 0o766)
    makedirs(audpath, 0o766)
except FileExistsError as e:
    # If we resume, get the latest chunk file we already have
    seg = max([int(f[:f.index('.')]) for f in listdir(vidpath)], default=1)
    if seg > 0:
        # Step back one file just in case the latest chunk got only partially
        # downloaded (we want to overwrite it, for good measure)
        seg -= 1
print(f'Starting from segment: {seg}')

padding = 10
try:
    while True:
        video_url = f'{video_link}{seg}'
        sound_url = f'{sound_link}{seg}'

        video_output_file = vidpath + sep + f'{seg:0{padding}}.ts'
        audio_output_file = audpath + sep + f'{seg:0{padding}}.ts'

        urllib.request.urlretrieve(video_url, video_output_file)
        urllib.request.urlretrieve(sound_url, audio_output_file)

        print(f"Downloaded part {seg}")
        seg += 1

except urllib.error.URLError as e:
    print(f'network error {e}')
except (IOError) as e:
    print(f'file error: {e}')
