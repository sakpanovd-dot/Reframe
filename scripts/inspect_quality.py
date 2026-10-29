"""Measure annotated containment and write diagnostic contact sheets."""
import json
import argparse
import sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2
import numpy as np
from reframe.contracts import ReframePlan, Keyframe
from reframe.media import probe, frames
from reframe.render import Renderer


def main():
    root = Path("work/samples")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostics', type=Path, default=root / 'diagnostics.json')
    parser.add_argument('--output', type=Path, default=root)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.diagnostics.read_text(encoding='utf-8'))
    p = data["plan"]
    p["keyframes"] = [Keyframe(**k) for k in p["keyframes"]]
    plan = ReframePlan(**p)
    renderer = Renderer(plan)
    annotations = json.loads((root / "annotations.json").read_text())
    result = defaultdict(lambda: {"frames": 0, "crop": 0, "visible": 0, "contained": 0})
    for item in annotations:
        metric = result[item["scene"]]
        mode, x, y, _ = renderer.position(item["time"])
        metric["frames"] += 1
        metric["crop"] += mode == "crop"
        if item["box"]:
            metric["visible"] += 1
            x1, y1, x2, y2 = item["box"]
            # Annotation encloses the photo crop, slightly larger than the actual subject.
            inside = x <= x1 + 8 and x2 - 8 <= x + plan.crop_width and y <= y1 + 8 and y2 - 8 <= y + plan.crop_height
            metric["contained"] += mode == "fit" or inside
    (args.output / "quality.json").write_text(json.dumps(dict(result), indent=2))
    simple = {"single_person", "walking_person", "small_motion", "animal", "object"}
    checks = {"containment_at_least_95_percent": all(not m["visible"] or m["contained"] / m["visible"] >= .95 for m in result.values()),
              "simple_scenes_crop_at_least_70_percent": all(result[name]["crop"] / result[name]["frames"] >= .7 for name in simple)}
    (args.output / "quality-summary.json").write_text(json.dumps(checks, indent=2))
    print(json.dumps(dict(result), indent=2))
    tiles = []
    next_time = 1.5
    for t, _, image in frames(probe(root / "corpus.mp4")):
        if t + .001 < next_time:
            continue
        next_time += 3
        mode, x, y, _ = renderer.position(t)
        source = image.copy()
        if mode == "crop":
            cv2.rectangle(source, (x, y), (x + plan.crop_width, y + plan.crop_height), (124, 244, 195), 3)
        sample = min(data["analysis"]["samples"], key=lambda s: abs(s["time"] - t))
        for obj in sample["objects"]:
            x1, y1, x2, y2 = map(round, obj["box"])
            cv2.rectangle(source, (x1, y1), (x2, y2), (80, 160, 255), 1)
        output = renderer.frame(image, t)
        tile = np.full((230, 460, 3), (24, 24, 24), np.uint8)
        tile[25:205, :320] = cv2.resize(source, (320, 180))
        tile[25:205, 340:441] = cv2.resize(output, (101, 180))
        scene = annotations[min(round(t * 25), len(annotations)-1)]["scene"]
        cv2.putText(tile, f"{scene} / {mode}", (8, 17), cv2.FONT_HERSHEY_SIMPLEX, .45, (230, 230, 230), 1)
        tiles.append(tile)
    sheet = np.vstack([np.hstack(tiles[i:i + 3]) for i in range(0, len(tiles), 3)])
    cv2.imwrite(str(args.output / "contact-sheet.jpg"), sheet)
    if not all(checks.values()):
        raise SystemExit("Quality gate failed; inspect quality.json and contact-sheet.jpg")


if __name__ == "__main__":
    main()
