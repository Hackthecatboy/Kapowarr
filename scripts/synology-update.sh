#!/usr/bin/env bash
# Run on the NAS. Images are built locally; nothing is pushed to a registry.
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"
mode=${1:---update}
case "$mode" in
    --check|--start|--update) ;;
    *) echo "Usage: bash scripts/synology-update.sh [--check|--start|--update]" >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo "Only one option is supported." >&2; exit 2; }
[[ -f .env.synology ]] || {
    echo "Copy .env.synology.example to .env.synology and fill in NAS paths and IDs." >&2
    exit 1
}
if docker compose version >/dev/null 2>&1; then
    compose=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    compose=(docker-compose)
else
    echo "Container Manager's Docker Compose command is not available in PATH." >&2
    exit 1
fi
compose+=(--project-name kapowarr-dev --env-file .env.synology -f compose.synology.yml)
"${compose[@]}" config --quiet
docker info >/dev/null
echo "Checkout: $repo_dir"
echo "Compose: ${compose[*]}"
if [[ "$mode" == --check ]]; then
    "${compose[@]}" config
    echo "Configuration and Docker access checked; no build or deployment performed."
    exit 0
fi

if [[ "$mode" == --update ]]; then
    branch=$(git branch --show-current)
    [[ "$branch" == feature/sonarr-integrations ]] || {
        echo "Expected feature/sonarr-integrations; found '$branch'." >&2; exit 1
    }
    [[ -z "$(git status --porcelain)" ]] || {
        echo "Checkout has local changes. Resolve them before updating." >&2; exit 1
    }
    git fetch origin feature/sonarr-integrations
    # Permit only a checkout equal to or behind the fetched branch.
    git merge-base --is-ancestor HEAD FETCH_HEAD || {
        echo "Local history has diverged from GitHub; no deployment performed." >&2; exit 1
    }
    git merge --ff-only FETCH_HEAD
    # Re-read the updated script and configuration before building.
    exec bash "$repo_dir/scripts/synology-update.sh" --start
fi

echo "Building local image before changing the running container..."
"${compose[@]}" build kapowarr
echo "Stopping the test service for a consistent database-folder backup..."
"${compose[@]}" stop kapowarr
if ! "${compose[@]}" run --rm --no-deps -T --entrypoint python3 kapowarr -c '
from datetime import datetime, timezone
from pathlib import Path
from shutil import copytree
source = Path("/app/db")
if any(source.iterdir()):
    destination = Path("/backups") / datetime.now(timezone.utc).strftime("db-%Y%m%dT%H%M%S%fZ")
    copytree(source, destination)
    print("Database folder backed up to", destination)
else:
    print("Fresh database folder; no backup needed.")
'; then
    echo "Backup failed. Restarting the existing test container; deployment aborted." >&2
    "${compose[@]}" start kapowarr || true
    exit 1
fi
"${compose[@]}" up -d --no-build kapowarr
echo "Waiting for the web health check..."
container_id=$("${compose[@]}" ps -q kapowarr)
for ((attempt=0; attempt<60; attempt++)); do
    status=$(docker inspect --format '{{.State.Health.Status}}' "$container_id")
    if [[ "$status" == healthy ]]; then
        "${compose[@]}" ps
        echo "Kapowarr is healthy. Open http://NAS-IP:<KAPOWARR_PORT> (default 5657)."
        exit 0
    fi
    if [[ "$status" == unhealthy ]]; then break; fi
    sleep 5
done
echo "Container did not become healthy. Database backup retained; inspect the logs." >&2
"${compose[@]}" logs --tail 80 kapowarr
exit 1
