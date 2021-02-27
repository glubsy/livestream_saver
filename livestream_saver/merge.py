from os import sep, listdir, system, remove
from pathlib import Path
from shutil import copyfileobj
import logging

logger = logging.getLogger(__name__)


def get_metadata_info(path):
    try:
        with open(path + sep + "metadata.json", 'r') as fp:
            return load(fp)
    except Exception as e:
        print(f"Exception while trying to load metadata.json: {e}")
        return {}

def concat(datatype, video_id, seg_list, output_dir):
    concat_filename = f"concat_{video_id}_{datatype}.ts"
    concat_filepath = output_dir + sep + concat_filename
    ext = "m4a" if datatype == "audio" else "mp4"
    ffmpeg_output_filename = f"{output_dir}{sep}\
{video_id}_{datatype}_v2_ffmpeg.{ext}"

    with open(concat_filepath,"wb") as f:
        for i in seg_list:
            with open(i, "rb") as ff:
                copyfileobj(ff, f)

    system(f"ffmpeg -loglevel panic -y -i \"{concat_filepath}\" -c:a copy \"\
{ffmpeg_output_filename}\"")
    remove(concat_filepath)

    return ffmpeg_output_filename

def merge(info, data_dir, output_dir=None):
    if not output_dir:
        output_dir = data_dir

    video_files = collect(data_dir + sep + "vid")
    audio_files = collect(data_dir + sep + "aud")

    # if len(audio_files) != int(audio_files[-1].split("_audio")[0])
    #     or len(video_files) != int(video_files[-1].split("_video")[0]):
    #     logger.critical(f"Number of segments doesn't match last segment number!")
    #     return

    ffmpeg_output_path_video = concat("video", info.get('id'), video_files, output_dir)
    ffmpeg_output_path_audio = concat("audio", info.get('id'), audio_files, output_dir)

    final_output_name = f"{info.get('author')} [{info.get('download_date')}] \
{info.get('title')}_[{info.get('video_resolution')}]_{info.get('id')}.mp4"

    final_output_path = output_dir + sep + final_output_name

    system(f"ffmpeg -hide_banner -loglevel panic -y \
-i \"{ffmpeg_output_path_audio}\" \
-i \"{ffmpeg_output_path_video}\" \
-c:a copy -c:v copy \"{final_output_path}\"")

    remove(ffmpeg_output_path_audio)
    remove(ffmpeg_output_path_video)
    return final_output_path

def collect(data_path):
    files = [p for p in Path(data_path).glob('*.ts')]
    files.sort()
    return files

