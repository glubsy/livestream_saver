#!/usr/bin/env python
from os import sep, path, makedirs, listdir
from platform import system
import logging
from datetime import date, datetime
from time import time, sleep
from json import dumps, dump, loads
from contextlib import closing
from enum import Flag, auto
from urllib.request import urlopen
import urllib.error
import requests
from livestream_saver import exceptions
from livestream_saver import util
from livestream_saver import itag

SYSTEM = system()
ISPOSIX = SYSTEM == 'Linux' or SYSTEM == 'Darwin'
ISWINDOWS = SYSTEM == 'Windows'
COPY_BUFSIZE = 1024 * 1024 if ISWINDOWS else 64 * 1024

# logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


class YoutubeLiveStream:
    def __init__(self, url, output_dir, cookie={}, video_id=None, max_video_quality=None, log_level=logging.INFO):
        self.url = url
        self.max_video_quality = max_video_quality

        self.video_info = {}
        self.video_info['id'] = self.get_video_id(url) if not video_id else video_id

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
        self.error = None

        self.output_dir = self.create_output_dir(output_dir)

        self.setup_logger(self.output_dir, log_level)

        self.video_outpath = f'{self.output_dir}{sep}vid'
        self.audio_outpath = f'{self.output_dir}{sep}aud'

        self.update_json()
        self.populate_info()
        self.download_thumbnail()


    def create_output_dir(self, output_dir):
        capturedirname = f"stream_capture_{self.video_info['id']}"
        capturedirpath = f'{output_dir}{sep}{capturedirname}'
        makedirs(capturedirpath, 0o766, exist_ok=True)
        return capturedirpath


    def setup_logger(self, path, log_level):
        self.logger = logging.getLogger("download" + "." + self.video_info['id'])
        self.logger.setLevel(logging.DEBUG)
        # File output
        logfile = logging.FileHandler(\
            filename=path + sep + "download.log", delay=True)
        logfile.setLevel(logging.DEBUG)
        formatter = logging.Formatter(\
            '%(asctime)s - %(levelname)s - %(name)s - %(message)s')
        logfile.setFormatter(formatter)
        self.logger.addHandler(logfile)

        # Console output
        conhandler = logging.StreamHandler()
        conhandler.setLevel(log_level)
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
                makedirs(path, 0o766)
            except FileExistsError:
                dir_existed = True

        # the sequence numbers to begin from
        seg = 1

        if dir_existed:
            # Get the latest downloaded segment number,
            # unless one directory holds an earlier segment than the other.
            # video_last_segment = max([int(f[:f.index('.')]) for f in listdir(paths[0])])
            # audio_last_segment = max([int(f[:f.index('.')]) for f in listdir(paths[1])])
            # seg = min(video_last_segment, audio_last_segment)
            seg = min([
                    max([int(f[:f.index('.')].split('_')[0])
                    for f in listdir(p)], default=1)
                    for p in paths
                ])

            # Step back one file just in case the latest segment got only partially
            # downloaded (we want to overwrite it to avoid a corrupted segment)
            if seg > 1:
                self.logger.warning(f"An output directory already existed. \
We assume a failed download attempt. Last segment available was {seg}.")
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
        self.logger.debug(f"is_live() status {self.status}")


    def update_json(self):
        _json = self.get_json(self.url, self.cookie)
        self.json = _json
        if not _json:
            self.logger.critical(f"WARNING: invalid JSON for {self.url}: {_json}")
            self.status &= ~Status.AVAILABLE
            return
        self.logger.debug("\n" + dumps(_json, indent=4))


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

        self.video_info['scheduled_time'] = self.get_scheduled_time(self.json.get('playabilityStatus', {}))

        self.logger.debug(f"Video ID: {self.video_info['id']}")
        self.logger.debug(f"Video title: {self.video_info['title']}")
        self.logger.debug(f"Video author: {self.video_info['author']}")


    def download_thumbnail(self):
        thumbnail_path = self.output_dir + sep + 'thumbnail'
        if self.thumbnail_url and not path.exists(thumbnail_path):
            with closing(urlopen(self.thumbnail_url)) as in_stream:
                self.write_to_file(in_stream, thumbnail_path)


    def update_metadata(self):
        if not self.video_info.get('download_date'):
            self.video_info['download_date'] = date.fromtimestamp(time()).__str__()

        if self.video_info.get('video_itag'):
            for k, v in itag.video_height_ranking.items():
                if self.video_info['video_itag'] in v:
                    self.video_info['video_resolution'] = k
                    break

        if self.video_info.get('scheduled_timestamp'):
            self.video_info['scheduled_time'] = datetime.utcfromtimestamp(self.video_info['scheduled_timestamp'])

        # TODO get the description once the stream has started

        metadata_file = self.output_dir + sep + 'metadata.json'
        if path.exists(metadata_file):
            # FIXME this avoids writing this file more than once for now. No further updates.
            return
        with open(metadata_file, 'w') as fp:
            dump(obj=self.video_info, fp=fp, indent=4)


    def update_status(self):
        if not self.json:
            return

        self.is_live()

        self.logger.info("Stream seems to be viewed live. Good.")\
        if self.status & Status.VIEWED_LIVE else\
        self.logger.warning("Stream is not being viewed live. This might not work!")

        # Check if video is indeed available through its reported status.
        playabilityStatus = self.json.get('playabilityStatus', {})
        status = playabilityStatus.get('status')

        if status == 'LIVE_STREAM_OFFLINE':
            self.status |= Status.OFFLINE
            sched_time = self.get_scheduled_time(playabilityStatus)
            if sched_time is not None:
                self.status |= Status.WAITING
                self.video_info['scheduled_timestamp'] = sched_time
                self.logger.info(f"Scheduled start time: {sched_time}. We wait...")
                raise exceptions.WaitingException(self.video_info['id'], playabilityStatus\
                    .get('reason', 'No reason found.'), sched_time)
            elif (Status.LIVE | Status.VIEWED_LIVE) not in self.status:
                raise exceptions.WaitingException(self.video_info['id'], \
                    playabilityStatus.get('reason', 'No reason found.'))
            raise exceptions.OfflineException(self.video_info['id'], \
                playabilityStatus.get('reason', 'No reason found.'))

        elif status == 'LOGIN_REQUIRED':
            raise exceptions.NoLoginException(self.video_info['id'], \
                playabilityStatus.get('reason', 'No reason found.'))

        elif status == 'UNPLAYABLE':
            raise exceptions.UnplayableException(self.video_info['id'], \
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

        self.logger.info(f"Stream status {self.status}")


    def update_download_urls(self):
        video_quality = self.get_best_quality(self.json, "video", self.max_video_quality)
        audio_quality = self.get_best_quality(self.json, "audio")

        if video_quality:
            self.video_base_url = self.get_base_url(self.json, video_quality)
            self.video_info['video_itag'] = video_quality
        if audio_quality:
            self.audio_base_url = self.get_base_url(self.json, audio_quality)
            self.video_info['audio_itag'] = audio_quality

        self.logger.debug(f"Video base url {self.video_base_url}")
        self.logger.debug(f"Audio base url {self.audio_base_url}")


    def download(self):
        self.seg = self.get_first_segment((self.video_outpath, self.audio_outpath))
        self.logger.info(f'Will start downloading from segment number {self.seg}.')

        while not self.done and not self.error:
            try:
                self.update_json()
                self.update_status()

                self.logger.debug(f"Status is {self.status}")

                if not self.status == Status.OK:
                    self.logger.critical(f"Could not download {self.url}: \
stream unavailable or not a livestream.")
                    return
            except exceptions.WaitingException as e:
                self.logger.warning(f"Status is {self.status}. \
Waiting for 60 seconds...")
                sleep(60)
                continue
            except exceptions.OfflineException as e:
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
                except exceptions.EmptySegmentException as e:
                    self.logger.info(e)
                    self.update_json()
                    self.is_live()
                    if Status.LIVE | Status.VIEWED_LIVE in self.status:
                        self.logger.warning(f"It seems the stream has not \
really ended. Retrying in 20 secs...")
                        sleep(20)
                        continue
                    self.logger.warning(f"The stream is not live anymore. Done.")
                    self.done = True
                    break
                except Exception as e:
                    self.logger.exception(f"Unhandled exception. Aborting.")
                    self.error = f"{e}"
                    break
        if self.done:
            self.logger.info(f"Finished downloading {self.video_info.get('id')}.")
        if self.error:
            self.logger.critical(f"Some kind of error occured during download? {self.error}")


    def do_download(self):
        if not self.video_base_url or not self.audio_base_url:
            raise Exception("Missing video or audio base url!")
        attempt = 0
        while True:
            try:
                video_segment_url = f'{self.video_base_url}&sq={self.seg}'
                audio_segment_url = f'{self.audio_base_url}&sq={self.seg}'
                self.logger.info(f"Downloading segment {self.seg}...")

                # To have zero-padded filenames (not compatible with
                # merge.py from https://github.com/mrwnwttk/youtube_stream_capture
                # as it doesn't expect any zero padding )
                video_segment_filename = f'{self.video_outpath}{sep}{self.seg:0{10}}_video.ts'
                audio_segment_filename = f'{self.audio_outpath}{sep}{self.seg:0{10}}_audio.ts'

                # urllib.request.urlretrieve(video_segment_url, video_segment_filename)
                # # Assume stream has ended if last segment is empty
                # if stat(video_segment_filename).st_size == 0:
                #     unlink(video_segment_filename)
                #     break

                with closing(urlopen(video_segment_url)) as in_stream:
                    headers = in_stream.headers
                    status = in_stream.status
                    self.logger.debug(f"Seg status: {status}")
                    self.logger.debug(f"Seg headers: {headers}")
                    if not self.write_to_file(in_stream, video_segment_filename):
                        if status == 204 and not headers.get('X-Segment-Lmt'):
                            raise exceptions.EmptySegmentException(\
                                f"Segment {self.seg} is empty, stream might have ended...")
                        self.logger.critical(f"Waiting for 60 seconds before retrying...")
                        sleep(60)
                        continue

                # urllib.request.urlretrieve(audio_segment_url, audio_segment_filename)
                with closing(urlopen(audio_segment_url)) as in_stream:
                    self.write_to_file(in_stream, audio_segment_filename)

                self.seg += 1
            except urllib.error.URLError as e:
                self.logger.critical(f'Network error: {e.reason}')
                if attempt > 30:
                    raise e
                attempt += 1
                self.logger.critical(f"Waiting for 60 seconds before retrying...")
                sleep(60)
                continue
            except (IOError) as e:
                self.logger.critical(f'File error: {e}')
                raise e


    def print_found_quality(self, item, datatype):
        if datatype == "video":
            keys = ["itag", "qualityLabel", "mimeType", "bitrate", "quality", "fps"]
        else:
            keys = ["itag", "audioQuality", "mimeType", "bitrate", "audioSampleRate"]
        try:
            result = f"Available {datatype} quality: "
            for k in keys:
                result += f"{k}: {item.get(k)}\t"
            self.logger.info(result)
        except Exception as e:
            self.logger.critical(f"Exception while trying to print found {datatype} quality: {e}")


    def get_best_quality(self, _json, datatype, maxq=None):
        # Select the best possible quality, with maxq (str) as the highest possible

        quality_ids = []
        label = 'qualityLabel' if datatype == 'video' else 'audioQuality'
        streamingData = _json.get('streamingData', {})
        adaptiveFormats = streamingData.get('adaptiveFormats', {})

        if not streamingData or not adaptiveFormats:
            self.logger.error(f"Could not get {datatype} quality format. \
    Missing streamingData or adaptiveFormats")
            return None

        for _dict in adaptiveFormats:
            if _dict.get(label, None) is not None:
                quality_ids.append(_dict.get('itag'))
                self.print_found_quality(_dict, datatype)

        if datatype == "video":
            #  Select only resolutions below user-defined maxq.
            # global itag.video_height_ranking
            ranking = []
            for k, v in itag.video_height_ranking.items():
                if maxq and int(k) > maxq:
                    continue
                for height in v:
                    ranking.append(height)
        else:
            # global itag.quality_audio_ranking
            ranking = itag.quality_audio_ranking

        for i in ranking:
            if i in quality_ids:
                chosen_quality = i
                for d in _json['streamingData']['adaptiveFormats']:
                    if chosen_quality == d.get('itag'):
                        if datatype == "video":
                            chosen_quality_labels = f"{d.get('qualityLabel')} \
    type: {d.get('mimeType')} bitrate: {d.get('bitrate')}"
                        else:
                            chosen_quality_labels = f"{d.get('audioQuality')} \
    type: {d.get('mimeType')} bitrate: {d.get('bitrate')}"
                break

        self.logger.warning(f"Chosen {datatype} quality: \
    itag {chosen_quality}; height: {chosen_quality_labels}")

        return chosen_quality


    def get_scheduled_time(self, playabilityStatus):
        s = playabilityStatus.get('liveStreamability', {})\
                                .get('liveStreamabilityRenderer', {}) \
                                .get('offlineSlate', {}) \
                                .get('liveStreamOfflineSlateRenderer', {}) \
                                .get('scheduledStartTime')
        if s:
            return int(s)
        return s

    def get_base_url(self, _json, itag):
        for _dict in _json['streamingData']['adaptiveFormats']:
            if _dict.get('itag', None) == itag:
                return _dict.get('url', None)


    def get_video_id(self, url):
        # Argument format:
        # https://youtu.be/njrI8ZDQ7ho or https://youtube.com/?v=njrI8ZDQ7ho
        if "?v=" in url:
            video_id = url.split("v=")[1]
        elif "youtu.be" in url:
            video_id = url.split('/')[-1]

        if 11 > len(video_id) > 12:
            self.logger.critical(f"Error getting videoID. Length = {len(self.video_id)} \
    (too long?) {self.video_id}")
        return video_id


    def get_video_id_re(self, url_pattern):
        """
        Naive way to get the video ID from the canonical URL.
        """
        pattern = r"(?:v=|\/)([0-9A-Za-z_-]{11}).*"
        regex = re.compile(pattern)
        results = regex.search(url_pattern)
        if not results:
            self.logger.warning(f"Error while looking for {url_pattern}")
        self.logger.info(f"matched regex search: {url_pattern}")
        return results.group(1)


    def get_json(self, url, cookie={}):
        """Returns a dictionary from the json string."""
        headers = {
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 \
    (KHTML, like Gecko) Chrome/88.0.4324.96 Safari/537.36',
        'accept-language': 'en-US,en'
        }

        req = requests.get(url, headers=headers, cookies=cookie)
        self.logger.debug(f"JSON GET status code: {req.status_code}")
        if req.status_code == 429:
            self.logger.critical("Too many requests. \
    Please try again later or get a new IP (also a new cookie?).")
            return {}

        # We could also use youtube-dl --dump-json instead
        content_page = req.text\
                    .split("ytInitialPlayerResponse = ")[1]\
                    .split(";var meta = document.")[0]
        try:
            j = loads(content_page)
        except Exception as e:
            self.logger.critical(f"Exception while loading json: {e}")
            return {}
        return j


    def write_to_file(self, fsrc, fdst, length=0):
        """Copy data from file-like object fsrc to file-like object fdst.
        If no bytes are read from fsrc, do not create fdst and return False.
        Return True when file has been created and data has been written."""
        # Localize variable access to minimize overhead.
        if not length:
            length = COPY_BUFSIZE
        fsrc_read = fsrc.read

        try:
            buf = fsrc_read(length)
        except Exception as e:
            # FIXME handle these errors better, for now we just ignore and move on:
            # ValueError: invalid literal for int() with base 16: b''
            # http.client.IncompleteRead: IncompleteRead
            logger.exception(e)
            buf = None

        if not buf:
            return False
        with open(fdst, 'wb') as out_file:
            fdst_write = out_file.write
            while buf:
                fdst_write(buf)
                buf = fsrc_read(length)
        return True


class Status(Flag):
    OFFLINE = auto()
    AVAILABLE = auto()
    LIVE = auto()
    VIEWED_LIVE = auto()
    WAITING = auto()
    OK = AVAILABLE | LIVE | VIEWED_LIVE
