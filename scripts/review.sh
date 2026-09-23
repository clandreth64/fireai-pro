#!/bin/sh
# FireAI ground-truth review — one-command launcher (macOS / Linux / Git Bash).
#   sh scripts/review.sh
# Needs Docker. Builds fireai:dev the first time, then serves http://127.0.0.1:8765 (this computer only).
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE=fireai:dev
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -f "$REPO/docker/Dockerfile" -t "$IMAGE" "$REPO"
echo "Review tool: http://127.0.0.1:8765   (Ctrl+C to stop)"
MSYS_NO_PATHCONV=1 docker run --rm -it --user root -p 127.0.0.1:8765:8765 -v "$REPO:/app" -w /app "$IMAGE" \
    python scripts/gt_review_server.py --host 0.0.0.0 --port 8765
