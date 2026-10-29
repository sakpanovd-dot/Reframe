"""How well a plan keeps the main face in frame, from CLI diagnostics JSON.

For every analysis sample shown as a crop, take the largest detected
face and measure which share of its box lies inside the crop. This is a proxy
for "the person is in frame", not a semantic judgement of who matters.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reframe.contracts import Keyframe, ReframePlan
from reframe.render import Renderer


def visible(face, x, y, width, height):
    x1, y1, x2, y2 = face
    area = max(1e-6, (x2 - x1) * (y2 - y1))
    overlap = max(0, min(x2, x + width) - max(x1, x)) * max(0, min(y2, y + height) - max(y1, y))
    return overlap / area


def report(diagnostics):
    data = json.loads(Path(diagnostics).read_text(encoding='utf-8'))
    p = data['plan']
    plan = ReframePlan(**{**p, 'keyframes': [Keyframe(**k) for k in p['keyframes']]})
    renderer = Renderer(plan)
    rows = []
    for sample in data['analysis']['samples']:
        faces = sample.get('faces') or []
        mode, x, y, width = renderer.position(sample['time'])
        if not faces or mode != 'crop':  # a panel shows one of several videos by design
            continue
        face = max(faces, key=lambda f: (f['box'][2] - f['box'][0]) * (f['box'][3] - f['box'][1]))['box']
        rows.append((sample['time'], visible(face, x, y, width, width * plan.crop_height / plan.crop_width)))
    total = len(rows) or 1
    summary = dict(samples_with_face=len(rows),
                   full=round(sum(v >= .9 for _, v in rows) / total, 3),
                   cut=round(sum(.1 <= v < .9 for _, v in rows) / total, 3),
                   lost=round(sum(v < .1 for _, v in rows) / total, 3),
                   worst=[(round(t, 2), round(v, 2)) for t, v in sorted(rows, key=lambda r: r[1])[:8]])
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('diagnostics', nargs='+', type=Path)
    for path in parser.parse_args().diagnostics:
        print(path, json.dumps(report(path), ensure_ascii=False))
