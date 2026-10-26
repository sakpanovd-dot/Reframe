import argparse
import multiprocessing
from pathlib import Path

from .contracts import ExportRequest
from .pipeline import run


def main():
    parser = argparse.ArgumentParser(description="Reframe: автоматическое кадрирование")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--preset", default="9:16")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--fit-only", action="store_true", help="Диагностика экспорта без модели")
    parser.add_argument("--diagnostics", type=Path)
    args = parser.parse_args()
    request = ExportRequest(args.source, args.destination, args.preset,
                            args.destination.suffix.lower().lstrip("."), args.overwrite,
                            args.cpu, args.fit_only, args.diagnostics)
    run(request, multiprocessing.Event(), lambda e: print(f"{e.stage}: {e.progress:.0%} {e.detail}", flush=True))


if __name__ == "__main__":
    main()
