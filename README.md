# skillware

Turn AI Engineer conference talks into reusable Agent Skills. The MVP pack is
**The AI Engineer Playbook**, grounded in local transcripts and source evidence.

C02 provides the Python 3.12 scaffold, typed entities, JSON-schema export, SQLite store,
and an offline fixture adapter. The six pipeline commands are stubs: each prints
`not implemented` and exits `2`. No extraction or quality result is implied.

```sh
uv sync
uv run skillware --help
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

The command order is `ingest → extract → cluster → synthesize → validate → eval`.
Regenerate the six JSON schemas with `uv run python -m skillware.schema`.
Storage defaults to `.skillware/claims.sqlite3` (ASSUMPTION A1); pass a different
path to `skillware.store.Store`. Keep local transcripts in ignored `data/`.

See [SPEC](docs/SPEC.md), [ARCHITECTURE](docs/ARCHITECTURE.md), and
[CONTRIBUTING](CONTRIBUTING.md). Future model access uses existing CLI subscriptions
only: no API keys, paid API budget, Anthropic credits, or provider fallback.
Tests use fixtures without a model or network. Missing gold labels and unrun
quality evaluations remain BLOCKED.

Code is [Apache-2.0](LICENSE). Talk content belongs to its rights holders; publish
short quotes and links, never full transcripts. Reuse terms must be resolved with
AI Engineer before launch.
