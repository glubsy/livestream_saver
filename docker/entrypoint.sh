#!/bin/bash
set -e

echo "Upgrading yt-dlp to the latest version..."
pip install --upgrade --no-cache-dir yt-dlp

exec python ./livestream_saver.py "$@"
