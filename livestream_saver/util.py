import logging
import os
import requests
import json
import pathlib
from platform import system
from livestream_saver.itag import *

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

SYSTEM = system()
ISPOSIX = SYSTEM == 'Linux' or SYSTEM == 'Darwin'
ISWINDOWS = SYSTEM == 'Windows'
COPY_BUFSIZE = 1024 * 1024 if ISWINDOWS else 64 * 1024


def parse_cookie_file(cookiefile):
    cookies = {}
    with open(cookiefile, 'r') as fp:
        content = fp.read()
        for line in content.split('\n'):
            if line.startswith('#'):
                continue
            if 'youtube' in line:
                elements = line.split('\t')
                cookies[elements[-2]] = elements[-1]
    return cookies


def get_cookie(path):
    try:
        cookie_path = pathlib.Path(path).absolute()
        if not cookie_path.exists():
            logger.debug("Cookie file does not exist, defaulting to empty cookie...")
            return {}

        logger.debug(f"Found cookie at {cookie_path}")
        cookie_content = parse_cookie_file(cookie_path)
        if cookie_content == {}:
            logger.debug("Empty cookie!")
        else:
            return cookie_content
    except Exception as e:
        logger.debug(f"Could not parse cookie, defaulting to empty cookie. Error: {e}")
        return {}


def print_found_quality(item, datatype):
    if datatype == "video":
        keys = ["itag", "qualityLabel", "mimeType", "bitrate", "quality", "fps"]
    else:
        keys = ["itag", "audioQuality", "mimeType", "bitrate", "audioSampleRate"]
    try:
        result = f"Available {datatype} quality: "
        for k in keys:
            result += f"{k}: {item.get(k)}\t"
        logger.warning(result)
    except Exception as e:
        logger.critical(f"Exception while trying to print found {datatype} quality: {e}")


def get_best_quality(json, datatype, limit=None):
    # Select the best possible quality, with limit (str) as the highest possible

    quality_ids = []
    label = 'qualityLabel' if datatype == 'video' else 'audioQuality'
    streamingData = json.get('streamingData', {})
    adaptiveFormats = streamingData.get('adaptiveFormats', {})

    if not streamingData or not adaptiveFormats:
        logger.debug(f"ERROR: could not get {datatype} quality format. Missing streamingData or adaptiveFormats")
        return None

    for _dict in adaptiveFormats:
        if _dict.get(label, None) is not None:
            quality_ids.append(_dict.get('itag'))
            print_found_quality(_dict, datatype)

    if datatype == "video":
        #  Select only resolutions below user-defined limit.
        # global video_height_ranking
        ranking = []
        for k, v in video_height_ranking.items():
            if limit and int(k) > limit:
                continue
            for height in v:
                ranking.append(height)
    else:
        # global quality_audio_ranking
        ranking = quality_audio_ranking

    for i in ranking:
        if i in quality_ids:
            chosen_quality = i
            for d in json['streamingData']['adaptiveFormats']:
                if chosen_quality == d.get('itag'):
                    if datatype == "video":
                        chosen_quality_labels = f"{d.get('qualityLabel')} \
type: {d.get('mimeType')} bitrate: {d.get('bitrate')}"
                    else:
                        chosen_quality_labels = f"{d.get('audioQuality')} \
type: {d.get('mimeType')} bitrate: {d.get('bitrate')}"
            break

    logger.warning(f"Chosen {datatype} quality: itag {chosen_quality}; height: {chosen_quality_labels}")

    return chosen_quality


def get_base_url(json, itag):
    for _dict in json['streamingData']['adaptiveFormats']:
        if _dict.get('itag', None) == itag:
            return _dict.get('url', None)


def get_video_id(url):
    # Argument format:
    # https://youtu.be/njrI8ZDQ7ho or https://youtube.com/?v=njrI8ZDQ7ho
    if "?v=" in url:
        video_id = url.split("v=")[1]
    elif "youtu.be" in url:
        video_id = url.split('/')[-1]

    if 11 > len(video_id) > 12:
        logger.critical(f"Error getting videoID. Length = {len(self.video_id)} \
(too long?) {self.video_id}")
    return video_id


def get_details(json, item):
    return json.get('videoDetails', {}).get(item)


def get_json(url, cookie={}):
    headers = {
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 \
(KHTML, like Gecko) Chrome/88.0.4324.96 Safari/537.36'
    }

    req = requests.get(url, headers=headers, cookies=cookie)
    logger.debug(f"JSON GET status code: {req.status_code}")
    if req.status_code == 429:
        logger.critical("Too many requests. Please try again later or get a new IP (also a new cookie?).")
        return {}

    content_page = req.text.split("ytInitialPlayerResponse = ")[1]
    content_page = content_page.split(";var meta = document.")[0]
    try:
        j = json.loads(content_page)
    except Exception as e:
        logger.critical(f"Exception while loading json: {e}")
        return {}
    return j


def write_to_file(fsrc, fdst, length=0):
    """Copy data from file-like object fsrc to file-like object fdst.
    If no bytes are read from fsrc, do not create fdst and return False.
    Return True when file has been created and data has been written."""
    # Localize variable access to minimize overhead.
    if not length:
        length = COPY_BUFSIZE
    fsrc_read = fsrc.read

    buf = fsrc_read(length)
    if not buf:
        return False
    with open(fdst, 'wb') as out_file:
        fdst_write = out_file.write
        while buf:
            fdst_write(buf)
            buf = fsrc_read(length)
    return True
