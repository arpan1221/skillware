"""Expose pipeline boundaries without claiming implemented stages."""

import typer
from typer.core import TyperGroup


class PipelineGroup(TyperGroup):
    """Keep the documented pipeline order in help output."""

    def list_commands(self, ctx):
        return list(self.commands)


app = typer.Typer(
    cls=PipelineGroup,
    help="Build evidence-backed Agent Skills from local conference transcripts.",
    no_args_is_help=True,
    add_completion=False,
)


def _not_implemented(stage: str) -> None:
    typer.echo(f"{stage}: not implemented", err=True)
    raise typer.Exit(code=2)


@app.command()
def ingest() -> None:
    """Read local transcripts and source metadata."""
    _not_implemented("ingest")


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
