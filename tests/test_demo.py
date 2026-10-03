"""make demo: the pipeline runner."""

import sys
from pathlib import Path

import pytest

from scripts import demo
from scripts.demo import Demo, StepFailed, StepResult

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("AEROTRACE_OFFLINE", "1")  # never reach for the API from a test


def test_full_pipeline_on_tinyxml2_prints_each_step_with_time_and_counts(tmp_path, capsys):
    from tests.conftest import ensure_repo

    ensure_repo("tinyxml2")
    code = demo.main(["tinyxml2", "--no-serve", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    for step in ("clone", "extract", "enrich", "load", "explain"):
        assert f"ok {step}" in out
    assert "416 functions" in out and "virtual links" in out and "template sentences" in out
    assert "total" in out and "s  " in out  # a time for every step
    assert (tmp_path / "tinyxml2.graph.json").is_file() and (tmp_path / "tinyxml2.db").is_file()


def test_it_stops_at_the_first_failure_with_a_plain_message(tmp_path, capsys):
    repos = tmp_path / "repos"
    (repos / "broken").mkdir(parents=True)
    (repos / "broken" / "README.md").write_text("no C++ here")  # the clone step sees it exists; extract finds nothing
    code = demo.main(["broken", "--no-serve", "--data-dir", str(tmp_path / "data"), "--repos-dir", str(repos)])
    err = capsys.readouterr()
    assert code == 1
    assert "ok clone" in err.out and "ok extract" not in err.out
    assert "x extract failed" in err.err and "Nothing after it was run" in err.err
    assert "no C/C++ source files" in err.err  # the end of the step's own output is shown
    assert "make demo REPO=broken" in err.err
    assert not (tmp_path / "data" / "broken.db").exists()  # enrich and load never ran


def test_a_missing_repo_with_no_url_is_explained(tmp_path, capsys):
    code = demo.main(["nowhere", "--no-serve", "--data-dir", str(tmp_path / "d"), "--repos-dir", str(tmp_path / "r")])
    err = capsys.readouterr().err
    assert code == 1 and "x clone failed" in err and "repos-config/nowhere.yml" in err


def test_run_reports_the_exit_code_and_the_end_of_the_output(tmp_path):
    d = Demo("x", tmp_path, tmp_path)
    with pytest.raises(StepFailed) as e:
        d.run("boom", [sys.executable, "-c", "print('line1'); print('the real reason'); raise SystemExit(3)"])
    assert e.value.code == 3 and "the real reason" in e.value.tail and e.value.name == "boom"
    assert d.run("fine", [sys.executable, "-c", "print('hello')"]).strip() == "hello"


def test_explain_goes_offline_when_there_is_no_key_or_when_asked(monkeypatch, tmp_path):
    d = Demo("x", tmp_path, tmp_path)
    monkeypatch.delenv("AEROTRACE_OFFLINE")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert d.explain_mode() == (True, "no ANTHROPIC_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert d.explain_mode() == (False, "")
    monkeypatch.setenv("AEROTRACE_OFFLINE", "1")
    assert d.explain_mode() == (True, "AEROTRACE_OFFLINE is set")


def test_summary_adds_up_the_times():
    text = demo.summary([StepResult("a", 1.5), StepResult("b", 2.0)])
    assert "a" in text and "1.5s" in text and "total" in text and "3.5s" in text


def test_clone_urls_come_from_the_repo_settings():
    from extractor.common.config import find_config

    assert find_config("jsbsim").url == "https://github.com/JSBSim-Team/jsbsim"
    assert find_config("tinyxml2").url == "https://github.com/leethomason/tinyxml2"
