import json
import logging
import os

import pytest

from reframe import local_state


def test_settings_replace_failure_preserves_old_file(tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    path.write_text('{"preset":"1:1"}', encoding='utf-8')
    def fail(*args):
        raise PermissionError('locked')
    with monkeypatch.context() as patch:
        patch.setattr(local_state.os, 'replace', fail)
        assert not local_state.save_settings(path, {'preset': '4:5'})
    assert local_state.read_settings(path) == {'preset': '1:1'}
    assert not list(tmp_path.glob('.reframe-settings-*'))
    assert local_state.save_settings(path, {'preset': '4:5'})
    assert local_state.read_settings(path) == {'preset': '4:5'}
    assert not list(tmp_path.glob('.reframe-settings-*'))


@pytest.mark.parametrize('blocked', ['directory', 'file'])
def test_logging_falls_back_when_path_is_unwritable(tmp_path, monkeypatch, blocked):
    directory = tmp_path / 'work'
    if blocked == 'directory':
        directory.write_bytes(b'existing data')
    else:
        directory.mkdir()
        (directory / 'reframe.log').mkdir()
    configurations = []
    monkeypatch.setattr(logging, 'basicConfig', lambda **kwargs: configurations.append(kwargs))
    assert not local_state.configure_logging(tmp_path)
    handler = configurations[0]['handlers'][0]
    assert type(handler) is logging.StreamHandler
    handler.close()
    if blocked == 'directory':
        assert directory.read_bytes() == b'existing data'


def test_file_logging_keeps_local_destination(tmp_path, monkeypatch):
    configurations = []
    monkeypatch.setattr(logging, 'basicConfig', lambda **kwargs: configurations.append(kwargs))
    assert local_state.configure_logging(tmp_path)
    handler = configurations[0]['handlers'][0]
    try:
        handler.emit(logging.LogRecord('test', logging.INFO, '', 0, 'local record', (), None))
    finally:
        handler.close()
    assert 'local record' in (tmp_path / 'work' / 'reframe.log').read_text(encoding='utf-8')


def _job(folder, name='.reframe-' + 'a' * 32, age=2 * 24 * 3600, now=1_000_000_000):
    directory = folder / name
    directory.mkdir(parents=True)
    (directory / 'video.mp4').write_bytes(b'partial')
    for path in (directory / 'video.mp4', directory):
        os.utime(path, (now - age, now - age))
    return directory


def test_stale_job_folders_are_removed_only_in_remembered_folders(tmp_path):
    now = 1_000_000_000
    root, results, other = tmp_path / 'app', tmp_path / 'results', tmp_path / 'other'
    root.mkdir()
    stale = _job(results, now=now)
    fresh = _job(results, '.reframe-' + 'b' * 32, age=3600, now=now)
    # A running job: the folder itself is old, but its file is still being written.
    running = _job(results, '.reframe-' + 'c' * 32, now=now)
    os.utime(running / 'video.mp4', (now - 60, now - 60))
    unrelated = [_job(results, name, now=now) for name in ('.reframe-user', 'reframe-' + 'd' * 32, '.reframe-' + 'E' * 32)]
    stray_file = results / ('.reframe-' + 'f' * 32)
    stray_file.write_bytes(b'not a folder')
    os.utime(stray_file, (now - 10 ** 6, now - 10 ** 6))
    elsewhere = _job(other, now=now)
    assert local_state.cleanup_stale_jobs(root, now) == []  # nothing remembered yet
    local_state.remember_job_folder(root, results)
    assert local_state.cleanup_stale_jobs(root, now) == [stale]
    assert not stale.exists()
    assert fresh.exists() and running.exists() and stray_file.exists() and elsewhere.exists()
    assert all(path.exists() for path in unrelated)
    assert results.is_dir()


def test_remembered_folders_are_unique_recent_and_tolerate_damage(tmp_path):
    root = tmp_path / 'app'
    root.mkdir()
    (root / 'work').mkdir()
    (root / 'work' / local_state.JOB_FOLDERS).write_text('{broken', encoding='utf-8')
    assert local_state.cleanup_stale_jobs(root) == []
    folders = [tmp_path / f'folder{i}' for i in range(local_state.MAX_FOLDERS + 5)]
    for folder in folders + [folders[0]]:
        local_state.remember_job_folder(root, folder)
    remembered = json.loads((root / 'work' / local_state.JOB_FOLDERS).read_text(encoding='utf-8'))
    assert remembered[0] == str(folders[0].resolve())
    assert len(remembered) == len(set(remembered)) == local_state.MAX_FOLDERS
    (root / 'work' / local_state.JOB_FOLDERS).write_text(json.dumps([42, None, str(tmp_path / 'missing')]), encoding='utf-8')
    assert local_state.cleanup_stale_jobs(root) == []


def test_remembering_never_breaks_a_job(tmp_path):
    root = tmp_path / 'app'
    root.mkdir()
    (root / 'work').write_bytes(b'not a directory')
    local_state.remember_job_folder(root, tmp_path)  # must not raise


def test_locked_stale_folder_is_logged_and_skipped(tmp_path, monkeypatch, caplog):
    now = 1_000_000_000
    root, results = tmp_path / 'app', tmp_path / 'results'
    root.mkdir()
    stale = _job(results, now=now)
    local_state.remember_job_folder(root, results)
    def locked(path):
        raise PermissionError('in use')
    monkeypatch.setattr(local_state.shutil, 'rmtree', locked)
    with caplog.at_level(logging.WARNING):
        assert local_state.cleanup_stale_jobs(root, now) == []
    assert stale.exists() and 'Cannot remove old temporary folder' in caplog.text
