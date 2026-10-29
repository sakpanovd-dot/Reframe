# Reframe development

Reframe is a local Windows video reframing application, Python 3.12 + PySide6/QML.
User-facing text is Russian. Read README.md and TESTING.md before making changes.

## Architecture and invariants

- Keep detection/tracking, crop planning, rendering, container export, and GUI separate.
- Aspect presets belong in reframe/presets.json; containers in the exporter registry.
- Never overwrite the input. Publish the output only after successful verification.
- Preserve video timing and the primary audio track; support VFR, rotation, SAR, and no audio.
- Processing stays local, outside the GUI process, with cancellation and cleanup.
- Uncertain scenes must never silently lose the subject. In the editorial 16:9 -> 9:16 strategy
  live footage is always cropped (best guess + review-report flag): the user rejects blurred
  backgrounds. Flat title cards/packshots are fitted on their own extended background; split
  screens show one panel. Other aspect pairs keep the conservative full-frame fallback.
- Do not upload videos, model weights, environments, logs, settings, or test artifacts to Git.

## Setup and checks

- Windows: setup.ps1; run with .venv/Scripts/python.exe -m reframe.
- Cloud/Linux: follow CLOUD.md. Linux testing does not certify the Windows UI or NVIDIA support.
- Install dependencies from requirements-lock.txt, then pip install --no-deps -e .
- Download the verified model with python scripts/download_model.py during network-enabled setup.
- Run python -m pytest -q for behavioral changes. FFmpeg and ffprobe must be on PATH.
- For QML changes, run scripts/smoke_ui.py and inspect its screenshot in work/.
- Synthetic quality checks require separately downloaded photos; instructions are in README.md.
- scripts/benchmark.py uses Windows APIs; do not treat it as a portable cloud benchmark.
- Report actual checks and remaining limitations. Never claim GPU or real-video acceptance without evidence.

## Git workflow

The remote repository is sakpanovd-dot/Reframe. Make cloud changes in a feature branch
and submit a pull request. Do not overwrite concurrent work or force-push main.
After merging, synchronize the laptop with git pull --ff-only when its working tree is clean.
