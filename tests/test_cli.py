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


@pytest.mark.parametrize("stage", STAGES)
def test_stage_stubs_exit_two_without_writing(stage, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, [stage])
    assert result.exit_code == 2
    assert f"{stage}: not implemented" in _plain(result.output)
    assert list(tmp_path.iterdir()) == []
