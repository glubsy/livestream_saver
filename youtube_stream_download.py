#!/usr/bin/env python3
# With the help of this script you can download parts from the Youtube Video that is live streamed, from start of the stream till the end
# https://gist.github.com/glubsy/6e9b3061e074f528ea7153647f9fe615

import urllib.request
import os

# Note: you need to be logged in to get the URL, we do not use cookies here.
#E.G: "https://r4---sn-gqn-p5ns.googlevideo.com/videoplayback?expire=1603041842& ..... 2.20201016.02.00&sq=..."
#The sound link should contain: &mime=audio in it.
#Here's an example from NASA LIVE:
#VIDEO: https://r5---sn-gqn-p5ns.googlevideo.com/videoplayback?expire=1603165657&ei=eQmOX8TeFtS07gO1xLWwDA&ip=x.x.x.x&id=DDU-rZs-Ic4.1&itag=137&aitags=133%2C134%2C135%2C136%2C137%2C160&source=yt_live_broadcast&requiressl=yes&mh=PU&mm=44%2C29&mn=sn-gqn-p5ns%2Csn-c0q7lnsl&ms=lva%2Crdu&mv=m&mvi=5&pl=20&initcwndbps=1350000&vprv=1&live=1&hang=1&noclen=1&mime=video%2Fmp4&gir=yes&mt=1603143920&fvip=5&keepalive=yes&fexp=23915654&c=WEB&sparams=expire%2Cei%2Cip%2Cid%2Caitags%2Csource%2Crequiressl%2Cvprv%2Clive%2Chang%2Cnoclen%2Cmime%2Cgir&sig=AOq0QJ8wRQIgQMnxy1Yk3HLTpqbOGmjZYH1CXCTNx6u6PgngAVGi4EQCIQDWyaye-u_KGyVQ0HRUsyKVaAzyXbmzDqOGVGpIyP7VtA%3D%3D&lsparams=mh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Cinitcwndbps&lsig=AG3C_xAwRAIgR5QVZh23NcLE2nRpo5IT-axGEfUCJrXKMmJHjXQdkCYCIFLsIFacvPpy98zaNSB0RfXswacyc-Ru3sYeEjTFym43&alr=yes&cpn=LlPCcTsE_3Xao9Xh&cver=2.20201016.02.00&sq=2504043&rn=13&rbuf=21958
#AUDIO: https://r5---sn-gqn-p5ns.googlevideo.com/videoplayback?expire=1603165657&ei=eQmOX8TeFtS07gO1xLWwDA&ip=x.x.x.x&id=DDU-rZs-Ic4.1&itag=140&source=yt_live_broadcast&requiressl=yes&mh=PU&mm=44%2C29&mn=sn-gqn-p5ns%2Csn-c0q7lnsl&ms=lva%2Crdu&mv=m&mvi=5&pl=20&initcwndbps=1350000&vprv=1&live=1&hang=1&noclen=1&mime=audio%2Fmp4&gir=yes&mt=1603143920&fvip=5&keepalive=yes&fexp=23915654&c=WEB&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cvprv%2Clive%2Chang%2Cnoclen%2Cmime%2Cgir&sig=AOq0QJ8wRAIgWFTZLV1G33cKJoitlK7dUgNg1KuXyvC6F9F7Lc6x3gcCIHaGjehjvVAjUd6cqMnTLtBq9pPRfQWXM3bwI1qQYqpx&lsparams=mh%2Cmm%2Cmn%2Cms%2Cmv%2Cmvi%2Cpl%2Cinitcwndbps&lsig=AG3C_xAwRAIgR5QVZh23NcLE2nRpo5IT-axGEfUCJrXKMmJHjXQdkCYCIFLsIFacvPpy98zaNSB0RfXswacyc-Ru3sYeEjTFym43&alr=yes&cpn=LlPCcTsE_3Xao9Xh&cver=2.20201016.02.00&sq=2504045&rn=20&rbuf=17971
# Use MPV or VLC to play the parts. ffmpeg to re-encode / re-mux and then concatenate.

# You can copy the entire link here, it will be split automatically below
vid_link = ""
vid_link = f'{vid_link.split(r"&sq=")[0]}&sq=' # vid_link = "VIDEO LINK THE END -> &sq=" # Look for the substring mime=video to make sure

# You can copy the entire link here, it will be split automatically below
sound_link = ""
sound_link = f'{sound_link.split(r"&sq=")[0]}&sq=' # sound_link = "AUDIO LINK THE END -> &sq= " # Look for the substring mime=audio to make sure

# Each part should be equivalent to 1 seconds of video
# Please note if what you got on the your link from the sq parameter looks like this &sq=2504043 (high value) 
# don't expect the script to work starting from 1 anymore, because the first part _probably_ already expired.
# Try to download appropriate part numbers, like range(2501043, 2504043).

# Note: itag determines the quality. 140 for audio seems best, 135 for video means 480p.

# Get the hash ID right after "id=" and before ".1&itag=" (might change in the future?)
YT_HASH = vid_link.split("&id=")[1].split('.1')[0]
print(f'Capturing video with Hash ID: {YT_HASH}')

rootpath = f'stream_capture_{YT_HASH}'
vidpath = f'{rootpath}{os.sep}vid'
audpath = f'{rootpath}{os.sep}aud'

# os.makedirs(vidpath, 0o766, exist_ok=True) 
# os.makedirs(audpath, 0o766, exist_ok=True)

# the sequence numbers to begin and end at
begin = 1
# we do not know when to end, but don't end too soon
end = 10000000

try:
    os.makedirs(vidpath, 0o766) 
    os.makedirs(audpath, 0o766)
except FileExistsError as e:
    # If we resume, get the latest chunk file we already have
    begin = max([int(f[:f.index('.')]) for f in os.listdir(vidpath)])
    if begin > 1:
        # Step back one file just in case the latest chunk got only partially downloaded
        begin -= 1
print(f'Starting from part: {begin}')

try:
    for i in range(begin, end):
        vide_dow = f'{vid_link}{i}'
        sound_dow = f'{sound_link}{i}'
        
        # To have zero-padded filenames (not compatible with 
        # merge.py from https://github.com/mrwnwttk/youtube_stream_capture 
        # as it doesn't expect any zero padding )
        name_vid = vidpath + os.sep + f'{i:0{len(str(end))}}.mp4'
        name_sound = audpath + os.sep + f'{i:0{len(str(end))}}.m4a'
                
        # name_vid = f'{vidpath}{os.sep}{i}.mp4'
        # name_sound = f'{audpath}{os.sep}{i}.m4a'

        urllib.request.urlretrieve(vide_dow, name_vid) 
        urllib.request.urlretrieve(sound_dow, name_sound) 
        
        print(f"Downloaded part {i}")
except urllib.error.URLError as e:
    print(f'network error {e.reason}')
except (IOError) as e:
    print(f'file error: {e}')
