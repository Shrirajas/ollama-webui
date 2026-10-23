#!/usr/bin/env bash
# Starts this app's own llama-server in Docker: the models in models.ini, router mode.
# It is a container of its own (llama-server-webui), apart from any other llama-server
# on the machine, and listens on 127.0.0.1 only. The API key is LLAMACPP_API_KEY in
# backend/.env, the same one the app sends.
#
#   llamacpp/server/start.sh          start (or restart with a changed models.ini)
#   docker stop llama-server-webui    stop; the GPU is free again
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="${LLAMACPP_CONTAINER:-llama-server-webui}"
PORT="${LLAMACPP_PORT:-8101}"
IMAGE="${LLAMACPP_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-cuda}"
# Ollama's OLLAMA_MODELS (its systemd unit) moved to /home/ollama/models on 15 Oct 2026.
BLOBS="${OLLAMA_BLOBS:-${OLLAMA_MODELS:-/home/ollama/models}/blobs}"

KEY="$(sed -n 's/^LLAMACPP_API_KEY=//p' "$HERE/../../backend/.env" 2>/dev/null | tail -1)"
if [ -z "$KEY" ]; then
  echo "Set LLAMACPP_API_KEY=<some long random text> in backend/.env first." >&2
  exit 1
fi

# Every model file in models.ini must be there before the running server is removed.
for f in $(sed -nE 's#^(model|mmproj) *= */models/##p' "$HERE/models.ini"); do
  if [ ! -r "$BLOBS/$f" ]; then
    echo "Missing or unreadable: $BLOBS/$f (named in models.ini). The running llama-server was left as it is." >&2
    exit 1
  fi
done

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --gpus all \
  -p "127.0.0.1:$PORT:8080" \
  -v "$BLOBS:/models:ro" \
  -v "$HERE/models.ini:/presets/models.ini:ro" \
  -e LLAMA_API_KEY="$KEY" \
  "$IMAGE" \
  --models-preset /presets/models.ini --models-max 2 --host 0.0.0.0 --port 8080
echo "llama-server: http://127.0.0.1:$PORT (docker logs -f $NAME)"
