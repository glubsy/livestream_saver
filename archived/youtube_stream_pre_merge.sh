#!/bin/bash
# To be used after using the youtube_stream_parts_download.py script
# and right before mrwnwttk's merge.py script 
# from https://github.com/mrwnwttk/youtube_stream_capture

# Where I installed the merge script (CHANGE)
MERGE_SCRIPT="${HOME}/INSTALLED/youtube_stream_capture/merge.py"

# Make sure the capture directory is there
CAP_DIR=$(find . -maxdepth 1 -type d -iname 'stream_capture*');
if [[ ${CAP_DIR} == '' ]]; then 
	echo "Error getting youtube hash from \"stream_capture_HASH_ID\" directory. Make sure it is present.";
	exit;
elif [[ $(find "${CAP_DIR}" -maxdepth 1 -type d -iname 'aud') == '' 
	|| $(find "${CAP_DIR}" -maxdepth 1 -type d -iname 'vid') == '' ]]; then
	echo "aud or vid directory not found in ${CAP_DIR}";
	exit;
fi

# Get the Youtube Hash ID from the directory name if present
YT_HASH="${CAP_DIR##./stream_capture_}";
if [[ "${YT_HASH}" == "./stream_capture" ]]; then 
	YT_HASH="AAAAAAAAAAA";
	echo "Could not detect youtube hash ID in capture dirname, using default ${YT_HASH}";
else
	echo "Detected youtube hash ID is $YT_HASH";
fi

# Create directory expected by merge.py
target_dirname="segments_${YT_HASH}";
mkdir -p "${target_dirname}";

# Create symlinks to our previously downloaded chunks
cp -s $(pwd)/stream_capture_${YT_HASH}/aud/* ${target_dirname};
cp -s $(pwd)/stream_capture_${YT_HASH}/vid/* ${target_dirname};

# Add the hash ID after the digits of each file, as expected by merge.py
AUDIO_REGEX='s/(\d*)\.m4a/$1_'${YT_HASH}'_audio\.ts/';
VIDEO_REGEX='s/(\d*)\.mp4/$1_'${YT_HASH}'_video\.ts/';
perl-rename "${AUDIO_REGEX}" ${target_dirname}/*;
perl-rename "${VIDEO_REGEX}" ${target_dirname}/*;

# Optionally, remove any leading padding zeros we added
perl-rename 's/(.*\/)0*(\d*_.*)/$1$2/' ${target_dirname}/*;

# Call the merge script with bogus youtube URL
python "${MERGE_SCRIPT}" "https://www.youtube.com/watch?v=${YT_HASH}";
