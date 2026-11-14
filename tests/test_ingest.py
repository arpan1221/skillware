"""Offline ingest evidence uses only synthetic recorded transcript fixtures."""

import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from skillware.ingest.parser import canonical_url, parse_markdown
from skillware.ingest.pipeline import ingest_transcripts
from skillware.schema import EntityRef, Segment, Stage, StageOutcome, Talk
from skillware.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def load_rows(db, model):
    table = {Talk: "talks", Segment: "segments"}[model]
    with Store(db) as store:
        return [
            model.model_validate_json(row[0])
            for row in store.connection.execute(f"SELECT payload FROM {table}")
        ]


def assert_source_versions_resolve(db):
    talks = load_rows(db, Talk)
    assert talks
    for talk in talks:
        source = Path(talk.transcript_path)
        raw = source.read_bytes()
        assert source.name == f"{talk.transcript_sha256}.txt"
        assert hashlib.sha256(raw).hexdigest() == talk.transcript_sha256
        for segment in load_rows(db, Segment):
            if segment.talk_id == talk.id and segment.talk_version == talk.version:
                assert (
                    segment.text
                    == raw.decode("utf-8")[segment.source_start : segment.source_end]
                )


def setup_input(tmp_path, name="talk-01.md"):
    cache = tmp_path / "cache"
    cache.mkdir()
    target = cache / name
    shutil.copyfile(FIXTURES / name, target)
    return cache, target, tmp_path / "private.sqlite3"


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.md")), ids=lambda p: p.name)
def test_recorded_transcript_offsets_timing_and_attribution(tmp_path, fixture):
    cache, target, db = setup_input(tmp_path, fixture.name)
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    (talk,) = load_rows(db, Talk)
    segments = load_rows(db, Segment)
    raw = target.read_bytes()
    source = raw.decode("utf-8")
    assert talk.transcript_sha256 == hashlib.sha256(raw).hexdigest()
    assert talk.company is None
    assert talk.fetch_provenance is None
    assert len(segments) == 2
    for segment in segments:
        assert segment.text == source[segment.source_start : segment.source_end]
        assert segment.talk_version == talk.version
    number = int(fixture.stem.split("-")[1])
    if number % 4 == 1:
        assert segments[0].start_seconds == Decimal("1.250")
        assert segments[0].end_seconds == Decimal("3.500")
        assert segments[1].start_seconds == Decimal(4)
        assert segments[1].end_seconds is None
    elif number % 4 == 2:
        assert segments[0].start_seconds == Decimal(10)
        assert segments[1].start_seconds is None
        assert "café" in segments[0].text
    elif number % 4 == 3:
        assert segments[0].start_seconds == Decimal("3723.125")
        assert segments[1].speaker_override == "Guest Example"
        assert segments[1].speaker_override_evidence in segments[1].text
    else:
        assert all(s.start_seconds is None and s.end_seconds is None for s in segments)
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["config_version"]
    assert receipt["counts"]["talks"] == 1
    assert receipt["input_hashes"][str(target)] == talk.transcript_sha256
    with Store(db) as store:
        assert {EntityRef.model_validate(ref) for ref in receipt["output_refs"]} == {
            entity.ref for entity in [talk, *segments]
        }
        assert all(
            store.resolve(EntityRef.model_validate(ref))
            for ref in receipt["output_refs"]
        )


def test_at_least_twenty_recorded_transcripts_ingest_together(tmp_path):
    cache = tmp_path / "cache"
    shutil.copytree(FIXTURES, cache)
    result = ingest_transcripts(cache, tmp_path / "private.db", tmp_path)
    assert len(list(FIXTURES.glob("*.md"))) >= 20
    assert result.outcome == StageOutcome.PASS
    assert result.counts["talks"] == 24
    assert result.counts["segments"] == 48


def test_identical_rerun_and_renamed_source_are_idempotent(tmp_path):
    cache, target, db = setup_input(tmp_path)
    first = ingest_transcripts(cache, db, tmp_path)
    (talk,) = load_rows(db, Talk)
    target.rename(cache / "renamed.md")
    second = ingest_transcripts(cache, db, tmp_path)
    assert first.outcome == second.outcome == StageOutcome.PASS
    assert second.counts["unchanged"] == 1
    assert second.counts["talks"] == second.counts["segments"] == 0
    assert load_rows(db, Talk) == [talk]
    assert json.loads(second.receipt_path.read_text())["output_refs"] == []
    assert_source_versions_resolve(db)


def test_changed_transcript_new_version_retains_old_evidence(tmp_path):
    cache, target, db = setup_input(tmp_path)
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    (original,) = load_rows(db, Talk)
    original_segments = load_rows(db, Segment)
    target.write_text(target.read_text().replace("queue", "revised queue"))
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    talks = load_rows(db, Talk)
    assert len(talks) == 2
    assert {talk.id for talk in talks} == {original.id}
    assert len({talk.version for talk in talks}) == 2
    assert all(segment in load_rows(db, Segment) for segment in original_segments)
    assert ingest_transcripts(cache, db, tmp_path).counts["unchanged"] == 1
    assert_source_versions_resolve(db)
    target.unlink()
    assert_source_versions_resolve(db)
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.BLOCKED
    assert_source_versions_resolve(db)


def test_local_source_store_is_private_and_survives_input_directory_deletion(tmp_path):
    cache, target, db = setup_input(tmp_path)
    raw = target.read_bytes()
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    (talk,) = load_rows(db, Talk)
    snapshot = Path(talk.transcript_path)
    assert snapshot.parent == db.with_suffix(db.suffix + ".objects")
    assert snapshot.parent.stat().st_mode & 0o777 == 0o700
    assert snapshot.stat().st_mode & 0o777 == 0o400
    assert snapshot.read_bytes() == raw
    shutil.rmtree(cache)
    assert_source_versions_resolve(db)


def test_corrupt_immutable_local_history_fails_without_new_rows(tmp_path):
    cache, target, db = setup_input(tmp_path)
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    original = load_rows(db, Talk)
    snapshot = Path(original[0].transcript_path)
    snapshot.unlink()
    snapshot.write_bytes(b"Synthetic tampering.")
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.FAIL
    assert any("source history hash mismatch" in error for error in result.errors)
    assert load_rows(db, Talk) == original
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []


@pytest.mark.parametrize("kind", ["symlink", "release_pack", "repository"])
def test_local_source_store_rejects_unsafe_output_roots(tmp_path, kind):
    cache, target, db = setup_input(tmp_path)
    forbidden = tmp_path / "forbidden"
    forbidden.mkdir()
    if kind == "symlink":
        objects = tmp_path / "objects-link"
        objects.symlink_to(forbidden, target_is_directory=True)
    elif kind == "release_pack":
        (forbidden / "SKILL.md").write_text("Synthetic release pack marker.")
        objects = forbidden / "objects"
    else:
        objects = Path(__file__).resolve().parents[1] / "rejected-c03-objects"
    result = ingest_transcripts(cache, db, tmp_path, objects=objects)
    assert result.outcome == StageOutcome.FAIL
    assert result.counts["talks"] == result.counts["segments"] == 0
    assert load_rows(db, Talk) == []
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []
    assert not list(forbidden.glob("*.txt"))
    if kind != "symlink":
        assert not objects.exists()


def test_existing_corrupt_object_cannot_be_overwritten_or_committed(tmp_path):
    cache, target, db = setup_input(tmp_path)
    objects = tmp_path / "private-objects"
    objects.mkdir()
    content_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    snapshot = objects / f"{content_hash}.txt"
    snapshot.write_bytes(b"Synthetic corrupt object.")
    result = ingest_transcripts(cache, db, tmp_path, objects=objects)
    assert result.outcome == StageOutcome.FAIL
    assert snapshot.read_bytes() == b"Synthetic corrupt object."
    assert load_rows(db, Talk) == []


def test_legacy_mutable_source_paths_block_without_rewriting_old_versions(tmp_path):
    cache, target, db = setup_input(tmp_path)
    parsed = parse_markdown(target.read_bytes())
    legacy = Talk(
        id="talk-legacy-local",
        version="legacy-local-v2",
        title=parsed.metadata["title"],
        speaker=parsed.metadata["speaker"],
        source_url=parsed.metadata["source_url"],
        transcript_path=str(target),
        transcript_sha256=parsed.sha256,
        ingest_provenance={"config_version": "c03-ingest-v2"},
    )
    with Store(db) as store:
        with store.stage_transaction(
            Stage.INGEST, config_version="c03-ingest-v2", input_hashes={}
        ) as attempt:
            store.put(legacy)
            attempt.finish(StageOutcome.PASS, output_refs=[legacy.ref])
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("legacy mutable transcript history" in error for error in result.errors)
    assert load_rows(db, Talk) == [legacy]
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []


def test_fetch_url_does_not_supply_missing_source_attribution(tmp_path):
    cache, target, db = setup_input(tmp_path)
    target.write_text(
        "\n".join(
            line
            for line in target.read_text().splitlines()
            if not line.startswith("source_url:")
        )
    )
    target.with_suffix(".json").write_text(
        json.dumps(
            {
                "source_url": "https://ai.engineer/acquired.md",
                "fetched_at": "2026-10-01T12:00:00Z",
                "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
    )
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("missing source_url" in error for error in result.errors)
    assert load_rows(db, Talk) == []
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []


def test_unlistable_input_directory_is_recorded_and_prevents_partial_ingest(
    tmp_path, monkeypatch
):
    import os

    cache, target, db = setup_input(tmp_path)
    hidden = cache / "unlistable"
    hidden.mkdir()
    real_scandir = os.scandir

    def denied_scandir(path):
        if Path(path) == hidden:
            raise PermissionError(13, "synthetic directory read denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", denied_scandir)
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.FAIL
    assert any("synthetic directory read denied" in error for error in result.errors)
    assert load_rows(db, Talk) == []
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["outcome"] == "FAIL"
    assert receipt["output_refs"] == []
    with Store(db) as store:
        assert (
            store.connection.execute("SELECT outcome FROM stage_runs").fetchone()[0]
            == "FAIL"
        )


def test_fetch_provenance_and_new_fetch_date_version(tmp_path):
    cache, target, db = setup_input(tmp_path)
    sidecar = target.with_suffix(".json")
    payload = {
        "source_url": "https://www.ai.engineer/transcripts/example.md",
        "fetched_at": "2026-10-01T12:00:00+00:00",
        "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "etag": "synthetic-etag",
        "last_modified": "Thu, 01 Oct 2026 12:00:00 GMT",
    }
    sidecar.write_text(json.dumps(payload))
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    (original,) = load_rows(db, Talk)
    assert original.fetch_provenance.source_url == payload["source_url"]
    assert original.fetch_provenance.fetched_at.isoformat() == payload["fetched_at"]
    assert original.fetch_provenance.content_sha256 == original.transcript_sha256
    payload["last_checked_at"] = "2026-10-02T12:00:00+00:00"
    sidecar.write_text(json.dumps(payload))
    assert ingest_transcripts(cache, db, tmp_path).counts["unchanged"] == 1
    payload["fetched_at"] = "2026-10-03T12:00:00+00:00"
    sidecar.write_text(json.dumps(payload))
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    assert len(load_rows(db, Talk)) == 2
    assert {talk.id for talk in load_rows(db, Talk)} == {original.id}


@pytest.mark.parametrize("mutation", ["hash", "date", "url"])
def test_invalid_fetch_provenance_fails_without_rows(tmp_path, mutation):
    cache, target, db = setup_input(tmp_path)
    payload = {
        "source_url": "https://www.ai.engineer/test.md",
        "fetched_at": "2026-10-01T12:00:00Z",
        "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    payload[
        {"hash": "content_sha256", "date": "fetched_at", "url": "source_url"}[mutation]
    ] = {"hash": "0" * 64, "date": "2026-10-01T12:00:00", "url": "file:///tmp/private"}[
        mutation
    ]
    target.with_suffix(".json").write_text(json.dumps(payload))
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.FAIL
    assert load_rows(db, Talk) == []


def test_duplicate_filename_and_canonical_url_variants_create_one_talk(tmp_path):
    cache, target, db = setup_input(tmp_path)
    shutil.copyfile(target, cache / "identical.md")
    (cache / "variant.md").write_text(
        target.read_text().replace(
            "https://www.ai.engineer/fixtures/talk-01",
            "http://ai.engineer/fixtures/talk-01/?utm_source=fixture#part",
        )
    )
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    assert result.counts["duplicates"] == 2
    assert len(load_rows(db, Talk)) == 1
    for path in cache.glob("*.md"):
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot = db.with_suffix(db.suffix + ".objects") / f"{content_hash}.txt"
        assert snapshot.read_bytes() == path.read_bytes()
    assert_source_versions_resolve(db)


def test_video_url_variants_deduplicate(tmp_path):
    cache, target, db = setup_input(tmp_path)
    target.write_text(
        target.read_text().replace(
            "https://www.ai.engineer/fixtures/talk-01",
            "https://youtu.be/abcdefghijk?t=3",
        )
    )
    (cache / "variant.md").write_text(
        target.read_text().replace(
            "https://youtu.be/abcdefghijk?t=3",
            "https://youtube.com/watch?v=abcdefghijk&feature=share",
        )
    )
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    (talk,) = load_rows(db, Talk)
    assert talk.youtube_video_id == "abcdefghijk"


def test_undecided_duplicate_blocks_all_writes(tmp_path):
    cache, target, db = setup_input(tmp_path)
    # A shared canonical identity with conflicting transcript revisions is
    # undecided. A repeated title/speaker alone is not an identity match.
    (cache / "possibly-same.md").write_text(
        target.read_text().replace("queue", "revised queue")
    )
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("undecided duplicate" in error for error in result.errors)
    assert load_rows(db, Talk) == []
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []


def test_distinct_source_with_same_title_and_speaker_is_independent(tmp_path):
    cache, target, db = setup_input(tmp_path)
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    target.write_text(
        target.read_text().replace("/fixtures/talk-01", "/alternate/talk-01")
    )
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    talks = load_rows(db, Talk)
    assert len(talks) == len({talk.id for talk in talks}) == 2
    assert result.counts["talks"] == 1
    assert_source_versions_resolve(db)


def test_same_source_two_different_transcripts_is_undecided(tmp_path):
    cache, target, db = setup_input(tmp_path)
    (cache / "revision.md").write_text(
        target.read_text().replace("queue", "revised queue")
    )
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.BLOCKED
    assert load_rows(db, Talk) == []


@pytest.mark.parametrize("missing", ["speaker", "source_url", "title"])
def test_missing_attribution_blocks_stage(tmp_path, missing):
    cache, target, db = setup_input(tmp_path)
    target.write_text(
        "\n".join(
            line
            for line in target.read_text().splitlines()
            if not line.startswith(missing + ":")
        )
    )
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any(f"missing {missing}" in error for error in result.errors)
    assert load_rows(db, Talk) == []


def test_ambiguous_speaker_blocks_stage(tmp_path):
    cache, target, db = setup_input(tmp_path)
    target.write_text(
        target.read_text().replace(
            'speaker: "Fixture Speaker 01"', 'speaker: ["One", "Two"]'
        )
    )
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.BLOCKED
    assert load_rows(db, Talk) == []


def test_manifest_supplies_metadata_and_conflicts_fail(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    target = cache / "local.md"
    target.write_text("[00:01] A synthetic manifest-only transcript.\n")
    manifest = cache / "manifest.jsonl"
    row = {
        "path": "local.md",
        "title": "Synthetic manifest example",
        "speaker": "Ada Example",
        "source_url": "https://www.ai.engineer/manifest/example",
        "company": None,
    }
    manifest.write_text(json.dumps(row) + "\n")
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path, manifest)
    assert result.outcome == StageOutcome.PASS
    assert str(manifest) in result.input_hashes
    (talk,) = load_rows(db, Talk)
    assert talk.title == row["title"] and talk.speaker == row["speaker"]
    target.write_text('---\nspeaker: "Different Person"\n---\n\n' + target.read_text())
    result = ingest_transcripts(cache, db, tmp_path, manifest)
    assert result.outcome == StageOutcome.FAIL
    assert any("contradictory speaker" in error for error in result.errors)
    assert load_rows(db, Talk) == [talk]


@pytest.mark.parametrize("kind", ["absent", "empty", "manifest"])
def test_missing_inputs_record_blocked_receipt(tmp_path, kind):
    cache = tmp_path / "cache"
    if kind != "absent":
        cache.mkdir()
    manifest = cache / "missing.jsonl" if kind == "manifest" else None
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path, manifest)
    assert result.outcome == StageOutcome.BLOCKED
    assert json.loads(result.receipt_path.read_text())["outcome"] == "BLOCKED"
    with Store(db) as store:
        assert (
            store.connection.execute("SELECT outcome FROM stage_runs").fetchone()[0]
            == "BLOCKED"
        )


@pytest.mark.parametrize(
    "kind", ["outside", "file_symlink", "dir_symlink", "manifest_escape"]
)
def test_input_containment_failures_record_fail(tmp_path, kind):
    cache, target, db = setup_input(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copyfile(target, outside / "outside.md")
    manifest = None
    source = cache
    if kind == "outside":
        source = outside
    elif kind == "file_symlink":
        (cache / "escape.md").symlink_to(outside / "outside.md")
    elif kind == "dir_symlink":
        (cache / "escape").symlink_to(outside, target_is_directory=True)
    else:
        manifest = cache / "manifest.jsonl"
        manifest.write_text(json.dumps({"path": "../outside/outside.md"}) + "\n")
    result = ingest_transcripts(source, db, cache, manifest)
    assert result.outcome == StageOutcome.FAIL
    assert load_rows(db, Talk) == []


@pytest.mark.parametrize("kind", ["database", "receipt"])
def test_output_symlinks_rejected_before_writes(tmp_path, kind):
    cache, _, db = setup_input(tmp_path)
    outside = tmp_path / "untouched"
    outside.write_text("original")
    path = db if kind == "database" else db.with_suffix(db.suffix + ".ingest.json")
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        ingest_transcripts(cache, db, tmp_path)
    assert outside.read_text() == "original"


@pytest.mark.parametrize("stamp", ["00:65", "01:65:10", "00:10 --> 00:05"])
def test_invalid_timestamps_fail_without_inventing_timing(tmp_path, stamp):
    cache, target, db = setup_input(tmp_path)
    target.write_text(target.read_text().replace("00:01.250 --> 00:03.500", stamp))
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.FAIL
    assert load_rows(db, Segment) == []


def test_crlf_unicode_offsets_are_character_offsets():
    raw = "# Café synthetic\r\nSpeaker: Ada\r\nSource: https://ai.engineer/example\r\n\r\n[00:01.5] Café 🧪 source.\r\n".encode()
    parsed = parse_markdown(raw)
    (segment,) = parsed.segments
    assert parsed.text[segment.start : segment.end] == "[00:01.5] Café 🧪 source.\r\n"
    assert segment.start_seconds == Decimal("1.5")


def test_canonical_url_keeps_identity_queries():
    assert (
        canonical_url("HTTP://WWW.AI.ENGINEER/talk/?id=1&utm_source=x#part")
        == "https://ai.engineer/talk?id=1"
    )
    assert canonical_url("https://ai.engineer/talk?id=1") != canonical_url(
        "https://ai.engineer/talk?id=2"
    )


@pytest.mark.parametrize("parameter", ["t", "start", "feature", "si", "time_continue"])
def test_generic_source_identity_parameters_are_not_youtube_tracking(parameter):
    assert canonical_url(f"https://ai.engineer/talk?{parameter}=a") != canonical_url(
        f"https://ai.engineer/talk?{parameter}=b"
    )


def test_explicit_video_id_links_alternate_source_urls_and_persists_aliases(tmp_path):
    cache, target, db = setup_input(tmp_path)
    target.write_text(
        target.read_text().replace(
            "company: null", 'company: null\nyoutube_video_id: "abcdefghijk"'
        )
    )
    alternate = cache / "alternate.md"
    alternate.write_text(
        target.read_text().replace("/fixtures/talk-01", "/other-source/one")
    )
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    (talk,) = load_rows(db, Talk)
    aliases = json.loads(talk.ingest_provenance["identity_aliases"])
    assert "url:https://ai.engineer/other-source/one" in aliases
    assert "url:https://ai.engineer/fixtures/talk-01" in aliases
    assert result.counts["duplicates"] == 1
    assert ingest_transcripts(cache, db, tmp_path).counts["unchanged"] == 1
    target.unlink()
    alternate.write_text(
        alternate.read_text().replace('youtube_video_id: "abcdefghijk"\n', "")
    )
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    assert {row.id for row in load_rows(db, Talk)} == {talk.id}


def test_different_youtube_talks_do_not_match_by_watch_path(tmp_path):
    cache, target, db = setup_input(tmp_path)
    target.write_text(
        target.read_text().replace(
            "https://www.ai.engineer/fixtures/talk-01",
            "https://youtube.com/watch?v=abcdefghijk",
        )
    )
    other = cache / "other.md"
    other.write_text(
        (FIXTURES / "talk-02.md")
        .read_text()
        .replace(
            "https://www.ai.engineer/fixtures/talk-02",
            "https://youtube.com/watch?v=lmnopqrstuv",
        )
    )
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    assert len(load_rows(db, Talk)) == 2


def test_transcript_hash_duplicate_with_unlinked_sources_is_blocked(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    raw = "[00:01] Identical synthetic source text.\n"
    for name in ("one.md", "two.md"):
        (cache / name).write_text(raw)
    manifest = cache / "manifest.jsonl"
    rows = [
        {
            "path": name,
            "title": "Synthetic same text",
            "speaker": "Ada Example",
            "source_url": f"https://ai.engineer/{name}",
        }
        for name in ("one.md", "two.md")
    ]
    manifest.write_text("\n".join(json.dumps(row) for row in rows))
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path, manifest)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("duplicate source attribution" in error for error in result.errors)
    assert load_rows(db, Talk) == []


@pytest.mark.parametrize("kind", ["input_db", "manifest_receipt", "sidecar_receipt"])
def test_output_input_collisions_do_not_overwrite_evidence(tmp_path, kind):
    cache, target, db = setup_input(tmp_path)
    manifest = None
    if kind == "input_db":
        db = target
        victim = target
    elif kind == "manifest_receipt":
        db = cache / "output"
        manifest = cache / "output.ingest.json"
        manifest.write_text(json.dumps({"path": target.name}) + "\n")
        victim = manifest
    else:
        db = cache / "talk-01"
        # Change the input name so its fetch sidecar is the selected receipt.
        target = target.rename(cache / "talk-01.ingest.md")
        victim = target.with_suffix(".json")
        victim.write_text(
            json.dumps(
                {
                    "source_url": "https://ai.engineer/source.md",
                    "fetched_at": "2026-10-01T12:00:00Z",
                    "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )
        )
    original = victim.read_bytes()
    with pytest.raises(ValueError, match="collides"):
        ingest_transcripts(cache, db, tmp_path, manifest)
    assert victim.read_bytes() == original


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_sqlite_auxiliary_symlinks_are_rejected(tmp_path, suffix):
    cache, _, db = setup_input(tmp_path)
    victim = tmp_path / "untouched"
    victim.write_text("original")
    Path(str(db) + suffix).symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        ingest_transcripts(cache, db, tmp_path)
    assert victim.read_text() == "original"
    assert not db.exists()


def test_bold_case_insensitive_metadata_and_transcript_heading():
    source = "# Synthetic example\n**Speaker:** Ada Example\n**Source:** https://ai.engineer/example\n\n# Transcript\n[00:01] Synthetic body.\n"
    parsed = parse_markdown(source.encode())
    assert parsed.metadata["speaker"] == "Ada Example"
    assert parsed.metadata["source_url"] == "https://ai.engineer/example"
    assert not parsed.issues
    assert len(parsed.segments) == 1
    parsed = parse_markdown(
        b"---\nTitle: Synthetic example\nSpeaker: Ada Example\nSource URL: https://ai.engineer/example\n---\nSynthetic body.\n"
    )
    assert parsed.metadata["title"] == "Synthetic example"
    assert parsed.metadata["speaker"] == "Ada Example"
    assert parsed.metadata["source_url"] == "https://ai.engineer/example"


def test_immutable_fetch_object_is_retained_as_transcript_path(tmp_path):
    cache, target, db = setup_input(tmp_path)
    content_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    objects = cache / ".objects"
    objects.mkdir()
    blob = objects / f"{content_hash}.txt"
    blob.write_bytes(target.read_bytes())
    target.with_suffix(".json").write_text(
        json.dumps(
            {
                "source_url": "https://ai.engineer/source.md",
                "fetched_at": "2026-10-01T12:00:00Z",
                "content_sha256": content_hash,
            }
        )
    )
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    (talk,) = load_rows(db, Talk)
    assert talk.transcript_path == str(blob)
    target.write_text("Changed active input")
    assert (
        hashlib.sha256(Path(talk.transcript_path).read_bytes()).hexdigest()
        == talk.transcript_sha256
    )


def test_changed_video_identity_on_existing_source_fails(tmp_path):
    cache, target, db = setup_input(tmp_path)
    target.write_text(
        target.read_text().replace(
            "company: null", 'company: null\nyoutube_video_id: "abcdefghijk"'
        )
    )
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    (original,) = load_rows(db, Talk)
    target.write_text(target.read_text().replace("abcdefghijk", "lmnopqrstuv"))
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.FAIL
    assert any(
        "contradictory duplicate video identity" in error for error in result.errors
    )
    assert load_rows(db, Talk) == [original]


def test_new_alias_bridging_two_persisted_talks_is_blocked(tmp_path):
    cache, target, db = setup_input(tmp_path)
    other = cache / "other.md"
    other.write_text(
        (FIXTURES / "talk-02.md")
        .read_text()
        .replace("company: null", 'company: null\nyoutube_video_id: "abcdefghijk"')
    )
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    originals = load_rows(db, Talk)
    other.unlink()
    target.write_text(
        target.read_text().replace(
            "company: null", 'company: null\nyoutube_video_id: "abcdefghijk"'
        )
    )
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("bridges existing identities" in error for error in result.errors)
    assert load_rows(db, Talk) == originals


URL_COLLISIONS = [
    (
        "https://ai.engineer/talk?session=first",
        "https://ai.engineer/talk?session=second",
    ),
    ("https://ai.engineer/talks/a%2Fb", "https://ai.engineer/talks/a/b"),
    ("https://ai.engineer/talks/a%3Fb", "https://ai.engineer/talks/a?b"),
    ("https://ai.engineer/talks/a%23b", "https://ai.engineer/talks/a#b"),
    ("https://ai.engineer:80/talks/a", "https://ai.engineer/talks/a"),
    ("http://ai.engineer:443/talks/a", "https://ai.engineer/talks/a"),
    ("https://ai.engineer:8443/talks/a", "https://ai.engineer/talks/a"),
    (
        "https://youtube.com:80/watch?v=abcdefghijk",
        "https://youtube.com/watch?v=abcdefghijk",
    ),
]


@pytest.mark.parametrize("left,right", URL_COLLISIONS)
def test_canonical_url_preserves_distinct_endpoint_identity(left, right):
    assert canonical_url(left) != canonical_url(right)
    assert canonical_url(canonical_url(left)) == canonical_url(left)
    assert canonical_url(canonical_url(right)) == canonical_url(right)


@pytest.mark.parametrize(
    "source,expected",
    [
        ("https://ai.engineer/a%2fb", "https://ai.engineer/a%2Fb"),
        ("https://ai.engineer/%7e%41%2e%5f%2d", "https://ai.engineer/~A._-"),
        ("https://ai.engineer:443/a", "https://ai.engineer/a"),
        ("http://ai.engineer:80/a", "https://ai.engineer/a"),
        ("https://ai.engineer/a%252Fb", "https://ai.engineer/a%252Fb"),
    ],
)
def test_canonical_url_normalizes_only_safe_path_escapes_and_default_ports(
    source, expected
):
    assert canonical_url(source) == expected


@pytest.mark.parametrize("left,right", URL_COLLISIONS)
@pytest.mark.parametrize("separate_runs", [False, True])
def test_distinct_url_identities_create_independent_talks_without_silent_version(
    tmp_path, left, right, separate_runs
):
    cache, target, db = setup_input(tmp_path)
    original_source = "https://www.ai.engineer/fixtures/talk-01"
    first = target.read_text().replace(original_source, left)
    second = first.replace(left, right).replace("queue", "revised queue")
    target.write_text(first)
    if separate_runs:
        assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
        originals = load_rows(db, Talk)
        segments = load_rows(db, Segment)
        target.write_text(second)
    else:
        originals, segments = [], []
        (cache / "other.md").write_text(second)
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    talks = load_rows(db, Talk)
    assert len(talks) == len({talk.id for talk in talks}) == 2
    assert all(original in talks for original in originals)
    assert all(segment in load_rows(db, Segment) for segment in segments)
    receipt = json.loads(result.receipt_path.read_text())
    expected_talks = 1 if separate_runs else 2
    assert receipt["counts"]["talks"] == expected_talks
    assert receipt["counts"]["segments"] == expected_talks * 2
    assert len(receipt["output_refs"]) == expected_talks * 3
    assert_source_versions_resolve(db)


@pytest.mark.parametrize("left,right", [URL_COLLISIONS[1], URL_COLLISIONS[4]])
def test_legacy_collapsed_alias_is_blocked_instead_of_reused(tmp_path, left, right):
    cache, target, db = setup_input(tmp_path)
    source = target.read_text().replace(
        "https://www.ai.engineer/fixtures/talk-01", left
    )
    target.write_text(source)
    parsed = parse_markdown(target.read_bytes())
    legacy = Talk(
        id="talk-legacy-collision",
        version="legacy-v1",
        title=parsed.metadata["title"],
        speaker=parsed.metadata["speaker"],
        source_url=left,
        transcript_path=str(target),
        transcript_sha256=parsed.sha256,
        ingest_provenance={
            "config_version": "c03-ingest-v1",
            "identity_aliases": json.dumps([f"url:{right}", f"hash:{parsed.sha256}"]),
        },
    )
    with Store(db) as store:
        with store.stage_transaction(
            Stage.INGEST, config_version="c03-ingest-v1", input_hashes={}
        ) as attempt:
            store.put(legacy)
            attempt.finish(StageOutcome.PASS, output_refs=[legacy.ref])
    target.write_text(source.replace(left, right).replace("queue", "new queue"))
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("unverified legacy URL alias" in error for error in result.errors)
    assert load_rows(db, Talk) == [legacy]
    assert load_rows(db, Segment) == []
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []


@pytest.mark.parametrize("field", ["source_url", "speaker"])
def test_unchanged_source_cannot_silently_change_manifest_attribution(tmp_path, field):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "source.md").write_text("[00:01] Synthetic unchanged source.\n")
    row = {
        "path": "source.md",
        "title": "Synthetic unchanged source",
        "speaker": "Ada Example",
        "source_url": "https://ai.engineer/one",
    }
    manifest = cache / "manifest.jsonl"
    manifest.write_text(json.dumps(row))
    db = tmp_path / "private.db"
    assert (
        ingest_transcripts(cache, db, tmp_path, manifest).outcome == StageOutcome.PASS
    )
    original = load_rows(db, Talk)
    segments = load_rows(db, Segment)
    row[field] = (
        "https://ai.engineer/unlinked" if field == "source_url" else "Grace Example"
    )
    manifest.write_text(json.dumps(row))
    result = ingest_transcripts(cache, db, tmp_path, manifest)
    assert result.outcome == (
        StageOutcome.BLOCKED if field == "source_url" else StageOutcome.FAIL
    )
    assert load_rows(db, Talk) == original
    assert load_rows(db, Segment) == segments
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []


@pytest.mark.parametrize("left,right", [URL_COLLISIONS[0], URL_COLLISIONS[3]])
def test_distinct_url_endpoints_with_distinct_talk_metadata_are_independent(
    tmp_path, left, right
):
    cache, target, db = setup_input(tmp_path)
    target.write_text(
        target.read_text().replace("https://www.ai.engineer/fixtures/talk-01", left)
    )
    (cache / "second.md").write_text(
        (FIXTURES / "talk-02.md")
        .read_text()
        .replace("https://www.ai.engineer/fixtures/talk-02", right)
    )
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    assert len({talk.id for talk in load_rows(db, Talk)}) == 2


def test_duplicate_local_copy_does_not_erase_changed_fetch_provenance(tmp_path):
    cache, target, db = setup_input(tmp_path)
    fetched = cache / "z-fetched.md"
    fetched.write_bytes(target.read_bytes())
    provenance = {
        "source_url": "https://ai.engineer/source.md",
        "fetched_at": "2026-11-01T00:00:00Z",
        "content_sha256": hashlib.sha256(fetched.read_bytes()).hexdigest(),
    }
    fetched.with_suffix(".json").write_text(json.dumps(provenance))
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    (original,) = load_rows(db, Talk)
    assert original.fetch_provenance is not None
    assert original.fetch_provenance.source_url == provenance["source_url"]
    provenance["fetched_at"] = "2026-11-02T00:00:00Z"
    fetched.with_suffix(".json").write_text(json.dumps(provenance))
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    rows = load_rows(db, Talk)
    assert len(rows) == 2
    assert {talk.id for talk in rows} == {original.id}
    assert len({talk.fetch_provenance.fetched_at for talk in rows}) == 2
    assert ingest_transcripts(cache, db, tmp_path).counts["unchanged"] == 1


def test_conflicting_duplicate_fetch_provenance_blocks_without_rows(tmp_path):
    cache, target, db = setup_input(tmp_path)
    duplicate = cache / "duplicate.md"
    duplicate.write_bytes(target.read_bytes())
    for day, path in enumerate((target, duplicate), 1):
        path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "source_url": "https://ai.engineer/source.md",
                    "fetched_at": f"2026-11-0{day}T00:00:00Z",
                    "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        )
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("duplicate fetch provenance" in error for error in result.errors)
    assert load_rows(db, Talk) == []
    assert load_rows(db, Segment) == []
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []


@pytest.mark.parametrize("history_count", [1, 2])
def test_review_followup_local_duplicate_preserves_or_blocks_history(
    tmp_path, history_count
):
    cache, target, db = setup_input(tmp_path)
    sidecar = target.with_suffix(".json")
    for day in range(1, history_count + 1):
        sidecar.write_text(
            json.dumps(
                {
                    "source_url": "https://ai.engineer/source.md",
                    "fetched_at": f"2026-11-0{day}T00:00:00Z",
                    "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )
        )
        assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    before_talks = load_rows(db, Talk)
    before_segments = load_rows(db, Segment)
    sidecar.rename(tmp_path / "saved-sidecar.json")
    target.rename(cache / "local-copy.md")
    result = ingest_transcripts(cache, db, tmp_path)
    assert load_rows(db, Talk) == before_talks
    assert load_rows(db, Segment) == before_segments
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []
    if history_count == 1:
        assert result.outcome == StageOutcome.PASS
        assert result.counts["unchanged"] == 1
    else:
        assert result.outcome == StageOutcome.BLOCKED
        assert any("historical fetch provenance" in error for error in result.errors)


def test_review_followup_changed_bytes_do_not_inherit_fetch_history(tmp_path):
    cache, target, db = setup_input(tmp_path)
    sidecar = target.with_suffix(".json")
    sidecar.write_text(
        json.dumps(
            {
                "source_url": "https://ai.engineer/source.md",
                "fetched_at": "2026-11-01T00:00:00Z",
                "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
    )
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    (original,) = load_rows(db, Talk)
    sidecar.rename(tmp_path / "saved-sidecar.json")
    target.write_text(target.read_text() + "\nSynthetic changed revision.\n")
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    rows = load_rows(db, Talk)
    assert len(rows) == 2
    assert original in rows
    revised = next(talk for talk in rows if talk.version != original.version)
    assert revised.transcript_sha256 != original.transcript_sha256
    assert revised.fetch_provenance is None
