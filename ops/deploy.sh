#!/usr/bin/env bash
set -euo pipefail
umask 077

cd /opt/vpn-monitor-bot
exec 9>/run/lock/vpn-monitor-bot-deploy.lock
flock -n 9 || exit 0

git fetch --no-tags origin refs/heads/deploy
previous=$(git rev-parse HEAD)
candidate=$(git rev-parse FETCH_HEAD)
[[ "$previous" != "$candidate" ]] || exit 0
git merge-base --is-ancestor "$previous" "$candidate"

image=vpn-monitor-bot-vpn-monitor-bot
candidate_image="$image:$candidate"
export VPN_MONITOR_IMAGE="$candidate_image"
git checkout --detach "$candidate"

if docker compose config --quiet && docker compose build; then
    if docker compose up -d --no-build --wait --wait-timeout 90; then
        docker image tag "$candidate_image" "$image:latest"
        docker image rm "$candidate_image"
        printf 'Deployed %s\n' "$candidate"
        exit 0
    fi
    git checkout --detach "$previous"
    unset VPN_MONITOR_IMAGE
    docker compose up -d --no-build --wait --wait-timeout 90
else
    git checkout --detach "$previous"
fi

printf 'Deployment failed for %s. Previous revision: %s\n' "$candidate" "$previous" >&2
exit 1
