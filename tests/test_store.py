"""Storage contract tests use disposable local databases and synthetic evidence."""

import json
import sqlite3
from pathlib import Path

import pytest

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
from skillware.store import DEFAULT_DB_PATH, SCHEMA_VERSION, Store


@pytest.fixture
def talk() -> Talk:
    return Talk(
        id="talk-1",
        title="Synthetic local talk",
        speaker="Example Speaker",
        company=None,
        source_url="https://example.test/talk",
        youtube_video_id=None,
        transcript_path="data/synthetic.md",
        transcript_sha256="a" * 64,
    )


@pytest.fixture
def segment(talk: Talk) -> Segment:
    return Segment(
        id="segment-1",
        talk_id=talk.id,
        talk_version=talk.version,
        ordinal=0,
        text="Measure the baseline.",
        source_start=0,
        source_end=21,
        start_seconds=None,
        end_seconds=None,
    )


@pytest.fixture
def evidence_graph(talk, segment):
    claim = Claim(
        id="claim-1",
        segment_id=segment.id,
        segment_version=segment.version,
        statement="Measure a baseline before making a change.",
        applicability="When comparing changes in a synthetic example.",
        quote="Measure the baseline.",
        quote_start=0,
        quote_end=21,
        repo_ref_ids=["repo-1"],
        repo_ref_versions={"repo-1": "1"},
        provenance={
            "input_sha256": "a" * 64,
            "prompt_sha256": "b" * 64,
            "schema_sha256": "c" * 64,
            "adapter_id": "fixture",
            "provider_id": "fixture",
            "model_id": "fixture",
            "run_receipt": "synthetic-run",
        },
    )
    repo = RepoRef(
        id="repo-1",
        url="https://example.test/owner/project",
        host="example.test",
        owner="owner",
        name="project",
        claim_ids=[claim.id],
        claim_versions={claim.id: claim.version},
        license_evidence="No snapshot was supplied for this synthetic mention.",
    )
    cluster = Cluster(
        id="cluster-1",
        proposition=claim.statement,
        claim_ids=[claim.id],
        claim_versions={claim.id: claim.version},
        supporting_claim_ids=[claim.id],
        distinct_talk_count=1,
        distinct_supporting_talk_count=1,
        distinct_opposing_talk_count=0,
        rationale="One synthetic talk is insufficient publication evidence.",
        clustering_config_sha256="d" * 64,
        evidence_relations=[
            {
                "claim_id": claim.id,
                "claim_version": claim.version,
                "relation": "support",
                "rationale": "Synthetic relation.",
            }
        ],
    )
    skill = Skill(
        id="skill-1",
        name="synthetic-draft",
        description="A private synthetic storage fixture, not a publishable skill.",
        body="Measure a baseline. [1]",
        cluster_ids=[cluster.id],
        cluster_versions={cluster.id: cluster.version},
        citations=[
            {
                "claim_id": claim.id,
                "claim_version": claim.version,
                "text": "Synthetic source; publication blocked.",
            }
        ],
    )
    return (talk, segment, claim, repo, cluster, skill)


def test_default_path_is_private_and_configurable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with Store() as store:
        assert store.path == DEFAULT_DB_PATH == Path(".skillware/claims.sqlite3")
        assert (tmp_path / DEFAULT_DB_PATH).is_file()
    custom = tmp_path / "other" / "custom.sqlite3"
    with Store(custom) as store:
        assert store.path == custom
        assert custom.is_file()


def test_foreign_keys_and_schema_version_survive_reopening(tmp_path):
    path = tmp_path / "claims.sqlite3"
    for _ in range(2):
        with Store(path) as store:
            assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            version = store.connection.execute(
                "SELECT version FROM schema_version"
            ).fetchone()[0]
            assert version == SCHEMA_VERSION


def test_unknown_schema_version_is_rejected_without_rewriting(tmp_path):
    path = tmp_path / "claims.sqlite3"
    with Store(path) as store:
        store.connection.execute("UPDATE schema_version SET version = 999")
    with pytest.raises(ValueError, match="unsupported database schema version"):
        Store(path)
    with sqlite3.connect(path) as connection:
        version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == 999


def test_legacy_schema_is_rejected_without_rewriting(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES (1)")
        connection.execute(
            "CREATE TABLE talks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO talks VALUES (?, ?)", ("talk-1", "legacy evidence")
        )
    original_bytes = path.read_bytes()
    with pytest.raises(ValueError, match="unsupported database schema version"):
        Store(path)
    assert path.read_bytes() == original_bytes
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        )
        assert connection.execute("SELECT * FROM talks").fetchall() == [
            ("talk-1", "legacy evidence")
        ]


def test_incomplete_database_is_rejected_without_repair(tmp_path):
    path = tmp_path / "claims.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
    with pytest.raises(ValueError, match="incomplete database schema"):
        Store(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == [("schema_version",)]


def test_typed_writes_require_stage_transaction(tmp_path, talk):
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(RuntimeError, match="writes require a stage transaction"):
            store.put(talk)
        assert store.get(Talk, talk.id) is None


def test_stage_commits_records_and_receipt_together(tmp_path, talk, segment):
    path = tmp_path / "claims.sqlite3"
    with Store(path) as store, Store(path) as reader:
        with store.stage_transaction(
            Stage.INGEST,
            config_version="fixture-v1",
            input_hashes={"transcript": "a" * 64},
        ) as attempt:
            # Deferred FKs permit writes in either order inside one stage.
            store.put(segment)
            store.put(talk)
            assert reader.get(Talk, talk.id) is None
            receipt_count = reader.connection.execute(
                "SELECT count(*) FROM stage_runs"
            ).fetchone()[0]
            assert receipt_count == 0
            attempt.finish(StageOutcome.PASS, output_refs=[talk.ref, segment.ref])
        assert reader.get(Talk, talk.id) == talk
        assert reader.get(Segment, segment.id) == segment
    with Store(path) as store:
        assert store.get(Talk, talk.id) == talk
        receipt = store.connection.execute("SELECT * FROM stage_runs").fetchone()
        assert receipt["stage"] == "ingest"
        assert receipt["outcome"] == "PASS"
        assert receipt["config_version"] == "fixture-v1"
        assert json.loads(receipt["input_hashes"]) == {"transcript": "a" * 64}
        assert json.loads(receipt["output_refs"]) == [
            talk.ref.model_dump(),
            segment.ref.model_dump(),
        ]
        assert receipt["error"] is None


def test_failed_stage_rolls_back_output_and_keeps_prior_success(
    tmp_path, talk, segment
):
    with Store(tmp_path / "claims.sqlite3") as store:
        with store.stage_transaction(Stage.INGEST, config_version="v1") as attempt:
            store.put(talk)
            attempt.finish(StageOutcome.PASS, output_refs=[talk.ref])
        with pytest.raises(RuntimeError, match="fixture failed"):
            with store.stage_transaction(Stage.INGEST, config_version="v2"):
                store.put(segment)
                raise RuntimeError("fixture failed")
        assert store.get(Talk, talk.id) == talk
        assert store.get(Segment, segment.id) is None
        receipts = store.connection.execute(
            "SELECT * FROM stage_runs ORDER BY rowid"
        ).fetchall()
        assert [row["outcome"] for row in receipts] == ["PASS", "FAIL"]
        assert receipts[1]["error"] == "RuntimeError: fixture failed"
        assert json.loads(receipts[1]["output_refs"]) == []


@pytest.mark.parametrize("outcome", [StageOutcome.FAIL, StageOutcome.BLOCKED])
def test_nonpassing_outcome_discards_output_and_retains_attempt(
    tmp_path, talk, outcome
):
    with Store(tmp_path / "claims.sqlite3") as store:
        with store.stage_transaction(Stage.INGEST, config_version="v1") as attempt:
            store.put(talk)
            attempt.finish(
                outcome, output_refs=[talk.ref], error="missing prerequisite"
            )
        assert store.get(Talk, talk.id) is None
        receipt = store.connection.execute("SELECT * FROM stage_runs").fetchone()
        assert receipt["outcome"] == outcome.value
        assert receipt["error"] == "missing prerequisite"
        assert json.loads(receipt["output_refs"]) == []


def test_no_declared_outcome_is_blocked_not_pass(tmp_path, talk):
    with Store(tmp_path / "claims.sqlite3") as store:
        with store.stage_transaction(Stage.INGEST, config_version="v1"):
            store.put(talk)
        assert store.get(Talk, talk.id) is None
        receipt = store.connection.execute("SELECT * FROM stage_runs").fetchone()
        assert receipt["outcome"] == "BLOCKED"
        assert receipt["error"] == "stage outcome was not declared"


def test_dangling_reference_rejects_commit_and_records_failure(tmp_path, segment):
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with store.stage_transaction(Stage.INGEST, config_version="v1") as attempt:
                store.put(segment)
                attempt.finish(StageOutcome.PASS, output_refs=[segment.ref])
        assert store.get(Segment, segment.id) is None
        receipts = store.connection.execute("SELECT * FROM stage_runs").fetchall()
        assert len(receipts) == 1
        assert receipts[0]["outcome"] == "FAIL"
        assert "FOREIGN KEY" in receipts[0]["error"]


def test_existing_evidence_cannot_be_overwritten(tmp_path, talk):
    with Store(tmp_path / "claims.sqlite3") as store:
        with store.stage_transaction(Stage.INGEST, config_version="v1") as attempt:
            store.put(talk)
            attempt.finish(StageOutcome.PASS, output_refs=[talk.ref])
        changed = talk.model_copy(update={"title": "Changed evidence"})
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            with store.stage_transaction(Stage.INGEST, config_version="v2"):
                store.put(changed)
        assert store.get(Talk, talk.id) == talk


def test_nested_stage_is_rejected_and_outer_output_rolled_back(tmp_path, talk):
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(RuntimeError, match="nested stage transactions"):
            with store.stage_transaction(Stage.INGEST, config_version="v1"):
                store.put(talk)
                with store.stage_transaction(Stage.EXTRACT, config_version="v1"):
                    pytest.fail("a nested stage must not run")
        assert store.get(Talk, talk.id) is None
        outcome = store.connection.execute("SELECT outcome FROM stage_runs").fetchone()[
            0
        ]
        assert outcome == "FAIL"


def test_six_entity_roundtrip_and_join_tables(tmp_path, evidence_graph):
    path = tmp_path / "claims.sqlite3"
    with Store(path) as store:
        with store.stage_transaction(Stage.INGEST, config_version="fixture") as attempt:
            for entity in reversed(evidence_graph):
                store.put(entity)
            attempt.finish(
                StageOutcome.PASS,
                output_refs=[entity.ref for entity in evidence_graph],
            )
    with Store(path) as store:
        for entity in evidence_graph:
            assert store.get(type(entity), entity.id) == entity
        repo_links = store.connection.execute("SELECT * FROM claim_repo_refs")
        assert [tuple(row) for row in repo_links] == [("claim-1", "1", "repo-1", "1")]
        cluster_links = store.connection.execute("SELECT * FROM cluster_claims")
        assert {tuple(row) for row in cluster_links} == {
            ("cluster-1", "1", "claim-1", "1", "member"),
            ("cluster-1", "1", "claim-1", "1", "support"),
            ("cluster-1", "1", "claim-1", "1", "evidence"),
        }
        skill_links = store.connection.execute("SELECT * FROM skill_clusters")
        assert [tuple(row) for row in skill_links] == [
            ("skill-1", "1", "cluster-1", "1")
        ]
        citation_links = store.connection.execute("SELECT * FROM skill_citations")
        assert [tuple(row) for row in citation_links] == [
            ("skill-1", "1", 0, "claim-1", "1")
        ]


@pytest.mark.parametrize("omitted_index", [0, 1, 2, 3, 4])
def test_each_missing_entity_link_rejects_whole_stage(
    tmp_path, evidence_graph, omitted_index
):
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with store.stage_transaction(
                Stage.INGEST, config_version="fixture"
            ) as attempt:
                written = [
                    entity
                    for index, entity in enumerate(evidence_graph)
                    if index != omitted_index
                ]
                for entity in written:
                    store.put(entity)
                attempt.finish(
                    StageOutcome.PASS, output_refs=[entity.ref for entity in written]
                )
        for entity in evidence_graph:
            assert store.get(type(entity), entity.id) is None
        outcome = store.connection.execute("SELECT outcome FROM stage_runs").fetchone()[
            0
        ]
        assert outcome == "FAIL"


def test_caught_partial_write_cannot_commit_a_passing_stage(tmp_path, evidence_graph):
    with Store(tmp_path / "claims.sqlite3") as store:
        store.connection.execute(
            "CREATE TRIGGER reject_skill_link BEFORE INSERT ON skill_clusters "
            "BEGIN SELECT RAISE(ABORT, 'synthetic link rejection'); END"
        )
        with pytest.raises(RuntimeError, match="stage contains a failed typed write"):
            with store.stage_transaction(
                Stage.INGEST, config_version="fixture"
            ) as attempt:
                for entity in evidence_graph[:-1]:
                    store.put(entity)
                with pytest.raises(
                    sqlite3.IntegrityError, match="synthetic link rejection"
                ):
                    store.put(evidence_graph[-1])
                attempt.finish(StageOutcome.PASS)
        for entity in evidence_graph:
            assert store.get(type(entity), entity.id) is None
        outcome = store.connection.execute("SELECT outcome FROM stage_runs").fetchone()[
            0
        ]
        assert outcome == "FAIL"


@pytest.mark.parametrize(
    "changed_index, field, version_field",
    [(2, "repo_ref_ids", "repo_ref_versions"), (3, "claim_ids", "claim_versions")],
)
def test_nonreciprocal_repository_links_reject_stage(
    tmp_path, evidence_graph, changed_index, field, version_field
):
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(ValueError, match="links must be reciprocal"):
            with store.stage_transaction(
                Stage.INGEST, config_version="fixture"
            ) as attempt:
                for index, entity in enumerate(evidence_graph):
                    if index == changed_index:
                        entity = entity.model_copy(
                            update={field: [], version_field: {}}
                        )
                    store.put(entity)
                attempt.finish(StageOutcome.PASS)
        for entity in evidence_graph:
            assert store.get(type(entity), entity.id) is None
        outcome = store.connection.execute("SELECT outcome FROM stage_runs").fetchone()[
            0
        ]
        assert outcome == "FAIL"


def _versioned_graph(evidence_graph, version):
    """Create a new synthetic graph whose edges pin the same explicit version."""
    result = []
    for entity in evidence_graph:
        payload = entity.model_dump()
        payload["version"] = version
        if isinstance(entity, Talk):
            payload["title"] = f"Synthetic talk revision {version}"
            payload["transcript_sha256"] = "b" * 64
        elif isinstance(entity, Segment):
            payload["talk_version"] = version
        elif isinstance(entity, Claim):
            payload["segment_version"] = version
            payload["repo_ref_versions"] = {
                repo_id: version for repo_id in entity.repo_ref_ids
            }
        elif isinstance(entity, RepoRef):
            payload["claim_versions"] = {
                claim_id: version for claim_id in entity.claim_ids
            }
            for fact in payload["facts"]:
                fact["claim_version"] = version
        elif isinstance(entity, Cluster):
            payload["claim_versions"] = {
                claim_id: version for claim_id in entity.claim_ids
            }
            for relation in payload["evidence_relations"]:
                relation["claim_version"] = version
        elif isinstance(entity, Skill):
            payload["cluster_versions"] = {
                cluster_id: version for cluster_id in entity.cluster_ids
            }
            for citation in payload["citations"]:
                citation["claim_version"] = version
        result.append(type(entity).model_validate(payload))
    return tuple(result)


def test_new_version_preserves_stable_identity_and_prior_evidence(tmp_path, talk):
    revised = talk.model_copy(
        update={"version": "2", "title": "Revised title", "transcript_sha256": "b" * 64}
    )
    path = tmp_path / "claims.sqlite3"
    with Store(path) as store:
        for entity in (talk, revised):
            with store.stage_transaction(
                Stage.INGEST, config_version="fixture"
            ) as stage:
                store.put(entity)
                stage.finish(StageOutcome.PASS, output_refs=[entity.ref])
        duplicate = revised.model_copy(update={"title": "Forbidden overwrite"})
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            with store.stage_transaction(Stage.INGEST, config_version="fixture"):
                store.put(duplicate)
    with Store(path) as store:
        assert store.get(Talk, talk.id) == talk
        assert store.get(Talk, talk.id, version="1") == talk
        assert store.get(Talk, talk.id, version="2") == revised
        assert store.get(Talk, talk.id, version="3") is None
        assert store.resolve(talk.ref) == talk
        assert store.resolve(revised.ref) == revised
        assert store.resolve(talk.ref.model_copy(update={"version": "3"})) is None
        assert store.connection.execute("SELECT COUNT(*) FROM talks").fetchone()[0] == 2


def test_all_six_entity_versions_and_reference_pins_survive_reopening(
    tmp_path, evidence_graph
):
    revised_graph = _versioned_graph(evidence_graph, "2")
    path = tmp_path / "claims.sqlite3"
    with Store(path) as store:
        for graph in (evidence_graph, revised_graph):
            with store.stage_transaction(
                Stage.INGEST, config_version="fixture"
            ) as stage:
                for entity in reversed(graph):
                    store.put(entity)
                stage.finish(
                    StageOutcome.PASS, output_refs=[entity.ref for entity in graph]
                )
    with Store(path) as store:
        for original, revised in zip(evidence_graph, revised_graph, strict=True):
            assert original.id == revised.id
            assert store.get(type(original), original.id) == original
            assert store.get(type(revised), revised.id, version="2") == revised
            assert store.resolve(original.ref) == original
            assert store.resolve(revised.ref) == revised
        assert {
            tuple(row)
            for row in store.connection.execute(
                "SELECT id, version, talk_id, talk_version FROM segments"
            )
        } == {("segment-1", version, "talk-1", version) for version in ("1", "2")}
        assert {
            tuple(row)
            for row in store.connection.execute(
                "SELECT id, version, segment_id, segment_version FROM claims"
            )
        } == {("claim-1", version, "segment-1", version) for version in ("1", "2")}
        assert {
            tuple(row)
            for row in store.connection.execute("SELECT * FROM claim_repo_refs")
        } == {("claim-1", version, "repo-1", version) for version in ("1", "2")}
        assert {
            tuple(row)
            for row in store.connection.execute("SELECT * FROM cluster_claims")
        } == {
            ("cluster-1", version, "claim-1", version, role)
            for version in ("1", "2")
            for role in ("member", "support", "evidence")
        }
        assert {
            tuple(row)
            for row in store.connection.execute("SELECT * FROM skill_clusters")
        } == {("skill-1", version, "cluster-1", version) for version in ("1", "2")}
        assert {
            tuple(row)
            for row in store.connection.execute("SELECT * FROM skill_citations")
        } == {("skill-1", version, 0, "claim-1", version) for version in ("1", "2")}
        receipts = store.connection.execute(
            "SELECT output_refs FROM stage_runs ORDER BY rowid"
        ).fetchall()
        for receipt, graph in zip(
            receipts, (evidence_graph, revised_graph), strict=True
        ):
            pins = json.loads(receipt["output_refs"])
            assert pins == [entity.ref.model_dump() for entity in graph]
            assert [
                store.resolve(EntityRef.model_validate(pin)) for pin in pins
            ] == list(graph)


@pytest.mark.parametrize("changed_index", [0, 1, 2, 3, 4])
def test_existing_id_with_missing_referenced_version_rejects_whole_stage(
    tmp_path, evidence_graph, changed_index
):
    graph = list(evidence_graph)
    graph[changed_index] = graph[changed_index].model_copy(update={"version": "2"})
    # Isolate the missing version from a separate reciprocal-link mismatch:
    # the replacement version does not declare links to the original graph.
    if changed_index == 2:
        graph[changed_index] = graph[changed_index].model_copy(
            update={"repo_ref_ids": [], "repo_ref_versions": {}}
        )
    elif changed_index == 3:
        graph[changed_index] = graph[changed_index].model_copy(
            update={"claim_ids": [], "claim_versions": {}, "facts": []}
        )
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with store.stage_transaction(
                Stage.INGEST, config_version="fixture"
            ) as stage:
                for entity in reversed(graph):
                    store.put(entity)
                stage.finish(
                    StageOutcome.PASS, output_refs=[entity.ref for entity in graph]
                )
        for entity in graph:
            assert store.get(type(entity), entity.id, version="1") is None
            assert store.get(type(entity), entity.id, version="2") is None
        receipts = store.connection.execute("SELECT * FROM stage_runs").fetchall()
        assert len(receipts) == 1
        assert receipts[0]["outcome"] == "FAIL"
        assert json.loads(receipts[0]["output_refs"]) == []


def test_repository_links_must_reciprocate_exact_versions(tmp_path, evidence_graph):
    talk, segment, claim, repo, _, _ = evidence_graph
    revised_claim = claim.model_copy(update={"version": "2"})
    mismatched_repo = repo.model_copy(update={"claim_versions": {claim.id: "2"}})
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(ValueError, match="links must be reciprocal"):
            with store.stage_transaction(
                Stage.EXTRACT, config_version="fixture"
            ) as stage:
                for entity in (talk, segment, claim, revised_claim, mismatched_repo):
                    store.put(entity)
                stage.finish(StageOutcome.PASS)
        assert store.get(Claim, claim.id, version="1") is None
        assert store.get(Claim, claim.id, version="2") is None
        assert store.get(RepoRef, repo.id) is None
        receipt = store.connection.execute("SELECT * FROM stage_runs").fetchone()
        assert receipt["outcome"] == "FAIL"


def test_stage_output_dictionary_retains_exact_version_and_resolves(tmp_path, talk):
    revised = talk.model_copy(update={"version": "2", "title": "Revised talk"})
    with Store(tmp_path / "claims.sqlite3") as store:
        with store.stage_transaction(Stage.INGEST, config_version="fixture") as stage:
            store.put(talk)
            store.put(revised)
            stage.finish(
                StageOutcome.PASS,
                output_refs=[talk.ref.model_dump(), revised.ref.model_dump()],
            )
        receipt = store.connection.execute("SELECT * FROM stage_runs").fetchone()
        pins = json.loads(receipt["output_refs"])
        assert pins == [talk.ref.model_dump(), revised.ref.model_dump()]
        assert [store.resolve(EntityRef.model_validate(pin)) for pin in pins] == [
            talk,
            revised,
        ]


def test_stage_output_missing_version_is_rejected_and_rolled_back(tmp_path, talk):
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(ValueError, match="version"):
            with store.stage_transaction(
                Stage.INGEST, config_version="fixture"
            ) as stage:
                store.put(talk)
                stage.finish(
                    StageOutcome.PASS,
                    output_refs=[{"entity_type": "Talk", "id": talk.id}],
                )
        assert store.get(Talk, talk.id) is None
        receipt = store.connection.execute("SELECT * FROM stage_runs").fetchone()
        assert receipt["outcome"] == "FAIL"
        assert json.loads(receipt["output_refs"]) == []


def test_stage_output_cannot_resolve_to_another_existing_version(tmp_path, talk):
    missing_version = talk.ref.model_copy(update={"version": "2"})
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(ValueError, match="output"):
            with store.stage_transaction(
                Stage.INGEST, config_version="fixture"
            ) as stage:
                store.put(talk)
                stage.finish(StageOutcome.PASS, output_refs=[missing_version])
        assert store.get(Talk, talk.id) is None
        receipt = store.connection.execute("SELECT * FROM stage_runs").fetchone()
        assert receipt["outcome"] == "FAIL"
        assert json.loads(receipt["output_refs"]) == []


@pytest.mark.parametrize("reference", ["external_evidence", "citation"])
def test_nonmembership_claim_references_pin_the_exact_version(
    tmp_path, evidence_graph, reference
):
    graph = list(evidence_graph)
    if reference == "external_evidence":
        external_claim = graph[2].model_copy(
            update={
                "id": "external-claim",
                "version": "2",
                "repo_ref_ids": [],
                "repo_ref_versions": {},
            }
        )
        cluster_payload = graph[4].model_dump()
        cluster_payload["evidence_relations"].append(
            {
                "claim_id": external_claim.id,
                "claim_version": "1",
                "relation": "oppose",
                "rationale": "Synthetic opposition from an external candidate.",
            }
        )
        graph[4] = Cluster.model_validate(cluster_payload)
        graph.append(external_claim)
    else:
        skill_payload = graph[5].model_dump()
        skill_payload["citations"][0]["claim_version"] = "2"
        graph[5] = Skill.model_validate(skill_payload)
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with store.stage_transaction(
                Stage.INGEST, config_version="fixture"
            ) as stage:
                for entity in graph:
                    store.put(entity)
                stage.finish(
                    StageOutcome.PASS, output_refs=[entity.ref for entity in graph]
                )
        for entity in graph:
            assert store.resolve(entity.ref) is None
        receipt = store.connection.execute("SELECT * FROM stage_runs").fetchone()
        assert receipt["outcome"] == "FAIL"
        assert json.loads(receipt["output_refs"]) == []


def test_default_lookup_never_selects_latest_available_version(tmp_path, talk):
    revised = talk.model_copy(update={"version": "2"})
    with Store(tmp_path / "claims.sqlite3") as store:
        with store.stage_transaction(Stage.INGEST, config_version="fixture") as stage:
            store.put(revised)
            stage.finish(StageOutcome.PASS, output_refs=[revised.ref])
        assert store.get(Talk, talk.id) is None
        assert store.get(Talk, talk.id, version="1") is None
        assert store.resolve(talk.ref) is None
        assert store.get(Talk, talk.id, version="2") == revised


def test_two_versions_of_same_talk_count_as_one_supporting_identity(
    tmp_path, evidence_graph
):
    talk, segment, original_claim, _, original_cluster, _ = evidence_graph
    revised_talk = talk.model_copy(
        update={"version": "2", "transcript_sha256": "b" * 64}
    )
    second_segment = segment.model_copy(update={"id": "segment-2", "talk_version": "2"})
    first_claim = original_claim.model_copy(
        update={"repo_ref_ids": [], "repo_ref_versions": {}}
    )
    second_claim = first_claim.model_copy(
        update={"id": "claim-2", "segment_id": second_segment.id}
    )
    cluster = original_cluster.model_copy(
        update={
            "claim_ids": [first_claim.id, second_claim.id],
            "claim_versions": {first_claim.id: "1", second_claim.id: "1"},
            "supporting_claim_ids": [first_claim.id, second_claim.id],
            "evidence_relations": [],
        }
    )
    with Store(tmp_path / "claims.sqlite3") as store:
        with store.stage_transaction(Stage.CLUSTER, config_version="fixture") as stage:
            written = (
                talk,
                revised_talk,
                segment,
                second_segment,
                first_claim,
                second_claim,
                cluster,
            )
            for entity in written:
                store.put(entity)
            stage.finish(
                StageOutcome.PASS, output_refs=[entity.ref for entity in written]
            )
        counts = store.connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT talks.id) "
            "FROM cluster_claims AS links "
            "JOIN claims ON claims.id = links.claim_id "
            "AND claims.version = links.claim_version "
            "JOIN segments ON segments.id = claims.segment_id "
            "AND segments.version = claims.segment_version "
            "JOIN talks ON talks.id = segments.talk_id "
            "AND talks.version = segments.talk_version "
            "WHERE links.cluster_id = ? AND links.cluster_version = ? "
            "AND links.role = 'support'",
            (cluster.id, cluster.version),
        ).fetchone()
        assert tuple(counts) == (2, 1)
        stored_cluster = store.get(Cluster, cluster.id, version=cluster.version)
        assert stored_cluster.distinct_supporting_talk_count == 1
        assert stored_cluster.label.value == "insufficient"


@pytest.mark.parametrize("declared", ["none", "partial"])
def test_pass_with_unlisted_written_row_fails_loudly_and_rolls_back(
    tmp_path, talk, segment, declared
):
    output_refs = [] if declared == "none" else [talk.ref]
    with Store(tmp_path / "claims.sqlite3") as store:
        with pytest.raises(ValueError, match="missing from its output_refs") as error:
            with store.stage_transaction(
                Stage.INGEST, config_version="fixture"
            ) as stage:
                store.put(talk)
                store.put(segment)
                stage.finish(StageOutcome.PASS, output_refs=output_refs)
        assert "Segment:segment-1@1" in str(error.value)
        assert ("Talk:talk-1@1" in str(error.value)) is (declared == "none")
        assert store.get(Talk, talk.id) is None
        assert store.get(Segment, segment.id) is None
        receipts = store.connection.execute("SELECT * FROM stage_runs").fetchall()
        assert len(receipts) == 1
        assert receipts[0]["outcome"] == "FAIL"
        assert "missing from its output_refs" in receipts[0]["error"]
        assert json.loads(receipts[0]["output_refs"]) == []


def test_pass_receipt_listing_every_written_row_commits(tmp_path, evidence_graph):
    refs = [entity.ref for entity in evidence_graph]
    with Store(tmp_path / "claims.sqlite3") as store:
        with store.stage_transaction(Stage.INGEST, config_version="fixture") as stage:
            for entity in evidence_graph:
                store.put(entity)
            stage.finish(StageOutcome.PASS, output_refs=list(reversed(refs)))
        for entity in evidence_graph:
            assert store.resolve(entity.ref) == entity
        receipt = store.connection.execute("SELECT * FROM stage_runs").fetchone()
        assert receipt["outcome"] == "PASS"
        recorded = {
            EntityRef.model_validate(ref) for ref in json.loads(receipt["output_refs"])
        }
        assert recorded == set(refs)
