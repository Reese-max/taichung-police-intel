#!/usr/bin/env bash
# Relay host (always on, outside Taiwan): ask the Taiwan host for one S-001
# capture, check it, and publish it to the relay repository's main branch.
# The SSH key for the Taiwan host only runs capture-s001.ps1 there (forced
# command); the deploy key can write the relay repository and nothing else.
set -euo pipefail
umask 077

HOME_DIR="${GOVINTEL_RELAY_HOME:-$HOME/govintel-relay}"
TAIWAN_HOST="${GOVINTEL_RELAY_TAIWAN_HOST:?set GOVINTEL_RELAY_TAIWAN_HOST to user@address}"
RELAY_REPOSITORY="${GOVINTEL_RELAY_REPOSITORY:-git@github.com:Reese-max/govintel-source-relay.git}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$HOME_DIR/repo"
SSH_COMMON=(-o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$HOME_DIR/known_hosts")

exec 9>"$HOME_DIR/relay.lock"
if ! flock -n 9; then
  echo "RELAY_SKIPPED reason=already_running"
  exit 0
fi

capture="$(mktemp)"
trap 'rm -f "$capture"' EXIT
# The forced command ignores what is asked for; "capture" only names the intent.
# At most one byte past the checker's limit lands; check_relay.py refuses it.
timeout 120s ssh "${SSH_COMMON[@]}" -o ConnectTimeout=20 -i "$HOME_DIR/keys/taiwan_capture" \
  "$TAIWAN_HOST" capture < /dev/null | head -c 7100001 > "$capture"
python3 "$HERE/check_relay.py" "$capture"
observed_at="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["observed_at"])' "$capture")"

export GIT_SSH_COMMAND="ssh ${SSH_COMMON[*]} -i $HOME_DIR/keys/relay_deploy"
if [[ ! -d "$REPO/.git" ]]; then
  git init -q -b main "$REPO"
  git -C "$REPO" remote add origin "$RELAY_REPOSITORY"
fi
if timeout 60s git -C "$REPO" fetch -q --depth=1 origin main 2>/dev/null; then
  git -C "$REPO" checkout -q -B main FETCH_HEAD
fi
mkdir -p "$REPO/relay"
cp "$capture" "$REPO/relay/S-001.json"
git -C "$REPO" add relay/S-001.json
if git -C "$REPO" diff --cached --quiet; then
  echo "RELAY_UNCHANGED observed_at=$observed_at"
  exit 0
fi
git -C "$REPO" -c user.name="govintel-source-relay" -c user.email="relay@users.noreply.github.com" \
  commit -q -m "chore(relay): S-001 capture $observed_at"
timeout 60s git -C "$REPO" push -q origin HEAD:refs/heads/main
echo "RELAY_PUBLISHED source=S-001 observed_at=$observed_at"
