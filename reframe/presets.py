import json
from pathlib import Path
from .contracts import Preset


def load_presets(path: Path | None = None) -> dict[str, Preset]:
    rows = json.loads((path or Path(__file__).with_name("presets.json")).read_text(encoding="utf-8"))
    presets = {}
    for row in rows:
        preset = Preset(**row)
        if preset.id in presets or min(preset.width, preset.height) < 2:
            raise ValueError("Invalid or duplicate preset")
        presets[preset.id] = preset
    return presets
