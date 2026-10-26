from __future__ import annotations

from collections import Counter
from itertools import groupby
from math import gcd

import numpy as np

from .contracts import Keyframe, ReframePlan


def dimensions(width, height, preset):
    # Exact aspect ratio AND even dimensions; never enlarge the crop.
    divisor = gcd(preset.width, preset.height)
    a, b = preset.width // divisor, preset.height // divisor
    step = 2 if a % 2 or b % 2 else 1
    k = int(min(width / a, height / b)) // step * step
    if k < step:
        from .contracts import ReframeError
        raise ReframeError("Исходное видео слишком маленькое для выбранных пропорций.")
    cw, ch = a * k, b * k
    out_k = min(k, divisor) // step * step
    return cw, ch, a * out_k, b * out_k


def make_plan(analysis, preset) -> ReframePlan:
    w, h = analysis.width, analysis.height
    cw, ch, ow, oh = dimensions(w, h, preset)
    keys = []
    same_ratio = abs(w / h - preset.ratio) < .005
    for scene, iterator in groupby(analysis.samples, lambda s: s.scene):
        samples = list(iterator)
        frequency = Counter(o.track_id for s in samples for o in s.objects)
        selected = None
        last_box = None
        last_seen = -100.
        velocity = np.zeros(4)
        center = np.array([w / 2, h / 2], dtype=float)
        pending_id, stable_since = None, 0.
        crop_active = False
        scene_keys = []
        safety_boxes = {}
        for s in samples:
            if same_ratio:
                scene_keys.append(Keyframe(s.time, scene, "crop", (w - cw) / 2, (h - ch) / 2))
                continue
            ranked = []
            for obj in s.objects:
                x1, y1, x2, y2 = obj.box
                area = (x2 - x1) * (y2 - y1) / (w * h)
                distance = np.hypot(((x1 + x2) / 2 - w / 2) / w, ((y1 + y2) / 2 - h / 2) / h)
                score = .4 * obj.confidence + .25 * min(1, area * 4) + .15 * (1 - distance) + .2 * frequency[obj.track_id] / len(samples)
                if obj.track_id == selected:
                    score += .15
                ranked.append((score, obj))
            ranked.sort(key=lambda item: item[0], reverse=True)
            best = ranked[0][1] if ranked else None
            ambiguous = len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .10
            box = None
            if best is not None and not ambiguous:
                if selected == best.track_id and last_box is not None:
                    velocity = .5 * velocity + .5 * (np.array(best.box) - last_box) / max(.001, s.time - last_seen)
                else:
                    velocity = np.zeros(4)
                selected = best.track_id
                last_box, last_seen = np.array(best.box), s.time
                box = last_box
            elif not ranked and last_box is not None and s.time - last_seen <= .4:
                box = last_box + velocity * (s.time - last_seen)
            safe = box is not None and box[2] - box[0] <= cw * .88 and box[3] - box[1] <= ch * .88
            if safe:
                if pending_id != selected:
                    pending_id, stable_since = selected, s.time
                    crop_active = False
                if s.time - stable_since >= .4 - 1e-6:
                    crop_active = True
                target = (box[:2] + box[2:]) / 2
                delta = target - center
                deadzone = np.array([cw, ch]) * .12
                center += np.sign(delta) * np.maximum(0, np.abs(delta) - deadzone)
                # Guarantee the entire selected box + 6% margin fits before smoothing.
                low = box[2:] + np.array([cw, ch]) * .06 - np.array([cw, ch]) / 2
                high = box[:2] - np.array([cw, ch]) * .06 + np.array([cw, ch]) / 2
                center = np.clip(center, low, high)
                center = np.clip(center, [cw / 2, ch / 2], [w - cw / 2, h - ch / 2])
            else:
                crop_active, pending_id = False, None
            scene_keys.append(Keyframe(s.time, scene, "crop" if crop_active else "fit",
                                       float(center[0] - cw / 2), float(center[1] - ch / 2)))
            if crop_active and box is not None:
                safety_boxes[s.time] = np.array(box)
        # Smooth only within uninterrupted crop runs; never across scene cuts or fit sections.
        for _, run in groupby(scene_keys, lambda k: k.mode):
            run = list(run)
            for i, key in enumerate(run):
                neighbors = run[max(0, i - 1):i + 2]
                position = np.array([np.mean([k.x for k in neighbors]), np.mean([k.y for k in neighbors])])
                mode = key.mode
                boxes = [safety_boxes[k.time] for k in neighbors if k.time in safety_boxes]
                if mode == "crop" and boxes:
                    # Constrain smoothing to a safe envelope covering adjacent detections.
                    # If motion is too fast for one crop, preserve the complete frame.
                    low = np.max([b[2:] for b in boxes], axis=0) - [cw, ch] + np.array([cw, ch]) * .03
                    high = np.min([b[:2] for b in boxes], axis=0) - np.array([cw, ch]) * .03
                    low = np.maximum(low, [0, 0])
                    high = np.minimum(high, [w - cw, h - ch])
                    if np.any(low > high):
                        mode = "fit"
                    else:
                        position = np.clip(position, low, high)
                keys.append(Keyframe(key.time, key.scene, mode, float(position[0]), float(position[1])))
    # Unsafe motion can introduce short fit gaps after smoothing. Require a stable
    # crop candidate again, and remove brief crop islands to avoid mode flicker.
    stable_keys = []
    for _, iterator in groupby(keys, lambda k: k.scene):
        scene_keys = list(iterator)
        last_fit = -100.
        stabilized = []
        for key in scene_keys:
            if key.mode == "fit":
                last_fit = key.time
            mode = key.mode if key.time - last_fit >= .4 - 1e-6 else "fit"
            stabilized.append(Keyframe(key.time, key.scene, mode, key.x, key.y))
        for mode, iterator2 in groupby(stabilized, lambda k: k.mode):
            run = list(iterator2)
            if mode == "crop" and len(run) > 0 and run[-1].time - run[0].time < .4 - 1e-6 and not same_ratio:
                run = [Keyframe(k.time, k.scene, "fit", k.x, k.y) for k in run]
            stable_keys.extend(run)
    return ReframePlan(w, h, cw, ch, ow, oh, stable_keys)
