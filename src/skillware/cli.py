"""Ingest private transcripts and expose the remaining pipeline boundaries."""

import json
import sqlite3
from pathlib import Path
from typing import Annotated

import typer
from typer.core import TyperGroup

from skillware.schema import StageOutcome

STAGES = ("ingest", "extract", "cluster", "synthesize", "validate", "eval")


class PipelineGroup(TyperGroup):
    """Keep the documented pipeline order in help output."""

    def list_commands(self, ctx):
        return [stage for stage in STAGES if stage in self.commands]


app = typer.Typer(
    cls=PipelineGroup,
    help="Build evidence-backed Agent Skills from conference transcripts.",
    no_args_is_help=True,
    add_completion=False,
)


def _not_implemented(stage: str) -> None:
    typer.echo(f"{stage}: not implemented", err=True)
    raise typer.Exit(code=2)


ingest_app = typer.Typer(
    help="Fetch AI Engineer Markdown or parse declared local transcripts.",
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
)
app.add_typer(ingest_app, name="ingest")


def _report(result) -> None:
    typer.echo(
        json.dumps(
            {
                "outcome": result.outcome.value,
                "counts": result.counts,
                "errors": result.errors,
                "receipt_path": str(result.receipt_path),
            },
            sort_keys=True,
        )
    )
    if result.outcome != StageOutcome.PASS:
        raise typer.Exit(code=1 if result.outcome == StageOutcome.FAIL else 2)


def _blocked(message: str, *, stage: str = "ingest") -> None:
    # Invalid CLI declarations have no trustworthy artifact destination. Emit a
    # diagnostic receipt to stdout rather than write outside the declared roots.
    typer.echo(
        json.dumps(
            {
                "stage": stage,
                "config_version": "c03-fetch-v1"
                if stage == "ingest.fetch"
                else "c03-ingest-v3",
                "input_hashes": {},
                "counts": {},
                "output_refs": [],
                "outcome": "BLOCKED",
                "errors": [message],
                "receipt_path": None,
            },
            sort_keys=True,
        )
    )
    raise typer.Exit(code=2)


@ingest_app.callback()
def ingest(
    ctx: typer.Context,
    cache: Annotated[
        Path | None, typer.Option(help="Cached or local Markdown directory.")
    ] = None,
    db: Annotated[Path | None, typer.Option(help="Private SQLite output path.")] = None,
    manifest: Annotated[
        Path | None, typer.Option(help="Local JSONL source metadata.")
    ] = None,
    root: Annotated[
        Path | None, typer.Option(help="Input root; defaults to --cache.")
    ] = None,
    objects: Annotated[
        Path | None,
        typer.Option(
            help="Private source store outside repos; defaults to <db>.objects."
        ),
    ] = None,
) -> None:
    """Parse private transcripts with source identity and provenance."""
    if ctx.invoked_subcommand is not None:
        if any(value is not None for value in (cache, db, manifest, root, objects)):
            _blocked("place fetch options after 'ingest fetch'")
        return
    if cache is None or db is None:
        _blocked("ingest requires --cache and --db")
    from skillware.ingest.pipeline import ingest_transcripts

    try:
        result = ingest_transcripts(cache, db, root or cache, manifest, objects)
    except (OSError, ValueError, sqlite3.Error) as exc:
        _blocked(str(exc))
    _report(result)


@ingest_app.command(name="fetch")
def fetch(
    out: Annotated[
        Path | None, typer.Option(help="Private cache directory outside repositories.")
    ] = None,
    root: Annotated[
        Path | None, typer.Option(help="Cache root; defaults to --out.")
    ] = None,
    url: Annotated[
        list[str] | None,
        typer.Option(
            help="AI Engineer Markdown/index URL; repeatable; default discovery."
        ),
    ] = None,
) -> None:
    """Retrieve AI Engineer Markdown with robots, pacing, and conditional caching."""
    if out is None:
        _blocked("ingest fetch requires --out", stage="ingest.fetch")
    from skillware.ingest.fetch import fetch_transcripts

    try:
        result = fetch_transcripts(out, root or out, url)
    except (OSError, ValueError) as exc:
        _blocked(str(exc), stage="ingest.fetch")
    _report(result)


@app.command()
def extract() -> None:
    """Propose atomic claims and repository mentions."""
    _not_implemented("extract")


@app.command()
def cluster() -> None:
    """Assess scoped agreement and opposition across talks."""
    _not_implemented("cluster")


@app.command()
def synthesize() -> None:
    """Draft skills from reviewed evidence."""
    _not_implemented("synthesize")


@app.command()
def validate() -> None:
    """Validate the complete candidate skill pack."""
    _not_implemented("validate")


@app.command(name="eval")
def evaluate() -> None:
    """Evaluate against reviewed labels and criteria."""
    _not_implemented("eval")


if __name__ == "__main__":
    app()
