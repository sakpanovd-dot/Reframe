"""CPU benchmark: 1080p with real inference, long video with fit-only rendering."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reframe.contracts import ExportRequest
from reframe.media import binary, command
from reframe.pipeline import run


def peak_memory_mb():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return round(counters.PeakWorkingSetSize / 1024 ** 2, 1)


def main():
    root = Path("work/benchmarks")
    root.mkdir(parents=True, exist_ok=True)
    reports = []
    for name, duration, fit_only in [("1080p", 10, False), ("long", 601, True)]:
        source = root / f"{name}.mp4"
        if name == "1080p":
            args = ["-i", "work/samples/corpus.mp4", "-t", str(duration), "-vf", "scale=1920:1080"]
        else:
            args = ["-f", "lavfi", "-i", f"color=c=gray:s=320x180:r=25:d={duration}"]
        command([binary("ffmpeg"), "-v", "error", "-y", *args, "-an", "-c:v", "libx264", "-preset", "ultrafast", str(source)])
        started = time.perf_counter()
        stages = {}
        def emit(event):
            if event.stage not in stages:
                stages[event.stage] = round(time.perf_counter() - started, 2)
        run(ExportRequest(source, root / f"{name}-out.mp4", overwrite=True, cpu_only=True, fit_only=fit_only), threading.Event(), emit)
        report = {"name": name, "source_seconds": duration, "elapsed_seconds": round(time.perf_counter() - started, 2),
                  "peak_process_memory_mb": peak_memory_mb(), "fit_only": fit_only, "stages": stages}
        reports.append(report)
        print(json.dumps(report), flush=True)
        (root / "results.json").write_text(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
