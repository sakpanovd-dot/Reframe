"""Compare exact-frame reuse with uncached CPU analysis; verify identical plans."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from reframe.analysis import analyze
from reframe.detector import YoloXDetector
from reframe.media import probe
from reframe.planner import make_plan
from reframe.presets import load_presets


class MeasuredDetector(YoloXDetector):
    def __init__(self, cached):
        super().__init__(cpu_only=True)
        self.cached = cached
        self.inferences = 0

    def detect(self, image):
        return super().detect(image) if self.cached else self._detect(image)

    def _detect(self, image):
        self.inferences += 1
        return super()._detect(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    if args.output and args.output.resolve() == args.source.resolve():
        parser.error('output must not overwrite the input video')
    cv2.setNumThreads(2)
    info = probe(args.source)
    presets = load_presets()
    reference = None
    rows = []
    for repeat in range(args.repeats):
        # Alternate order to reduce bias from warm filesystem/CPU caches.
        for cached in ([False, True] if repeat % 2 == 0 else [True, False]):
            detector = MeasuredDetector(cached)
            start = time.perf_counter()
            analysis = analyze(info, detector, None, lambda event: None)
            seconds = time.perf_counter() - start
            payload = {'analysis': asdict(analysis), 'plans': {
                name: asdict(make_plan(analysis, preset)) for name, preset in presets.items()
            }}
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            if reference is not None and digest != reference:
                raise RuntimeError('Analysis or crop plans changed between runs')
            reference = digest
            row = dict(repeat=repeat + 1, cached=cached, seconds=seconds,
                       inferences=detector.inferences, samples=len(analysis.samples),
                       result_sha256=digest)
            rows.append(row)
            print(json.dumps(row), flush=True)
            del detector
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(dict(platform=platform.platform(),
            processor=platform.processor(), duration=info.duration, runs=rows), indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
