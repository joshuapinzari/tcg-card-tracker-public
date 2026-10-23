"""Distribution contracts: commands, source imports, and public-file boundaries."""
from pathlib import Path
import subprocess
import sys
import pytest
from tools.verify_repository import check_repository
from tools.check_public_release import check_tree

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_static_checks():
    assert check_repository(ROOT)==[]
    assert check_tree(ROOT)==[]


@pytest.mark.parametrize('command',['daily','backfill-raw','backfill-graded','build-research',
    'refresh-scan','lockbox','evaluate-v3','evaluate-v4','regime-research','init-db'])
def test_supported_help_does_not_need_database_or_make_provider_calls(command):
    result=subprocess.run([sys.executable,'-m','services',command,'--help'],cwd=ROOT,
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==0, result.stderr
    assert 'usage:' in result.stdout.lower()


def test_verifier_detects_removed_import_not_just_present_module(tmp_path):
    (tmp_path/'database').mkdir()
    (tmp_path/'database/__init__.py').write_text('')
    (tmp_path/'database/operations.py').write_text('def connect():\n    pass\n')
    (tmp_path/'app.py').write_text('from database.operations import removed_history_function\n')
    problems=check_repository(tmp_path)
    assert any('missing imported name database.operations.removed_history_function' in p for p in problems)


@pytest.mark.parametrize('filename',['.env','local.sqlite3','snapshot.parquet','prices.csv','logs/debug.log'])
def test_public_check_rejects_unwanted_artifacts(tmp_path,filename):
    target=tmp_path/filename
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text('synthetic fixture, not real data')
    assert any(filename in p for p in check_tree(tmp_path))


def test_public_check_flags_credentials_in_tests_too(tmp_path):
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/test_example.py').write_text("value = '" + 'ghp_' + 'x'*40 + "'\n")
    assert any('GitHub token' in p for p in check_tree(tmp_path))


def test_public_check_never_echoes_secret_values(tmp_path):
    fake='ghp_'+'x'*40
    (tmp_path/'app.py').write_text("TOKEN = '"+fake+"'\n")
    problems=check_tree(tmp_path)
    assert problems and all(fake not in p for p in problems)
