from __future__ import annotations

from collections import Counter
from itertools import groupby
from math import gcd

import numpy as np

from .contracts import Keyframe, ReframePlan


def same_aspect(width, height, preset):
    """Exact display aspect match; includes probe's rotation and SAR correction."""
    return width * preset.height == height * preset.width


def excludes_activity(boxes, x, y, width, height, source_area):
    for x1, y1, x2, y2 in boxes:
        area = max(0, x2 - x1) * max(0, y2 - y1)
        overlap = max(0, min(x2, x + width) - max(x1, x)) * max(0, min(y2, y + height) - max(y1, y))
        if area >= source_area * .015 and overlap < area * .4:
            return True
    return False


def unexplained_activity(nearby, selected, width, height):
    subject = [obj.box for sample in nearby for obj in sample.objects if obj.track_id == selected]
    if not subject:
        return [box for sample in nearby for box in sample.motion_boxes]
    # The motion mask includes both the old and the new location of a moving
    # subject. Explain that swept area before looking for independent activity.
    x1 = min(b[0] for b in subject) - width * .03
    y1 = min(b[1] for b in subject) - height * .03
    x2 = max(b[2] for b in subject) + width * .03
    y2 = max(b[3] for b in subject) + height * .03
    result = []
    for sample in nearby:
        for a, b, c, d in sample.motion_boxes:
            area = max(0, c - a) * max(0, d - b)
            overlap = max(0, min(c, x2) - max(a, x1)) * max(0, min(d, y2) - max(b, y1))
            if overlap < area * .4:
                result.append((a, b, c, d))
    return result


def dimensions(width, height, preset, upscale=False):
    """Crop and output sizes with the exact aspect ratio and even dimensions.

    By default the crop is never enlarged. With upscale the output is always the
    preset size (e.g. 1080x1920), so a 1080p source gives a full-size vertical.
    """
    divisor = gcd(preset.width, preset.height)
    a, b = preset.width // divisor, preset.height // divisor
    step = 2 if a % 2 or b % 2 else 1
    k = int(min(width / a, height / b)) // step * step
    if k < step:
        from .contracts import ReframeError
        raise ReframeError("Исходное видео слишком маленькое для выбранных пропорций.")
    cw, ch = a * k, b * k
    out_k = (divisor if upscale else min(k, divisor)) // step * step
    return cw, ch, a * out_k, b * out_k


def make_plan(analysis, preset, upscale=False) -> ReframePlan:
    w, h = analysis.width, analysis.height
    cw, ch, ow, oh = dimensions(w, h, preset, upscale)
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
        for sample_index, s in enumerate(samples):
            if same_ratio:
                scene_keys.append(Keyframe(s.time, scene, "crop", (w - cw) / 2, (h - ch) / 2, "same_ratio"))
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
            reason = "ambiguous" if ambiguous else "no_subject" if box is None else "subject_too_large"
            if safe:
                if pending_id != selected:
                    pending_id, stable_since = selected, s.time
                    crop_active = False
                if s.time - stable_since >= .4 - 1e-6:
                    crop_active = True
                reason = "tracking" if crop_active else "stabilizing"
                target = (box[:2] + box[2:]) / 2
                delta = target - center
                deadzone = np.array([cw, ch]) * .12
                center += np.sign(delta) * np.maximum(0, np.abs(delta) - deadzone)
                # Guarantee the entire selected box + 6% margin fits before smoothing.
                low = box[2:] + np.array([cw, ch]) * .06 - np.array([cw, ch]) / 2
                high = box[:2] - np.array([cw, ch]) * .06 + np.array([cw, ch]) / 2
                center = np.clip(center, low, high)
                center = np.clip(center, [cw / 2, ch / 2], [w - cw / 2, h - ch / 2])
                # Independent image motion can contradict a confident but false
                # semantic detection. Look on both sides within this scene so
                # held animation frames do not repeatedly re-enable a bad crop.
                nearby = [other for other in samples[max(0, sample_index - 2):sample_index + 3]
                          if abs(other.time - s.time) <= .5]
                nearby_motion = unexplained_activity(nearby, selected, w, h)
                if excludes_activity(nearby_motion, center[0] - cw / 2, center[1] - ch / 2, cw, ch, w * h):
                    crop_active, pending_id = False, None
                    reason = "motion_outside_crop"
            else:
                crop_active, pending_id = False, None
            scene_keys.append(Keyframe(s.time, scene, "crop" if crop_active else "fit",
                                       float(center[0] - cw / 2), float(center[1] - ch / 2), reason))
            if crop_active and box is not None:
                safety_boxes[s.time] = np.array(box)
        # Smooth only within uninterrupted crop runs; never across scene cuts or fit sections.
        for _, run in groupby(scene_keys, lambda k: k.mode):
            run = list(run)
            for i, key in enumerate(run):
                neighbors = run[max(0, i - 1):i + 2]
                position = np.array([np.mean([k.x for k in neighbors]), np.mean([k.y for k in neighbors])])
                mode = key.mode
                reason = key.reason
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
                        reason = "fast_motion"
                    else:
                        position = np.clip(position, low, high)
                keys.append(Keyframe(key.time, key.scene, mode, float(position[0]), float(position[1]), reason))
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
            stabilized.append(Keyframe(key.time, key.scene, mode, key.x, key.y,
                                       key.reason if mode == key.mode else "cooldown"))
        for mode, iterator2 in groupby(stabilized, lambda k: k.mode):
            run = list(iterator2)
            if mode == "crop" and len(run) > 0 and run[-1].time - run[0].time < .4 - 1e-6 and not same_ratio:
                run = [Keyframe(k.time, k.scene, "fit", k.x, k.y, "short_crop") for k in run]
            stable_keys.extend(run)
    return ReframePlan(w, h, cw, ch, ow, oh, stable_keys)
