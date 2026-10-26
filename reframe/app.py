from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path
from queue import Empty
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot, QTranslator, QLibraryInfo, QLocale
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .contracts import ExportRequest, ReframeError
from .detector import model_path
from .export import EXPORTERS
from .media import CREATE_FLAGS, probe
from .pipeline import worker, validate_destination
from .presets import load_presets

ROOT = Path(__file__).resolve().parent.parent


class Controller(QObject):
    changed = Signal()
    inspected = Signal(object, str)
    closeReady = Signal()

    def __init__(self):
        super().__init__()
        self._state = {"source": "", "sourceUrl": "", "previewUrl": "", "name": "",
                       "metadata": "", "warning": "", "busy": False, "loading": False,
                       "stage": "Готов к работе", "detail": "", "progress": 0., "elapsed": "",
                       "error": "", "result": "", "preset": "9:16", "container": "mp4",
                       "cancelling": False}
        self.presets = load_presets()
        self.info = None
        self.process = None
        self.context = mp.get_context("spawn")
        self.events = None
        self.cancel_event = None
        self.terminal_received = False
        self.started = 0.
        self.close_after_cancel = False
        self.probe_cancel = threading.Event()
        try:
            saved = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
            if saved.get("preset") in self.presets:
                self._state["preset"] = saved["preset"]
            if saved.get("container") in EXPORTERS:
                self._state["container"] = saved["container"]
        except (OSError, ValueError):
            pass
        self.inspected.connect(self._inspection_finished)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    @Property("QVariantMap", notify=changed)
    def state(self):
        return self._state

    @Property("QVariantList", constant=True)
    def formats(self):
        return [{"id": p.id, "label": p.title, "description": p.description,
                 "ratio": p.ratio} for p in self.presets.values()]

    @Property("QStringList", constant=True)
    def containers(self):
        return [key.upper() for key in EXPORTERS]

    def update(self, **kwargs):
        self._state.update(kwargs)
        self.changed.emit()

    @Slot()
    def chooseSource(self):
        if self._state["busy"] or self._state["loading"]:
            return
        path, _ = QFileDialog.getOpenFileName(None, "Выберите видео", "", "Видео (*.mp4 *.mov *.mkv *.avi);;Все файлы (*)")
        if path:
            self.loadSource(QUrl.fromLocalFile(path).toString())

    @Slot(str)
    def loadSource(self, url):
        if self._state["busy"] or self._state["loading"]:
            return
        path = QUrl(url).toLocalFile()
        if not path:
            self.update(error="Перетащите локальный видеофайл.")
            return
        self.info = None
        self.probe_cancel.clear()
        self.update(loading=True, error="", result="", stage="Проверка видео", source="", sourceUrl="",
                    previewUrl="", name="", metadata="", warning="", detail="")

        def inspect():
            try:
                info = probe(Path(path), self.probe_cancel)
                self.inspected.emit(info, "")
            except Exception as error:
                self.inspected.emit(None, str(error) if isinstance(error, ReframeError)
                                    else "Не удалось открыть видео. Проверьте файл и FFmpeg.")
        threading.Thread(target=inspect, daemon=True).start()

    @Slot(object, str)
    def _inspection_finished(self, info, error):
        self.info = info
        if error:
            self.update(loading=False, error=error, stage="Не удалось открыть видео")
            return
        minutes, seconds = divmod(round(info.duration), 60)
        url = QUrl.fromLocalFile(str(info.path)).toString()
        self.update(loading=False, source=str(info.path), sourceUrl=url, previewUrl=url,
                    name=info.path.name, metadata=f"{info.width} × {info.height}  ·  {minutes}:{seconds:02d}  ·  {info.fps:.2f} кадр/с",
                    warning="\n".join(info.warnings), stage="Готов к созданию", progress=0, detail="", elapsed="")

    @Slot(str)
    def selectPreset(self, value):
        if not self._state["busy"] and value in self.presets:
            self.update(preset=value)

    @Slot(str)
    def selectContainer(self, value):
        if not self._state["busy"] and value.lower() in EXPORTERS:
            self.update(container=value.lower())

    @Slot()
    def start(self):
        if not self.info or self._state["busy"]:
            return
        if not model_path().exists():
            self.update(error="Модель не установлена. Выполните python scripts/download_model.py из папки Reframe.")
            return
        ext = EXPORTERS[self._state["container"]].extension
        default = self.info.path.with_name(self.info.path.stem + "_" + self._state["preset"].replace(":", "x") + ext)
        path, _ = QFileDialog.getSaveFileName(None, "Сохранить результат", str(default),
                                             f"Видео {ext.upper()} (*{ext})",
                                             options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        destination = Path(path).with_suffix(ext)
        try:
            overwrite = destination.exists()
            request = ExportRequest(self.info.path, destination, self._state["preset"], self._state["container"], overwrite)
            validate_destination(request)
            if overwrite:
                answer = QMessageBox.question(None, "Заменить файл?", f"Файл «{destination.name}» уже существует. Заменить его после успешной обработки?",
                                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                              QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
            (ROOT / "settings.json").write_text(json.dumps({"preset": request.preset, "container": request.container}), encoding="utf-8")
            self.events, self.cancel_event = self.context.Queue(), self.context.Event()
            self.process = self.context.Process(target=worker, args=(request, self.cancel_event, self.events))
            self.terminal_received = False
            self.started = time.monotonic()
            self.process.start()
            self.update(busy=True, cancelling=False, result="", error="", stage="Подготовка", detail="", progress=0, elapsed="0:00")
        except Exception as error:
            self.update(error=str(error))

    @Slot()
    def cancel(self):
        if self.cancel_event is not None and self._state["busy"]:
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
            if not self.terminal_received:
                self.update(stage="Ошибка", error="Процесс обработки неожиданно завершился. См. work/reframe.log.")
            self.process.close()
            self.process = None
            self.events.close()
            self.events = None
            self.update(busy=False, cancelling=False)
            if self.close_after_cancel:
                self.closeReady.emit()

    def _drain_events(self):
        while True:
            try:
                event = self.events.get_nowait()
            except Empty:
                break
            if event.kind == "progress":
                if not self._state["cancelling"]:
                    self.update(stage=event.stage, progress=event.progress, detail=event.detail)
            else:
                self.terminal_received = True
                if event.kind == "done":
                    self.update(stage="Готово", progress=1, result=event.detail,
                                previewUrl=QUrl.fromLocalFile(event.detail).toString(), detail="Видео сохранено")
                elif event.kind == "error":
                    self.update(stage="Не удалось создать видео", error=event.detail, detail="")
                else:
                    self.update(stage="Отменено", detail="Исходное видео сохранено", progress=0)

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
