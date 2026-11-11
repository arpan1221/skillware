"""CLI fetch-to-database checks using the recorded synthetic HTTP fixture."""

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from skillware.cli import app
from skillware.ingest import fetch
from skillware.schema import EntityRef, Talk
from skillware.store import Store

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_fetch_ingest_revalidation_and_revision(tmp_path, monkeypatch):
    recorded = json.loads((FIXTURES / "http" / "transcripts.json").read_text())
    cache, db = tmp_path / "cache", tmp_path / "private.sqlite3"
    url = "https://www.ai.engineer/talks/fixture.md"
    requests = []
    responses = []

    def transport(request_url, headers):
        requests.append((request_url, headers))
        item = recorded[responses.pop(0)]
        return fetch.Response(item["status"], item["headers"], item["body"].encode())

    monkeypatch.setattr(fetch, "http_get", transport)
    monkeypatch.setattr(fetch.time, "sleep", lambda seconds: None)
    original = None
    for response, expected_versions in (
        ("first", 1),
        ("unchanged", 1),
        ("first", 1),
        ("changed", 2),
    ):
        responses.extend(["robots", response])
        result = runner.invoke(
            app, ["ingest", "fetch", "--out", str(cache), "--url", url]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["outcome"] == "PASS"
        result = runner.invoke(app, ["ingest", "--cache", str(cache), "--db", str(db)])
        assert result.exit_code == 0, result.output
        receipt = json.loads(
            Path(json.loads(result.output)["receipt_path"]).read_text()
        )
        with Store(db) as store:
            talks = [
                Talk.model_validate_json(row[0])
                for row in store.connection.execute("SELECT payload FROM talks")
            ]
            assert len(talks) == expected_versions
            assert len({talk.id for talk in talks}) == 1
            assert all(
                store.resolve(EntityRef.model_validate(ref))
                for ref in receipt["output_refs"]
            )
            if original is None:
                original = talks[0]
            assert original in talks
            if response in {"unchanged", "first"} and original is not None:
                assert talks[0].fetch_provenance == original.fetch_provenance
            for talk in talks:
                assert talk.fetch_provenance.source_url == url
                assert talk.fetch_provenance.content_sha256 == talk.transcript_sha256
                history = cache / ".objects" / f"{talk.transcript_sha256}.txt"
                assert (
                    hashlib.sha256(history.read_bytes()).hexdigest()
                    == talk.transcript_sha256
                )
    assert not responses
    assert requests[3][1]["If-None-Match"] == '"fixture-v1"'


def test_cli_missing_inputs_and_contradiction_write_nonpassing_receipts(tmp_path):
    cache, db = tmp_path / "inputs", tmp_path / "private.sqlite3"
    result = runner.invoke(app, ["ingest", "--cache", str(cache), "--db", str(db)])
    assert result.exit_code == 2, result.output
    receipt = json.loads(Path(json.loads(result.output)["receipt_path"]).read_text())
    assert receipt["outcome"] == "BLOCKED"
    assert receipt["output_refs"] == []
    cache.mkdir()
    (cache / "talk.md").write_bytes(
        (FIXTURES / "transcripts" / "talk-01.md").read_bytes()
    )
    manifest = cache / "metadata.jsonl"
    manifest.write_text(
        json.dumps({"path": "talk.md", "speaker": "Contradictory Speaker"}) + "\n"
    )
    result = runner.invoke(
        app,
        ["ingest", "--cache", str(cache), "--db", str(db), "--manifest", str(manifest)],
    )
    assert result.exit_code == 1, result.output
    receipt = json.loads(Path(json.loads(result.output)["receipt_path"]).read_text())
    assert receipt["outcome"] == "FAIL"
    assert "contradictory speaker" in " ".join(receipt["errors"])
    with Store(db) as store:
        assert store.connection.execute("SELECT count(*) FROM talks").fetchone()[0] == 0
        assert [
            row[0]
            for row in store.connection.execute(
                "SELECT outcome FROM stage_runs ORDER BY rowid"
            )
        ] == ["BLOCKED", "FAIL"]


def test_cli_explicit_private_source_store_preserves_local_inputs(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = cache / "talk.md"
    source.write_bytes((FIXTURES / "transcripts" / "talk-01.md").read_bytes())
    db = tmp_path / "private.sqlite3"
    objects = tmp_path / "source-history"
    args = ["ingest", "--cache", str(cache), "--db", str(db), "--objects", str(objects)]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    source.rename(cache / "renamed.md")
    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["counts"]["unchanged"] == 1
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM talks").fetchall()
    talk = Talk.model_validate_json(row[0])
    assert Path(talk.transcript_path).parent == objects
    (cache / "renamed.md").unlink()
    assert (
        hashlib.sha256(Path(talk.transcript_path).read_bytes()).hexdigest()
        == talk.transcript_sha256
    )
