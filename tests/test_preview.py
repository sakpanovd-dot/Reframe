"""Regression: progress must not reload or clear a paused preview."""
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from reframe.app import Controller
from reframe.media import binary, command


def wait_for(app, condition, timeout=15):
    end = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > end:
            raise AssertionError("Preview did not reach expected state")
        time.sleep(.01)


def test_progress_does_not_notify_media_properties():
    app = QApplication.instance() or QApplication([])
    controller = Controller()
    source_events = QSignalSpy(controller.previewSourceChanged)
    processing_events = QSignalSpy(controller.processingChanged)
    controller.update(previewUrl=QUrl.fromLocalFile("C:/test.mp4").toString(), busy=True)
    for i in range(50):
        controller.update(progress=i / 50, elapsed=str(i), stage="Создание видео", busy=True)
    assert source_events.count() == 1
    assert processing_events.count() == 1
    controller.update(busy=False)
    controller.update(previewUrl=QUrl.fromLocalFile("C:/result.mp4").toString())
    assert source_events.count() == 2
    assert processing_events.count() == 2
    controller.timer.stop()


def test_qml_keeps_paused_frame_through_progress(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "preview.mp4"
    command([binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
             "color=c=red:s=320x180:r=25:d=2", "-c:v", "libx264", str(source)])
    controller = Controller()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("controller", controller)
    warnings = []
    engine.warnings.connect(lambda messages: warnings.extend(str(m) for m in messages))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "reframe/qml/Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    player = window.findChild(QMediaPlayer, "previewPlayer")
    assert player is not None
    try:
        controller.update(previewUrl=QUrl.fromLocalFile(str(source)).toString())
        player.play()
        wait_for(app, lambda: player.videoSink() is not None and player.videoSink().videoFrame().isValid())
        controller.update(busy=True)
        wait_for(app, lambda: player.playbackState() == QMediaPlayer.PlaybackState.PausedState)
        sink = player.videoSink()
        baseline = sink.videoFrame().toImage().pixelColor(100, 70)
        assert baseline.red() > 180
        source_events = QSignalSpy(player.sourceChanged)
        states = []
        player.mediaStatusChanged.connect(states.append)
        for i in range(40):
            controller.update(progress=i / 40, elapsed=str(i))
            app.processEvents()
            time.sleep(.015)
            assert sink.videoFrame().isValid()
            assert sink.videoFrame().toImage().pixelColor(100, 70) == baseline
        assert source_events.count() == 0
        assert QMediaPlayer.MediaStatus.LoadingMedia not in states
        assert player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        controller.update(busy=False)
        app.processEvents()
        assert sink.videoFrame().isValid()
        assert not warnings
    finally:
        controller.update(busy=False)
        player.stop()
        controller.timer.stop()
        window.close()
        engine.deleteLater()
        app.processEvents()
