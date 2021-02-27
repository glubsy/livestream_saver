#!/usr/bin/env python
from os import sep, path
import logging
from datetime import date, datetime
from time import time, sleep
from json import dumps
from contextlib import closing
from enum import Flag, auto
from urllib.request import urlopen
import urllib.error
from livestream_saver import exceptions
from livestream_saver import util
from livestream_saver import itag

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class YoutubeLiveStream():
    def __init__(self, url, output_dir, max_video_quality, cookie):
        self.url = url
        self.max_video_quality = max_video_quality

        self.video_info = {}
        self.video_info['id'] = get_video_id(url)

        self.cookie = cookie
        self.json = None
        # self.video_title = None
        # self.video_author = None
        self.thumbnail_url = None
        # self.video_itag = None
        # self.audio_itag = None
        self.video_base_url = None
        self.audio_base_url = None
        self.seg = 1
        self.status = Status.OFFLINE
        # self.scheduled_timestamp = None
        self.logger = None
        self.done = False

        self.output_dir = self.create_output_dir(output_dir)

        self.setup_logger(self.output_dir)

        self.video_outpath = f'{self.output_dir}{sep}vid'
        self.audio_outpath = f'{self.output_dir}{sep}aud'

        self.update_json()
        self.populate_info()
        self.download_thumbnail()


    def create_output_dir(self, output_dir):
        capturedirname = f"stream_capture_{self.video_info['id']}"
        capturedirpath = f'{output_dir}{sep}{capturedirname}'
        os.makedirs(capturedirpath, 0o766, exist_ok=True)
        return capturedirpath


    def setup_logger(self, path):
        self.logger = logging.getLogger(__name__)
        logfile = logging.FileHandler(\
            filename=path + sep +  "stream_download.log", delay=True)
        logfile.setLevel(logging.DEBUG)
        formatter = logging.Formatter(\
            '%(asctime)s - %(levelname)s - %(name)s - %(message)s')
        logfile.setFormatter(formatter)
        self.logger.addHandler(logfile)

        conhandler = logging.StreamHandler()
        conhandler.setLevel(logging.INFO)
        conhandler.setFormatter(formatter)
        self.logger.addHandler(conhandler)


    def get_first_segment(self, paths):
        """
        Create each path in paths. If one already existed, return the last
        segment already downloaded, otherwise return 1.
        """
        # If one of the directories exists, assume we are resuming a previously
        # failed download attempt.
        dir_existed = False
        for path in paths:
            try:
                os.makedirs(path, 0o766)
            except FileExistsError:
                dir_existed = True

        # the sequence numbers to begin from
        seg = 1

        if dir_existed:
            # Get the latest downloaded segment number,
            # unless one directory holds an earlier segment than the other.
            # video_last_segment = max([int(f[:f.index('.')]) for f in os.listdir(paths[0])])
            # audio_last_segment = max([int(f[:f.index('.')]) for f in os.listdir(paths[1])])
            # seg = min(video_last_segment, audio_last_segment)
            seg = min([
                    max([int(f[:f.index('.')].split('_')[0])
                    for f in os.listdir(p)], default=1)
                    for p in paths
                ])

            # Step back one file just in case the latest segment got only partially
            # downloaded (we want to overwrite it to avoid a corrupted segment)
            if seg > 1:
                self.logger.warning(f"An output directory already existed. \
We assume a failed download attempt.\nLast segment available was {seg}.")
                seg -= 1
        return seg


    def is_live(self):
        isLive = self.json.get('videoDetails', {}).get('isLive')
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


    def update_json(self):
        json = get_json(self.url, self.cookie)
        self.json = json
        if not json:
            self.logger.critical(f"WARNING: invalid JSON for {self.url}: {json}")
            self.status &= ~Status.AVAILABLE
            return
        logger.debug(dumps(json, indent=4))


    def populate_info(self):
        if not self.json:
            return

        self.video_info['title'] = self.json.get('videoDetails', {}).get('title')
        self.video_info['author'] = self.json.get('videoDetails', {}).get('author')

        if not self.thumbnail_url:
            tlist = self.json.get('videoDetails', {}).get('thumbnail', {}).get('thumbnails', [])
            if tlist:
                # Grab the last one, probably always highest resolution
                self.thumbnail_url = tlist[-1].get('url')

        self.video_info['scheduled_time'] = get_scheduled_time(self.json.get('playabilityStatus', {}))

        self.logger.info(f"Video ID: {self.video_info['id']}")
        self.logger.info(f"Video title: {self.video_info['title']}")
        self.logger.info(f"Video author: {self.video_info['author']}")


    def download_thumbnail(self):
        thumbnail_path = self.output_dir + sep + 'thumbnail.jpg'
        if self.thumbnail_url and not os.path.exists(thumbnail_path):
            with closing(urlopen(self.thumbnail_url)) as in_stream:
                write_to_file(in_stream, thumbnail_path)


    def update_metadata(self):
        if not self.video_info.get('download_date'):
            self.video_info['download_date'] = date.fromtimestamp(time()).__str__()

        if self.video_info.get('video_itag'):
            for k, v in video_height_ranking.items():
                if self.video_info['video_itag'] in v:
                    self.video_info['video_resolution'] = k
                    break

        if self.video_info.get('scheduled_timestamp'):
            self.video_info['scheduled_time'] = datetime.utcfromtimestamp(self.video_info['scheduled_timestamp'])

        metadata_file = self.output_dir + sep + 'metadata.json'
        if os.path.exists(metadata_file):
            return
        with open(metadata_file, 'w') as fp:
            json.dump(obj=self.video_info, fp=fp, indent=4)


    def update_status(self):
        if not self.json:
            return

        self.is_live()

        self.logger.info("Stream seems to be viewed live. Good.")\
        if self.status & Status.VIEWED_LIVE else\
        self.logger.info("Stream is not being viewed live. This might not work!")

        # Check if video is indeed available through its reported status.
        playabilityStatus = self.json.get('playabilityStatus', {})
        status = playabilityStatus.get('status')

        if status == 'LIVE_STREAM_OFFLINE':
            self.status |= Status.OFFLINE
            sched_time = get_scheduled_time(playabilityStatus)
            if sched_time is not None:
                self.status |= Status.WAITING
                self.video_info['scheduled_timestamp'] = sched_time
                self.logger.info(f"Scheduled start time: {sched_time}. We wait...")
                raise WaitingException(self.video_info['id'], playabilityStatus\
                    .get('reason', 'No reason found.'), sched_time)
            elif (Status.LIVE | Status.VIEWED_LIVE) not in self.status:
                raise WaitingException(self.video_info['id'], \
                    playabilityStatus.get('reason', 'No reason found.'))
            raise OfflineException(self.video_info['id'], \
                playabilityStatus.get('reason', 'No reason found.'))

        elif status == 'LOGIN_REQUIRED':
            raise NoLoginException(self.video_info['id'], \
                playabilityStatus.get('reason', 'No reason found.'))

        elif status == 'UNPLAYABLE':
            raise UnplayableException(self.video_info['id'], \
playabilityStatus.get('reason', 'No reason found.'))

        elif status != 'OK':
            subreason = playabilityStatus.get('errorScreen', {})\
                                         .get('playerErrorMessageRenderer', {})\
                                         .get('subreason', {})\
                                         .get('simpleText', \
                                              'No subreason found in JSON.')
            self.logger.warning(f"Livestream {self.video_info['id']} \
playability status is: {status} \
{playabilityStatus.get('reason', 'No reason found')}. Sub-reason: {subreason}")
            self.status &= ~Status.AVAILABLE
            # return
        else: # status == 'OK'
            self.status |= Status.AVAILABLE
            self.status &= ~Status.OFFLINE
            self.status &= ~Status.WAITING

        self.logger.debug(f"Stream status {self.status}")


    def update_download_urls(self):
        video_quality = get_best_quality(self.json, "video", self.max_video_quality)
        audio_quality = get_best_quality(self.json, "audio")

        if video_quality:
            self.video_base_url = get_base_url(self.json, video_quality)
            self.video_info['video_itag'] = video_quality
        if audio_quality:
            self.audio_base_url = get_base_url(self.json, audio_quality)
            self.video_info['audio_itag'] = audio_quality

        self.logger.debug(f"Video base url {self.video_base_url}")
        self.logger.debug(f"Audio base url {self.audio_base_url}")


    def download(self):
        self.seg = self.get_first_segment((self.video_outpath, self.audio_outpath))
        self.logger.info(f'Will start downloading from segment number {self.seg}.')

        while not self.done:
            try:
                self.update_json()
                self.update_status()

                self.logger.debug(f"Status is {self.status}")

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

            self.update_download_urls()
            self.update_metadata()

            while True:
                try:
                    self.do_download()
                except EmptySegmentException as e:
                    self.logger.info(e)
                    self.update_json()
                    self.is_live()
                    if Status.LIVE | Status.VIEWED_LIVE in self.status:
                        self.logger.critical(f"It seems the stream has not really ended. Retrying in 20 secs...")
                        sleep(5)
                        continue
                    self.logger.critical(f"The stream is not live anymore. Done.")
                    self.done = True
                    return
                except Exception as e:
                    self.logger.critical(f"Unhandled exception. Aborting. {e}. ")
                    return


    def do_download(self):
        if not self.video_base_url or not self.audio_base_url:
            raise Exception("Missing video or audio base url!")
        attempt = 0
        while True:
            try:
                video_segment_url = f'{self.video_base_url}&sq={self.seg}'
                audio_segment_url = f'{self.audio_base_url}&sq={self.seg}'
                self.logger.warning(f"Downloading segment {self.seg}...")

                # To have zero-padded filenames (not compatible with
                # merge.py from https://github.com/mrwnwttk/youtube_stream_capture
                # as it doesn't expect any zero padding )
                video_segment_filename = f'{self.video_outpath}{sep}{self.seg:0{10}}_video.ts'
                audio_segment_filename = f'{self.audio_outpath}{sep}{self.seg:0{10}}_audio.ts'

                # urllib.request.urlretrieve(video_segment_url, video_segment_filename)
                # # Assume stream has ended if last segment is empty
                # if os.stat(video_segment_filename).st_size == 0:
                #     os.unlink(video_segment_filename)
                #     break

                with closing(urlopen(video_segment_url)) as in_stream:
                    headers = in_stream.headers
                    status = in_stream.status
                    self.logger.info(f"Seg status: {status}")
                    self.logger.debug(f"Seg headers: {headers}")
                    if not write_to_file(in_stream, video_segment_filename)\
                        and status == 204\
                        and not headers.get('X-Segment-Lmt'):
                        raise EmptySegmentException(f"Segment {self.seg} is empty, stream might have ended...")

                # urllib.request.urlretrieve(audio_segment_url, audio_segment_filename)
                with closing(urlopen(audio_segment_url)) as in_stream:
                    write_to_file(in_stream, audio_segment_filename)

                self.seg += 1
            except urllib.error.URLError as e:
                self.logger.critical(f'Network error: {e.reason}')
                if attempt > 30:
                    raise e
                attempt += 1
                self.logger.info(f"Waiting for 60 seconds...")
                sleep(10)
                continue
            except (IOError) as e:
                self.logger.critical(f'File error: {e}')
                raise e


class Status(Flag):
    OFFLINE = auto()
    AVAILABLE = auto()
    LIVE = auto()
    VIEWED_LIVE = auto()
    WAITING = auto()
    OK = AVAILABLE | LIVE | VIEWED_LIVE
