@AGENTS.md

# Claude Code notes

Read README.md, TESTING.md and NEXT_TASKS.md before changing code. Talk to the user in Russian;
the user is not a programmer, the product is for their brother, a professional video editor.

## Branches (important)

- Active development branch: `claude/dev`. `main` is outdated and must not be touched.
- Start each task from `claude/dev` on a new branch `claude/<short-task-name>`.
- Open the pull request **into `claude/dev`**, never into `main`. Do not merge it yourself.
- Never force-push. Never rewrite `claude/dev` or `fix/input-codecs-preview`.

## Cloud (Linux) sessions

- Run `bash scripts/cloud_setup.sh` once if `.venv` is missing. It installs FFmpeg/Qt libraries,
  the locked Python packages and both models (YOLOX-s, YuNet), then runs the tests.
- There are no videos in the repository (`user test/` and `work/` are ignored). Real-video quality
  checks happen on the user's Windows laptop; in the cloud rely on unit tests and synthetic clips
  generated with FFmpeg.
- Linux checks do not certify the Windows UI, NVIDIA, or real-video quality. Say so in the PR.
- QML changes: `QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software python scripts/smoke_ui.py`,
  then look at the screenshot in `work/`.

## Product decisions already made with the editor (do not undo)

- 16:9 -> 9:16 editorial strategy: live footage is always cropped; no blurred background.
- Title cards and packshots on flat colour: fitted whole on their own extended background.
- Split screens: one panel full frame, switch at most every 2 s.
- Burned-in captions/plates and logos over live footage are ignored on purpose (the editor
  re-renders them separately).
- Dialogue that does not fit: alternate speakers, shots >= 2 s; two-shot when faces fit.
- Quality matters more than speed; processing runs in the background.
