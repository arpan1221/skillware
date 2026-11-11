"""Synthetic structural examples, not extraction gold labels or quality evals."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from skillware.schema import (
    ENTITY_MODELS,
    Claim,
    Cluster,
    EntityRef,
    ExtractionProvenance,
    InspectionStatus,
    RepoRef,
    Segment,
    Skill,
    Stage,
    StageOutcome,
    Talk,
    export_json_schemas,
)

HASH = "a" * 64


@pytest.fixture
def records():
    """A linked, explicitly unreviewed evidence chain with a repo mention."""
    return [
        Talk(
            id="talk-1",
            title="Synthetic engineering talk",
            speaker="Example Speaker",
            company=None,
            source_url="https://www.youtube.com/watch?v=example0001",
            youtube_video_id="example0001",
            transcript_path="data/example.md",
            transcript_sha256=HASH,
        ),
        Segment(
            id="segment-1",
            talk_id="talk-1",
            talk_version="1",
            ordinal=0,
            text="Measure before changing retrieval.",
            source_start=10,
            source_end=44,
            start_seconds=Decimal("0.12345678901234567890"),
            end_seconds=Decimal("12.500"),
        ),
        Claim(
            id="claim-1",
            segment_id="segment-1",
            segment_version="1",
            statement="Measure a retrieval change on reviewed examples.",
            applicability="When adjusting retrieval parameters.",
            quote="Measure before changing retrieval.",
            quote_start=0,
            quote_end=34,
            repo_ref_ids=["repo-1"],
            repo_ref_versions={"repo-1": "1"},
            provenance=ExtractionProvenance(
                input_sha256=HASH,
                prompt_sha256="b" * 64,
                schema_sha256="c" * 64,
                adapter_id="fixture",
                provider_id="fixture",
                model_id="fixed-response",
                run_receipt="run-1",
            ),
        ),
        RepoRef(
            id="repo-1",
            url="https://example.org/team/project",
            host="example.org",
            owner="team",
            name="project",
            claim_ids=["claim-1"],
            claim_versions={"claim-1": "1"},
            license=None,
            license_evidence="No local snapshot was supplied.",
            facts=[
                {
                    "claim_id": "claim-1",
                    "claim_version": "1",
                    "claimed": "Contains a retrieval example.",
                }
            ],
        ),
        Cluster(
            id="cluster-1",
            proposition="Measure before changing retrieval parameters.",
            claim_ids=["claim-1"],
            claim_versions={"claim-1": "1"},
            supporting_claim_ids=["claim-1"],
            opposing_claim_ids=[],
            evidence_relations=[
                {
                    "claim_id": "claim-1",
                    "claim_version": "1",
                    "relation": "support",
                    "rationale": "The synthetic text proposes measurement first.",
                }
            ],
            distinct_talk_count=1,
            distinct_supporting_talk_count=1,
            distinct_opposing_talk_count=0,
            label="insufficient",
            rationale="Only one synthetic talk; no semantic review ran.",
            clustering_config_sha256=HASH,
        ),
        Skill(
            id="skill-1",
            name="measure-retrieval",
            description="Measure retrieval changes before adoption.",
            body="Draft research note. Validation and eval are BLOCKED.",
            cluster_ids=["cluster-1"],
            cluster_versions={"cluster-1": "1"},
            citations=[
                {
                    "claim_id": "claim-1",
                    "claim_version": "1",
                    "text": (
                        "Example Speaker (company unknown), Synthetic engineering talk"
                    ),
                    "url": "https://www.youtube.com/watch?v=example0001&t=0",
                }
            ],
            version="0.1",
            compatibility="A local evaluation environment.",
            metadata={"source": "synthetic"},
            allowed_tools="Read",
        ),
    ]


@pytest.mark.parametrize(
    "index", range(6), ids=[model.__name__ for model in ENTITY_MODELS]
)
def test_entity_json_round_trip_preserves_references_and_versions(records, index):
    record = records[index]
    restored = type(record).model_validate_json(record.model_dump_json())
    assert restored == record
    assert restored.id == record.id
    assert restored.version == record.version
    assert restored.schema_version == 1


def test_fractional_timing_precision_and_unknown_values_survive_json(records):
    segment = records[1]
    restored = Segment.model_validate_json(segment.model_dump_json())
    assert str(restored.start_seconds) == "0.12345678901234567890"
    assert str(restored.end_seconds) == "12.500"
    unknown = segment.model_dump() | {"start_seconds": None, "end_seconds": None}
    assert (
        Segment.model_validate_json(Segment(**unknown).model_dump_json()).start_seconds
        is None
    )
    zero = Segment(**(unknown | {"start_seconds": Decimal("0")}))
    assert zero.start_seconds == 0
    assert zero.end_seconds is None


def test_unresolved_metadata_is_explicit_and_checks_default_blocked(records):
    unknown = records[0].model_dump() | {
        "title": None,
        "speaker": None,
        "source_url": None,
        "youtube_video_id": None,
    }
    talk = Talk.model_validate_json(Talk(**unknown).model_dump_json())
    assert talk.title is talk.speaker is talk.company is talk.source_url is None
    assert records[2].citation_status is StageOutcome.BLOCKED
    assert records[2].provenance.review_status is StageOutcome.BLOCKED
    assert records[4].assessment_status is StageOutcome.BLOCKED
    assert records[4].evidence_relations[0].review_status is StageOutcome.BLOCKED
    assert (
        records[5].validation_status is records[5].eval_status is StageOutcome.BLOCKED
    )


def test_enum_contracts():
    assert [outcome.value for outcome in StageOutcome] == ["PASS", "FAIL", "BLOCKED"]
    assert [stage.value for stage in Stage] == [
        "ingest",
        "extract",
        "cluster",
        "synthesize",
        "validate",
        "eval",
    ]
    assert {state.value for state in InspectionStatus} == {
        "mentioned",
        "unavailable",
        "inspected",
        "mismatch",
    }


@pytest.mark.parametrize("state", list(InspectionStatus))
def test_repo_states_and_claimed_observed_evidence_round_trip(records, state):
    data = records[3].model_dump() | {
        "inspection_status": state,
        "commit": "d" * 40,
        "license": "Apache-2.0",
        "license_evidence": "LICENSE at the pinned commit.",
        "facts": [
            {
                "claim_id": "claim-1",
                "claim_version": "1",
                "claimed": "Provides a retrieval implementation.",
                "observed": {
                    "fact": "The file contains only an interface.",
                    "file_path": "src/retrieval.py",
                    "line_start": 2,
                    "line_end": 4,
                    "blob_sha256": HASH,
                },
            }
        ],
    }
    repo = RepoRef(**data)
    restored = RepoRef.model_validate_json(repo.model_dump_json())
    assert restored.inspection_status is state
    assert restored.facts[0].observed.blob_sha256 == HASH
    assert restored.facts[0].claimed != restored.facts[0].observed.fact


def test_cluster_preserves_separate_support_and_opposition_counts(records):
    data = records[4].model_dump() | {
        "claim_ids": ["claim-1", "claim-2", "claim-3"],
        "claim_versions": {"claim-1": "1", "claim-2": "1", "claim-3": "1"},
        "supporting_claim_ids": ["claim-1", "claim-2"],
        "opposing_claim_ids": ["claim-3"],
        "distinct_talk_count": 3,
        "distinct_supporting_talk_count": 2,
        "distinct_opposing_talk_count": 1,
        "label": "contested",
        "rationale": "Synthetic opposing positions remain visible.",
    }
    cluster = Cluster.model_validate_json(Cluster(**data).model_dump_json())
    assert cluster.distinct_supporting_talk_count == 2
    assert cluster.distinct_opposing_talk_count == 1
    assert cluster.distinct_talk_count == 3
    assert cluster.assessment_status is StageOutcome.BLOCKED


@pytest.mark.parametrize(
    "changes",
    [
        {"source_start": -1},
        {"source_end": 0},
        {"start_seconds": "-0.1"},
        {"start_seconds": "20", "end_seconds": "10"},
        {"start_seconds": "NaN"},
        {"speaker_override": "Second Speaker"},
    ],
)
def test_segment_rejects_invalid_offsets_timing_and_unbacked_override(records, changes):
    with pytest.raises(ValidationError):
        Segment(**(records[1].model_dump() | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"quote_start": -1},
        {"quote_end": 0},
        {"quote": " \t\n"},
        {"quote": " ".join(["word"] * 26)},
        {"citation_status": "verified"},
        {"repo_ref_ids": ["repo-1", "repo-1"]},
    ],
)
def test_claim_rejects_invalid_span_quote_budget_or_status(records, changes):
    with pytest.raises(ValidationError):
        Claim(**(records[2].model_dump() | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"supporting_claim_ids": ["absent-claim"]},
        {"opposing_claim_ids": ["claim-1"]},
        {"claim_ids": ["claim-1", "claim-1"]},
        {"distinct_supporting_talk_count": 2},
        {"distinct_opposing_talk_count": -1},
        {"label": "similar"},
    ],
)
def test_cluster_rejects_inconsistent_membership_and_counts(records, changes):
    with pytest.raises(ValidationError):
        Cluster(**(records[4].model_dump() | changes))


@pytest.mark.parametrize(
    "name", ["", "Uppercase", "bad--name", "-bad", "bad-", "a" * 65]
)
def test_skill_name_matches_frontmatter_contract(records, name):
    with pytest.raises(ValidationError):
        Skill(**(records[5].model_dump() | {"name": name}))


def test_invalid_hashes_unknown_fields_and_schema_versions_fail(records):
    for change in (
        {"transcript_sha256": "not-a-hash"},
        {"unexpected": True},
        {"schema_version": 2},
    ):
        with pytest.raises(ValidationError):
            Talk(**(records[0].model_dump() | change))


@pytest.mark.parametrize(
    "index, field_name", [(1, "talk_version"), (2, "segment_version")]
)
def test_direct_references_require_explicit_nonempty_versions(
    records, index, field_name
):
    record = records[index]
    data = record.model_dump()
    del data[field_name]
    with pytest.raises(ValidationError, match=field_name):
        type(record)(**data)
    for version in ("", " \t", None):
        with pytest.raises(ValidationError, match=field_name):
            type(record)(**(data | {field_name: version}))


@pytest.mark.parametrize(
    "index, field_name",
    [
        (2, "repo_ref_versions"),
        (3, "claim_versions"),
        (4, "claim_versions"),
        (5, "cluster_versions"),
    ],
)
def test_reference_version_maps_match_exactly_the_listed_ids(
    records, index, field_name
):
    record = records[index]
    data = record.model_dump()
    for versions in ({}, data[field_name] | {"unlisted-id": "1"}):
        with pytest.raises(ValidationError, match=field_name):
            type(record)(**(data | {field_name: versions}))
    del data[field_name]
    with pytest.raises(ValidationError, match=field_name):
        type(record)(**data)


@pytest.mark.parametrize(
    "index, field_name", [(3, "facts"), (4, "evidence_relations"), (5, "citations")]
)
def test_nested_claim_references_require_explicit_versions(records, index, field_name):
    record = records[index]
    data = record.model_dump()
    del data[field_name][0]["claim_version"]
    with pytest.raises(ValidationError, match="claim_version"):
        type(record)(**data)


@pytest.mark.parametrize("index, field_name", [(3, "facts"), (4, "evidence_relations")])
def test_nested_claim_references_cannot_conflict_with_linked_versions(
    records, index, field_name
):
    record = records[index]
    data = record.model_dump()
    data[field_name][0]["claim_version"] = "other-version"
    with pytest.raises(ValidationError, match="versions must match claim_versions"):
        type(record)(**data)


def test_external_evidence_relation_keeps_its_own_version_pin(records):
    data = records[4].model_dump()
    data["evidence_relations"].append(
        {
            "claim_id": "outside-candidate-group",
            "claim_version": "reviewed-revision",
            "relation": "unresolved",
            "rationale": "Related evidence remains visible outside this candidate group.",
        }
    )
    cluster = Cluster.model_validate_json(Cluster(**data).model_dump_json())
    assert cluster.evidence_relations[-1].claim_version == "reviewed-revision"
    assert cluster.claim_versions == {"claim-1": "1"}


def test_entity_refs_are_exact_immutable_and_separate_from_entity_payloads(records):
    for record in records:
        reference = record.ref
        assert reference.model_dump() == {
            "entity_type": type(record).__name__,
            "id": record.id,
            "version": record.version,
        }
        assert EntityRef.model_validate_json(reference.model_dump_json()) == reference
        assert "ref" not in record.model_dump()
        with pytest.raises(ValidationError, match="frozen"):
            reference.version = "changed"
    with pytest.raises(ValidationError, match="entity_type"):
        EntityRef(entity_type="Unknown", id="record", version="1")
    with pytest.raises(ValidationError, match="version"):
        EntityRef(entity_type="Talk", id="record")


def test_exported_schemas_are_deterministic_and_match_checked_in_contract(tmp_path):
    exported = export_json_schemas(tmp_path)
    assert {path.name for path in exported} == {
        f"{model.__name__}.json" for model in ENTITY_MODELS
    }
    checked_in = Path(__file__).resolve().parents[1] / "schemas"
    for model, path in zip(ENTITY_MODELS, exported, strict=True):
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["title"] == model.__name__
        assert schema["additionalProperties"] is False
        assert "id" in schema["required"]
        assert path.read_bytes() == (checked_in / path.name).read_bytes()
    first_bytes = [path.read_bytes() for path in exported]
    export_json_schemas(tmp_path)
    assert first_bytes == [path.read_bytes() for path in exported]
