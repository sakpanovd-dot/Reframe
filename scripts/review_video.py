"""Create local review sheets from source, encoded output and CLI diagnostics.

This reports plan coverage, not subject-retention accuracy (which needs annotation).
"""
import argparse
from bisect import bisect_right
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2
import numpy as np
from reframe.contracts import Keyframe, ReframePlan
from reframe.media import frames, probe
from reframe.render import Renderer


def thumbnails(path, targets, size):
    result = []
    index = 0
    for t, _, image in frames(probe(path)):
        if index >= len(targets):
            break
        if t + 1e-6 < targets[index]:
            continue
        scale = min(size[0] / image.shape[1], size[1] / image.shape[0])
        thumb = cv2.resize(image, (round(image.shape[1] * scale), round(image.shape[0] * scale)))
        while index < len(targets) and targets[index] <= t + 1e-6:
            result.append((t, thumb.copy(), scale))
            index += 1
    if len(result) != len(targets):
        raise ValueError(f'Not all review times could be decoded: {path}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('result', type=Path)
    parser.add_argument('diagnostics', type=Path)
    parser.add_argument('--output', type=Path, default=Path('work/video-review'))
    args = parser.parse_args()
    data = json.loads(args.diagnostics.read_text(encoding='utf-8'))
    analysis, p = data['analysis'], data['plan']
    plan = ReframePlan(**{**p, 'keyframes': [Keyframe(**k) for k in p['keyframes']]})
    renderer = Renderer(plan)
    totals = Counter()
    reasons = Counter()
    runs = []
    for i, key in enumerate(plan.keyframes):
        end = plan.keyframes[i + 1].time if i + 1 < len(plan.keyframes) else analysis['duration']
        totals[key.mode] += end - key.time
        reasons[key.reason or 'unspecified'] += end - key.time
        if runs and (runs[-1]['scene'], runs[-1]['mode']) == (key.scene, key.mode):
            runs[-1]['end'] = end
        else:
            runs.append(dict(start=key.time, end=end, scene=key.scene, mode=key.mode))
    crops = [r for r in runs if r['mode'] in ('crop', 'panel')]
    targets = list(np.linspace(0, max(0, analysis['duration'] - .5), 16))
    for run in crops:
        targets.extend([run['start'], (run['start'] + run['end']) / 2, max(run['start'], run['end'] - .1)])
    targets = sorted(set(round(float(t), 6) for t in targets))
    sources = thumbnails(args.source, targets, (480, 270))
    outputs = thumbnails(args.result, targets, (152, 270))
    sample_times = [s['time'] for s in analysis['samples']]
    tiles = []
    for source, output in zip(sources, outputs):
        t, image, scale = source
        mode, x, y, width = renderer.position(t)
        sample = analysis['samples'][max(0, bisect_right(sample_times, t) - 1)]
        for obj in sample['objects']:
            x1, y1, x2, y2 = [round(v * scale) for v in obj['box']]
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 160, 255), 1)
        if mode in ('crop', 'panel'):
            cv2.rectangle(image, (round(x * scale), round(y * scale)),
                          (round((x + width) * scale), round((y + width * plan.crop_height / plan.crop_width) * scale)),
                          (90, 255, 120), 2)
        tile = np.full((310, 660, 3), 24, np.uint8)
        tile[30:30 + image.shape[0], :image.shape[1]] = image
        out = output[1]
        tile[30:30 + out.shape[0], 500:500 + out.shape[1]] = out
        cv2.putText(tile, f'{t:.2f}s | scene {sample["scene"]} | {mode} | source / encoded',
                    (8, 20), cv2.FONT_HERSHEY_SIMPLEX, .5, (230, 230, 230), 1)
        tiles.append(tile)
    args.output.mkdir(parents=True, exist_ok=True)
    for offset in range(0, len(tiles), 8):
        page = tiles[offset:offset + 8]
        page += [np.zeros_like(tiles[0])] * (8 - len(page))
        sheet = np.vstack([np.hstack(page[i:i + 2]) for i in range(0, 8, 2)])
        path = args.output / f'sheet-{offset // 8 + 1}.jpg'
        if not cv2.imwrite(str(path), sheet):
            raise OSError(f'Cannot write {path}')
    summary = dict(duration=analysis['duration'], scene_count=len({s['scene'] for s in analysis['samples']}),
                   mode_seconds=dict(totals), reason_seconds=dict(reasons), crop_runs=crops, review_times=targets,
                   note='Coverage is not accuracy. Orange boxes are prior sampled detections, not frame annotations.')
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
