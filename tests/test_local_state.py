import logging

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
