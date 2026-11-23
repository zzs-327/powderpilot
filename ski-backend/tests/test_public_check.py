"""发布检查离线契约：不访问 git 历史、API 或安装依赖元数据。"""
import sys

import pytest

from scripts import pre_open_source_check as check


@pytest.fixture
def mocked_scans(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['pre_open_source_check', '--quiet'])
    monkeypatch.setattr(check, 'load_publicignore', lambda: ([], []))
    monkeypatch.setattr(check, 'exported_files', lambda excludes: [])
    for name in ('check_secrets', 'check_paths'):
        monkeypatch.setattr(check, name, lambda files: [])
    for name in ('check_history', 'check_licenses', 'check_untracked'):
        monkeypatch.setattr(check, name, lambda: [])


def test_clean_scan_returns_zero(mocked_scans, capsys):
    assert check.main() == 0
    assert '可以公开' in capsys.readouterr().out


@pytest.mark.parametrize('scan', ['check_secrets', 'check_history', 'check_licenses', 'internal'])
def test_every_blocking_scan_returns_one(mocked_scans, monkeypatch, capsys, scan):
    if scan == 'internal':
        monkeypatch.setattr(check, 'exported_files', lambda excludes: [check.REPO_ROOT / '内部文档.md'])
    elif scan == 'check_secrets':
        monkeypatch.setattr(check, scan, lambda files: ['fake blocking finding'])
    else:
        monkeypatch.setattr(check, scan, lambda: ['fake blocking finding'])
    assert check.main() == 1
    assert '阻断问题' in capsys.readouterr().out


def test_warnings_do_not_block(mocked_scans, monkeypatch):
    monkeypatch.setattr(check, 'check_paths', lambda files: ['fake local path'])
    monkeypatch.setattr(check, 'check_untracked', lambda: ['new-test.py'])
    assert check.main() == 0


@pytest.mark.parametrize('line', [
    'password=placeholder', 'password="placeholder"', 'passwd="change_me"',
    'pwd="your_password"', 'secret_key="your_password"',
    'password="PLACEHOLDER"', 'url="mysql+pymysql://test:placeholder@localhost/db"',
    'password=DEMO_PASSWORD', 'SECRET_KEY: ci-only-not-a-real-secret',
])
def test_placeholders_and_bare_constants_are_not_secrets(tmp_path, monkeypatch, line):
    monkeypatch.setattr(check, 'REPO_ROOT', tmp_path)
    path = tmp_path / 'fixture.txt'
    path.write_text(line, encoding='utf-8')
    assert check.check_secrets([path]) == []


@pytest.mark.parametrize('prefix,value,suffix', [
    ('password="', 'NotARealSecret', '"'), ('password=', 'fakeSecret123', ''),
    ('secret_key="', 'NotARealSecret', '"'),
    ('SECRET_KEY: ', 'ci-production-secret123', ''),
    ('url="mysql+pymysql://test:', 'NotARealSecret', '@localhost/db"'),
    ('key="sk-', '0123456789abcdef0123456789abcdef', '"'),
    ('AK=', '0123456789abcdef0123456789abcdef', ''),
])
def test_non_placeholder_fake_secrets_are_reported(tmp_path, monkeypatch, prefix, value, suffix):
    line = prefix + value + suffix
    monkeypatch.setattr(check, 'REPO_ROOT', tmp_path)
    path = tmp_path / 'fixture.txt'
    path.write_text(line, encoding='utf-8')
    assert check.check_secrets([path])
