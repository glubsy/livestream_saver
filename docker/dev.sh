#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

image_name="livestream_saver"
image_tag="testing"
config_dir="${HOME}/.config/livestream_saver"

channel_target="${1:-}"
if [[ -z "${channel_target}" ]]; then
  read -r -p "Channel URL or config section to monitor: " channel_target
fi

if [[ -z "${channel_target}" ]]; then
  echo "No channel provided."
  exit 1
fi

cd "${repo_root}"

docker build \
  -f ./docker/Containerfile \
  --build-context trunk=./ \
  -t "${image_name}:latest" \
  -t "${image_name}:${image_tag}" \
  .

run_args=(monitor)

if [[ "${channel_target}" =~ ^https?:// || "${channel_target}" =~ ^/ || "${channel_target}" =~ ^www\.youtube\.com/ || "${channel_target}" =~ ^youtube\.com/ ]]; then
  run_args+=("${channel_target}")
else
  run_args+=(-s "${channel_target}")
fi

exec docker run --rm -it \
  --network=host \
  --mount type=bind,src="${config_dir}",target="/root/.config/livestream_saver" \
  --mount type=volume,src="downloads",target="/downloads" \
  "${image_name}:${image_tag}" \
  "${run_args[@]}"
