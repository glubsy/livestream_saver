FROM python:3.13.3-bookworm

RUN apt update && apt -y install ffmpeg

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY "./livestream_saver/" ./livestream_saver/
COPY "livestream_saver.py" .

# Provide default files, but this directory path should be mounted instead
COPY "./ytdlp_config.json" "/root/.config/livestream_saver/ytdlp_config.json"
COPY "./livestream_saver.cfg" "/root/.config/livestream_saver/livestream_saver.cfg"

ENV LSS_OUTPUT_DIR="/downloads"
ENV LSS_CONFIG_DIR="/root/.config/livestream_saver"
ENV LSS_COOKIES_FILE="/root/.config/livestream_saver/cookies.txt"

ENTRYPOINT [ "python", "./livestream_saver.py" ]

CMD [ "monitor", ]