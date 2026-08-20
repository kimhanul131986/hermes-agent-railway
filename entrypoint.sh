#!/usr/bin/env bash
set -e

AUTO_UPDATE="${AUTO_UPDATE:-true}"
GDRIVE_SYNC_INTERVAL="${GDRIVE_SYNC_INTERVAL:-900}"
GDRIVE_LOCAL_PATH="${GDRIVE_LOCAL_PATH:-/root/.hermes/drive/obsidian}"
GDRIVE_SOURCE_SUBPATH="${GDRIVE_SOURCE_SUBPATH:-02_wiki}"
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
    mkdir -p "$GDRIVE_LOCAL_PATH"
    local scheduler_started="false"

    while true; do
      if python /gdrive_sync.py --once; then
        if [ "$scheduler_started" = "false" ]; then
          start_marketing_scheduler
          scheduler_started="true"
        fi
      else
        echo "[Google Drive ERROR] API sync failed; scheduler will wait and retry after ${GDRIVE_SYNC_INTERVAL}s."
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
