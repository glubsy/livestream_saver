#!/bin/bash
set -e

# echo "Upgrading yt-dlp to the latest version..."
# pip install --upgrade --no-cache-dir yt-dlp

cd /opt/bgutil-ytdlp-pot-provider/server/node_modules
deno run --allow-env --allow-net --allow-ffi=. --allow-read=. ../src/main.ts &

cd /app
exec python ./livestream_saver.py "$@"
