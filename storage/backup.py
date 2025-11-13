from __future__ import annotations
import os
import zipfile
from datetime import datetime, timezone
from typing import Tuple

try:
    from core.config import SAVE_FOLDER  # typically 'characters'
except Exception:
    SAVE_FOLDER = 'characters'

BACKUPS_DIR = os.path.join(SAVE_FOLDER, 'backups')

def _timestamp() -> str:
    # Use UTC to avoid TZ ambiguity
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

def _should_include(file_path: str) -> bool:
    # Only include JSON files by default
    return file_path.lower().endswith('.json')

def create_backup() -> Tuple[str, int]:
    """Create a zip backup of the SAVE_FOLDER into SAVE_FOLDER/backups.

    Returns (zip_path, file_count).
    """
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    ts = _timestamp()
    zip_name = f"characters_backup_{ts}.zip"
    zip_path = os.path.join(BACKUPS_DIR, zip_name)

    count = 0
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(SAVE_FOLDER):
            # Skip the backups directory itself
            if os.path.normpath(root).startswith(os.path.normpath(BACKUPS_DIR)):
                continue
            for fname in files:
                fpath = os.path.join(root, fname)
                if not _should_include(fpath):
                    continue
                # Store paths relative to SAVE_FOLDER in the zip
                rel = os.path.relpath(fpath, SAVE_FOLDER)
                zf.write(fpath, arcname=rel)
                count += 1
    return zip_path, count
