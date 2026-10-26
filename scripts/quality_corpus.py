"""Reproducible 12-scene synthetic motion corpus using two real demo photos.

This is an engineering regression set, not a replacement for users' real videos.
Sources are documented in work/samples/SOURCES.md and README.md.
"""
import json
import sys
import time
from pathlib import Path
import threading
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import av
import cv2
import numpy as np

from reframe.contracts import ExportRequest
from reframe.pipeline import run


def main():
    root = Path("work/samples")
    root.mkdir(parents=True, exist_ok=True)
    dog = cv2.imread(str(root / "dog.jpg"))
    people = cv2.imread(str(root / "people.jpg"))
    if dog is None or people is None:
        raise SystemExit("Download the two documented demo photos first.")
    person = cv2.resize(people[25:720, 740:1170], (132, 214))
    pet = cv2.resize(dog[220:560, 125:320], (114, 200))
    bike = cv2.resize(dog[65:182, 450:710], (132, 60))
    definitions = [
        ("single_person", person, "still"), ("walking_person", person, "moving"),
        ("fast_motion", person, "fast"), ("small_motion", person, "jitter"),
        ("animal", pet, "moving"), ("object", bike, "moving"),
        ("two_people", person, "two"), ("large_subject", person, "large"),
        ("brief_occlusion", person, "occlusion"), ("subject_leaves", person, "leaves"),
        ("hard_cut", person, "cut"), ("empty_scene", None, "empty")]
    annotations = []
    source = root / "corpus.mp4"
    with av.open(str(source), "w") as output:
        stream = output.add_stream("libx264", rate=25)
        stream.width, stream.height, stream.pix_fmt = 640, 360, "yuv420p"
        stream.options = {"crf": "18", "preset": "veryfast"}
        index = 0
        for scene, (name, sprite, mode) in enumerate(definitions):
            for n in range(75):
                color = (24, 31, 42) if scene % 2 == 0 else (135, 160, 150)
                canvas = np.full((360, 640, 3), color, np.uint8)
                box = None
                if sprite is not None:
                    obj = sprite
                    x, y = 254, 72
                    if mode in {"moving", "fast", "occlusion", "leaves"}:
                        x = int(65 + n / 74 * 370)
                    if mode == "fast":
                        x = int(250 + 180 * np.sin(n / 74 * np.pi * 2))
                    if mode == "jitter":
                        x += int(8 * np.sin(n * .5))
                    if mode == "large":
                        obj = cv2.resize(sprite, (240, 350)); x, y = 200, 5
                    if mode == "cut" and n >= 38:
                        x = 440
                        canvas[:] = (160, 80, 45)
                    if mode == "cut" and n < 38:
                        x = 50
                    visible = not (mode == "occlusion" and 32 <= n < 39) and not (mode == "leaves" and n >= 35)
                    if visible:
                        hh, ww = obj.shape[:2]
                        canvas[y:y + hh, x:x + ww] = obj
                        box = [x, y, x + ww, y + hh]
                    if mode == "two":
                        canvas[:] = color
                        canvas[y:y + 214, 65:197] = sprite
                        canvas[y:y + 214, 440:572] = sprite
                        box = None
                annotations.append({"time": index / 25, "scene": name, "box": box,
                                    "expect_crop": mode in {"still", "moving", "jitter"}})
                frame = av.VideoFrame.from_ndarray(canvas, format="bgr24")
                frame.pts = index
                for packet in stream.encode(frame):
                    output.mux(packet)
                index += 1
        for packet in stream.encode():
            output.mux(packet)
    (root / "annotations.json").write_text(json.dumps(annotations), encoding="utf-8")
    started = time.perf_counter()
    stages = {}
    def emit(event):
        if event.stage not in stages:
            stages[event.stage] = time.perf_counter() - started
            print(event.stage, flush=True)
    run(ExportRequest(source, root / "corpus-9x16.mp4", overwrite=True, cpu_only=True,
                      diagnostics=root / "diagnostics.json"), threading.Event(), emit)
    elapsed = time.perf_counter() - started
    (root / "benchmark.json").write_text(json.dumps({"seconds": elapsed, "duration": 36,
                                                      "stages": stages}, indent=2), encoding="utf-8")
    print(f"36 s video: {elapsed:.2f} s on CPU", flush=True)


if __name__ == "__main__":
    main()
