#!/usr/bin/env bash
# Starts the embedding model for workspace search by meaning: EmbeddingGemma-300M in a
# llama-server container of its own (llama-embed-webui), on the CPU only (no --gpus: the
# card is full with llama-server-webui), on 127.0.0.1 only. Same API key as start.sh.
# It restarts with Docker; without it workspaces search by words alone.
#
#   llamacpp/server/start-embed.sh     start (or restart)
#   docker stop llama-embed-webui      stop
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="${EMBED_CONTAINER:-llama-embed-webui}"
PORT="${EMBED_PORT:-8102}"
IMAGE="${EMBED_IMAGE:-ghcr.io/ggml-org/llama.cpp:server}"
# From huggingface.co/ggml-org/embeddinggemma-300M-GGUF
MODEL="${EMBED_MODEL:-/home/ahana/models/embed/embeddinggemma-300M-Q8_0.gguf}"

KEY="$(sed -n 's/^LLAMACPP_API_KEY=//p' "$HERE/../../backend/.env" 2>/dev/null | tail -1)"
if [ -z "$KEY" ]; then
  echo "Set LLAMACPP_API_KEY=<some long random text> in backend/.env first." >&2
  exit 1
fi
if [ ! -r "$MODEL" ]; then
  echo "Missing or unreadable: $MODEL" >&2
  exit 1
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true
# One slot of 2048 tokens (the model's input limit); a batch that size, so a whole passage is embedded at once.
docker run -d --name "$NAME" --restart unless-stopped \
  -p "127.0.0.1:$PORT:8080" \
  -v "$(dirname "$MODEL"):/models:ro" \
  -e LLAMA_API_KEY="$KEY" \
  "$IMAGE" \
  -m "/models/$(basename "$MODEL")" --alias embeddinggemma \
  --embedding --pooling mean -ngl 0 -c 2048 -np 1 -b 2048 -ub 2048 --host 0.0.0.0 --port 8080
echo "embeddings: http://127.0.0.1:$PORT (docker logs -f $NAME)"
