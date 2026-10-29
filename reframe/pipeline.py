from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from .analysis import analyze
from .contracts import AnalysisResult, Cancelled, ExportRequest, ProgressEvent, ReframeError, Sample
from .detector import YoloXDetector
from .export import EXPORTERS, choose_encoder, encode_video, mux_audio, verify
from .media import check_cancel, probe
from .planner import make_plan, same_aspect
from .presets import load_presets
from .local_state import configure_logging
from .editorial import applies, make_editorial_plan
from .review import intervals, html_report


def validate_destination(request):
    source, destination = request.source.resolve(), request.destination.resolve()
    if source == destination or (destination.exists() and os.path.samefile(source, destination)):
        raise ReframeError("Исходное видео нельзя перезаписать. Выберите другое имя результата.")
    if request.diagnostics:
        diagnostic = request.diagnostics.resolve()
        if diagnostic in {source, destination} or (diagnostic.exists() and any(
                path.exists() and os.path.samefile(diagnostic, path) for path in (source, destination))):
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


def cleanup_work(work):
    """A transient Windows file lock must not turn a saved video into a failure.

    Only remove the directory owned by this job. Leave persistent failures for
    manual cleanup and keep the original processing exception, if there is one.
    """
    for attempt in range(3):
        try:
            shutil.rmtree(work)
            return
        except FileNotFoundError:
            if not work.exists():
                return
        except OSError:
            if attempt == 2:
                logging.warning("Cannot remove temporary job directory: %s", work, exc_info=True)
                return
        if attempt < 2:
            time.sleep(.1)
    logging.warning("Temporary job directory remains after cleanup: %s", work)


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
        if same_aspect(info.width, info.height, presets[request.preset]):
            report(ProgressEvent("Подготовка кадра", 1, "Пропорции совпадают — сохраняем весь кадр"))
            analysis = AnalysisResult(info.width, info.height, info.duration,
                                      [Sample(0., 0, [])], backend="skipped_same_ratio")
        else:
            detector = None if request.fit_only else YoloXDetector(request.cpu_only)
            analysis = analyze(info, detector, cancel, report)
        report(ProgressEvent("Планирование кадра", 1))
        editorial = not request.fit_only and applies(info.width, info.height, presets[request.preset])
        plan = (make_editorial_plan if editorial else make_plan)(analysis, presets[request.preset])
        review = intervals(plan, info.duration) if editorial else []
        if request.diagnostics:
            validate_destination(replace(request, destination=destination))
            request.diagnostics.parent.mkdir(parents=True, exist_ok=True)
            request.diagnostics.write_text(json.dumps({"analysis": asdict(analysis), "plan": asdict(plan),
                                                      "strategy": "editorial" if editorial else "conservative", "review": review},
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
        # The source or destination may have been replaced while the job ran.
        # Recheck aliases immediately before publishing the verified result.
        validate_destination(replace(request, destination=destination))
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
        if editorial:
            review_path = destination.with_name(destination.stem + f'.review-{uuid.uuid4().hex}.html')
            created = False
            try:
                with review_path.open('x', encoding='utf-8') as output:
                    created = True
                    output.write(html_report(destination, review))
                detail = dict(path=str(review_path), count=len(review), error='')
            except OSError:
                logging.exception('Video saved, but editorial report could not be written')
                if created:
                    try:
                        review_path.unlink(missing_ok=True)
                    except OSError:
                        logging.warning('Partial review report remains: %s', review_path)
                detail = dict(path='', count=len(review), error='Видео сохранено, но отчёт записать не удалось.')
            report(ProgressEvent('Проверка монтажёром', 1, json.dumps(detail, ensure_ascii=False), kind='review'))
        report(ProgressEvent("Готово", 1, str(destination), kind="done"))
        return destination
    finally:
        cleanup_work(work)


def worker(request, cancel, queue):
    configure_logging(Path(__file__).resolve().parent.parent)
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
            message = "Не удалось обработать видео. Подробности — в work/reframe.log или консоли запуска."
        queue.put(ProgressEvent("Ошибка", 0, message, kind="error"))
