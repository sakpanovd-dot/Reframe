from __future__ import annotations

import json

import logging
import multiprocessing as mp
import os
from pathlib import Path
from queue import Empty
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot, QTranslator, QLibraryInfo, QLocale
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QFileDialog, QSystemTrayIcon

from .contracts import ExportRequest, ReframeError
from .detector import face_model_path, model_path
from .export import EXPORTERS
from .media import CREATE_FLAGS, probe
from .pipeline import worker, validate_destination
from .presets import load_presets
from .planner import same_aspect
from .local_state import read_settings, save_settings, configure_logging

ROOT = Path(__file__).resolve().parent.parent
VIDEO_FILTER = ("Видео (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mpg *.mpeg *.ts *.mts *.m2ts *.wmv *.flv *.ogv)"
                ";;Все файлы (*)")
STATUS = {"waiting": "В очереди", "processing": "Обработка", "done": "Готово",
          "error": "Ошибка", "cancelled": "Отменено"}


def output_path(source: Path, preset: str, container: str, folder: str = "", reserved=()) -> Path:
    """Next free 'name_9x16.mp4' beside the source (or in the chosen folder); never an existing file."""
    directory = Path(folder) if folder else source.parent
    stem, ext = f"{source.stem}_{preset.replace(':', 'x')}", EXPORTERS[container].extension
    candidate, number = directory / f"{stem}{ext}", 2
    taken = {str(path).lower() for path in reserved}
    while candidate.exists() or str(candidate).lower() in taken:
        candidate, number = directory / f"{stem} ({number}){ext}", number + 1
    return candidate


class Controller(QObject):
    changed = Signal()
    inspected = Signal(object, str, str)
    closeReady = Signal()
    previewSourceChanged = Signal()
    processingChanged = Signal()
    queueChanged = Signal()

    def __init__(self):
        super().__init__()
        self._state = {"source": "", "sourceUrl": "", "previewUrl": "", "name": "",
                       "metadata": "", "warning": "", "busy": False, "loading": False,
                       "stage": "Готов к работе", "detail": "", "progress": 0., "elapsed": "",
                       "error": "", "result": "", "preset": "9:16", "container": "mp4",
                       "cancelling": False, "reviewPath": "", "reviewCount": 0, "reviewError": "",
                       "outputDir": "", "queueText": "", "selected": -1, "upscale": False}
        self.presets = load_presets()
        self.items = []  # dicts: id, path, name, info, status, detail, result, review fields
        self.next_id = 0
        self.current = None
        self.pending_probes = 0
        self.process = None
        self.context = mp.get_context("spawn")
        self.events = None
        self.cancel_event = None
        self.terminal_received = False
        self.started = 0.
        self.close_after_cancel = False
        self.stop_requested = False
        self.probe_cancel = threading.Event()
        self.tray = None
        saved = read_settings(ROOT / "settings.json")
        if isinstance(saved.get("preset"), str) and saved["preset"] in self.presets:
            self._state["preset"] = saved["preset"]
        if isinstance(saved.get("container"), str) and saved["container"] in EXPORTERS:
            self._state["container"] = saved["container"]
        if isinstance(saved.get("upscale"), bool):
            self._state["upscale"] = saved["upscale"]
        if isinstance(saved.get("outputDir"), str) and saved["outputDir"] and Path(saved["outputDir"]).is_dir():
            self._state["outputDir"] = saved["outputDir"]
        self.inspected.connect(self._inspection_finished)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    @Property("QVariantMap", notify=changed)
    def state(self):
        return self._state

    @Property(QUrl, notify=previewSourceChanged)
    def previewSource(self):
        return QUrl(self._state["previewUrl"])

    @Property(bool, notify=processingChanged)
    def processing(self):
        return self._state["busy"]

    @Property("QVariantList", notify=queueChanged)
    def queue(self):
        return [{"id": item["id"], "name": item["name"], "status": item["status"],
                 "statusText": STATUS[item["status"]], "detail": item["detail"]} for item in self.items]

    @Property("QVariantList", constant=True)
    def formats(self):
        return [{"id": p.id, "label": p.title, "description": p.description,
                 "ratio": p.ratio, "size": f"{p.width}×{p.height}"} for p in self.presets.values()]

    @Property("QStringList", constant=True)
    def containers(self):
        return [key.upper() for key in EXPORTERS]

    @property
    def info(self):
        item = self._selected_item()
        return item["info"] if item else None

    def update(self, **kwargs):
        changes = {key: value for key, value in kwargs.items() if self._state.get(key) != value}
        if not changes:
            return
        self._state.update(kwargs)
        self.changed.emit()
        if "previewUrl" in changes:
            self.previewSourceChanged.emit()
        if "busy" in changes:
            self.processingChanged.emit()

    def _selected_item(self):
        return next((item for item in self.items if item["id"] == self._state["selected"]), None)

    def _item(self, item_id):
        return next((item for item in self.items if item["id"] == item_id), None)

    def _show_item(self, item):
        """Mirror one queue entry into the flat state used by the preview and action buttons."""
        if item is None:
            self.update(selected=-1, source="", sourceUrl="", previewUrl="", name="", metadata="", warning="",
                        result="", reviewPath="", reviewCount=0, reviewError="")
            return
        info = item["info"]
        minutes, seconds = divmod(round(info.duration), 60)
        url = QUrl.fromLocalFile(str(info.path)).toString()
        preview = url
        if item["result"]:
            preview = QUrl.fromLocalFile(item["result"]).toString()
        # Never switch the paused player's source while a job is running.
        if self._state["busy"] and self._state["selected"] == item["id"]:
            preview = self._state["previewUrl"]
        self.update(selected=item["id"], source=str(info.path), sourceUrl=url, previewUrl=preview,
                    name=info.path.name,
                    metadata=f"{info.width} × {info.height}  ·  {minutes}:{seconds:02d}  ·  {info.fps:.2f} кадр/с",
                    warning="\n".join(info.warnings), result=item["result"], reviewPath=item["reviewPath"],
                    reviewCount=item["reviewCount"], reviewError=item["reviewError"])

    @Slot()
    def chooseSource(self):
        paths, _ = QFileDialog.getOpenFileNames(None, "Выберите видео", "", VIDEO_FILTER)
        if paths:
            self.addSources([QUrl.fromLocalFile(path).toString() for path in paths])

    @Slot(str)
    def loadSource(self, url):
        self.addSources([url])

    @Slot("QVariantList")
    def addSources(self, urls):
        if self._state["cancelling"]:
            return
        paths = [QUrl(str(url)).toLocalFile() for url in urls]
        if not any(paths):
            self.update(error="Перетащите локальный видеофайл.")
            return
        paths = [path for path in paths if path]
        self.probe_cancel.clear()
        self.pending_probes += len(paths)
        self.update(loading=True, error="")
        if not self._state["busy"]:
            self.update(stage="Проверка видео", detail="")

        def inspect():
            for path in paths:
                try:
                    self.inspected.emit(probe(Path(path), self.probe_cancel), "", path)
                except Exception as error:
                    logging.exception("Cannot inspect source video: %s", path)
                    if isinstance(error, ReframeError):
                        message = str(error)
                    elif isinstance(error, PermissionError):
                        message = "Нет доступа к видео. Проверьте права на файл или скопируйте его в доступную папку."
                    else:
                        message = "Не удалось открыть видео. Проверьте файл и FFmpeg. Подробности — в work/reframe.log или консоли запуска."
                    self.inspected.emit(None, message, path)
        threading.Thread(target=inspect, daemon=True).start()

    @Slot(object, str, str)
    def _inspection_finished(self, info, error, path):
        self.pending_probes -= 1
        loading = self.pending_probes > 0
        if error:
            name = Path(path).name
            self.update(loading=loading, error=error if len(self.items) == 0 else f"{name}: {error}")
            if not self._state["busy"] and not loading:
                self.update(stage="Не удалось открыть видео")
            return
        # Videos dropped while a batch runs join it, so the editor can keep adding work.
        item = dict(id=self.next_id, path=info.path, name=info.path.name, info=info, status="waiting",
                    detail="", result="", reviewPath="", reviewCount=0, reviewError="", batch=self._state["busy"])
        self.next_id += 1
        self.items.append(item)
        self.queueChanged.emit()
        self.update(loading=loading)
        if not self._state["busy"]:
            self._show_item(item)
            if not loading:
                waiting = sum(i["status"] == "waiting" for i in self.items)
                self.update(stage="Готов к созданию", progress=0, detail="", elapsed="",
                            queueText=f"В очереди: {waiting}" if len(self.items) > 1 else "")
        else:
            self._update_queue_text()

    @Slot(int)
    def selectItem(self, item_id):
        item = self._item(item_id)
        if item is not None:
            self._show_item(item)

    @Slot(int)
    def removeItem(self, item_id):
        item = self._item(item_id)
        if item is None or item["status"] == "processing":
            return
        self.items.remove(item)
        self.queueChanged.emit()
        if self._state["selected"] == item_id:
            self._show_item(self.items[-1] if self.items else None)
            if not self.items and not self._state["busy"]:
                self.update(stage="Готов к работе", detail="", queueText="")
        self._update_queue_text()

    @Slot(str)
    def selectPreset(self, value):
        if not self._state["busy"] and value in self.presets:
            self.update(preset=value)

    @Slot(str)
    def selectContainer(self, value):
        if not self._state["busy"] and value.lower() in EXPORTERS:
            self.update(container=value.lower())

    @Slot(bool)
    def setUpscale(self, value):
        """Output size: as large as the source allows (default) or always the preset size."""
        if not self._state["busy"]:
            self.update(upscale=bool(value))
            self._save_settings()

    @Slot()
    def chooseOutputFolder(self):
        folder = QFileDialog.getExistingDirectory(None, "Папка для результатов", self._state["outputDir"])
        if folder:
            self.update(outputDir=folder)
            self._save_settings()

    @Slot()
    def resetOutputFolder(self):
        self.update(outputDir="")
        self._save_settings()

    def _save_settings(self):
        save_settings(ROOT / "settings.json", {"preset": self._state["preset"], "container": self._state["container"],
                                               "outputDir": self._state["outputDir"], "upscale": self._state["upscale"]})

    def _update_queue_text(self):
        total = sum(item["status"] in ("waiting", "processing") for item in self.items)
        if self._state["busy"] and self.current is not None:
            done = sum(item.get("batch") and item["status"] not in ("waiting", "processing") for item in self.items)
            batch = sum(bool(item.get("batch")) for item in self.items)
            self.update(queueText=f"Видео {done + 1} из {batch}" if batch > 1 else "")
        else:
            self.update(queueText=f"В очереди: {total}" if len(self.items) > 1 and total else "")

    @Slot()
    def start(self):
        if self._state["busy"]:
            return
        waiting = [item for item in self.items if item["status"] == "waiting"]
        if not waiting:
            # Repeat the selected video, e.g. after changing the format.
            item = self._selected_item()
            if item is None:
                return
            item.update(status="waiting", detail="")
            waiting = [item]
        preset = self.presets[self._state["preset"]]
        if not (model_path().exists() and face_model_path().exists()) and any(not same_aspect(i["info"].width, i["info"].height, preset)
                                             for i in waiting):
            self.update(error="Модель не установлена. Выполните python scripts/download_model.py из папки Reframe.")
            return
        for item in self.items:
            item["batch"] = item in waiting
        self._save_settings()
        self.close_after_cancel = self.stop_requested = False
        self.update(busy=True, error="", result="", reviewPath="", reviewCount=0, reviewError="")
        self._start_next()

    def _start_next(self):
        item = next((i for i in self.items if i["status"] == "waiting" and i.get("batch")), None)
        if item is None or self.stop_requested:
            self._finish_batch()
            return
        reserved = [i["destination"] for i in self.items if i.get("destination") and i["status"] == "processing"]
        try:
            destination = output_path(item["path"], self._state["preset"], self._state["container"],
                                      self._state["outputDir"], reserved)
            request = ExportRequest(item["path"], destination, self._state["preset"], self._state["container"],
                                    upscale=self._state["upscale"])
            validate_destination(request)
        except (ReframeError, OSError) as error:
            item.update(status="error", detail=str(error) if isinstance(error, ReframeError) else
                        "Нет доступа к папке сохранения. Выберите другую папку для результатов.")
            self.queueChanged.emit()
            self._start_next()
            return
        item.update(status="processing", detail="", destination=destination)
        self.current = item["id"]
        self.queueChanged.emit()
        self.events, self.cancel_event = self.context.Queue(), self.context.Event()
        self.process = self.context.Process(target=worker, args=(request, self.cancel_event, self.events))
        self.terminal_received = False
        self.started = time.monotonic()
        self.process.start()
        self._update_queue_text()
        self.update(cancelling=False, stage="Подготовка", detail=item["name"], progress=0, elapsed="0:00")

    def _finish_batch(self):
        self.current = None
        batch = [i for i in self.items if i.get("batch")]
        done = sum(i["status"] == "done" for i in batch)
        failed = sum(i["status"] == "error" for i in batch)
        cancelled = any(i["status"] == "cancelled" for i in batch)
        self.update(busy=False, cancelling=False)
        self._update_queue_text()
        if cancelled:
            self.update(stage="Отменено", progress=0,
                        detail="Исходное видео сохранено" if len(batch) == 1 else
                        f"Готово: {done}. Оставшиеся видео ждут в очереди.")
        elif len(batch) == 1 and failed:
            item = batch[0]
            self.update(stage="Не удалось создать видео", error=item["detail"], detail="")
        elif len(batch) == 1:
            item = batch[0]
            self.update(stage="Готово", progress=1, warning="",
                        detail=item["reviewError"] or (f"Видео сохранено. Отмечено интервалов для проверки: {item['reviewCount']}."
                                                       if item["reviewPath"] else "Видео сохранено"))
        else:
            self.update(stage="Готово" if not failed else "Готово с ошибками", progress=1, warning="",
                        detail=f"Создано видео: {done} из {len(batch)}." + (" Ошибки отмечены в списке." if failed else ""))
        selected = self._selected_item()
        if selected is not None:
            self._show_item(selected)
        if not cancelled:
            self._notify(done, failed, len(batch))

    def _notify(self, done, failed, total):
        """Processing runs in the background: tell the editor when the batch ends."""
        app = QApplication.instance()
        if app is None:
            return
        for window in app.topLevelWindows():
            app.alert(window)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self.tray is None:
            pixmap = QPixmap(32, 32)
            pixmap.fill(QColor("#C3F47C"))
            self.tray = QSystemTrayIcon(QIcon(pixmap), self)
            self.tray.setToolTip("Reframe")
        self.tray.show()
        text = f"Создано видео: {done} из {total}." if total > 1 else "Видео сохранено." if done else "Не удалось создать видео."
        if failed and total > 1:
            text += f" Ошибок: {failed}."
        self.tray.showMessage("Reframe — обработка завершена", text,
                              QSystemTrayIcon.MessageIcon.Warning if failed else QSystemTrayIcon.MessageIcon.Information,
                              8000)
        QTimer.singleShot(10000, self.tray.hide)

    @Slot()
    def cancel(self):
        if self.cancel_event is not None and self._state["busy"]:
            self.stop_requested = True
            self.cancel_event.set()
            self.update(cancelling=True, stage="Отмена…", detail="Завершаем обработку и удаляем временные файлы")

    @Slot()
    def poll(self):
        if self.process is None:
            return
        elapsed = round(time.monotonic() - self.started)
        self.update(elapsed=f"{elapsed // 60}:{elapsed % 60:02d}")
        self._drain_events()
        if not self.process.is_alive():
            self.process.join()
            self._drain_events()
            item = self._item(self.current)
            if not self.terminal_received and item is not None:
                item.update(status="error", detail="Процесс обработки неожиданно завершился. См. work/reframe.log или консоль запуска.")
            self.process.close()
            self.process = None
            self.events.close()
            self.events = None
            self.queueChanged.emit()
            if self.close_after_cancel:
                self.update(busy=False, cancelling=False)
                self.closeReady.emit()
                return
            self._start_next()

    def _drain_events(self):
        item = self._item(self.current)
        while True:
            try:
                event = self.events.get_nowait()
            except Empty:
                break
            if event.kind == "progress":
                if not self._state["cancelling"]:
                    self.update(stage=event.stage, progress=event.progress,
                                detail=item["name"] if item and self._state["queueText"] else event.detail)
            elif event.kind == "review":
                if item is not None:
                    review = json.loads(event.detail)
                    item.update(reviewPath=review["path"], reviewCount=review["count"], reviewError=review["error"])
            else:
                self.terminal_received = True
                if item is None:
                    continue
                if event.kind == "done":
                    item.update(status="done", result=event.detail, detail=Path(event.detail).name)
                    if self._state["selected"] == item["id"]:
                        self.update(result=item["result"], reviewPath=item["reviewPath"],
                                    reviewCount=item["reviewCount"], reviewError=item["reviewError"])
                elif event.kind == "error":
                    item.update(status="error", detail=event.detail)
                else:
                    item.update(status="cancelled", detail="Исходное видео сохранено")
                self.queueChanged.emit()

    @Slot()
    def openReview(self):
        if self._state['reviewPath']:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._state['reviewPath']))

    @Slot()
    def openResult(self):
        if self._state["result"]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._state["result"]))

    @Slot()
    def showFolder(self):
        if self._state["result"]:
            path = str(Path(self._state["result"]).resolve())
            if os.name == "nt":
                subprocess.Popen(["explorer.exe", "/select,", path], creationflags=CREATE_FLAGS)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    @Slot(result=bool)
    def requestClose(self):
        if self._state["busy"]:
            if not self._state["cancelling"]:
                from PySide6.QtWidgets import QMessageBox
                answer = QMessageBox.question(None, "Обработка продолжается", "Отменить обработку и закрыть Reframe?",
                                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                              QMessageBox.StandardButton.No)
                if answer == QMessageBox.StandardButton.Yes:
                    self.close_after_cancel = True
                    self.cancel()
            return False
        self.probe_cancel.set()
        return True


def main():
    mp.freeze_support()
    configure_logging(ROOT)
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    app = QApplication(sys.argv)
    app.setApplicationName("Reframe")
    app.setOrganizationName("Reframe")
    translator = QTranslator(app)
    if translator.load("qt_ru", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    QLocale.setDefault(QLocale("ru_RU"))
    controller = Controller()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("controller", controller)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "Main.qml")))
    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
