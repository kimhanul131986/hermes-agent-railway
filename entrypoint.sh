#!/usr/bin/env bash
set -e

AUTO_UPDATE="${AUTO_UPDATE:-true}"
GDRIVE_SYNC_INTERVAL="${GDRIVE_SYNC_INTERVAL:-900}"
GDRIVE_LOCAL_PATH="${GDRIVE_LOCAL_PATH:-/root/.hermes/drive/obsidian}"

start_gdrive_sync() {
  if [ -z "${GDRIVE_SERVICE_ACCOUNT_JSON:-}" ] || [ -z "${GDRIVE_ROOT_FOLDER_ID:-}" ]; then
    echo "Google Drive sync disabled: required variables are not configured."
    return
  fi

  local credentials_path="/tmp/gdrive-service-account.json"
  printf '%s' "$GDRIVE_SERVICE_ACCOUNT_JSON" > "$credentials_path"
  chmod 600 "$credentials_path"
  mkdir -p "$GDRIVE_LOCAL_PATH"

  (
    while true; do
      echo "Syncing Google Drive Obsidian folder (read-only remote)..."
      if rclone copy ":drive,service_account_file=${credentials_path},root_folder_id=${GDRIVE_ROOT_FOLDER_ID}:" \
          "$GDRIVE_LOCAL_PATH" \
          --create-empty-src-dirs \
          --checkers 8 \
          --transfers 4; then
        echo "Google Drive sync complete."
      else
        echo "Google Drive sync failed; retrying after ${GDRIVE_SYNC_INTERVAL}s."
      fi
      sleep "$GDRIVE_SYNC_INTERVAL"
    done
  ) &
}

if [ "$AUTO_UPDATE" = "true" ]; then
  echo "Checking for Hermes updates..."
  cd /opt/hermes-agent
  if git pull --recurse-submodules 2>&1 | grep -v 'Already up to date'; then
    echo "Updating dependencies..."
    VIRTUAL_ENV=/opt/hermes-agent/venv uv pip install -e ".[all]" --quiet
    echo "Update complete."
  else
    echo "Already up to date."
  fi
fi

start_gdrive_sync

hermes dashboard --host 127.0.0.1 --port 9119 --no-open &

exec python /auth_proxy.py