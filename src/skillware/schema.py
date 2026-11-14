"""Versioned internal records for the six entities defined in docs/SPEC.md.

These records may contain private source text and paths. JSON-schema export
describes their shape; serializing a record is not a release/publication check.
Cross-record evidence checks belong to the future validate stage.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1)]
NonEmptyText = Annotated[str, Field(min_length=1, pattern=r"\S")]
SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Offset = Annotated[int, Field(ge=0, strict=True)]
Seconds = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class StageOutcome(StrEnum):
    """Unrun or missing checks are BLOCKED, never an implicit PASS."""

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


class Stage(StrEnum):
    INGEST = "ingest"
    EXTRACT = "extract"
    CLUSTER = "cluster"
    SYNTHESIZE = "synthesize"
    VALIDATE = "validate"
    EVAL = "eval"


class InspectionStatus(StrEnum):
    MENTIONED = "mentioned"
    UNAVAILABLE = "unavailable"
    INSPECTED = "inspected"
    MISMATCH = "mismatch"


class ClusterLabel(StrEnum):
    CONSENSUS = "consensus"
    CONTESTED = "contested"
    INSUFFICIENT = "insufficient"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class EntityRef(Record):
    """An exact internal reference; the stable ID alone never selects a version."""

    model_config = ConfigDict(frozen=True)

    entity_type: Literal["Talk", "Segment", "Claim", "RepoRef", "Cluster", "Skill"]
    id: Identifier
    version: NonEmptyText


def _check_version_keys(
    ids: list[str], versions: dict[str, str], field_name: str
) -> None:
    if set(versions) != set(ids):
        raise ValueError(f"{field_name} must pin exactly the referenced IDs")


class Entity(Record):
    """A stable entity ID and explicit evidence/serialization versions."""

    id: Identifier
    version: NonEmptyText = "1"
    schema_version: Literal[1] = 1

    @property
    def ref(self) -> EntityRef:
        return EntityRef(
            entity_type=type(self).__name__, id=self.id, version=self.version
        )


class FetchProvenance(Record):
    """Source-backed retrieval facts for a private Markdown cache entry."""

    source_url: NonEmptyText
    fetched_at: datetime
    content_sha256: SHA256

    @field_validator("fetched_at")
    @classmethod
    def utc_fetch_date(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetch date must include a UTC offset")
        return value.astimezone(UTC)


class Talk(Entity):
    # A missing attribution must be representable rather than guessed.
    title: NonEmptyText | None
    speaker: NonEmptyText | None
    company: NonEmptyText | None = None
    source_url: NonEmptyText | None
    youtube_video_id: NonEmptyText | None = None
    transcript_path: NonEmptyText
    transcript_sha256: SHA256
    fetch_provenance: FetchProvenance | None = None
    ingest_provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_fetch_hash(self) -> Self:
        if (
            self.fetch_provenance is not None
            and self.fetch_provenance.content_sha256 != self.transcript_sha256
        ):
            raise ValueError("fetch content hash must match transcript_sha256")
        return self


class Segment(Entity):
    talk_id: Identifier
    talk_version: NonEmptyText
    ordinal: Offset
    text: str = Field(repr=False, description="Private source text; never export.")
    source_start: Offset = Field(
        description="Inclusive character offset in the source."
    )
    source_end: Offset = Field(description="Exclusive character offset in the source.")
    start_seconds: Seconds | None = None
    end_seconds: Seconds | None = None
    speaker_override: NonEmptyText | None = None
    speaker_override_evidence: NonEmptyText | None = None

    @model_validator(mode="after")
    def check_source_order(self) -> Self:
        if self.source_end < self.source_start:
            raise ValueError("source_end must be at or after source_start")
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("end_seconds must be at or after start_seconds")
        if (self.speaker_override is None) != (self.speaker_override_evidence is None):
            raise ValueError(
                "speaker override and its source evidence must occur together"
            )
        return self


class ExtractionProvenance(Record):
    input_sha256: SHA256
    prompt_sha256: SHA256
    schema_sha256: SHA256
    adapter_id: Identifier
    provider_id: Identifier
    model_id: Identifier
    run_receipt: Identifier
    review_status: StageOutcome = StageOutcome.BLOCKED


class Claim(Entity):
    segment_id: Identifier
    segment_version: NonEmptyText
    statement: NonEmptyText
    applicability: NonEmptyText
    quote: NonEmptyText
    quote_start: Offset = Field(
        description="Inclusive character offset in the segment."
    )
    quote_end: Offset = Field(description="Exclusive character offset in the segment.")
    citation_status: StageOutcome = StageOutcome.BLOCKED
    repo_ref_ids: list[Identifier] = Field(default_factory=list)
    repo_ref_versions: dict[Identifier, NonEmptyText] = Field(default_factory=dict)
    provenance: ExtractionProvenance

    @field_validator("quote")
    @classmethod
    def short_quote(cls, value: str) -> str:
        if len(value.split()) > 25:
            raise ValueError(
                "a quote must contain at most 25 whitespace-delimited words"
            )
        return value

    @model_validator(mode="after")
    def check_quote_order(self) -> Self:
        if self.quote_end <= self.quote_start:
            raise ValueError("quote_end must be after quote_start")
        if len(set(self.repo_ref_ids)) != len(self.repo_ref_ids):
            raise ValueError("repo_ref_ids must be unique")
        _check_version_keys(
            self.repo_ref_ids, self.repo_ref_versions, "repo_ref_versions"
        )
        return self


class RepositoryObservation(Record):
    """A static observation, not a claim of runtime correctness."""

    fact: NonEmptyText
    file_path: NonEmptyText
    line_start: Annotated[int, Field(ge=1, strict=True)]
    line_end: Annotated[int, Field(ge=1, strict=True)]
    blob_sha256: SHA256

    @model_validator(mode="after")
    def check_line_order(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("line_end must be at or after line_start")
        return self


class RepositoryFact(Record):
    claim_id: Identifier
    claim_version: NonEmptyText
    claimed: NonEmptyText
    observed: RepositoryObservation | None = None


class RepoRef(Entity):
    url: NonEmptyText
    host: NonEmptyText
    owner: NonEmptyText
    name: NonEmptyText
    claim_ids: list[Identifier] = Field(default_factory=list)
    claim_versions: dict[Identifier, NonEmptyText] = Field(default_factory=dict)
    inspection_status: InspectionStatus = InspectionStatus.MENTIONED
    commit: NonEmptyText | None = None
    license: NonEmptyText | None = Field(
        default=None, description="Null explicitly means the license is unknown."
    )
    license_evidence: NonEmptyText
    facts: list[RepositoryFact] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_claim_links(self) -> Self:
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("claim_ids must be unique")
        _check_version_keys(self.claim_ids, self.claim_versions, "claim_versions")
        if any(fact.claim_id not in self.claim_ids for fact in self.facts):
            raise ValueError("repository facts must link to a listed claim")
        if any(
            fact.claim_version != self.claim_versions[fact.claim_id]
            for fact in self.facts
        ):
            raise ValueError("repository fact versions must match claim_versions")
        return self


class EvidenceRelation(Record):
    claim_id: Identifier
    claim_version: NonEmptyText
    relation: Literal["support", "oppose", "unresolved"]
    rationale: NonEmptyText
    review_status: StageOutcome = StageOutcome.BLOCKED


class Cluster(Entity):
    proposition: NonEmptyText
    claim_ids: list[Identifier] = Field(default_factory=list)
    claim_versions: dict[Identifier, NonEmptyText] = Field(default_factory=dict)
    supporting_claim_ids: list[Identifier] = Field(default_factory=list)
    opposing_claim_ids: list[Identifier] = Field(default_factory=list)
    # Relations may also preserve opposition found outside this candidate group.
    evidence_relations: list[EvidenceRelation] = Field(default_factory=list)
    distinct_talk_count: Offset
    distinct_supporting_talk_count: Offset
    distinct_opposing_talk_count: Offset
    label: ClusterLabel = ClusterLabel.INSUFFICIENT
    rationale: NonEmptyText
    assessment_status: StageOutcome = StageOutcome.BLOCKED
    clustering_config_sha256: SHA256

    @model_validator(mode="after")
    def check_membership(self) -> Self:
        members = set(self.claim_ids)
        support = set(self.supporting_claim_ids)
        opposition = set(self.opposing_claim_ids)
        for ids in (self.claim_ids, self.supporting_claim_ids, self.opposing_claim_ids):
            if len(set(ids)) != len(ids):
                raise ValueError("claim lists must contain unique IDs")
        _check_version_keys(self.claim_ids, self.claim_versions, "claim_versions")
        if any(
            relation.claim_id in members
            and relation.claim_version != self.claim_versions[relation.claim_id]
            for relation in self.evidence_relations
        ):
            raise ValueError("evidence relation versions must match claim_versions")
        if not (support | opposition) <= members:
            raise ValueError("supporting/opposing claims must be cluster members")
        if support & opposition:
            raise ValueError(
                "an atomic claim cannot both support and oppose a proposition"
            )
        if self.distinct_talk_count > len(members):
            raise ValueError("distinct talk count exceeds member claim count")
        if self.distinct_supporting_talk_count > min(
            self.distinct_talk_count, len(support)
        ):
            raise ValueError("supporting talk count exceeds available membership")
        if self.distinct_opposing_talk_count > min(
            self.distinct_talk_count, len(opposition)
        ):
            raise ValueError("opposing talk count exceeds available membership")
        # This is structural consistency only. Publication must derive counts
        # and labels from deduplicated, publishable claims and reviewed relations.
        return self


class Citation(Record):
    claim_id: Identifier
    claim_version: NonEmptyText
    text: NonEmptyText
    url: NonEmptyText | None = None


class Skill(Entity):
    pack_name: Literal["The AI Engineer Playbook"] = "The AI Engineer Playbook"
    name: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
    ]
    description: Annotated[str, Field(min_length=1, max_length=1024, pattern=r"\S")]
    body: NonEmptyText
    cluster_ids: list[Identifier] = Field(default_factory=list)
    cluster_versions: dict[Identifier, NonEmptyText] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    validation_status: StageOutcome = StageOutcome.BLOCKED
    eval_status: StageOutcome = StageOutcome.BLOCKED
    license: NonEmptyText | None = None
    compatibility: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: NonEmptyText | None = Field(
        default=None,
        description="Maps to the optional allowed-tools frontmatter field.",
    )

    @field_validator("cluster_ids")
    @classmethod
    def unique_clusters(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("cluster_ids must be unique")
        return value

    @model_validator(mode="after")
    def check_cluster_versions(self) -> Self:
        _check_version_keys(self.cluster_ids, self.cluster_versions, "cluster_versions")
        return self


ENTITY_MODELS = (Talk, Segment, Claim, RepoRef, Cluster, Skill)


def export_json_schemas(directory: str | Path = "schemas") -> list[Path]:
    """Write deterministic JSON schemas; no source records or private data."""
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    paths = []
    for model in ENTITY_MODELS:
        path = destination / f"{model.__name__}.json"
        schema = model.model_json_schema(mode="serialization")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        path.write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export skillware's six entity JSON schemas."
    )
    parser.add_argument("directory", nargs="?", default="schemas")
    export_json_schemas(parser.parse_args().directory)


if __name__ == "__main__":
    main()
