"""Malformed metadata and unresolved source labels cannot yield passing rows."""

import json

import pytest

from skillware.ingest.parser import canonical_url, parse_markdown, validate_metadata
from skillware.ingest.pipeline import ingest_transcripts
from skillware.schema import Segment, StageOutcome
from skillware.store import Store


def _transcript(*, speaker="Ada Example", body="[00:01] Synthetic body."):
    return (
        f"---\ntitle: Synthetic attribution fixture\nspeaker: {speaker}\n"
        f"source_url: https://ai.engineer/synthetic-attribution\n---\n{body}\n"
    )


@pytest.mark.parametrize(
    "label",
    [
        "Unknown:",
        "**Unknown:**",
        "[Speaker: Unknown]",
        "[Speaker: Alice or Bob]",
        "**Alice and Bob:**",
        "[Speaker: ]",
    ],
)
def test_ambiguous_segment_labels_block_receipt_and_all_rows(tmp_path, label):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "talk.md").write_text(
        _transcript(body=f"[00:01] {label} Synthetic attribution fixture.")
    )
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("ambiguous segment speaker" in error for error in result.errors)
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["outcome"] == "BLOCKED"
    assert receipt["output_refs"] == []
    with Store(db) as store:
        assert store.connection.execute("SELECT count(*) FROM talks").fetchone()[0] == 0
        assert (
            store.connection.execute("SELECT count(*) FROM segments").fetchone()[0] == 0
        )


@pytest.mark.parametrize("prefix", ["", "[00:01] "])
@pytest.mark.parametrize(
    "name",
    [
        "Alice or Bob",
        "Alice and Bob",
        "unknown",
        "anonymous",
        "panel",
        "multiple",
        "tbd",
    ],
)
def test_review_attribution_ambiguous_plain_labels_block(tmp_path, prefix, name):
    source = _transcript(body=f"{prefix}{name}: Unresolved contribution.")
    parsed = parse_markdown(source.encode())
    validate_metadata(parsed)
    assert parsed.segments[0].speaker == name
    assert parsed.segments[0].evidence == f"{name}:"
    assert any("ambiguous segment speaker" in issue.message for issue in parsed.issues)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["outcome"] == "BLOCKED"
    assert receipt["output_refs"] == []
    with Store(db) as store:
        for table in ("talks", "segments"):
            assert (
                store.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                == 0
            )


@pytest.mark.parametrize(
    "label",
    [
        "Note",
        "Example",
        "Warning",
        "Tip",
        "Source",
        "Title",
        "the result",
        "The main point",
    ],
)
def test_review_attribution_prose_controls_keep_headline_speaker(tmp_path, label):
    source = _transcript(body=f"[00:01] {label}: Ordinary transcript prose.")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM segments").fetchall()
    segment = Segment.model_validate_json(row[0])
    assert segment.speaker_override is None
    assert segment.speaker_override_evidence is None
    assert segment.text == source[segment.source_start : segment.source_end]


@pytest.mark.parametrize("prefix", ["", "[00:01] "])
def test_review_attribution_unambiguous_plain_label_keeps_override(tmp_path, prefix):
    source = _transcript(body=f"{prefix}Alice: An attributed contribution.")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM segments").fetchall()
    segment = Segment.model_validate_json(row[0])
    assert segment.speaker_override == "Alice"
    assert segment.speaker_override_evidence == "Alice:"
    assert segment.text == source[segment.source_start : segment.source_end]


@pytest.mark.parametrize(
    "scalar",
    [
        "&person Ada Example",
        "*person",
        "!!str Ada Example",
        "|",
        ">-",
        "[Ada, Grace]",
        "{name: Ada}",
        '"Ada Example',
        "'Ada Example",
        '"Ada" trailing',
        "'Ada' trailing",
        "'Ada'Example'",
    ],
)
def test_unsupported_or_unterminated_scalar_blocks_ingest(tmp_path, scalar):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "talk.md").write_text(_transcript(speaker=scalar))
    result = ingest_transcripts(cache, tmp_path / "private.db", tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("ambiguous speaker" in error for error in result.errors)
    assert result.counts["talks"] == result.counts["segments"] == 0


@pytest.mark.parametrize(
    ("scalar", "expected"),
    [
        ("Ada Example", "Ada Example"),
        ('"Ada Example"', "Ada Example"),
        ("'Ada Example'", "Ada Example"),
        ("'Ada O''Brien'", "Ada O'Brien"),
        ('"&person Ada Example"', "&person Ada Example"),
        ("'*person'", "*person"),
    ],
)
def test_supported_plain_and_quoted_scalar_literals_keep_source_value(scalar, expected):
    parsed = parse_markdown(_transcript(speaker=scalar).encode())
    validate_metadata(parsed)
    assert parsed.metadata["speaker"] == expected
    assert parsed.issues == []


@pytest.mark.parametrize("label", ["**Optimization**", "**Ada Example**"])
def test_bold_prose_without_colon_never_becomes_speaker_override(tmp_path, label):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = _transcript(body=f"[00:01] {label} improves this synthetic example.")
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM segments").fetchall()
    segment = Segment.model_validate_json(row[0])
    assert segment.speaker_override is None
    assert segment.speaker_override_evidence is None
    assert segment.text == source[segment.source_start : segment.source_end]
    assert label in segment.text


@pytest.mark.parametrize(
    ("label", "name"),
    [
        ("**Grace Example:**", "Grace Example"),
        ("**Grace Example**:", "Grace Example"),
        ("José García:", "José García"),
        ("Jose\u0301 Garci\u0301a:", "Jose\u0301 Garci\u0301a"),
        ("Élodie Noël:", "Élodie Noël"),
        ("Łukasz Żółć:", "Łukasz Żółć"),
        ("Zoë O’Neil:", "Zoë O’Neil"),
        ("李 明:", "李 明"),
    ],
)
def test_explicit_unicode_or_bold_labels_persist_source_backed_override(
    tmp_path, label, name
):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = _transcript(body=f"[00:01] {label} A synthetic speaker contribution.")
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM segments").fetchall()
    segment = Segment.model_validate_json(row[0])
    assert segment.speaker_override == name
    assert segment.speaker_override_evidence == label
    assert label in segment.text
    assert segment.text == source[segment.source_start : segment.source_end]


@pytest.mark.parametrize("value", ["%FF", "%FE", "%C3%28", "%", "%2", "%GG"])
def test_invalid_query_encoding_fails_without_lossy_source_identity(tmp_path, value):
    url = f"https://ai.engineer/synthetic-attribution?session={value}"
    with pytest.raises(ValueError, match="invalid query encoding"):
        canonical_url(url)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "talk.md").write_text(
        _transcript().replace("https://ai.engineer/synthetic-attribution", url)
    )
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.FAIL
    assert any("invalid query encoding" in error for error in result.errors)
    assert result.counts["talks"] == result.counts["segments"] == 0
    with Store(db) as store:
        assert store.connection.execute("SELECT count(*) FROM talks").fetchone()[0] == 0


def test_identity_query_values_keep_repeated_parameter_order():
    first = "https://ai.engineer/talk?session=first&session=second&lang=en"
    second = "https://ai.engineer/talk?lang=en&session=second&session=first"
    assert canonical_url(first) != canonical_url(second)
    assert canonical_url(first) == canonical_url(
        "https://ai.engineer/talk?lang=en&session=first&session=second"
    )
    valid_unicode = "https://ai.engineer/talk?session=%EF%BF%BD"
    assert canonical_url(valid_unicode) == valid_unicode


@pytest.mark.parametrize("prefix", ["", "[00:01] "])
@pytest.mark.parametrize(
    "name",
    [
        "Tim de Vries",
        "Jean de la Fontaine",
        "Ludwig van Beethoven",
        "Anne-Marie d'Aboville",
        "Anne-Marie d’Aboville",
        "van der Waals",
        "María de los Ángeles López",
        "José Luis de la Vega García",
    ],
)
def test_review_particles_preserve_explicit_name_and_offsets(tmp_path, prefix, name):
    source = _transcript(body=f"{prefix}{name}: A source-backed contribution.")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM segments").fetchall()
    segment = Segment.model_validate_json(row[0])
    assert segment.speaker_override == name
    assert segment.speaker_override_evidence == f"{name}:"
    assert segment.text == source[segment.source_start : segment.source_end]


@pytest.mark.parametrize("prefix", ["", "[00:01] "])
@pytest.mark.parametrize(
    "label",
    ["This is Important", "The State of AI", "Tim de vries", "Anne-Marie x’Aboville"],
)
def test_review_particles_uncertain_mixed_case_blocks_instead_of_guessing(
    tmp_path, prefix, label
):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = _transcript(body=f"{prefix}{label}: Unresolved label.")
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("unresolved plain speaker" in error for error in result.errors)
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []
    with Store(db) as store:
        assert store.connection.execute("SELECT count(*) FROM talks").fetchone()[0] == 0
        assert (
            store.connection.execute("SELECT count(*) FROM segments").fetchone()[0] == 0
        )


@pytest.mark.parametrize("prefix", ["", "[00:01] "])
@pytest.mark.parametrize(
    "label",
    [
        "Alice / Bob:",
        "Alice/Bob:",
        "Alice & Bob:",
        "Alice&Bob:",
        "Alice;Bob:",
        "n/a:",
        "?:",
        "Alice / Bob and Carol:",
        "**Alice&Bob:**",
        "[Speaker: Alice&Bob]",
        "**Alice;Bob:**",
        "[Speaker: Alice/Bob]",
    ],
)
def test_review_boundary_ambiguous_prefix_cannot_fall_back(tmp_path, prefix, label):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = _transcript(body=f"{prefix}{label} Unresolved speaker contribution.")
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("ambiguous segment speaker" in error for error in result.errors)
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["outcome"] == "BLOCKED" and receipt["output_refs"] == []
    with Store(db) as store:
        assert store.connection.execute("SELECT count(*) FROM talks").fetchone()[0] == 0
        assert (
            store.connection.execute("SELECT count(*) FROM segments").fetchone()[0] == 0
        )


@pytest.mark.parametrize("prefix", ["", "[00:01] "])
@pytest.mark.parametrize(
    "body",
    [
        "Note: choose A/B.",
        "The main point: A & B.",
        "https://example.test/A/B",
        "Version v1.2: safe ordinary prose.",
    ],
)
def test_review_boundary_body_separators_are_not_speaker_labels(tmp_path, prefix, body):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = _transcript(body=prefix + body)
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM segments").fetchall()
    segment = Segment.model_validate_json(row[0])
    assert segment.speaker_override is None
    assert segment.text == source[segment.source_start : segment.source_end]


@pytest.mark.parametrize("prefix", ["", "[00:01] "])
@pytest.mark.parametrize(
    "body",
    [
        "The cost/benefit ratio: 2 to 1.",
        "The input/output ratio: two outputs per input.",
        "cost/benefit: a useful tradeoff.",
        "The design & implementation details: ordinary prose.",
        "The setup; teardown steps: ordinary prose.",
        "The pros and cons: an ordinary discussion.",
        "The advantages or disadvantages: an ordinary discussion.",
        "pros and cons: ordinary prose.",
        "and/or: alternatives in prose.",
        "Note/Example: ordinary prose.",
    ],
)
def test_review_prose_prefix_keeps_text_without_speaker_override(
    tmp_path, prefix, body
):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = _transcript(body=prefix + body)
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.PASS
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM segments").fetchall()
    segment = Segment.model_validate_json(row[0])
    assert segment.speaker_override is None
    assert segment.speaker_override_evidence is None
    assert segment.text == source[segment.source_start : segment.source_end]
    assert body in segment.text


@pytest.mark.parametrize("prefix", ["", "[00:01] "])
@pytest.mark.parametrize(
    "label",
    [
        "n/a :",
        "? :",
        "unknown :",
        "Alice/bob:",
        "Alice & bob:",
        "Alice and bob:",
        "unknown / anonymous:",
        "Tim de Vries/Bob:",
        "Tim de vries / Bob:",
        "**The cost/benefit ratio:**",
        "[Speaker: The cost/benefit ratio]",
    ],
)
def test_review_prose_prefix_retains_uncertain_or_explicit_attribution_blocks(
    tmp_path, prefix, label
):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "talk.md").write_text(
        _transcript(body=prefix + label + " Unresolved attribution.")
    )
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("ambiguous segment speaker" in message for message in result.errors)
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []
    with Store(db) as store:
        assert store.connection.execute("SELECT count(*) FROM talks").fetchone()[0] == 0
        assert (
            store.connection.execute("SELECT count(*) FROM segments").fetchone()[0] == 0
        )


@pytest.mark.parametrize("prefix", ["", "[00:01] "])
@pytest.mark.parametrize(
    "label",
    [
        "Alice and/or bob:",
        "Alice and / or bob:",
        "Alice or/and bob:",
        "n/a or bob:",
        "n/a / bob:",
        "bob/n/a:",
        "N/A&bob:",
        "Or/bob:",
    ],
)
def test_review_prose_tokens_keep_ambiguous_participants(tmp_path, prefix, label):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "talk.md").write_text(
        _transcript(body=prefix + label + " Unresolved attribution.")
    )
    db = tmp_path / "private.db"
    result = ingest_transcripts(cache, db, tmp_path)
    assert result.outcome == StageOutcome.BLOCKED
    assert any("ambiguous segment speaker" in message for message in result.errors)
    assert json.loads(result.receipt_path.read_text())["output_refs"] == []
    with Store(db) as store:
        assert store.connection.execute("SELECT count(*) FROM talks").fetchone()[0] == 0
        assert (
            store.connection.execute("SELECT count(*) FROM segments").fetchone()[0] == 0
        )


@pytest.mark.parametrize("prefix", ["", "[00:01] "])
@pytest.mark.parametrize(
    "body",
    [
        "The status is n/a: ordinary prose.",
        "plan/a: ordinary prose.",
        "n/abc: ordinary prose.",
    ],
)
def test_review_prose_tokens_keep_nonparticipant_prose(tmp_path, prefix, body):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = _transcript(body=prefix + body)
    (cache / "talk.md").write_text(source)
    db = tmp_path / "private.db"
    assert ingest_transcripts(cache, db, tmp_path).outcome == StageOutcome.PASS
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM segments").fetchall()
    segment = Segment.model_validate_json(row[0])
    assert segment.speaker_override is None
    assert segment.speaker_override_evidence is None
    assert segment.text == source[segment.source_start : segment.source_end]
    assert body in segment.text
