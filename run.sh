#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="srt-to-anki"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Build the Docker image
docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"

# Parse args: detect file paths and mount their directories into the container.
DOCKER_ARGS=()
RUN_ARGS=()
MOUNT_INDEX=0
SEEN_DIRS=()

mount_file() {
    local file="$1"
    local abs_path
    abs_path="$(cd "$(dirname "$file")" && pwd)/$(basename "$file")"
    local dir
    dir="$(dirname "$abs_path")"
    local basename
    basename="$(basename "$abs_path")"

    # Check if this directory is already mounted
    for i in "${!SEEN_DIRS[@]}"; do
        if [[ "${SEEN_DIRS[$i]}" == "$dir" ]]; then
            RUN_ARGS+=("/data${i}/${basename}")
            return
        fi
    done

    # New directory — add a mount
    SEEN_DIRS+=("$dir")
    DOCKER_ARGS+=(-v "${dir}:/data${MOUNT_INDEX}")
    RUN_ARGS+=("/data${MOUNT_INDEX}/${basename}")
    MOUNT_INDEX=$((MOUNT_INDEX + 1))
}

for arg in "$@"; do
    if [[ -f "$arg" ]]; then
        mount_file "$arg"
    else
        RUN_ARGS+=("$arg")
    fi
done

# Forward TTS-related env vars if set
for VAR in AZURE_TTS_KEY AZURE_TTS_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION POLLY_VOICE_ID POLLY_SPEED ELEVENLABS_API_KEY ELEVENLABS_VOICE_ID ELEVENLABS_SPEED GOOGLE_APPLICATION_CREDENTIALS; do
    if [[ -n "${!VAR:-}" ]]; then
        DOCKER_ARGS+=(-e "${VAR}=${!VAR}")
    fi
done

docker run --rm "${DOCKER_ARGS[@]}" "$IMAGE_NAME" "${RUN_ARGS[@]}"
