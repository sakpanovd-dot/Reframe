"""Best-effort local preferences and logging, independent of video processing."""
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

JOB_FOLDERS = 'job-folders.json'  # in work/, outside Git
JOB_DIRECTORY = re.compile(r'\.reframe-[0-9a-f]{32}')  # exactly the name pipeline.run gives its temporary folder
STALE_AFTER = 24 * 3600
MAX_FOLDERS = 50


def read_settings(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(path: Path, settings: dict) -> bool:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.reframe-settings-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            json.dump(settings, file, ensure_ascii=False)
        os.replace(temporary, path)
        return True
    except OSError:
        logging.warning('Cannot save preferences: %s', path, exc_info=True)
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logging.warning('Cannot remove temporary preferences: %s', temporary, exc_info=True)


def configure_logging(root: Path) -> bool:
    try:
        directory = root / 'work'
        directory.mkdir(exist_ok=True)
        handler = logging.FileHandler(directory / 'reframe.log', encoding='utf-8')
    except OSError:
        logging.basicConfig(handlers=[logging.StreamHandler()], level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s', force=True)
        logging.warning('File logging unavailable; using launch console', exc_info=True)
        return False
    logging.basicConfig(handlers=[handler], level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s', force=True)
    return True


def remember_job_folder(root: Path, folder: Path) -> None:
    """Remember where a job creates its temporary folder, so a crash leftover can be removed later."""
    path = root / 'work' / JOB_FOLDERS
    try:
        folders = [str(Path(folder).resolve())]
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(saved, list):
                folders += [item for item in saved if isinstance(item, str) and item not in folders]
        except (OSError, ValueError):
            pass
        path.parent.mkdir(exist_ok=True)
    except OSError:
        # Never fail a job because of housekeeping.
        logging.warning('Cannot remember job folder: %s', folder, exc_info=True)
        return
    save_settings(path, folders[:MAX_FOLDERS])


def _last_activity(directory: Path) -> float:
    # A running job keeps writing its files; the folder's own time alone is not enough.
    newest = directory.lstat().st_mtime
    for base, folders, files in os.walk(directory):
        for name in folders + files:
            newest = max(newest, os.lstat(os.path.join(base, name)).st_mtime)
    return newest


def cleanup_stale_jobs(root: Path, now: float | None = None) -> list[Path]:
    """Remove '.reframe-<id>' folders left by a crash: only in remembered folders, only after a day without changes."""
    try:
        saved = json.loads((root / 'work' / JOB_FOLDERS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if not isinstance(saved, list):
        return []
    now = time.time() if now is None else now
    removed = []
    for folder in saved:
        if not isinstance(folder, str):
            continue
        try:
            candidates = list(Path(folder).iterdir())
        except OSError:
            continue
        for directory in candidates:
            try:
                if not JOB_DIRECTORY.fullmatch(directory.name) or directory.is_symlink() or getattr(directory, "is_junction", lambda: False)() \
                        or not directory.is_dir() or now - _last_activity(directory) < STALE_AFTER:
                    continue
                shutil.rmtree(directory)
                removed.append(directory)
                logging.info('Removed temporary folder left by an interrupted job: %s', directory)
            except OSError:
                logging.warning('Cannot remove old temporary folder: %s', directory, exc_info=True)
    return removed
