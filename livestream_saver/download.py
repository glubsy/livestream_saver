#!/usr/bin/env python
from os import sep
import pdb
import logging
from contextlib import closing
from time import sleep
from enum import Flag, auto
from urllib.request import urlopen
import urllib.error
# from shutil import copyfileobj
# from subprocess import call
from livestream_saver.exceptions import *
from livestream_saver.util import *
from livestream_saver.itag import *

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class Status(Flag):
    OFFLINE = auto()
    AVAILABLE = auto()
    LIVE = auto()
    VIEWED_LIVE = auto()
    WAITING = auto()
    OK = AVAILABLE | LIVE | VIEWED_LIVE

class YoutubeLiveStream():
    def __init__(self, url, output_dir, video_quality, cookie):
        self.url = url
        self.output_dir = output_dir
        self.max_video_quality = video_quality
        self.video_id = get_video_id(url)
        self.cookie = cookie
        self.json = None
        self.video_title = None
        self.video_author = None
        self.video_base_url = None
        self.audio_base_url = None
        self.status = Status.OFFLINE
        self.scheduled_start_time = None
        self.logger = None

        self.run = True
        self.done = False

        capturedirname = f'stream_capture_{self.video_id}'
        capturedirpath = f'{output_dir}{sep}{capturedirname}'

        self.setup_logger(capturedirpath)

        self.video_outpath = f'{capturedirpath}{sep}vid'
        self.audio_outpath = f'{capturedirpath}{sep}aud'
        self.seg = self.get_first_segment((self.video_outpath, self.audio_outpath))


    def setup_logger(self, path):
        self.logger = logging.getLogger(__name__)
        logfile = logging.FileHandler(filename=path + sep +  "stream_download.log", delay=True)
        logfile.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
        logfile.setFormatter(formatter)
        self.logger.addHandler(logfile)

        conhandler = logging.StreamHandler()
        conhandler.setLevel(logging.WARNING)
        conhandler.setFormatter(formatter)
        self.logger.addHandler(conhandler)


    def get_first_segment(self, paths):
        """
        Creates each path in paths. If one already existed, return the last segment
        already downloaded, otherwise return 1.
        """
        dir_existed = False
        for path in paths:
            try:
                os.makedirs(path, 0o766)
            except FileExistsError:
                dir_existed = True

        # the sequence numbers to begin from
        seg = 1

        if dir_existed:
            # If one of the directories exists, assume we are resuming a previously
            # failed download attempt. Get the latest downloaded segment number,
            # unless one directory holds an earlier segment than the other.
            # video_last_segment = max([int(f[:f.index('.')]) for f in os.listdir(paths[0])])
            # audio_last_segment = max([int(f[:f.index('.')]) for f in os.listdir(paths[1])])
            # seg = min(video_last_segment, audio_last_segment)
            seg = min([
                    max([int(f[:f.index('.')]) for f in os.listdir(p)], default=1)
                    for p in paths
                ])

            # Step back one file just in case the latest segment got only partially
            # downloaded (we want to overwrite it to avoid a corrupted segment)
            if seg > 1:
                self.logger.warning(f"An output directory already existed. We assume a failed \
    download attempt.\nLast segment available was {seg}.")
                seg -= 1
        return seg


    def update_json(self):
        json = get_json(self.url, self.cookie)
        self.json = json
        if not json:
            self.logger.critical(f"WARNING: invalid JSON for {self.url}: {json}")
            self.status &= ~Status.AVAILABLE
            return
        logger.debug(json)


    def is_live(self):
        isLive = get_details(self.json, 'isLive')
        if isLive is not None and isLive is True:
            self.status |= Status.LIVE
        else:
            self.status &= ~Status.LIVE

        # Is this actually being streamed live?
        val = None
        for _dict in self.json.get('responseContext', {}).get('serviceTrackingParams', []):
            param = _dict.get('params', [])
            for key in param:
                if key.get('key') == 'is_viewed_live':
                    val = key.get('value')
                    break
        if val and val == "True":
            self.status |= Status.VIEWED_LIVE
        else:
            self.status &= ~Status.VIEWED_LIVE
        self.logger.critical(f"is_live() status {self.status}")


    def update_info(self):
        if not self.json:
            return
        if not self.video_title:
            get_details(self.json, 'title')
        if not self.video_author:
            get_details(self.json, 'author')

        self.is_live()

        self.logger.info("Stream seems to be viewed live. Good.")\
        if self.status & Status.VIEWED_LIVE else\
        self.logger.info("Stream is not being viewed live. This might not work!")

        # Check if video is indeed available through its reported status.
        playabilityStatus = self.json.get('playabilityStatus', {})
        status = playabilityStatus.get('status')

        if status == 'LIVE_STREAM_OFFLINE':
            self.status |= Status.OFFLINE
            scheduled_time = playabilityStatus.get('liveStreamability', {})\
                .get('liveStreamabilityRenderer', {}) \
                .get('offlineSlate', {}) \
                .get('liveStreamOfflineSlateRenderer', {}) \
                .get('scheduledStartTime')
            if scheduled_time is not None:
                self.logger.warning(f"Scheduled start time: {scheduled_time}. We wait...")
                self.status |= Status.WAITING
                self.scheduled_start_time = scheduled_time
                raise WaitingException(self.video_id, playabilityStatus.get('reason', 'No reason found.'), scheduled_time)
            elif (Status.LIVE | Status.VIEWED_LIVE) not in self.status:
                raise WaitingException(self.video_id, playabilityStatus.get('reason', 'No reason found.'))
            raise OfflineException(self.video_id, playabilityStatus.get('reason', 'No reason found.'))

        elif status == 'LOGIN_REQUIRED':
            raise NoLoginException(self.video_id, \
                playabilityStatus.get('reason', 'No reason found.'))

        elif status == 'UNPLAYABLE':
            raise UnplayableException(self.video_id, \
playabilityStatus.get('reason', 'No reason found.'))

        elif status != 'OK':
            subreason = playabilityStatus.get('errorScreen', {})\
                                         .get('playerErrorMessageRenderer', {})\
                                         .get('subreason', {})\
                                         .get('simpleText', \
                                              'No subreason found in JSON.')
            self.logger.warning(f"Livestream {self.video_id} playability status is not OK: \
{playabilityStatus.get('reason', 'No reason found')}. Sub-reason: {subreason}")
            self.status &= ~Status.AVAILABLE
            # return
        else: # status == 'OK'
            self.status |= Status.AVAILABLE
            self.status &= ~Status.OFFLINE
            self.status &= ~Status.WAITING

        video_quality = get_best_quality(self.json, "video", self.max_video_quality)
        audio_quality = get_best_quality(self.json, "audio")
        if video_quality:
            self.video_base_url = get_base_url(self.json, video_quality)
        if audio_quality:
            self.audio_base_url = get_base_url(self.json, audio_quality)

        self.logger.debug(f"Video ID {self.video_id}")
        self.logger.debug(f"Video status {self.status}")
        self.logger.debug(f"Video title {self.video_title}")
        self.logger.debug(f"Video author {self.video_author}")
        self.logger.debug(f"Video base url {self.video_base_url}")
        self.logger.debug(f"Audio base url {self.audio_base_url}")


    def download(self):
        self.logger.info(f'Will start downloading from segment number {self.seg}.')

        while not self.done:
            try:
                self.update_json()
                self.update_info()

                self.logger.critical(f"DEBUG status is {self.status}")

                if not self.status == Status.OK:
                    self.logger.critical(f"Could not download {self.url}: \
stream unavailable or not a livestream.")
                    return
            except WaitingException as e:
                self.logger.critical(f"{e}")
                logger.warning(f"Status is {self.status}. Waiting for 60 seconds...")
                sleep(5)
                continue
            except OfflineException as e:
                self.logger.critical(f"{e}")
                raise e
            except Exception as e:
                self.logger.critical(f"{e}")
                raise e

            while not self.done:
                try:
                    self.do_download()
                except EmptySegmentException as e:
                    self.update_json()
                    self.is_live()
                    if Status.LIVE | Status.VIEWED_LIVE in self.status:
                        self.logger.critical(f"It seems the stream has not really ended. Retrying in 20 secs...")
                        sleep(5)
                        continue
                    self.logger.critical(f"The stream is not live anymore. Done.")
                    self.done = True
                    break

                # except Exception as e: # Timeout, end of stream
                #     # TODO lookup the urlretrieve exception again "empty something"
                #     self.logger.critical(f"Unhandled Exception in download(): {e}")
                #     self.done = True
                #     break

    def do_download(self):
        padding = 10
        try:
            while True: 
                video_segment_url = f'{self.video_base_url}&sq={self.seg}'
                audio_segment_url = f'{self.audio_base_url}&sq={self.seg}'
                self.logger.warning(f"Downloading segment {self.seg}...")

                # To have zero-padded filenames (not compatible with
                # merge.py from https://github.com/mrwnwttk/youtube_stream_capture
                # as it doesn't expect any zero padding )
                video_segment_filename = f'{self.video_outpath}{sep}{self.seg:0{padding}}.mp4'
                audio_segment_filename = f'{self.audio_outpath}{sep}{self.seg:0{padding}}.m4a'

                #urllib.request.urlretrieve(video_segment_url, video_segment_filename)
                with closing(urlopen(video_segment_url)) as in_stream:
                    headers = in_stream.headers
                    status = in_stream.status
                    self.logger.info(f"Seg status: {status}")
                    self.logger.debug(f"Seg headers: {headers}")
                    if not write_to_file(in_stream, video_segment_filename)\
                        and status == 204\
                        and not headers.get('X-Segment-Lmt'):
                        self.logger.warning(f"Segment {self.seg} is empty, stream might have ended...")
                        raise EmptySegmentException("")

                # Assume stream has ended if last segment is empty
                # if os.stat(video_segment_filename).st_size == 0:
                #     os.unlink(video_segment_filename)
                #     self.done = True
                #     break

                #urllib.request.urlretrieve(audio_segment_url, audio_segment_filename)
                with closing(urlopen(audio_segment_url)) as in_stream:
                    write_to_file(in_stream, audio_segment_filename)

                self.seg += 1
        except urllib.error.URLError as e:
            self.logger.critical(f'Network error {e.reason}')
            raise e
        except (IOError) as e:
            self.logger.critical(f'File error: {e}')
            raise e

