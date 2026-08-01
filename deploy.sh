#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Deploy script for nargila-counter on CasaOS (Docker) over SSH.
# Runs locally on the Mac; drives the server via SSH.
#
# Secrets live only in ~/nargila-counter/.env on the server (never committed).
#
# Actions:
#   deploy    Build and restart on the server (default)
#   status    Show container status
#   logs      Follow container logs (Ctrl+C to exit)
#   stop      Stop the container
#   ssh       Open a shell on the server in the repo dir
# ----------------------------------------------------------------------------
set -euo pipefail

# ---------------------------- config (edit me) ------------------------------
SSH_HOST="casaos"                        # Host alias from ~/.ssh/config (User casaos)
REMOTE_DIR="/home/casaos/nargila-counter"  # repo path on the server (absolute, no ~)
MODE="run"                               # single container, no compose file
CONTAINER_NAME="nargila-counter-bot"
IMAGE_NAME="nargila-counter-bot"
ENV_FILE_ON_SERVER="$REMOTE_DIR/.env"    # read by `docker run --env-file`
RUN_MIGRATIONS=false                     # no Alembic for this project
GIT_BRANCH="master"
# ---------------------------------------------------------------------------

# The casaos user is not in the docker group, so we go through sudo (NOPASSWD).
DOCKER="sudo docker"

ACTION="${1:-deploy}"

remote() {
  ssh -o BatchMode=yes "$SSH_HOST" "$@"
}

run_remote_script() {
  ssh -o BatchMode=yes -T "$SSH_HOST" "bash -s"
}

case "$ACTION" in
  deploy)
    echo "==> Deploying $CONTAINER_NAME to $SSH_HOST:$REMOTE_DIR ($MODE mode)"

    case "$MODE" in
      run)
        run_remote_script <<EOF
set -euo pipefail
cd "$REMOTE_DIR"

echo "-- pulling latest code --"
git fetch --quiet origin
git reset --hard "origin/$GIT_BRANCH"
git log -1 --oneline

echo "-- building image --"
$DOCKER build --no-cache -t "$IMAGE_NAME" .

echo "-- restarting container --"
$DOCKER rm -f "$CONTAINER_NAME" 2>/dev/null || true
$DOCKER run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --env-file "$ENV_FILE_ON_SERVER" \
  -v "${CONTAINER_NAME}-data:/data" \
  "$IMAGE_NAME"

echo "-- container status --"
$DOCKER ps --filter "name=$CONTAINER_NAME" --format '{{.Names}}\t{{.Status}}'
EOF
        ;;

      *)
        echo "Unknown MODE: $MODE (expected 'run')" >&2
        exit 1
        ;;
    esac

    echo "==> Deploy complete. Run '$0 status' or '$0 logs' to verify."
    ;;

  status)
    remote "$DOCKER ps --filter 'name=$CONTAINER_NAME' --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
    ;;

  logs)
    remote "$DOCKER logs -f --tail=200 '$CONTAINER_NAME'"
    ;;

  stop)
    remote "$DOCKER stop '$CONTAINER_NAME'"
    ;;

  ssh)
    remote "cd '$REMOTE_DIR' && exec \${SHELL:-/bin/bash}"
    ;;

  *)
    cat <<EOF
Usage: $0 <action>

Actions:
  deploy    Build and restart on the server (default)
  status    Show container status
  logs      Follow container logs (Ctrl+C to exit)
  stop      Stop the container
  ssh       Open a shell on the server in $REMOTE_DIR
EOF
    exit 1
    ;;
esac
