#!/usr/bin/env bash
set -euo pipefail

BOT_TOKEN="8898548415:AAHqNwWn4ywZ0dwzfPT-j40zbCiOX_PQQbk"

REPO_URL="https://github.com/PokoRoko/nargila-counter.git"
REPO_DIR="$HOME/nargila-counter"
IMAGE_NAME="nargila-counter-bot"
CONTAINER_NAME="nargila-counter-bot"

# Clone or update
if [ -d "$REPO_DIR" ]; then
    echo "Updating repository..."
    cd "$REPO_DIR"
    git pull
else
    echo "Cloning repository..."
    cd "$HOME"
    git clone "$REPO_URL"
    cd "$REPO_DIR"
fi

# Remove old container
echo "Removing old container..."
sudo docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Build image
echo "Building image..."
sudo docker build --no-cache -t "$IMAGE_NAME" .

# Run with auto-restart
echo "Starting container..."
sudo docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -e TELEGRAM_BOT_TOKEN="$BOT_TOKEN" \
    -v nargila-counter-data:/data \
    "$IMAGE_NAME"

echo "Done. Showing logs (Ctrl+C to exit)..."
sudo docker logs -f "$CONTAINER_NAME"
