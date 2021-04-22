#!/bin/env python3
from os import sep, listdir, system, remove, path, stat
import subprocess
from json import load
from pathlib import Path
from shutil import copyfileobj
import logging
from imghdr import what

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


def get_metadata_info(path):
    try:
        with open(path + sep + "metadata.json", 'r') as fp:
            return load(fp)
    except Exception as e:
        logger.exception(f"Exception while trying to load metadata.json: {e}")
        return {}


def concat(datatype, video_id, seg_list, output_dir):
    """Concatenate segments.
    :param str datatype:
        The type of data. "video" or "audio"
    :param str video_id:
        Youtube ID.
    :param list seg_list:
        List of path to .ts files.
    :param str output_dir:
        Output directory where to write resulting file.
    :rtype: str
    :returns:
        Path to concatenated video or audio file.
    """
    concat_filename = f"concat_{video_id}_{datatype}.ts"
    concat_filepath = output_dir + sep + concat_filename
    ext = "m4a" if datatype == "audio" else "mp4"
    ffmpeg_output_filename = f"{output_dir}{sep}\
{video_id}_{datatype}_v2_ffmpeg.{ext}"

    if path.exists(ffmpeg_output_filename):
        logger.warning(f"Skipping concatenation because {ffmpeg_output_filename} \
already exists from a previous run.")
        return ffmpeg_output_filename

    if not path.exists(concat_filepath):
        # Concatenating segments
        with open(concat_filepath,"wb") as f:
            for i in seg_list:
                with open(i, "rb") as ff:
                    copyfileobj(ff, f)

    # Fixing broken container
    # '-c:a' if datatype == 'audio' else '-c:v' => '-c copy' might be enough.
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "panic", "-y", "-i",\
        f"{concat_filepath}", "-c", "copy", f"{ffmpeg_output_filename}"]
    subprocess.run(cmd)

    remove(concat_filepath)
    return ffmpeg_output_filename


def merge(info, data_dir, output_dir=None, delete_source=False):
    if not output_dir:
        output_dir = data_dir

    if not data_dir or not path.exists(data_dir):
        # logger.critical(f"Data directory \"{data_dir}\" not found.")
        return None

    # Reuse the logging handlers from the download module if possible
    # to centralize logs pertaining to stream video handling
    global logger
    logger = logging.getLogger("download" + "." + info['id'])
    if not logger.hasHandlers():
        logger.setLevel(logging.DEBUG)
        # File output
        logfile = logging.FileHandler(\
            filename=data_dir + sep +  "download.log", delay=True)
        logfile.setLevel(logging.DEBUG)
        formatter = logging.Formatter(\
            '%(asctime)s - %(levelname)s - %(name)s - %(message)s')
        logfile.setFormatter(formatter)
        logger.addHandler(logfile)

        # Console output
        conhandler = logging.StreamHandler()
        conhandler.setLevel(logging.DEBUG)
        conhandler.setFormatter(formatter)
        logger.addHandler(conhandler)

    video_seg_dir = data_dir + sep + "vid"
    audio_seg_dir = data_dir + sep + "aud"

    video_files = collect(video_seg_dir)
    audio_files = collect(audio_seg_dir)

    if not video_files:
        logger.critical(f"No video files found in {video_seg_dir}")
        return None

    # TODO add more checks to ensure all segments are available + duration?
    # if len(audio_files) != int(audio_files[-1].split("_audio")[0])
    #     or len(video_files) != int(video_files[-1].split("_video")[0]):
    #     logger.error(f"Number of segments doesn't match last segment number!")
    #     return

    ffmpeg_output_path_video = concat("video", info.get('id'), video_files, data_dir)
    ffmpeg_output_path_audio = concat("audio", info.get('id'), audio_files, data_dir)
    if not ffmpeg_output_path_audio or not ffmpeg_output_path_video:
        logger.error(f"Missing video or audio concatenated file!")
        return None

    final_output_name = f"{info.get('author')} [{info.get('download_date')}] \
{info.get('title')}_[{info.get('video_resolution')}]_{info.get('id')}.mp4"

    final_output_file = output_dir + sep + final_output_name

    try_thumb = True
    while True:
        ffmpeg_command = ["ffmpeg", "-hide_banner", "-y",\
                        "-i", f"{ffmpeg_output_path_video}",\
                        "-i", f"{ffmpeg_output_path_audio}"
                        ]
        metadata_cmd = metadata_arguments(info, data_dir,
                                          want_thumb=try_thumb
                                        )
        # ffmpeg -hide_banner -i video.mp4 -i audio.m4a -i thumbnail.jpg -map 0
        # -map 1 -map 2 -c:v:2 jpg -disposition:v:1 attached_pic -c copy out.mp4
        ffmpeg_command.extend(metadata_cmd)
        ffmpeg_command.extend(["-c", "copy", final_output_file])

        cproc = subprocess.run(ffmpeg_command, capture_output=True, text=True)

        logger.debug(f"Calling subprocess: {cproc.args}")
        ffmpeg_stderr = cproc.stderr
        logger.debug("FFmpeg STDERR:\n" + ffmpeg_stderr)

        if try_thumb \
           and 'Unable to parse option value "attached_pic"' in ffmpeg_stderr:
            logger.error("Failed to embed the thumbnail into the final video \
file! Trying again without it...")
            try_thumb = False
            continue

        if not path.exists(final_output_file):
            logger.critical("Missing final merged output file! \
Something went wrong.")
            return None
        break

    if path.exists(final_output_file) and stat(final_output_file).st_size == 0:
        logger.critical("Final merged output file is 0 length! \
Something went wrong. Try again with DEBUG log level and check for errors.")
        remove(final_output_file)
        return None

    logger.debug(f"Removing temporary audio/video concatenated files...")
    remove(ffmpeg_output_path_audio)
    remove(ffmpeg_output_path_video)

    logger.info(f"Successfully wrote file \"{final_output_file}\".")

    if delete_source:
        logger.info(f"Deleting source segments...")
        remove(video_seg_dir)
        remove(audio_seg_dir)

    return final_output_file


def metadata_arguments(info, data_path, want_thumb=True):
    cmd = []
    # Embed thumbnail if a valid one is found
    if want_thumb:
        thumb = get_thumbnail(data_path)
        if thumb:
            _type = what(thumb)
            if _type == "jpeg" or _type == "png":
                logger.debug(f"Using thumbnail: {thumb}. Type: {_type}.")
                cmd.extend(["-i", f"{thumb}",\
                            "-map", "0", "-map", "1", "-map", "2",\
                            #"-c:v:2" _type
                            "-c:a:2", "copy",\
                            "-disposition:v:1",\
                            "attached_pic"])
            else:
                # TODO convert to png in case of WEBP or other
                logger.error(f"Unsupported thumbnail file format: {_type}. \
Not embedding.")

    # These have to be placed AFTER, otherwise they affect one stream in particular
    if info.get('title'):
        cmd.extend(["-metadata", f"title={info.get('title')}"])
    if info.get('author'):
        cmd.extend(["-metadata", f"artist={info.get('author')}"])
    if info.get('download_date'):
        cmd.extend(["-metadata", f"date={info.get('download_date')}"])
    if info.get('description'):
        cmd.extend(["-metadata", f"description={info.get('description')}"])
    return cmd


def get_thumbnail(data_path):
    """Returns Path to file named "thumbnail" if found in data_path."""
    fl = list(Path(data_path).glob('thumbnail'))
    if fl:
        return fl[0]
    return None


def collect(data_path):
    if not path.exists(data_path):
        return []
    files = [p for p in Path(data_path).glob('*.ts')]
    files.sort()
    return files

