#!/usr/bin/env python3
"""Synchronize Markdown knowledge from Google Drive by folder ID."""

import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload


DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
DEFAULT_LOCAL_PATH = "/root/.hermes/drive/obsidian"


def log(message):
    print(f"[Google Drive] {message}", flush=True)


def credentials_info():
    encoded = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON_B64", "").strip()
    raw = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    if encoded:
        try:
            raw = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("GDRIVE_SERVICE_ACCOUNT_JSON_B64 is invalid") from exc
    if not raw:
        raise RuntimeError("Google Drive service account credentials are missing")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Google Drive service account JSON is invalid") from exc


def drive_service():
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info(), scopes=[DRIVE_READONLY]
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def list_children(service, parent_id):
    files = []
    page_token = None
    while True:
        response = service.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            spaces="drive",
            pageSize=1000,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            fields=(
                "nextPageToken,files(id,name,mimeType,modifiedTime,size,"
                "shortcutDetails,capabilities/canDownload)"
            ),
        ).execute()
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return files


def folder_target(item):
    if item.get("mimeType") == FOLDER_MIME:
        return item["id"]
    if item.get("mimeType") == SHORTCUT_MIME:
        details = item.get("shortcutDetails", {})
        if details.get("targetMimeType") == FOLDER_MIME:
            return details.get("targetId")
    return None


def resolve_source_folder(service, root_id, source_path):
    current_id = root_id
    normalized = source_path.strip().strip("/")
    if not normalized:
        return current_id
    for segment in (part for part in normalized.split("/") if part):
        children = list_children(service, current_id)
        match = next(
            (item for item in children if item.get("name") == segment and folder_target(item)),
            None,
        )
        if not match:
            available = sorted(
                item.get("name", "") for item in children if folder_target(item)
            )
            raise RuntimeError(
                f"folder '{segment}' was not found; visible folders: "
                f"{', '.join(available[:20]) or 'none'}"
            )
        current_id = folder_target(match)
    return current_id


def safe_name(name):
    return name.replace("/", "_").replace("\\", "_").strip() or "unnamed"


def download_file(service, file_id, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with temporary.open("wb") as output:
        downloader = MediaIoBaseDownload(output, request, chunksize=1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    os.replace(temporary, destination)


def sync_markdown(service, source_id, destination):
    downloaded = []
    visited = set()

    def walk(folder_id, relative):
        if folder_id in visited:
            return
        visited.add(folder_id)
        for item in list_children(service, folder_id):
            name = safe_name(item.get("name", ""))
            target_folder = folder_target(item)
            if target_folder:
                walk(target_folder, relative / name)
                continue
            file_id = item.get("id")
            if item.get("mimeType") == SHORTCUT_MIME:
                file_id = item.get("shortcutDetails", {}).get("targetId")
            if not name.lower().endswith(".md") or not file_id:
                continue
            output_path = destination / relative / name
            download_file(service, file_id, output_path)
            downloaded.append(str((relative / name).as_posix()))

    walk(source_id, Path())
    return downloaded


def run_sync():
    root_id = os.environ.get("GDRIVE_ROOT_FOLDER_ID", "").strip()
    if not root_id:
        raise RuntimeError("GDRIVE_ROOT_FOLDER_ID is missing")
    source_path = os.environ.get("GDRIVE_SOURCE_SUBPATH", "02_wiki")
    destination = Path(os.environ.get("GDRIVE_LOCAL_PATH", DEFAULT_LOCAL_PATH))
    log(f"API sync started: subpath='{source_path or '/'}'")
    service = drive_service()
    source_id = resolve_source_folder(service, root_id, source_path)
    downloaded = sync_markdown(service, source_id, destination)
    if not downloaded:
        raise RuntimeError("Drive API returned no Markdown files")
    log(f"API sync complete: {len(downloaded)} markdown file(s)")
    for filename in downloaded[:20]:
        log(f"synced: {filename}")
    if len(downloaded) > 20:
        log(f"and {len(downloaded) - 20} more file(s)")
    return len(downloaded)


def main():
    parser = argparse.ArgumentParser(description="Sync Obsidian Markdown through Drive API")
    parser.add_argument("--once", action="store_true", help="Run one synchronization")
    parser.parse_args()
    try:
        run_sync()
    except (RuntimeError, HttpError, OSError) as exc:
        log(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
