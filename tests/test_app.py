"""Exercise the real controller and spawned worker without manual dialogs."""
import json
import time
import logging
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QFileDialog

from reframe.app import Controller
from reframe.media import binary, command


@pytest.mark.parametrize("error, expected", [
    (PermissionError("access denied details"), "Нет доступа к видео"),
    (RuntimeError("decoder failure details"), "work/reframe.log"),
])
def test_open_error_is_actionable_and_logged(tmp_path, monkeypatch, caplog, error, expected):
    import reframe.app as app_module
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    app = QApplication.instance() or QApplication([])

    def fail(*args):
        raise error

    monkeypatch.setattr(app_module, "probe", fail)
    controller = Controller()
    try:
        with caplog.at_level(logging.ERROR):
            controller.loadSource(QUrl.fromLocalFile(str(tmp_path / "source.mp4")).toString())
            wait_for(app, lambda: not controller.state["loading"])
        assert expected in controller.state["error"]
        assert str(error) not in controller.state["error"]
        assert str(error) in caplog.text
        assert any(record.exc_info for record in caplog.records)
        assert controller.info is None
        assert not controller.state["busy"]
    finally:
        controller.timer.stop()


def wait_for(app, condition, timeout=30):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Controller timed out")
        time.sleep(.03)


def test_controller_complete_and_repeat(tmp_path, monkeypatch):
    import reframe.app as app_module
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.mp4"
    command([binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=0.8",
             "-c:v", "libx264", str(source)])
    target = tmp_path / "source_9x16.mp4"  # saved beside the source, no dialog
    controller = Controller()
    controller.loadSource(QUrl.fromLocalFile(str(source)).toString())
    wait_for(app, lambda: not controller.state["loading"])
    assert controller.info is not None, controller.state["error"]
    controller.start()
    assert controller.state["busy"], controller.state["error"]
    wait_for(app, lambda: not controller.state["busy"])
    assert controller.state["result"] == str(target), controller.state
    assert target.exists()
    target = tmp_path / "source_9x16 (2).mp4"  # repeat never overwrites
    controller.start()
    wait_for(app, lambda: not controller.state["busy"])
    assert controller.state["result"] == str(target), controller.state
    controller.timer.stop()


def test_controller_cancel_leaves_no_result(tmp_path, monkeypatch):
    import reframe.app as app_module
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.mp4"
    command([binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=320x180:r=25:d=3",
             "-c:v", "libx264", str(source)])
    target = tmp_path / "source_9x16.mp4"
    controller = Controller()
    controller.loadSource(QUrl.fromLocalFile(str(source)).toString())
    wait_for(app, lambda: not controller.state["loading"])
    controller.start()
    controller.cancel()
    wait_for(app, lambda: not controller.state["busy"])
    assert controller.state["stage"] == "Отменено", controller.state
    assert not target.exists()
    assert not list(tmp_path.glob(".reframe-*"))
    controller.timer.stop()


@pytest.mark.parametrize('settings_blocked', [False, True])
def test_matching_aspect_starts_without_installed_model(tmp_path, monkeypatch, settings_blocked):
    import reframe.app as app_module
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    monkeypatch.setenv('REFRAME_MODEL', str(tmp_path / 'missing.onnx'))
    if settings_blocked:
        (tmp_path / 'settings.json').mkdir()
    app = QApplication.instance() or QApplication([])
    source, target = tmp_path / 'source.mp4', tmp_path / 'source_16x9.mp4'
    command([binary('ffmpeg'), '-v', 'error', '-n', '-f', 'lavfi', '-i',
             'color=s=320x180:r=25:d=0.4', '-c:v', 'libx264', str(source)])
    controller = Controller()
    try:
        controller.loadSource(QUrl.fromLocalFile(str(source)).toString())
        wait_for(app, lambda: not controller.state['loading'])
        controller.selectPreset('9:16')
        controller.start()
        assert not controller.state['busy']
        assert 'Модель не установлена' in controller.state['error']
        controller.selectPreset('16:9')
        controller.start()
        assert controller.state['busy'], controller.state
        wait_for(app, lambda: not controller.state['busy'])
        assert controller.state['result'] == str(target), controller.state
        assert target.exists()
    finally:
        controller.timer.stop()


@pytest.mark.parametrize('contents', ['null', '[]', '{broken', '{"preset": [], "container": {}}'])
def test_invalid_settings_use_defaults(tmp_path, monkeypatch, contents):
    import reframe.app as app_module
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    (tmp_path / 'settings.json').write_text(contents, encoding='utf-8')
    app = QApplication.instance() or QApplication([])
    controller = Controller()
    try:
        assert controller.state['preset'] == '9:16'
        assert controller.state['container'] == 'mp4'
    finally:
        controller.timer.stop()


def test_output_size_setting_is_saved(tmp_path, monkeypatch):
    import reframe.app as app_module
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    app = QApplication.instance() or QApplication([])
    controller = Controller()
    try:
        assert controller.state['upscale'] is False
        controller.setUpscale(True)
    finally:
        controller.timer.stop()
    assert json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8'))['upscale'] is True
    restored = Controller()
    try:
        assert restored.state['upscale'] is True
    finally:
        restored.timer.stop()
    (tmp_path / 'settings.json').write_text('{"upscale": "yes"}', encoding='utf-8')
    invalid = Controller()
    try:
        assert invalid.state['upscale'] is False
    finally:
        invalid.timer.stop()


def make_clip(path, seconds=.4):
    command([binary("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=s=320x180:r=25:d={seconds}",
             "-c:v", "libx264", str(path)])


def test_output_names_are_unique_and_follow_folder(tmp_path):
    from reframe.app import output_path
    source = tmp_path / "clip.mov"
    assert output_path(source, "9:16", "mp4") == tmp_path / "clip_9x16.mp4"
    (tmp_path / "clip_9x16.mp4").write_bytes(b"")
    assert output_path(source, "9:16", "mp4") == tmp_path / "clip_9x16 (2).mp4"
    assert output_path(source, "9:16", "mp4", reserved=[tmp_path / "clip_9x16 (2).mp4"]) == tmp_path / "clip_9x16 (3).mp4"
    other = tmp_path / "out"
    assert output_path(source, "4:5", "mov", str(other)) == other / "clip_4x5.mov"


def test_queue_processes_all_videos_in_order_and_skips_broken(tmp_path, monkeypatch):
    import reframe.app as app_module
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    app = QApplication.instance() or QApplication([])
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    make_clip(first)
    make_clip(second)
    (tmp_path / "broken.mp4").write_bytes(b"not a video")
    out = tmp_path / "results"
    out.mkdir()
    controller = Controller()
    try:
        controller.selectPreset("16:9")
        controller.update(outputDir=str(out))
        controller.addSources([QUrl.fromLocalFile(str(p)).toString() for p in (first, tmp_path / "broken.mp4", second)])
        wait_for(app, lambda: not controller.state["loading"])
        assert [item["name"] for item in controller.queue] == ["a.mp4", "b.mp4"]
        assert "broken.mp4" in controller.state["error"]
        controller.start()
        assert controller.state["busy"]
        assert controller.state["queueText"] == "Видео 1 из 2"
        wait_for(app, lambda: not controller.state["busy"], timeout=60)
        assert [item["status"] for item in controller.queue] == ["done", "done"]
        assert (out / "a_16x9.mp4").exists() and (out / "b_16x9.mp4").exists()
        assert controller.state["stage"] == "Готово"
        assert "2 из 2" in controller.state["detail"]
        controller.selectItem(controller.queue[0]["id"])
        assert controller.state["result"] == str(out / "a_16x9.mp4")
        controller.removeItem(controller.queue[0]["id"])
        assert [item["name"] for item in controller.queue] == ["b.mp4"]
    finally:
        controller.timer.stop()


def test_cancel_stops_queue_and_keeps_rest_waiting(tmp_path, monkeypatch):
    import reframe.app as app_module
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    app = QApplication.instance() or QApplication([])
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    make_clip(first, 3)
    make_clip(second)
    controller = Controller()
    try:
        controller.selectPreset("16:9")
        controller.addSources([QUrl.fromLocalFile(str(p)).toString() for p in (first, second)])
        wait_for(app, lambda: not controller.state["loading"])
        controller.start()
        controller.cancel()
        wait_for(app, lambda: not controller.state["busy"], timeout=60)
        assert [item["status"] for item in controller.queue] == ["cancelled", "waiting"]
        assert controller.state["stage"] == "Отменено"
        assert not list(tmp_path.glob("*_16x9*.mp4"))
    finally:
        controller.timer.stop()
