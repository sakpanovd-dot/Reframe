import threading

import pytest

from reframe import pipeline
from reframe.contracts import Cancelled, ExportRequest, ReframeError, VideoInfo


@pytest.fixture
def job(tmp_path, monkeypatch):
    source = tmp_path / 'исходник.mp4'
    target = tmp_path / 'результат.mp4'
    source.write_bytes(b'original')
    target.write_bytes(b'old result')
    monkeypatch.setattr(pipeline, 'probe', lambda *a: VideoInfo(source, 320, 180, 1, 25, 0, None))
    monkeypatch.setattr(pipeline, 'choose_encoder', lambda *a: 'libx264')
    monkeypatch.setattr(pipeline, 'encode_video', lambda *a: None)
    monkeypatch.setattr(pipeline, 'mux_audio', lambda info, video, result, *a: result.write_bytes(b'verified video'))
    monkeypatch.setattr(pipeline, 'verify', lambda *a: None)
    monkeypatch.setattr(pipeline.time, 'sleep', lambda *a: None)
    return ExportRequest(source, target, preset='16:9', overwrite=True, cpu_only=True)


def deny_cleanup(*args):
    raise PermissionError('temporary file is locked')


def test_saved_output_stays_successful_when_cleanup_fails(job, monkeypatch, caplog):
    monkeypatch.setattr(pipeline.shutil, 'rmtree', deny_cleanup)
    events = []
    assert pipeline.run(job, threading.Event(), events.append) == job.destination
    assert job.destination.read_bytes() == b'verified video'
    assert job.source.read_bytes() == b'original'
    assert [e.kind for e in events if e.kind != 'progress'] == ['done']
    assert 'Cannot remove temporary job directory' in caplog.text
    assert len(list(job.destination.parent.glob('.reframe-*'))) == 1


@pytest.mark.parametrize('failure', [Cancelled(), OSError(28, 'disk full')])
def test_cleanup_failure_preserves_original_error_and_old_result(job, monkeypatch, failure):
    def fail(*args):
        raise failure
    monkeypatch.setattr(pipeline, 'verify', fail)
    monkeypatch.setattr(pipeline.shutil, 'rmtree', deny_cleanup)
    events = []
    with pytest.raises(type(failure)) as error:
        pipeline.run(job, threading.Event(), events.append)
    assert error.value is failure
    assert job.destination.read_bytes() == b'old result'
    assert not any(e.kind == 'done' for e in events)


def test_transient_cleanup_lock_is_retried(job, monkeypatch):
    remove = pipeline.shutil.rmtree
    calls = []
    def locked_once(path):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError('locked')
        remove(path)
    monkeypatch.setattr(pipeline.shutil, 'rmtree', locked_once)
    pipeline.run(job, threading.Event())
    assert len(calls) == 2
    assert not list(job.destination.parent.glob('.reframe-*'))


def test_diagnostics_cannot_write_through_hard_link_to_output(job):
    from dataclasses import replace
    diagnostic = job.destination.with_suffix('.json')
    diagnostic.hardlink_to(job.destination)
    with pytest.raises(ReframeError, match='Диагностический'):
        pipeline.validate_destination(replace(job, diagnostics=diagnostic))
    assert job.destination.read_bytes() == b'old result'


def test_source_alias_created_during_export_is_rejected(job, monkeypatch):
    def replace_output(*args):
        job.destination.unlink()
        job.destination.hardlink_to(job.source)
    monkeypatch.setattr(pipeline, 'verify', replace_output)
    with pytest.raises(ReframeError, match='Исходное'):
        pipeline.run(job, threading.Event())
    assert job.source.read_bytes() == b'original'
    assert job.destination.read_bytes() == b'original'
    assert not list(job.destination.parent.glob('.reframe-*'))


def test_file_created_during_export_is_not_replaced(job, monkeypatch):
    from dataclasses import replace
    job.destination.unlink()
    monkeypatch.setattr(pipeline, 'verify', lambda *a: job.destination.write_bytes(b'other application'))
    with pytest.raises(ReframeError, match='существует'):
        pipeline.run(replace(job, overwrite=False), threading.Event())
    assert job.destination.read_bytes() == b'other application'
    assert not list(job.destination.parent.glob('.reframe-*'))
