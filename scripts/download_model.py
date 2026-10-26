"""Run explicitly during setup. The application never downloads data itself."""
import hashlib
import json
import os
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def main():
    manifest = json.loads((ROOT / "reframe" / "model.json").read_text())
    folder = ROOT / "models"
    folder.mkdir(exist_ok=True)
    target = folder / manifest["filename"]
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == manifest["sha256"]:
        print("Модель уже установлена и проверена.")
        return
    partial = target.with_suffix(".part")
    try:
        with urllib.request.urlopen(manifest["url"], timeout=60) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        with partial.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        if checksum != manifest["sha256"]:
            raise RuntimeError("Контрольная сумма модели не совпала; файл не будет использован.")
        os.replace(partial, target)
        print("Модель установлена. Обработка видео доступна без интернета.")
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
