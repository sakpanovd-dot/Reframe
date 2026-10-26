from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .analysis import analyze
from .contracts import Cancelled, ExportRequest, ProgressEvent, ReframeError
from .detector import YoloXDetector
from .export import EXPORTERS, choose_encoder, encode_video, mux_audio, verify
from .media import check_cancel, probe
from .planner import make_plan
from .presets import load_presets


def validate_destination(request):
    source, destination = request.source.resolve(), request.destination.resolve()
    if source == destination or (destination.exists() and os.path.samefile(source, destination)):
        raise ReframeError("Исходное видео нельзя перезаписать. Выберите другое имя результата.")
    if request.diagnostics:
        diagnostic = request.diagnostics.resolve()
        if diagnostic in {source, destination} or (diagnostic.exists() and source.exists() and os.path.samefile(diagnostic, source)):
            raise ReframeError("Диагностический файл не должен совпадать с исходником или результатом.")
    if not destination.parent.is_dir():
        raise ReframeError("Папка сохранения не найдена.")
    if destination.exists() and not request.overwrite:
        raise ReframeError("Файл уже существует. Подтвердите замену или выберите другое имя.")
    if request.container not in EXPORTERS:
        raise ReframeError("Неизвестный формат экспорта.")
    if destination.suffix.lower() != EXPORTERS[request.container].extension:
        raise ReframeError("Расширение файла не соответствует выбранному формату.")
    return destination


def run(request: ExportRequest, cancel, emit=lambda event: None):
    import cv2
    cv2.setNumThreads(2)
    started = time.monotonic()
    destination = validate_destination(request)
    presets = load_presets()
    if request.preset not in presets:
        raise ReframeError("Неизвестные пропорции кадра.")
    work = destination.parent / f".reframe-{uuid.uuid4().hex}"
    work.mkdir()
    try:
        def report(event):
            emit(ProgressEvent(event.stage, event.progress, event.detail,
                               time.monotonic() - started, event.kind))
        report(ProgressEvent("Проверка видео", 0))
        info = probe(request.source, cancel)
        check_cancel(cancel)
        detector = None if request.fit_only else YoloXDetector(request.cpu_only)
        analysis = analyze(info, detector, cancel, report)
        report(ProgressEvent("Планирование кадра", 1))
        plan = make_plan(analysis, presets[request.preset])
        if request.diagnostics:
            request.diagnostics.parent.mkdir(parents=True, exist_ok=True)
            request.diagnostics.write_text(json.dumps({"analysis": asdict(analysis), "plan": asdict(plan)},
                                                      ensure_ascii=False), encoding="utf-8")
        codec = choose_encoder(work, request.cpu_only)
        video_path = work / "video.mp4"
        try:
            encode_video(info, plan, video_path, codec, cancel, report)
        except Exception as error:
            if codec != "h264_nvenc" or isinstance(error, Cancelled):
                raise
            logging.exception("NVENC export failed, retrying from start on CPU")
            report(ProgressEvent("Повтор на CPU", 0, "Видеокарта недоступна"))
            video_path.unlink(missing_ok=True)
            encode_video(info, plan, video_path, "libx264", cancel, report)
        report(ProgressEvent("Сохранение звука", 0))
        result = work / ("result" + EXPORTERS[request.container].extension)
        mux_audio(info, video_path, result, EXPORTERS[request.container], cancel)
        report(ProgressEvent("Проверка результата", 0))
        verify(info, plan, result, cancel)
        check_cancel(cancel)
        if not request.overwrite and destination.exists():
            raise ReframeError("Во время обработки появился файл с таким именем. Выберите другое имя.")
        if request.overwrite:
            os.replace(result, destination)
        else:
            # Windows rename does not replace an existing target, preventing races.
            if os.name == "nt":
                os.rename(result, destination)
            else:
                os.link(result, destination)
                result.unlink()
        report(ProgressEvent("Готово", 1, str(destination), kind="done"))
        return destination
    finally:
        shutil.rmtree(work)


def worker(request, cancel, queue):
    logdir = Path(__file__).resolve().parent.parent / "work"
    logdir.mkdir(exist_ok=True)
    logging.basicConfig(filename=logdir / "reframe.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8", force=True)
    last_emit = 0.

    def emit(event):
        nonlocal last_emit
        now = time.monotonic()
        if event.kind != "progress" or now - last_emit > .1 or event.progress in (0, 1):
            queue.put(event)
            last_emit = now
    try:
        run(request, cancel, emit)
    except Cancelled:
        queue.put(ProgressEvent("Отменено", 0, kind="cancelled"))
    except Exception as error:
        logging.exception("Processing failed")
        if isinstance(error, ReframeError):
            message = str(error)
        elif isinstance(error, PermissionError):
            message = "Нет доступа к файлу или папке сохранения. Выберите другую папку."
        elif isinstance(error, OSError) and error.errno == 28:
            message = "Недостаточно свободного места. Освободите место и повторите попытку."
        else:
            message = "Не удалось обработать видео. Подробности сохранены в work/reframe.log."
        queue.put(ProgressEvent("Ошибка", 0, message, kind="error"))
