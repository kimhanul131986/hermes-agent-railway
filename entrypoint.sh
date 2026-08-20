#!/usr/bin/env bash
set -e

AUTO_UPDATE="${AUTO_UPDATE:-true}"
GDRIVE_SYNC_INTERVAL="${GDRIVE_SYNC_INTERVAL:-900}"
GDRIVE_LOCAL_PATH="${GDRIVE_LOCAL_PATH:-/root/.hermes/drive/obsidian}"
GDRIVE_SOURCE_SUBPATH="${GDRIVE_SOURCE_SUBPATH:-Obsidian}"
MARKETING_SCHEDULER_ENABLED="${MARKETING_SCHEDULER_ENABLED:-false}"

start_marketing_scheduler() {
  if [ "$MARKETING_SCHEDULER_ENABLED" = "true" ]; then
    echo "[Scheduler] enabling marketing pipeline scheduler..."
    python /marketing_pipeline.py --scheduler &
  else
    echo "[Scheduler] marketing pipeline scheduler disabled."
  fi
}

gdrive_is_configured() {
  { [ -n "${GDRIVE_SERVICE_ACCOUNT_JSON_B64:-}" ] || [ -n "${GDRIVE_SERVICE_ACCOUNT_JSON:-}" ]; } \
    && [ -n "${GDRIVE_ROOT_FOLDER_ID:-}" ]
}

start_gdrive_sync() {
  if ! gdrive_is_configured; then
    echo "[Google Drive] sync disabled: required variables are not configured."
    start_marketing_scheduler
    return 0
  fi

  (
    set +e

    local credentials_path="/tmp/gdrive-service-account.json"
    if [ -n "${GDRIVE_SERVICE_ACCOUNT_JSON_B64:-}" ]; then
      printf '%s' "$GDRIVE_SERVICE_ACCOUNT_JSON_B64" | base64 -d > "$credentials_path"
    else
      printf '%s' "$GDRIVE_SERVICE_ACCOUNT_JSON" > "$credentials_path"
    fi

    chmod 600 "$credentials_path"
    mkdir -p "$GDRIVE_LOCAL_PATH"

    local remote=":drive,service_account_file=${credentials_path},root_folder_id=${GDRIVE_ROOT_FOLDER_ID},scope=drive.readonly,shared_with_me=true:"
    local source="${remote}${GDRIVE_SOURCE_SUBPATH}"
    local scheduler_started="false"

    while true; do
      echo "[Google Drive] sync started: subpath='${GDRIVE_SOURCE_SUBPATH:-/}'"
      rclone lsf "$source" --max-depth 1 --dirs-only || true

      if timeout 600 rclone copy "$source" \
          "$GDRIVE_LOCAL_PATH" \
          --filter "+ **/" \
          --filter "+ **.md" \
          --filter "- **" \
          --checkers 4 \
          --transfers 2 \
          --stats 60s \
          --stats-one-line \
          --log-level NOTICE; then
        local markdown_count
        markdown_count="$(find "$GDRIVE_LOCAL_PATH" -type f -name '*.md' | wc -l | tr -d ' ')"
        if [ "$markdown_count" -gt 0 ]; then
          echo "[Google Drive] sync complete: ${markdown_count} markdown file(s)."
          if [ "$scheduler_started" = "false" ]; then
            start_marketing_scheduler
            scheduler_started="true"
          fi
        else
          echo "[Google Drive ERROR] sync returned no markdown files; scheduler will wait."
        fi
      else
        echo "[Google Drive ERROR] sync failed or timed out; scheduler will wait and retry after ${GDRIVE_SYNC_INTERVAL}s."
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
