"""Local CPU endurance check with real inference, scene changes and audio markers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import av
import numpy as np

from reframe.contracts import ExportRequest
from reframe.media import binary, command, probe
from reframe.pipeline import run
from benchmark import peak_memory_mb


def video_timing(path):
    with av.open(str(path)) as container:
        previous = None
        times = []
        for frame in container.decode(video=0):
            if frame.pts is None:
                raise RuntimeError('Video frame has no timestamp')
            t = float(frame.pts * frame.time_base)
            if previous is not None and t <= previous:
                raise RuntimeError('Video timestamps are not strictly increasing')
            times.append(t)
            previous = t
    return np.asarray(times)


def audio_markers(path):
    """Locate audible samples in the two marker bursts, without retaining audio."""
    first, last = None, None
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            # The fixture and its AAC export are mono floating-point audio.
            samples = frame.to_ndarray().reshape(-1)
            if samples.dtype.kind != 'f':
                samples = samples.astype(float) / max(abs(np.iinfo(samples.dtype).min), np.iinfo(samples.dtype).max)
            active = np.flatnonzero(np.abs(samples) > .03)
            if active.size:
                start = float(frame.pts * frame.time_base)
                if first is None:
                    first = start + int(active[0]) / frame.sample_rate
                last = start + int(active[-1]) / frame.sample_rate
    if first is None:
        raise RuntimeError('Audio markers missing')
    return first, last


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Short landscape scene corpus to repeat')
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--width', type=int, default=640, help='Fixture width, e.g. 1920 for a 16:9 1080p source')
    parser.add_argument('--root', type=Path, default=Path('work/soak'))
    args = parser.parse_args()
    if args.seconds < 5:
        parser.error('--seconds must be at least 5')
    if args.width < 32 or args.width % 2:
        parser.error('--width must be an even integer of at least 32')
    if sys.platform != 'win32':
        parser.error('This measurement uses Windows process memory APIs')
    info = probe(args.source)
    if info.width <= info.height:
        parser.error('Use a landscape source so 9:16 requires real analysis')
    folder = args.root / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=False)
    source, output = folder / 'source.mp4', folder / 'result.mp4'
    print(f'Artifacts: {folder.resolve()}', flush=True)
    audio = f'aevalsrc=if(between(t,1,1.2)+between(t,{args.seconds-2},{args.seconds-1.8}),0.2*sin(2*PI*880*t),0):s=48000:d={args.seconds}'
    # Commas inside an expression must be escaped for the lavfi filter parser.
    audio = audio.replace(',', r'\,')
    command([binary('ffmpeg'), '-v', 'error', '-n', '-stream_loop', '-1', '-i', str(args.source),
             '-f', 'lavfi', '-i', audio, '-t', str(args.seconds), '-map', '0:v:0', '-map', '1:a:0',
             '-vf', f'scale={args.width}:-2', '-c:v', 'libx264', '-preset', 'veryfast', '-threads', '2',
             '-c:a', 'aac', str(source)])
    started = time.perf_counter()
    stages = {}
    last_notice = 0.
    def emit(event):
        nonlocal last_notice
        now = time.perf_counter() - started
        stages.setdefault(event.stage, round(now, 3))
        if now - last_notice >= 15 or event.kind == 'done':
            print(f'{event.stage}: {event.progress:.0%}, {now:.1f}s', flush=True)
            last_notice = now
    run(ExportRequest(source, output, cpu_only=True, diagnostics=folder / 'plan.json'), threading.Event(), emit)
    elapsed = time.perf_counter() - started
    peak = peak_memory_mb()
    original, result = video_timing(source), video_timing(output)
    if not len(original) or len(original) != len(result):
        raise RuntimeError('Video frame count changed')
    drift = float(np.max(np.abs((original - original[0]) - (result - result[0]))))
    input_audio, output_audio = audio_markers(source), audio_markers(output)
    audio_drift = max(abs(a - b) for a, b in zip(input_audio, output_audio))
    diagnostics = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
    analysis = diagnostics['analysis']
    checks = dict(video_timestamps=drift <= 1 / probe(source).fps,
                  audio_markers=audio_drift <= .1,
                  real_analysis=analysis['backend'] == 'CPU' and len(analysis['samples']) > 1,
                  temporary_files_removed=not list(folder.glob('.reframe-*')))
    fixture = probe(source)
    report = dict(seconds=args.seconds, width=fixture.width, height=fixture.height,
                  elapsed_seconds=elapsed, peak_process_memory_mb=peak,
                  frames=len(result), max_timestamp_difference=drift, input_audio_markers=input_audio,
                  output_audio_markers=output_audio, max_audio_marker_difference=audio_drift,
                  samples=len(analysis['samples']), scenes=len({s['scene'] for s in analysis['samples']}),
                  stages=stages, checks=checks)
    (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    if not all(checks.values()):
        raise SystemExit('Endurance verification failed; see report.json')


if __name__ == '__main__':
    main()
