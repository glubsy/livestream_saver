#!/bin/env python3
from shutil import rmtree
from typing import Optional, Dict, List, Iterable, Iterator
import subprocess
from json import load
from pathlib import Path
from shutil import copyfileobj, which
import logging
import re
from imghdr import what

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)

MAX_NAME_LEN = 255


def get_hash_from_path(path: Path) -> str:
    """Get the hash ID part from the directory name."""
    if match := re.compile(r"stream_capture_(.*)$").match(path.name):
        if _id := match.group(1):
            return _id
    return "UNKNOWN_ID"


def get_metadata_info(path: Path):
    try:
        with open(path / "metadata.json", 'r', encoding='utf-8') as fp:
            return load(fp)
    except Exception as e:
        logger.error(f"Exception while trying to load metadata.json: {e}.")
        return {
            "id": get_hash_from_path(path)
        }

class CorruptPacketError(Exception):
    pass

class NonMonotonousDTSError(Exception):
    pass

class DurationMismatchError(Exception):
    pass


def segname_to_int(path: Path) -> int:
    return int(path.stem[:-6])


class ConcatMethod():
    def __init__(
        self, segment_list: List[Path],
        datatype: str,
        video_id: str,
        output_dir: Path,
        missing_ints: List = [],
        corrupt_segs: Optional[List] = None
    ) -> None:
        self.datatype = datatype
        self.segment_list = segment_list
        self._missing_seg_ints = missing_ints
        self._corrupt_segments = corrupt_segs
        self._segment_duration = None
        self.error = None
        self.video_id = video_id
        self.output_dir = output_dir
        # Determine container type according to codec
        if datatype == "vp9":
            ext = "webm"
        elif datatype == "aac":
            ext = "m4a"
        elif datatype == "h264":
            ext = "mp4"
        else:
            ext = "m4a" if datatype == "audio" else "mp4"

        if not output_dir:
            logger.warning(
                f"{__class__.name} got empty output_dir arg! Falling back to CWD."
            )
            output_dir = Path()
        self._final_file: Path = output_dir / \
            f"{video_id}_{datatype}_ffmpeg.{ext}"

    def run_ffmpeg(self, cmd):
        cproc = None
        try:
            cproc = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True)
            logger.debug(f"{cproc.args} stderr output:\n{cproc.stderr}")
        except subprocess.CalledProcessError as e:
            logger.exception(
                f"{e.cmd} returned error {e.returncode}. "
                f"STDERR:\n{e.stderr}")
            raise
        except FileNotFoundError as e:
            logger.error(f"Failed to run ffmpeg: {e}.")
            raise

        # Something might be wrong? Those might just be harmless warning?
        # if cproc is not None\
        # and ("Found duplicated MOOV Atom. Skipped it" in cproc.stderr
        #     or "Failed to add index entry" in cproc.stderr):

        # These are usually fatal, we should remove the corrup segments, then retry:
        if cproc is not None and "Packet corrupt" in cproc.stderr:
            raise CorruptPacketError("Corrupt packet detected!")

        if cproc is not None and "Non-monotonous DTS" in cproc.stderr:
            # This error seems to happen when concatenating mpegts
            raise NonMonotonousDTSError("Non-monotonous DTS detected!")

    @property
    def segment_duration(self) -> float:
        if self._segment_duration is not None:
            return self._segment_duration
        # FIXME this value is very unpredictable and unreliable, need a better way.
        if segname_to_int(self.segment_list[0]) == 0:
            # Audio has a lower than 1.0 value for some reason, so round up:
            dur = round(probe(self.segment_list[0]).get("duration", 0.0))
        else:
            props = probe(self.segment_list[-1])
            total_dur = round(props.get("duration", 0.0))
            dur = total_dur - round(props.get("start_time", 0.0))
            logger.debug(
                "First segment is not actual first segment so duration is wrong."
                f" Computed duration from last segment instead: {dur}")
        self._segment_duration = dur
        return dur

    @property
    def total_expected_duration(self) -> float:
        # # Simply get the expected duration from what ffprobe reports
        # # in the very last segment metadata, as long as we have ALL segments:
        # if len(self.segment_list) == segname_to_int(self.segment_list[-1]) + 1:
        #     return round(probe(self.segment_list[-1]).get("duration", 0.0))

        # # Compute the most probable duration from number of segments if the
        # # very last segment number is different than the total number of segments
        # expected = self.segment_duration * len(self.segment_list)
        # logger.info(
        #     "Estimated duration computed from "
        #     f"(segment duration) {self.segment_duration} * number of "
        #     f"(segments available) {len(self.segment_list)} = {expected}.")
        # return expected

        return round(probe(self.segment_list[-1]).get("duration", 0.0))

    def exists(self):
        return self._final_file.is_file()

    @property
    def name(self):
        return self._final_file.name

    def unlink(self, **kwargs):
        return self._final_file.unlink(**kwargs)

    def __repr__(self) -> str:
        return self._final_file.__repr__()

    def __str__(self) -> str:
        return self._final_file.__str__()

    @property
    def duration(self) -> float:
        return probe(self._final_file).get("duration", 0.0)

    @property
    def corrupt_segments(self) -> List[Path]:
        if self._corrupt_segments is not None:
            return self._corrupt_segments
        self._corrupt_segments = get_corrupt(self.segment_list)
        return self._corrupt_segments

    def is_valid_duration(self, filepath: Path, duration: float) -> bool:
        """Check that the file duration is between 95% and 105% of the
        expected duration. Can be the final file, or a temporary file."""
        total_expected_duration = self.total_expected_duration
        round_min = round(total_expected_duration * 0.95)
        round_max = round(total_expected_duration * 1.05)
        round_dur = round(duration)

        logger.info(
            f"Checking duration of {filepath.name} ({round_dur}) against the "
            f"total expected duration {total_expected_duration} ...")

        if round_min <= round_dur <= round_max:
            logger.info(f"{filepath.name} duration of {round_dur} seems valid.")
            return True

        last_segnum = segname_to_int(self.segment_list[-1])
        theoretical_total = len(self.segment_list) \
            + len(self._missing_seg_ints) \
            + (len(self._corrupt_segments) if self._corrupt_segments is not None else 0)
        theoretical_dur = round(theoretical_total * self.segment_duration)

        logger.debug(
            f"A total of {theoretical_total} segments * {self.segment_duration}"
            f" seconds = {theoretical_dur} theoretical total duration.")
        if round_dur == theoretical_dur:
            logger.warning(
                "Duration is invalid, but it seems to correspond to the total "
                f"number of files we should have in theory ({last_segnum})."
                " We assume we recorded only part of the stream & duration is valid.")
            return True

        logger.warning(
            f"Invalid duration \"{round_dur}\" for {filepath.name}."
            f" Expected duration to be between around {total_expected_duration}"
            f" (between {round_min} and {round_max})"
            f" or {theoretical_dur}" if last_segnum != theoretical_total else ""
            ".")
        return False

    def make(self, *args, **kwargs) -> None:
        raise NotImplementedError()

    def setup_command(self, *args, **kwargs) -> List:
        """Setup command for ffmpeg."""
        raise NotImplementedError()


class ConcatDemuxer(ConcatMethod):
    """Using ffmpeg concat demuxer. WARNING: this method is broken due to
    PTS (presentation timestamps) messing the final duration!
    This is dead code."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.list_file_path = None  # the list.txt for ffmpeg concat demuxer

    def make(self, overwrite=False):
        if self._final_file.exists() and not overwrite:
            logger.info(
                f"Skipping concatenation because \"{self._final_file}\" "
                "already exists from a previous run. Not rebuilding.")
            return self._final_file
        cmd = self.setup_command()
        try:
            logger.info(f"Muxing {self.datatype} track file...")
            self.run_ffmpeg(cmd)

            duration = probe(self._final_file).get("duration", 0.0)
            if not self.is_valid_duration(self._final_file, duration):
                raise DurationMismatchError()
        except CorruptPacketError:
            corrupt = self.corrupt_segments
            if corrupt:
                # Doing f for f in segment_list if f not in corrupt
                corrupt_ints = list(path_list_to_int(corrupt))
                self.segment_list = list(
                    filter(lambda f: segname_to_int(f) not in corrupt_ints,
                    self.segment_list))
                cmd = self.setup_command()
                logger.info(f"Re-Muxing {self.datatype} track file...")
                self.run_ffmpeg(cmd)
            elif not self._final_file.exists():
                # Something else is wrong. Bail out.
                raise
            else:
                logger.warning("No corrupt segment detected. File might be alright.")
        finally:
            if self.list_file_path is not None:
                # self.list_file_path.unlink(missing_ok=True)
                pass

        logger.info(f"Successfully wrote {self.name}.")

    def setup_command(self) -> List:
        # http://ffmpeg.org/ffmpeg-formats.html#concat-1
        # Does not work, duration is always messed up.
        # Also a bunch of "Auto-inserting h264_mp4toannexb bitstream filter"
        # warnings (-auto_convert 0 might disable them, but no different result)
        # Still have no idea how to fix this method; better avoid it for now.
        self.list_file_path = self.output_dir / \
            f"list_{self.video_id}_{self.datatype}.txt"

        with open(self.list_file_path, "w") as f:
            for i in self.segment_list:
                f.write(f"file '{i}'\n")

        return ["ffmpeg", "-hide_banner", "-y",
               "-f", "concat",
               "-safe", "0",
            #    "-fflags", "+igndts",
               "-i", str(self.list_file_path),
               "-map_metadata", "-1", # remove metadata
            #  "-auto_convert", "0" # might disable warnings?
               "-c", "copy",
            #    "-movflags", "+faststart",
            #    "-bsf:a", "aac_adtstoasc",
            #    "-vsync", "2",
            #  "-bsf:v", "h264_mp4toannexb",  # or [hevc|h264]_mp4toannexb
            #  "-segment_time_metadata", "1", # default is 0
               str(self._final_file)]


class NativeConcatFile(ConcatMethod):
    """Concatenate segments with python (like cat).
    This method does not detect corrupt packets during concatenation!
    We have to check the duration of the final file to make sure it is not corrupted."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.temp_concat = self.output_dir / \
            f"{self.video_id}_{self.datatype}_{self.__class__.__name__}.ts"

    def make(self, overwrite=False):
        self.native_concat(overwrite=overwrite)
        # cmd = self.setup_ts_command() # First pass as .ts temporary file
        cmd = self.setup_command()

        try:
            # logger.info("Fixing mpeg-ts container with ffmpeg...")
            # self.run_ffmpeg(cmd)
            # Run again, but with the proper extension this time
            # UPDATE: this seems unnecessary after all.
            # cmd = self.setup_command()
            logger.info(f"Muxing {self.datatype} track file...")
            self.run_ffmpeg(cmd)

            duration = probe(self._final_file).get("duration", 0.0)
            if not self.is_valid_duration(self._final_file, duration):
                raise DurationMismatchError()

        except (CorruptPacketError, DurationMismatchError):
            logger.warning(
                "We encountered corrupt packets during first muxing.")
            corrupt = self.corrupt_segments
            if corrupt:
                # Recreate the list of segments minus the corrupted ones
                # f for f in segment_list if f not in corrupt
                corrupt_ints = list(path_list_to_int(corrupt))
                self.segment_list = list(
                    filter(lambda f: segname_to_int(f) not in corrupt_ints,
                    self.segment_list))
                self.native_concat(overwrite=True)

                # cmd = self.setup_ts_command()
                # logger.info("Fixing mpeg-ts container with ffmpeg...")
                # self.run_ffmpeg(cmd)

                cmd = self.setup_command()
                logger.info(f"Re-Muxing {self.datatype} track file...")
                self.run_ffmpeg(cmd)

                duration = probe(self._final_file).get("duration", 0.0)
                if not self.is_valid_duration(self._final_file, duration):
                    raise DurationMismatchError()
            elif not self._final_file.exists():
                # no corrupt packet, but something else is wrong.
                raise
            else:
                logger.warning("No corrupt segment detected. File might be alright.")
        except NonMonotonousDTSError as e:
            logger.warning(e)
            self.error = e
            cmd = self.setup_command(ignore_dts=True)
            logger.info("Retrying by ignoring dts...")
            # FIXME these exception handlers should be in a separate method
            # FIXME ignoring dts doesn't seem to change anything, useless?
            try:
                self.run_ffmpeg(cmd)
            except:
                pass
        finally:
            if self.temp_concat.exists():
                self.temp_concat.unlink()

        logger.info(f"Successfully wrote {self.name}.")

    def native_concat(self, overwrite=False) -> Optional[Path]:
        """Concatenate into a broken container that needs to be fixed by ffmpeg."""
        # TODO write this into a fifo/pipe and call ffmpeg on it in parallel?
        if not self.temp_concat.exists() or overwrite:
            logger.debug(f"Writing native concat file {self.temp_concat.name} ...")
            with open(self.temp_concat, "wb") as f:
                for i in self.segment_list:
                    with open(i, "rb") as ff:
                        copyfileobj(ff, f)
        if self.temp_concat.exists():
            return self.temp_concat
        return None

    def setup_command(self, ignore_dts = False) -> List:
        # '-c:a' if datatype == 'audio' else '-c:v' but '-c copy' might work for both here.
        cmd = ["ffmpeg", "-hide_banner", "-y",
               "-i", str(self.temp_concat),
               "-map_metadata", "-1", # remove metadata
               "-c", "copy",
               "-movflags", "+faststart",
            #    "-bsf:a", "aac_adtstoasc",
            #    "-bsf:v", "h264_mp4toannexb",  # or [hevc|h264]_mp4toannexb
            #    "-vsync", "2",
               str(self._final_file)]
        if ignore_dts:
            for arg in ["-fflags", "+igndts"]:
                cmd.insert(cmd.index("-i"), arg)
        return cmd

    def setup_ts_command(self):
        # This is the temporary file, it might help with fixing timestamps
        # doing this twice. UPDATE: probably not, so this is obsolete!
        concat_filename = f"concat_{self.video_id}_{self.datatype}.ts"
        self.concat_filepath = self.output_dir / concat_filename
        # Fix broken container. This seems to fix the messed up duration.
        # Note: '-c:a' if datatype == 'audio' else '-c:v' but '-c copy' might work for both here.
        return ["ffmpeg", "-hide_banner", "-y",
            #    "-fflags", "+igndts",
               "-i", str(self.temp_concat),
               "-map_metadata", "-1", # remove metadata
               "-c", "copy",
               "-bsf:a", "aac_adtstoasc",
               "-bsf:v", "h264_mp4toannexb",  # or [hevc|h264]_mp4toannexb
               "-movflags", "+faststart",
            #    "-vsync", "2",
               str(self.concat_filepath)]


def probe(fpath: Path) -> Dict:
    probecmd = ['ffprobe', '-v', 'quiet', '-hide_banner',
                '-show_streams', str(fpath)]
    try:
        probeproc = subprocess.run(probecmd, capture_output=True, text=True)
        # logger.debug(f"{probeproc.args} stderr output:\n{probeproc.stdout}")
    except FileNotFoundError as e:
        logger.error(f"Failed to use ffprobe: {e}.")
        return {}

    values = {}
    for line in probeproc.stdout.split("\n"):
        if "duration=" in line:
            val = line.split("=")[1]
            values["duration"] = float(val) if val != "N/A" else 0.0
            continue
        if "start_time=" in line:
            val = line.split("=")[1]
            values["start_time"] = float(val) if val != "N/A" else 0.0
            continue
        if "codec_name=" in line:
            val = line.split("=")[1]
            values["codec_name"] = val if val != "N/A" else None
            continue
    logger.debug(
        f"Probed \"{fpath.name}\". codec_name: {values.get('codec_name')}, "
        f"duration: {values.get('duration')}, "
        f"start_time: {values.get('start_time')}.")
    return values


def path_list_to_int(seg_list: List[Path]) -> Iterator[int]:
    # remove the "_audio/_video" part
    return (int(i.stem[:-6]) for i in seg_list)


def merge(info: Dict, data_dir: Path,
          output_dir: Optional[Path] = None,
          keep_concat: bool = False,
          delete_source: bool = False) -> Optional[Path]:
    if not output_dir:
        output_dir = data_dir

    if not data_dir or not data_dir.exists():
        # logger.critical(f"Data directory \"{data_dir}\" not found.")
        return None

    # Reuse the logging handlers from the download module if possible
    # to centralize logs pertaining to stream video handling
    global logger
    logger = logging.getLogger("download" + "." + info.get('id', "_"))
    if not logger.hasHandlers():
        logger.setLevel(logging.DEBUG)
        # File output
        logfile = logging.FileHandler(\
            filename=data_dir / "download.log", delay=True, encoding='utf-8')
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

    if not which("ffmpeg") or not which("ffprobe"):
        raise Exception(
            "Could not find ffmpeg or ffprobe! Make sure it is installed and "
            "discoverable from your PATH environment variable.")

    video_seg_dir = data_dir / "vid"
    audio_seg_dir = data_dir / "aud"

    video_files = collect(video_seg_dir)
    audio_files = collect(audio_seg_dir)

    if not video_files and not audio_files:
        raise Exception("Missing video or audio segment source files!")

    # Various checks on source segments to detect any missing:
    segment_number_mismatch = False
    if len(video_files) != len(audio_files):
        logger.warning(
            "Number of audio and video segments do not match! "
            f"{len(video_files)} video segments, {len(audio_files)} audio segments."
        )
        segment_number_mismatch = True

    # FIXME this is redundant with checks below
    missing_video_paths = print_missing_segments(video_files, "_video")
    missing_audio_paths = print_missing_segments(audio_files, "_audio")
    if missing_video_paths or missing_audio_paths:
        segment_number_mismatch = True
        logger.warning(f"Some segments appear to be missing!")

    # Compare each track with the other for missing segments:
    missing_video_ints = []
    missing_audio_ints = []
    video_as_int = list(path_list_to_int(video_files))
    audio_as_int = list(path_list_to_int(audio_files))
    seg_list_as_ints = video_as_int + audio_as_int

    affected_segs = [
        i for i in seg_list_as_ints
        if i not in video_as_int or i not in audio_as_int
    ]
    missing_audio_ints = [i for i in video_as_int if i not in audio_as_int]
    missing_video_ints = [i for i in audio_as_int if i not in video_as_int]
    if affected_segs:
        logger.warning(
            "Some segments appear to be missing! "
            f" Affected segments: {affected_segs}. "
            f" Missing video segments: {missing_video_ints}."
            f" Missing audio segments: {missing_audio_ints}")
    else:
        logger.info("No missing segment detected. All good.")
    del video_as_int
    del audio_as_int
    del seg_list_as_ints
    del affected_segs

    # Determine codec from one file
    vid_props = probe(video_files[0])
    aud_props = probe(audio_files[0])

    # We could either remove to balance both lists, or fill in. Removing
    # is probably better here, especially regarding audio.
    video_files = list(filter(
        lambda f: segname_to_int(f) not in missing_video_ints, video_files))
    audio_files = list(filter(
        lambda f: segname_to_int(f) not in missing_audio_ints, audio_files))

    # There is only one method that works currently
    methods = (NativeConcatFile,)
    attempt = 0
    got_errors = False
    concat_video_file = None
    concat_audio_file = None
    corrupt_vid_segs: Optional[List[Path]] = None
    corrupt_aud_segs: Optional[List[Path]] = None
    while attempt < len(methods):
        logger.info(f"Performing concat method {methods[attempt].__name__}")
        try:
            concat_video_file = methods[attempt](
                video_files, vid_props.get("codec_name", "video"),
                info.get("id", "UNKNOWN_ID"), output_dir,
                missing_video_ints, corrupt_vid_segs)
            concat_video_file.make()
            if concat_video_file.error is not None:
                got_errors = True

            if concat_video_file._corrupt_segments:
                # Keep a reference to reuse with next method if needed:
                video_files = concat_video_file.segment_list
                corrupt_vid_segs = concat_video_file._corrupt_segments

                # If we found corrupts segments in the video track, remove the
                # corresponding audio segments as well. --
                # UPDATE: the following seems to induce errors of type "invalid PTS"
                # during audio track playback; it also seems that we can actually
                # safely keep all audio segments even if the video is broken,
                # there will be no desync after all, only a few skipped frames in video.
                # new_missing = list(to_int(corrupt_vid_segs))
                # audio_files = list(filter(
                #    lambda f: segname_to_int(f) not in new_missing, audio_files))

            concat_audio_file = methods[attempt](
                audio_files, aud_props.get("codec_name", "audio"),
                info.get("id", "UNKNOWN_ID"), output_dir,
                missing_audio_ints, corrupt_aud_segs)
            concat_audio_file.make()
            if concat_audio_file.error is not None:
                got_errors = True

            # We have to rebuild the video after removing video segments
            # corresponding to the corrupt audio segments:
            # FIXME this is untested! Need some corrupt audio segments.
            if concat_audio_file._corrupt_segments:
                corrupt_aud_segs = concat_audio_file._corrupt_segments
                new_missing = list(path_list_to_int(corrupt_aud_segs))
                video_files = list(filter(
                    lambda f: segname_to_int(f) not in new_missing, video_files))
                concat_video_file.unlink(missing_ok=True)
                concat_video_file.segment_list = video_files
                concat_video_file.make(overwrite=True)
            break
        except Exception as e:
            logger.exception(e)
            attempt += 1
            # Remove any file created by this method, for consistency
            if attempt < len(methods):
                if concat_video_file:
                    concat_video_file.unlink(missing_ok=True)
                    concat_video_file = None
                if concat_audio_file:
                    concat_audio_file.unlink(missing_ok=True)
                    concat_audio_file = None
                continue
            break

    if not concat_video_file or not concat_video_file.exists():
        raise Exception(f"Missing concat video file: {concat_video_file}")
    if not concat_audio_file or not concat_audio_file.exists():
        raise Exception(f"Missing concat audio file: {concat_audio_file}")

    # Compare durations of each track:
    concats_have_different_durations = False
    concat_vid_props = probe(concat_video_file._final_file)
    concat_aud_props = probe(concat_audio_file._final_file)
    # cast to int to round down
    dur_dirr = abs(
        round(concat_aud_props.get("duration", 0.0)) \
        - round(concat_vid_props.get("duration", 0.0))
    )
    if dur_dirr <= 1:
        logger.info("No duration mismatch between concat files. All good.")
    # We may tolerate up to 2 seconds delta, but won't delete source files:
    if dur_dirr > 1:
        logger.warning( "Track duration mismatch: "
            f"Audio duration {concat_aud_props.get('duration')}, "
            f"Video duration {concat_vid_props.get('duration')}. ")
        concats_have_different_durations = True
    if dur_dirr > 2:
        logger.warning(
            "Aborting due to concat files duration difference superior to 2 seconds.")
        return None

    ext = "mp4"
    # Seems like an MP4 container can handle vp9 just fine. Perhaps we don't
    # really need MKV (which doesn't support embedded thumbnails yet anyway).
    # if vid_props.get("codec_name") == "vp9":
    #     ext = "mkv"

    if len(info.keys()) <= 1:
        # Failed to load metadata.json
        final_output_name = info.get('id', 'UNKNOWN_ID') + f".{ext}"
    else:
        final_output_name = sanitize_filename(
            f"{info.get('author')}_"
            f"[{info.get('download_date')}]_{info.get('title')}_"
            f"[{info.get('video_resolution')}]_{info.get('id')}"
            f".{ext}")

    final_output_file = output_dir / final_output_name

    # Final muxing of tracks, plus thumbnail and metadata embedding:
    try_thumb = True
    while True:
        ffmpeg_command = [
            "ffmpeg", "-hide_banner", "-y",
            # "-vsync", "2",
            "-i", str(concat_video_file),
            "-i", str(concat_audio_file)
        ]
        metadata_cmd = metadata_arguments(
            info, data_dir,
            want_thumb=try_thumb
        )
        # ffmpeg -hide_banner -i video.mp4 -i audio.m4a -i thumbnail.jpg -map 0
        # -map 1 -map 2 -c:v:2 jpg -disposition:v:1 attached_pic -c copy out.mp4
        ffmpeg_command.extend(metadata_cmd)
        ffmpeg_command.extend(["-c", "copy", str(final_output_file)])

        try:
            cproc = subprocess.run(
                ffmpeg_command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8"
            )
            logger.debug(f"{cproc.args} stderr output:\n{cproc.stderr}")
        except subprocess.CalledProcessError as e:
            logger.debug(
                f"{e.cmd} return code {e.returncode}. STDERR:\n{e.stderr}")

            if try_thumb \
            and 'Unable to parse option value "attached_pic"' in e.stderr:
                logger.error(
                    "Failed to embed the thumbnail into the final video "
                    "file! Trying again without it..."
                )
                try_thumb = False
                if final_output_file.exists() \
                and final_output_file.stat().st_size == 0:
                    logger.info(
                        "Removing zero length ffmpeg output "
                        "\"{}\" ...".format(final_output_file.name))
                    final_output_file.unlink()
                continue

        if not final_output_file.exists():
            logger.critical(
                "Missing final merged output file! Something went wrong.")
            return None
        break

    # This usually indicates some ffmpeg mishaps:
    if final_output_file.exists() and final_output_file.stat().st_size < 1000:
        logger.critical(
            "Final merged output file size is below 1KB! Something went wrong. "
            "Check for errors in DEBUG log level.")
        final_output_file.unlink()
        return None

    # TODO check final duration just in case.

    logger.info(f"Successfully wrote file \"{final_output_file.name}\".")

    if not keep_concat:
        logger.debug(
            f"Removing temporary audio/video concatenated files "
            "{} and {}".format(concat_audio_file.name, concat_video_file.name))
        concat_audio_file.unlink()
        concat_video_file.unlink()

    if delete_source:
        action_msg = "Not deleting source segments."
        if segment_number_mismatch:
            logger.warning(f"Some segments are missing. {action_msg}")
        elif concats_have_different_durations:
            logger.warning(
                f"There was a track duration mismatch. {action_msg}")
        elif corrupt_aud_segs or corrupt_vid_segs:
            logger.warning(f"Some segments were corrupted! {action_msg}")
        elif attempt > 0:
            logger.warning(f"A concat method failed. {action_msg}")
        elif got_errors:
            logger.warning(f"We got suspicious errors while concatenating. {action_msg}")
        else:
            logger.info("Deleting source segments in {} and {}...".format(
                video_seg_dir, audio_seg_dir)
            )
            rmtree(video_seg_dir)
            rmtree(audio_seg_dir)

    return final_output_file


def get_corrupt(filelist: List[Path]) -> List[Path]:
    """Return the list of corrupt files in filelist, detected with ffprobe.
    This is super slow, but ffmpeg does not report corrupt packet file unless
    its log level is set to debug level."""
    logger.info("Scanning for corrupt segment files...")
    probecmd = ['ffprobe', '-hide_banner', '-v', 'warning']
    corrupt = []
    num = 0
    for f in filelist:
        num += 1
        if num % 100 == 0:
            logger.info(f"{num} files scanned...")
        try:
            probeproc = subprocess.run(probecmd + [str(f)],
                capture_output=True, text=True
            )
            # logger.debug(f"{probeproc.args} stderr output:\n{probeproc.stderr}")
        except FileNotFoundError as e:
            logger.error(f"Failed to use ffprobe: {e}.")
        else:
            if "Packet corrupt" in probeproc.stderr:
                logger.warning(f"File segment \"{f}\" is corrupt!")
                corrupt.append(f)

    if corrupt:
        logger.warning(
            f"Found {len(corrupt)} corrupt packets via ffprobe: "
            f"{[f.name for f in corrupt]}."
            " Will not use them anymore")
    else:
        logger.info("No corrupt file detected.")
    return corrupt


def fillin_missing_segments(filelist: List[Path], missing: Iterable[int]) -> List[Path]:
    """Return a copy of filelist, where for each missing file in filelist,
    a duplicate of the previous entry is inserted in its place."""
    # This can be used to smooth out missing segments by repeating the previous
    # segment. This may or may not give a better result than removing missing #
    # segments atogether.
    # We can use the missing int as index in filelist to insert there, but we also
    # need to reconstruct the Path object from the int.
    if not missing:
        return filelist
    as_int = map(lambda x: int(x.stem[:-6]), filelist)
    filtered = []
    prev = None
    for f in as_int:
        if f in missing:
            # insert a copy (reference) of the previous entry in the list
            # dup = as_int[as_int.index(f) - 1]
            filtered.append(prev) # yield dup
            continue
        prev = f
        filtered.append(f)  # yield f
    return filtered

def remove_missing_segments(filelist: List[Path], missing: Iterable[int]) -> List[Path]:
    """Return a copy of filelist where Paths filenames that match those in
    the missing list are removed."""
    # This function is used to have an equal number of segment in both audio
    # and video streams.
    if not missing:
        return filelist
    as_int = map(lambda x: int(x.stem[:-6]), filelist)
    filtered = []
    for f in as_int:
        if f in missing:
            continue
        filtered.append(f)  # yield f
    return filtered


def print_missing_segments(filelist: List[Path], filetype: str) -> List[Path]:
    """
    Check that all segments are available.
    :param list filelist: a list of pathlib.Path
    :param str filetype: "_video" or "_audio"
    :return bool: list of missing segment Paths (that point to nothing)
    """
    missing = []
    first_segnum = 0
    last_segnum = 0
    if not filelist:
        raise Exception(f"Missing files in {filetype} filelist!")

    # Get the numbers from the file name
    # filename format is 0000000001_[audio|video].ts
    first_segnum = int(filelist[0].name.split(filetype + ".ts")[0])
    last_segnum = int(filelist[-1].name.split(filetype + ".ts")[0])

    if first_segnum == last_segnum:
        raise Exception(f"First and last {filetype} segments are the same number!?")

    if first_segnum != 0:
        logger.warning(
            f"First {filetype[1:]} segment number starts at {first_segnum} "
            "instead of 0.")

    # Numbering in filenames should start from 0
    if len(filelist) != last_segnum + 1:
        logger.warning(
            f"Number of {filetype[1:]} segments doesn't match last segment "
            f"number: Last {filetype[1:]} segment number: "
            f"{last_segnum} / {len(filelist)} total files.")
        i = first_segnum
        base_dir = filelist[0].parent
        for f in filelist:
            name = f"{i:0{10}}{filetype}.ts"
            if f.name != name:
                logger.warning(
                    f"Segment {name}.ts seems to be missing.")
                missing.append(Path(base_dir) / name)
                # Add a second time to account for missing segment
                i += 1
            i += 1
    return missing


def metadata_arguments(
        info: Dict,
        data_path: Path,
        want_thumb: bool = True) -> List[str]:
    cmd = []
    # Embed thumbnail if a valid one is found
    if want_thumb:
        cmd = get_thumbnail_command_prefix(data_path)

    # These have to be placed AFTER, otherwise they affect one stream in particular
    if title := info.get('title'):
        cmd.extend(["-metadata", f"title='{title}'"])
    if author := info.get('author'):
        cmd.extend(["-metadata", f"artist='{author}'"])
    if download_date := info.get('download_date'):
        cmd.extend(["-metadata", f"date='{download_date}'"])
    if description := info.get('description'):
        cmd.extend(["-metadata", f"description='{description}'"])
    return cmd


def get_thumbnail_command_prefix(data_path: Path) -> List:
    thumb_path = get_thumbnail_pathname(data_path)
    if not thumb_path:
        return []

    _type = what(thumb_path)
    logger.info(f"Detected thumbnail \"{thumb_path}\" type: {_type}.")

    if _type is None:
        return []

    if _type != "jpeg" and _type != "png":
        try:
            convert_thumbnail(thumb_path, _type)
        except Exception as e:
            logger.error(
                f"Failed converting thumbnail \"{thumb_path}\" "
                f"from detected {_type} format. {e}"
            )
            return []

    # https://ffmpeg.org/ffmpeg.html#toc-Stream-selection
    return [
        "-i", str(thumb_path),
        "-map", "0", "-map", "1", "-map", "2",
        # "-c:v:2", _type,
        # copy probably means no re-encoding again into jpg/png
        "-c:a:2", "copy",
        "-disposition:v:1",
        "attached_pic"
    ]


def convert_thumbnail(thumb_path: Path, fromformat: str) -> Path:
    """Move file 'thumbnail' pointed by thumb_path as 'thumbnail.fromformat',
    then convert the file to PNG and saves it as 'filename' from thumb_path."""
    try:
        from PIL import Image
    except ImportError as e:
        logger.error(f"Failed loading PIL (pillow) module. {e}")
        raise e

    # old_path = str(thumb_path)
    # new_name = ".".join((old_path, fromformat))
    # rename(old_path, new_name)

    new_name = Path(thumb_path.absolute().name + f".{fromformat}")
    if not new_name.exists():
        thumb_path.rename(new_name)

    # TODO Pillow can detect and try all available formats
    logger.info(f"Converting \"{new_name}\" to PNG...")
    with Image.open(new_name) as im:
        im.convert("RGB")
        im.save(thumb_path, "PNG")
    logger.info(f"Saved PNG thumbnail as \"{thumb_path}\"")
    return thumb_path


def get_thumbnail_pathname(data_path: Path) -> Optional[Path]:
    """Returns the first file named "thumbnail" if found in data_path."""
    fl = list(data_path.glob('thumbnail'))
    if fl:
        return fl[0]
    return None


def collect(data_path: Path) -> List[Path]:
    if not data_path.exists():
        logger.warning(f"{data_path} does not exist!")
        return []
    files = [p for p in data_path.glob('*.ts')]
    files.sort()
    return files


def sanitize_filename(filename: str) -> str:
    """Remove characters in name that are illegal in some file systems, and
    make sure it is not too long, including the extension."""
    extension = ""
    ext_idx = filename.rfind(".")
    if ext_idx > -1:
        extension = filename[ext_idx:]
        if not extension.isascii():
            # There is a risk that we failed to detect an actual extension.
            # Only preserve extension if it is valid ASCII, otherwise ignore it.
            extension = ""

    if extension:
        filename = filename[:-len(extension)]

    filename = "".join(
        c for c in filename if 31 < ord(c) and c not in r'<>:"/\|?*'
    )
    logger.debug(f"filename {filename}, extension {extension}")

    if not filename.isascii():
        name_bytes = filename.encode('utf-8')
        length_bytes = len(name_bytes)
        logger.debug(
            f"Length of problematic filename is {length_bytes} bytes "
            f"{'<' if length_bytes < MAX_NAME_LEN else '>='} {MAX_NAME_LEN}")
        if length_bytes > MAX_NAME_LEN:
            filename = simple_truncate(filename, MAX_NAME_LEN - len(extension))
    else:
        # Coerce filename length to 255 characters which is a common limit.
        filename = filename[:MAX_NAME_LEN - len(extension)]

    logger.debug(f"Sanitized name: {filename + extension} "
              f"({len((filename + extension).encode('utf-8'))} bytes)")
    assert(
        len(
            filename.encode('utf-8') + extension.encode('utf-8')
        ) <= MAX_NAME_LEN
    )
    return filename + extension


def simple_truncate(unistr: str, maxsize: int) -> str:
    # from https://joernhees.de/blog/2010/12/14/how-to-restrict-the-length-of-a-unicode-string/
    import unicodedata
    if not unicodedata.is_normalized("NFC", unistr):
        unistr = unicodedata.normalize("NFC", unistr)
    return str(
        unistr.encode("utf-8")[:maxsize],
        encoding="utf-8", errors='ignore'
    )
