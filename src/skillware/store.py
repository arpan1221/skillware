"""Private, local SQLite storage with explicit stage transaction boundaries.

Stable entity IDs may have multiple immutable evidence versions. References pin
both the stable ID and the exact version; reads never select a latest version.
Publication validation and downstream invalidation belong to later pipeline stages.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from skillware.schema import (
    Claim,
    Cluster,
    EntityRef,
    RepoRef,
    Segment,
    Skill,
    Stage,
    StageOutcome,
    Talk,
)

DEFAULT_DB_PATH = Path(".skillware/claims.sqlite3")
SCHEMA_VERSION = 2

Entity = Talk | Segment | Claim | RepoRef | Cluster | Skill
_TABLES = {
    Talk: "talks",
    Segment: "segments",
    Claim: "claims",
    RepoRef: "repo_refs",
    Cluster: "clusters",
    Skill: "skills",
}
_MODELS = {model.__name__: model for model in _TABLES}
_REQUIRED_TABLES = set(_TABLES.values()) | {
    "schema_version",
    "claim_repo_refs",
    "cluster_claims",
    "skill_clusters",
    "skill_citations",
    "stage_runs",
}

_DDL = """
CREATE TABLE schema_version (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version INTEGER NOT NULL
);
CREATE TABLE talks (
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
CREATE TABLE segments (
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    talk_id TEXT NOT NULL,
    talk_version TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (id, version),
    FOREIGN KEY (talk_id, talk_version) REFERENCES talks(id, version)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE claims (
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    segment_version TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (id, version),
    FOREIGN KEY (segment_id, segment_version) REFERENCES segments(id, version)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE repo_refs (
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
CREATE TABLE clusters (
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
CREATE TABLE skills (
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
CREATE TABLE claim_repo_refs (
    claim_id TEXT NOT NULL,
    claim_version TEXT NOT NULL,
    repo_ref_id TEXT NOT NULL,
    repo_ref_version TEXT NOT NULL,
    PRIMARY KEY (claim_id, claim_version, repo_ref_id, repo_ref_version),
    FOREIGN KEY (claim_id, claim_version) REFERENCES claims(id, version)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (repo_ref_id, repo_ref_version) REFERENCES repo_refs(id, version)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE cluster_claims (
    cluster_id TEXT NOT NULL,
    cluster_version TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    claim_version TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('member', 'support', 'oppose', 'evidence')),
    PRIMARY KEY (cluster_id, cluster_version, claim_id, claim_version, role),
    FOREIGN KEY (cluster_id, cluster_version) REFERENCES clusters(id, version)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (claim_id, claim_version) REFERENCES claims(id, version)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE skill_clusters (
    skill_id TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    cluster_version TEXT NOT NULL,
    PRIMARY KEY (skill_id, skill_version, cluster_id, cluster_version),
    FOREIGN KEY (skill_id, skill_version) REFERENCES skills(id, version)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (cluster_id, cluster_version) REFERENCES clusters(id, version)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE skill_citations (
    skill_id TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    claim_id TEXT NOT NULL,
    claim_version TEXT NOT NULL,
    PRIMARY KEY (skill_id, skill_version, ordinal),
    FOREIGN KEY (skill_id, skill_version) REFERENCES skills(id, version)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (claim_id, claim_version) REFERENCES claims(id, version)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE stage_runs (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL CHECK (
        stage IN ('ingest', 'extract', 'cluster', 'synthesize', 'validate', 'eval')
    ),
    config_version TEXT NOT NULL,
    input_hashes TEXT NOT NULL,
    output_refs TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('PASS', 'FAIL', 'BLOCKED')),
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
"""


@dataclass
class StageTransaction:
    """An attempt stays BLOCKED until its caller explicitly reports an outcome."""

    id: str = field(default_factory=lambda: str(uuid4()))
    outcome: StageOutcome = StageOutcome.BLOCKED
    output_refs: list[EntityRef] = field(default_factory=list)
    error: str | None = "stage outcome was not declared"

    def finish(
        self,
        outcome: StageOutcome,
        *,
        output_refs: list[EntityRef] | None = None,
        error: str | None = None,
    ) -> None:
        parsed_outcome = StageOutcome(outcome)
        parsed_refs = [EntityRef.model_validate(ref) for ref in output_refs or []]
        self.outcome = parsed_outcome
        self.output_refs = parsed_refs
        self.error = error


class Store:
    """A configurable SQLite store; typed writes require ``stage_transaction``.

    Only PASS commits stage output. FAIL, BLOCKED, and raised exceptions roll
    back output and retain a separate attempt receipt. Existing records are
    never updated by ``put``; a new version retains the same stable ID.
    No connection or database is created at import.
    """

    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self._stage_active = False
        self._write_error: BaseException | None = None
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self._initialize()
        except BaseException:
            self.connection.close()
            raise

    def _initialize(self) -> None:
        exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_version'"
        ).fetchone()
        if exists:
            versions = self.connection.execute(
                "SELECT version FROM schema_version"
            ).fetchall()
            if len(versions) != 1 or versions[0][0] != SCHEMA_VERSION:
                raise ValueError("unsupported database schema version")
            tables = {
                row[0]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if not _REQUIRED_TABLES <= tables:
                raise ValueError(
                    "incomplete database schema; no automatic repair is supported"
                )
            return
        try:
            self.connection.executescript("BEGIN IMMEDIATE;\n" + _DDL)
            self.connection.execute(
                "INSERT INTO schema_version (singleton, version) VALUES (1, ?)",
                (SCHEMA_VERSION,),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def put(self, entity: Entity) -> None:
        """Insert a validated immutable record and enforce its reference graph."""
        if not self._stage_active:
            raise RuntimeError("writes require a stage transaction")
        try:
            self._insert(entity)
        except BaseException as exc:
            self._write_error = exc
            raise

    def _insert(self, entity: Entity) -> None:
        model = type(entity)
        if model not in _TABLES:
            raise TypeError("unsupported entity type")
        entity = model.model_validate_json(entity.model_dump_json())
        columns = ["id", "version", "payload"]
        values = [entity.id, entity.version, entity.model_dump_json()]
        if isinstance(entity, Segment):
            columns.extend(("talk_id", "talk_version"))
            values.extend((entity.talk_id, entity.talk_version))
        elif isinstance(entity, Claim):
            columns.extend(("segment_id", "segment_version"))
            values.extend((entity.segment_id, entity.segment_version))
        self.connection.execute(
            f"INSERT INTO {_TABLES[model]} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in values)})",
            values,
        )
        if isinstance(entity, Claim):
            self._link_repos(
                [
                    (
                        entity.id,
                        entity.version,
                        repo_id,
                        entity.repo_ref_versions[repo_id],
                    )
                    for repo_id in entity.repo_ref_ids
                ]
            )
        elif isinstance(entity, RepoRef):
            self._link_repos(
                [
                    (
                        claim_id,
                        entity.claim_versions[claim_id],
                        entity.id,
                        entity.version,
                    )
                    for claim_id in entity.claim_ids
                ]
            )
        elif isinstance(entity, Cluster):
            for role, claim_ids in (
                ("member", entity.claim_ids),
                ("support", entity.supporting_claim_ids),
                ("oppose", entity.opposing_claim_ids),
            ):
                self.connection.executemany(
                    "INSERT OR IGNORE INTO cluster_claims VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            entity.id,
                            entity.version,
                            claim_id,
                            entity.claim_versions[claim_id],
                            role,
                        )
                        for claim_id in claim_ids
                    ],
                )
            self.connection.executemany(
                "INSERT OR IGNORE INTO cluster_claims VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        entity.id,
                        entity.version,
                        relation.claim_id,
                        relation.claim_version,
                        "evidence",
                    )
                    for relation in entity.evidence_relations
                ],
            )
        elif isinstance(entity, Skill):
            self.connection.executemany(
                "INSERT INTO skill_clusters VALUES (?, ?, ?, ?)",
                [
                    (
                        entity.id,
                        entity.version,
                        cluster_id,
                        entity.cluster_versions[cluster_id],
                    )
                    for cluster_id in entity.cluster_ids
                ],
            )
            self.connection.executemany(
                "INSERT INTO skill_citations VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        entity.id,
                        entity.version,
                        ordinal,
                        citation.claim_id,
                        citation.claim_version,
                    )
                    for ordinal, citation in enumerate(entity.citations)
                ],
            )

    def _link_repos(self, pairs: list[tuple[str, str, str, str]]) -> None:
        self.connection.executemany(
            "INSERT OR IGNORE INTO claim_repo_refs VALUES (?, ?, ?, ?)", pairs
        )

    def get[EntityT: (Talk, Segment, Claim, RepoRef, Cluster, Skill)](
        self, model: type[EntityT], entity_id: str, version: str = "1"
    ) -> EntityT | None:
        """Retrieve exactly ``version`` (default 1), never the latest evidence."""
        if model not in _TABLES:
            raise TypeError("unsupported entity type")
        row = self.connection.execute(
            f"SELECT payload FROM {_TABLES[model]} WHERE id = ? AND version = ?",
            (entity_id, version),
        ).fetchone()
        return None if row is None else model.model_validate_json(row[0])

    def resolve(self, reference: EntityRef) -> Entity | None:
        """Resolve a serialized entity reference to its exact evidence version."""
        reference = EntityRef.model_validate(reference)
        return self.get(_MODELS[reference.entity_type], reference.id, reference.version)

    @contextmanager
    def stage_transaction(
        self,
        stage: Stage,
        *,
        config_version: str,
        input_hashes: Mapping[str, str] | None = None,
    ) -> Iterator[StageTransaction]:
        """Commit stage data and a PASS receipt atomically, or retain failure only."""
        stage = Stage(stage)
        if self._stage_active or self.connection.in_transaction:
            raise RuntimeError("nested stage transactions are not supported")
        if not config_version:
            raise ValueError("config_version must not be empty")
        inputs = json.dumps(dict(input_hashes or {}), sort_keys=True)
        attempt = StageTransaction()
        started_at = datetime.now(UTC).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        self._stage_active = True
        self._write_error = None
        try:
            yield attempt
            if self._write_error is not None:
                raise RuntimeError(
                    f"stage contains a failed typed write: {self._write_error}"
                ) from self._write_error
            attempt.outcome = StageOutcome(attempt.outcome)
            if attempt.outcome != StageOutcome.PASS:
                self.connection.rollback()
                self.connection.execute("BEGIN IMMEDIATE")
                attempt.output_refs = []
            else:
                self._check_repo_links()
                self._check_output_refs(attempt.output_refs)
            self._record_attempt(attempt, stage, config_version, inputs, started_at)
            self.connection.commit()
        except BaseException as exc:
            self.connection.rollback()
            attempt.finish(StageOutcome.FAIL, error=f"{type(exc).__name__}: {exc}")
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self._record_attempt(attempt, stage, config_version, inputs, started_at)
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise
            raise
        finally:
            self._stage_active = False
            self._write_error = None

    def _check_repo_links(self) -> None:
        # Both serialized declarations must describe the same many-to-many link.
        # Missing targets are left to SQLite's deferred foreign-key check.
        rows = self.connection.execute(
            "SELECT links.claim_id, links.claim_version, "
            "links.repo_ref_id, links.repo_ref_version, "
            "claims.payload AS claim_payload, repo_refs.payload AS repo_payload "
            "FROM claim_repo_refs AS links "
            "JOIN claims ON claims.id = links.claim_id "
            "AND claims.version = links.claim_version "
            "JOIN repo_refs ON repo_refs.id = links.repo_ref_id "
            "AND repo_refs.version = links.repo_ref_version"
        )
        for row in rows:
            claim_repos = json.loads(row["claim_payload"])["repo_ref_versions"]
            repo_claims = json.loads(row["repo_payload"])["claim_versions"]
            if (
                claim_repos.get(row["repo_ref_id"]) != row["repo_ref_version"]
                or repo_claims.get(row["claim_id"]) != row["claim_version"]
            ):
                raise ValueError("claim/repository links must be reciprocal")

    def _check_output_refs(self, references: list[EntityRef]) -> None:
        for reference in references:
            if self.resolve(reference) is None:
                raise ValueError(f"output reference does not resolve: {reference}")

    def _record_attempt(
        self,
        attempt: StageTransaction,
        stage: Stage,
        config_version: str,
        inputs: str,
        started_at: str,
    ) -> None:
        self.connection.execute(
            "INSERT INTO stage_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt.id,
                stage.value,
                config_version,
                inputs,
                json.dumps(
                    [
                        EntityRef.model_validate(ref).model_dump(mode="json")
                        for ref in attempt.output_refs
                    ]
                ),
                attempt.outcome.value,
                attempt.error,
                started_at,
                datetime.now(UTC).isoformat(),
            ),
        )
