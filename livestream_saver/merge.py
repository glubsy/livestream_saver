from os import sep, listdir, system, remove, path
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

def merge(info, data_dir, output_dir=None, delete_source=False):
    if not output_dir:
        output_dir = data_dir
    
    if not data_dir or not path.exists(data_dir):
        print(f"Error: data directory \"{data_dir}\" not found.")
        return None

    video_seg_dir = data_dir + sep + "vid"
    audio_seg_dir = data_dir + sep + "aud"

    video_files = collect(video_seg_dir)
    audio_files = collect(audio_seg_dir)

    if not video_files:
        print(f"Error: no video files found in {video_seg_dir}.")
        return None

    # TODO add more checks to ensure all segments are available + duration?
    # if len(audio_files) != int(audio_files[-1].split("_audio")[0])
    #     or len(video_files) != int(video_files[-1].split("_video")[0]):
    #     logger.critical(f"Number of segments doesn't match last segment number!")
    #     return

    ffmpeg_output_path_video = concat("video", info.get('id'), video_files, data_dir)
    ffmpeg_output_path_audio = concat("audio", info.get('id'), audio_files, data_dir)

    final_output_name = f"{info.get('author')} [{info.get('download_date')}] \
{info.get('title')}_[{info.get('video_resolution')}]_{info.get('id')}.mp4"

    final_output_file = output_dir + sep + final_output_name

    base_cmd = f"ffmpeg -hide_banner -loglevel panic -y \
-i \"{ffmpeg_output_path_audio}\" \
-i \"{ffmpeg_output_path_video}\" \
-c:a copy -c:v copy "
    metada_cmd = metadata_arguments(info)
    ffmpeg_command = base_cmd + metadata_cmd + f"\"{final_output_file}\""
    # TODO embed thumbnail? 
    # ffmpeg -i in.mp4 -i IMAGE -map 0 -map 1 -c copy -c:v:1 png -disposition:v:1 attached_pic out.mp4

    system(ffmpeg_command)

    remove(ffmpeg_output_path_audio)
    remove(ffmpeg_output_path_video)

    if not path.exists(final_output_file):
        print("Error: Missing final merged output file!")
        return None

    print(f"Successfully wrote file \"{final_output_file}\".")

    if delete_source:
        print(f"Deleting source segments...")
        remove(video_seg_dir)
        remove(audio_seg_dir)

    return final_output_file


def metadata_arguments(info):
    if info.get('title'):
        cmd += f"-metadata 'title={info.get('title')}' "
    if info.get('author'):
        cmd += f"-metadata 'author={info.get('author')}' "
    if info.get('download_date'):
        cmd += f"-metadata 'date={info.get('download_date')}' "
    return cmd

def collect(data_path):
    if not path.exists(data_path):
        return []
    files = [p for p in Path(data_path).glob('*.ts')]
    files.sort()
    return files

