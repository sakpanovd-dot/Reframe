"""Best-effort local preferences and logging, independent of video processing."""
import json
import logging
import os
from pathlib import Path
import tempfile


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
