"""Documented preamble labels preserve identity and source evidence."""

import pytest

from skillware.ingest.parser import parse_markdown
from skillware.ingest.pipeline import ingest_transcripts
from skillware.schema import Segment, StageOutcome, Talk
from skillware.store import Store


@pytest.mark.parametrize("source_label", ["Source", "Source URL"])
@pytest.mark.parametrize("video_first", [False, True])
def test_spaced_preamble_identity_survives_ingest(tmp_path, source_label, video_first):
    headers = ["Speaker: Ada Example", f"{source_label}: https://example.test/talk"]
    headers.insert(0 if video_first else len(headers), "YouTube Video ID: abcdefghijk")
    text = (
        "# Synthetic preamble\n" + "\n".join(headers) + "\n\n[00:01] Synthetic body.\n"
    )
    parsed = parse_markdown(text.encode())
    assert parsed.issues == []
    assert parsed.metadata["youtube_video_id"] == "abcdefghijk"
    assert parsed.metadata["source_url"] == "https://example.test/talk"
    assert parsed.metadata["speaker"] == "Ada Example"
    assert len(parsed.segments) == 1
    assert parsed.segments[0].speaker is None

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "talk.md").write_text(text)
    db = tmp_path / "private.sqlite3"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM talks").fetchall()
        talk = Talk.model_validate_json(row[0])
        (row,) = store.connection.execute("SELECT payload FROM segments").fetchall()
        segment = Segment.model_validate_json(row[0])
    assert talk.youtube_video_id == "abcdefghijk"
    assert talk.source_url == "https://example.test/talk"
    assert segment.speaker_override is None
    assert segment.text == text[segment.source_start : segment.source_end]
    assert "Synthetic body." in segment.text
    assert "YouTube Video ID" not in segment.text
    (cache / "talk.md").rename(cache / "renamed.md")
    repeated = ingest_transcripts(cache, db, tmp_path)
    assert repeated.outcome == StageOutcome.PASS
    assert repeated.counts["unchanged"] == 1
    assert repeated.counts["talks"] == repeated.counts["segments"] == 0


@pytest.mark.parametrize(
    "first,second,key",
    [
        (
            "youtube_video_id: abcdefghijk",
            "YouTube Video ID: lmnopqrstuv",
            "youtube_video_id",
        ),
        (
            "Source: https://example.test/one",
            "Source URL: https://example.test/two",
            "source_url",
        ),
    ],
)
def test_spaced_preamble_conflicts_fail(first, second, key):
    source = f"# Synthetic\nSpeaker: Ada Example\n{first}\n{second}\n\nBody.\n"
    parsed = parse_markdown(source.encode())
    assert any(
        issue.outcome == StageOutcome.FAIL and f"contradictory {key}" in issue.message
        for issue in parsed.issues
    )
