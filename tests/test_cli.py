import re

import pytest
from typer.testing import CliRunner

from skillware.cli import app

STAGES = ("ingest", "extract", "cluster", "synthesize", "validate", "eval")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
runner = CliRunner()


def _plain(text: str) -> str:
    """Strip ANSI styling; Rich forces color under GITHUB_ACTIONS/FORCE_COLOR."""
    return ANSI_ESCAPE.sub("", text)


def test_help_lists_stages_in_pipeline_order():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    commands = re.findall(
        r"\b(?:ingest|extract|cluster|synthesize|validate|eval)\b",
        _plain(result.output),
    )
    assert commands == list(STAGES)


@pytest.mark.parametrize("stage", STAGES[1:])
def test_stage_stubs_exit_two_without_writing(stage, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, [stage])
    assert result.exit_code == 2
    assert f"{stage}: not implemented" in _plain(result.output)
    assert list(tmp_path.iterdir()) == []


def test_ingest_without_declarations_is_blocked_without_writing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 2
    assert '"outcome": "BLOCKED"' in result.output
    assert "requires --cache and --db" in result.output
    assert list(tmp_path.iterdir()) == []


def test_fetch_without_output_declaration_emits_blocked_receipt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["ingest", "fetch"])
    assert result.exit_code == 2
    assert '"outcome": "BLOCKED"' in result.output
    assert "requires --out" in result.output
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("args", [["ingest", "--help"], ["ingest", "fetch", "--help"]])
def test_ingest_help(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output


def test_fetch_rejects_repository_cache_before_transport(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/synthetic\n")
    result = runner.invoke(app, ["ingest", "fetch", "--out", str(tmp_path / "cache")])
    assert result.exit_code == 2, result.output
    assert "outside repositories" in result.output
    assert not (tmp_path / "cache").exists()
