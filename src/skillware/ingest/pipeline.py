"""Transactional, deterministic ingestion of declared local/private Markdown."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from skillware.ingest.parser import (
    Issue,
    ParsedTranscript,
    canonical_url,
    merge_metadata,
    parse_markdown,
    validate_metadata,
)
from skillware.ingest.paths import contained_path, private_cache
from skillware.schema import FetchProvenance, Segment, Stage, StageOutcome, Talk
from skillware.store import Store

CONFIG_VERSION = "c03-ingest-v3"


@dataclass
class IngestResult:
    outcome: StageOutcome
    counts: dict[str, int]
    errors: list[str]
    receipt_path: Path
    input_hashes: dict[str, str] = field(default_factory=dict)


@dataclass
class Candidate:
    path: Path
    parsed: ParsedTranscript
    fetch: FetchProvenance | None = None

    @property
    def body_hash(self) -> str:
        return _hash(
            "".join(self.parsed.text[s.start : s.end] for s in self.parsed.segments)
        )


def _hash(value: str | bytes) -> str:
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _identity(metadata: dict[str, str | None], sha256: str) -> set[str]:
    keys = {f"hash:{sha256}"}
    if metadata.get("youtube_video_id"):
        keys.add(f"video:{metadata['youtube_video_id']}")
    if metadata.get("source_url"):
        keys.add(f"url:{canonical_url(metadata['source_url'])}")
    return keys


def _stored_aliases(talk: Talk) -> set[str]:
    aliases = json.loads(talk.ingest_provenance.get("identity_aliases", "[]"))
    if not isinstance(aliases, list) or any(
        not isinstance(key, str) for key in aliases
    ):
        raise ValueError("invalid stored identity aliases")
    return set(aliases)


def _talk_identity(talk: Talk) -> set[str]:
    keys = _identity(
        {"source_url": talk.source_url, "youtube_video_id": talk.youtube_video_id},
        talk.transcript_sha256,
    )
    aliases = _stored_aliases(talk)
    if talk.ingest_provenance.get("config_version") != CONFIG_VERSION:
        # v1 lost reserved path escapes/ports; v2 could collapse malformed or
        # reordered repeated query values. Only the original source URL can
        # reconstruct a trusted identity; legacy aliases lack that spelling.
        aliases = {key for key in aliases if not key.startswith("url:")}
    return keys | aliases


def _manifest(
    manifest: Path | None, root: Path, hashes: dict[str, str], issues: list[Issue]
) -> dict[Path, dict]:
    result = {}
    if manifest is None:
        return result
    try:
        manifest = contained_path(manifest, root, must_exist=True)
        raw = manifest.read_bytes()
        hashes[str(manifest)] = _hash(raw)
        for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise ValueError(f"manifest line {line_number} requires a string path")
            path = contained_path(manifest.parent / row["path"], root, must_exist=True)
            if path in result:
                raise ValueError(f"duplicate manifest path at line {line_number}")
            result[path] = row
    except FileNotFoundError as exc:
        issues.append(Issue(StageOutcome.BLOCKED, f"missing manifest input: {exc}"))
    except (ValueError, OSError) as exc:
        issues.append(Issue(StageOutcome.FAIL, f"manifest: {exc}"))
    return result


def _read_candidates(
    cache: Path,
    root: Path,
    manifest: Path | None,
    hashes: dict[str, str],
    issues: list[Issue],
) -> list[Candidate]:
    metadata = _manifest(manifest, root, hashes, issues)
    candidates = []
    try:
        cache = contained_path(cache, root, must_exist=True)
        if not cache.is_dir():
            raise ValueError("cache/input must be a directory")
        # Walk every entry so even a directory symlink Python would not recurse into
        # cannot conceal an unvalidated input outside the declared root.
        paths = []

        def walk_error(error: OSError) -> None:
            raise error

        for directory, directories, files in os.walk(
            cache, followlinks=False, onerror=walk_error
        ):
            for name in directories + files:
                entry = contained_path(Path(directory) / name, root, must_exist=True)
                if entry.is_file() and entry.suffix.lower() in {".md", ".markdown"}:
                    paths.append(entry)
        if not paths:
            issues.append(Issue(StageOutcome.BLOCKED, "no Markdown inputs found"))
        unseen = set(metadata) - set(paths)
        if unseen:
            issues.append(
                Issue(
                    StageOutcome.BLOCKED,
                    "manifest inputs are outside selected cache/input",
                )
            )
        for path in sorted(paths):
            raw = path.read_bytes()
            hashes[str(path)] = _hash(raw)
            try:
                parsed = parse_markdown(raw)
                merge_metadata(parsed, metadata.get(path, {}), origin="manifest")
                provenance = None
                sidecar = path.with_suffix(".json")
                if sidecar.exists() or sidecar.is_symlink():
                    sidecar = contained_path(sidecar, root, must_exist=True)
                    sidecar_raw = sidecar.read_bytes()
                    hashes[str(sidecar)] = _hash(sidecar_raw)
                    payload = json.loads(sidecar_raw)
                    if not isinstance(payload, dict):
                        raise ValueError("fetch metadata must be an object")
                    provenance = FetchProvenance.model_validate(
                        {
                            key: payload.get(key)
                            for key in ("source_url", "fetched_at", "content_sha256")
                        }
                    )
                    canonical_url(provenance.source_url)
                    if provenance.content_sha256 != parsed.sha256:
                        raise ValueError("fetch content hash does not match transcript")
                    # Acquisition provenance does not establish source attribution.
                    # Talk metadata must come from the transcript or manifest.
                validate_metadata(parsed)
                issues.extend(
                    Issue(i.outcome, f"{path.name}: {i.message}") for i in parsed.issues
                )
                candidates.append(Candidate(path, parsed, provenance))
            except (ValueError, UnicodeError, ValidationError) as exc:
                issues.append(Issue(StageOutcome.FAIL, f"{path.name}: {exc}"))
    except FileNotFoundError as exc:
        issues.append(Issue(StageOutcome.BLOCKED, f"missing input: {exc}"))
    except (ValueError, OSError) as exc:
        issues.append(Issue(StageOutcome.FAIL, str(exc)))
    return candidates


def _plan(
    candidates: list[Candidate],
    existing: list[Talk],
    issues: list[Issue],
    counts: dict[str, int],
) -> list[Talk | Segment]:
    planned: list[Talk | Segment] = []
    known = [(talk, _talk_identity(talk)) for talk in existing]
    uncertain_aliases = {
        alias
        for talk, identities in known
        for alias in _stored_aliases(talk) - identities
        if alias.startswith("url:")
    }
    # Components collect all strong identity matches before selecting any Talk.
    # Otherwise an alias encountered later could bridge two already written IDs.
    groups: list[tuple[list[Candidate], set[str]]] = []
    for candidate in candidates:
        if candidate.parsed.issues:
            continue
        keys = _identity(candidate.parsed.metadata, candidate.parsed.sha256)
        if keys & uncertain_aliases:
            issues.append(
                Issue(
                    StageOutcome.BLOCKED,
                    "undecided duplicate matches an unverified legacy URL alias",
                )
            )
            continue
        expanded = True
        while expanded:
            before = len(keys)
            for _, aliases in known:
                if keys & aliases:
                    keys |= aliases
            expanded = len(keys) != before
        members = [candidate]
        overlapping = [group for group in groups if keys & group[1]]
        for group in overlapping:
            members.extend(group[0])
            keys |= group[1]
            groups.remove(group)
        # Absorbing one component can reveal another transitive match.
        while any(keys & group[1] for group in groups):
            for group in list(groups):
                if keys & group[1]:
                    members.extend(group[0])
                    keys |= group[1]
                    groups.remove(group)
        groups.append((members, keys))
    resolved: list[tuple[list[Candidate], set[str], str]] = []
    for members, keys in groups:
        matches = {talk.id for talk, aliases in known if keys & aliases}
        if len(matches) > 1:
            issues.append(
                Issue(
                    StageOutcome.BLOCKED,
                    "undecided duplicate bridges existing identities",
                )
            )
            continue
        stable_key = next(
            key
            for prefix in ("video:", "url:", "hash:")
            for key in sorted(keys)
            if key.startswith(prefix)
        )
        talk_id = next(iter(matches)) if matches else f"talk-{_hash(stable_key)}"
        for old, aliases in known:
            if old.id == talk_id:
                keys |= aliases
        resolved.append((members, keys, talk_id))
    for members, keys, talk_id in resolved:
        fetch_records = {
            member.fetch.model_dump_json() for member in members if member.fetch
        }
        if len(fetch_records) > 1:
            issues.append(
                Issue(StageOutcome.BLOCKED, "undecided duplicate fetch provenance")
            )
            continue
        # Content-hash order, rather than filename, keeps rename-only runs stable.
        # A local copy must not erase retrieval provenance from a fetched copy.
        candidate = min(
            members,
            key=lambda member: (
                member.fetch is None,
                member.parsed.sha256,
                str(member.path),
            ),
        )
        parsed, metadata = candidate.parsed, candidate.parsed.metadata
        current = [talk for talk in existing if talk.id == talk_id]
        # A canonical source cannot silently acquire a contradictory video ID
        # on a later run. Expanded keys include the persisted identity history.
        distinct_ids = {
            key.removeprefix("video:") for key in keys if key.startswith("video:")
        }
        if len(distinct_ids) > 1:
            issues.append(
                Issue(StageOutcome.FAIL, "contradictory duplicate video identity")
            )
            continue
        contradiction = any(
            member.parsed.metadata.get(key) != metadata.get(key)
            for member in members
            for key in ("title", "speaker", "company")
        )
        # A manifest cannot silently reattribute unchanged source bytes on a
        # later run. Evaluate this across runs as well as within one input batch.
        contradiction |= any(
            old.transcript_sha256 == member.parsed.sha256
            and getattr(old, key) is not None
            and member.parsed.metadata.get(key) is not None
            and getattr(old, key) != member.parsed.metadata[key]
            for old in current
            for member in members
            for key in ("title", "speaker", "company")
        )
        if contradiction:
            issues.append(
                Issue(StageOutcome.FAIL, "contradictory duplicate attribution")
            )
            continue
        if len({member.body_hash for member in members}) != 1:
            issues.append(
                Issue(StageOutcome.BLOCKED, "undecided duplicate transcript revision")
            )
            continue
        # Same hash establishes a duplicate, but different unlinked source URLs
        # still require an attribution decision rather than an arbitrary source.
        urls = {
            canonical_url(member.parsed.metadata["source_url"]) for member in members
        }
        urls.update(
            alias.removeprefix("url:")
            for old in current
            for alias in _talk_identity(old)
            if alias.startswith("url:")
        )
        if len(urls) > 1 and not distinct_ids:
            issues.append(
                Issue(StageOutcome.BLOCKED, "undecided duplicate source attribution")
            )
            continue
        counts["duplicates"] += len(members) - 1
        normalized = metadata.copy()
        normalized["source_url"] = canonical_url(normalized["source_url"])
        if distinct_ids:
            normalized["youtube_video_id"] = next(iter(distinct_ids))
        # last_checked_at is intentionally absent: a conditional cache check
        # does not produce a new evidence version.
        fetch = candidate.fetch.model_dump(mode="json") if candidate.fetch else None
        version = _hash(
            _json(
                {
                    "config": CONFIG_VERSION,
                    "metadata": normalized,
                    "transcript_sha256": parsed.sha256,
                    "fetch": fetch,
                    "identity_aliases": sorted(keys),
                }
            )
        )
        if any(talk.version == version for talk in current):
            counts["unchanged"] += 1
            continue
        source_path = candidate.path
        immutable = candidate.path.parent / ".objects" / f"{parsed.sha256}.txt"
        if candidate.fetch and immutable.exists():
            immutable = contained_path(
                immutable, candidate.path.parent, must_exist=True
            )
            if _hash(immutable.read_bytes()) != parsed.sha256:
                issues.append(
                    Issue(StageOutcome.FAIL, "immutable fetch object hash mismatch")
                )
                continue
            source_path = immutable
        talk = Talk(
            id=talk_id,
            version=version,
            title=metadata.get("title"),
            speaker=metadata.get("speaker"),
            company=metadata.get("company"),
            source_url=metadata.get("source_url"),
            youtube_video_id=normalized.get("youtube_video_id"),
            transcript_path=str(source_path),
            transcript_sha256=parsed.sha256,
            fetch_provenance=candidate.fetch,
            ingest_provenance={
                "config_version": CONFIG_VERSION,
                "input_sha256": parsed.sha256,
                "identity_aliases": _json(sorted(keys)),
            },
        )
        planned.append(talk)
        for ordinal, span in enumerate(parsed.segments):
            planned.append(
                Segment(
                    id=f"segment-{_hash(f'{talk_id}:{ordinal}')}",
                    version=version,
                    talk_id=talk_id,
                    talk_version=version,
                    ordinal=ordinal,
                    text=parsed.text[span.start : span.end],
                    source_start=span.start,
                    source_end=span.end,
                    start_seconds=span.start_seconds,
                    end_seconds=span.end_seconds,
                    speaker_override=span.speaker
                    if span.speaker != talk.speaker
                    else None,
                    speaker_override_evidence=span.evidence
                    if span.speaker != talk.speaker
                    else None,
                )
            )
    return planned


def _write_receipt(path: Path, payload: dict) -> None:
    contained_path(path, path.parent)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=".ingest-", delete=False, encoding="utf-8"
    ) as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    try:
        contained_path(path, path.parent)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _preserve_sources(
    planned: list[Talk | Segment], candidates: list[Candidate], objects: Path
) -> None:
    """Pin exact parsed bytes before committing rows; never replace an object."""
    snapshots: dict[str, Path] = {}
    for candidate in candidates:
        content_hash = candidate.parsed.sha256
        # Reuse the fetcher's verified immutable history when available.
        source = candidate.path.parent / ".objects" / f"{content_hash}.txt"
        if candidate.fetch and source.exists():
            private_cache(source.parent, source.parent)
            source = contained_path(source, source.parent, must_exist=True)
            if _hash(source.read_bytes()) != content_hash:
                raise ValueError("immutable fetch object hash mismatch")
            snapshots[content_hash] = source
            continue
        root = private_cache(objects, objects)
        blob = contained_path(root / f"{content_hash}.txt", root)
        data = candidate.parsed.text.encode("utf-8")
        if _hash(data) != content_hash:
            raise ValueError("parsed transcript bytes do not match source hash")
        if not blob.exists():
            # A uniquely named temporary file and exclusive link avoid replacing
            # evidence if concurrent ingest creates the same object first.
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), 0o400)
                    try:
                        os.link(temporary, blob, follow_symlinks=False)
                    except FileExistsError:
                        pass
                finally:
                    temporary.unlink(missing_ok=True)
        contained_path(blob, root, must_exist=True)
        if _hash(blob.read_bytes()) != content_hash:
            raise ValueError("immutable local source object hash mismatch")
        snapshots.setdefault(content_hash, blob)
    for entity in planned:
        if isinstance(entity, Talk):
            entity.transcript_path = str(snapshots[entity.transcript_sha256])


def _check_source_history(existing: list[Talk], issues: list[Issue]) -> None:
    for talk in existing:
        source = Path(talk.transcript_path)
        if source.name != f"{talk.transcript_sha256}.txt":
            issues.append(
                Issue(
                    StageOutcome.BLOCKED,
                    "legacy mutable transcript history requires re-ingest into a "
                    "new database; existing versions were not changed",
                )
            )
            continue
        try:
            source = contained_path(source, source.parent, must_exist=True)
            if _hash(source.read_bytes()) != talk.transcript_sha256:
                raise ValueError("immutable source history hash mismatch")
        except (ValueError, OSError) as exc:
            issues.append(Issue(StageOutcome.FAIL, f"source history: {exc}"))


def ingest_transcripts(
    cache: Path,
    db: Path,
    root: Path,
    manifest: Path | None = None,
    objects: Path | None = None,
) -> IngestResult:
    """Persist one all-or-nothing ingest stage and a private JSON stage receipt."""
    db = contained_path(db, db.parent)
    receipt_path = contained_path(db.with_suffix(db.suffix + ".ingest.json"), db.parent)
    outputs = {db, receipt_path}
    for suffix in ("-journal", "-wal", "-shm"):
        outputs.add(contained_path(Path(str(db) + suffix), db.parent))
    hashes: dict[str, str] = {}
    issues: list[Issue] = []
    candidates = _read_candidates(cache, root, manifest, hashes, issues)
    if outputs & {Path(path) for path in hashes}:
        raise ValueError("database or receipt output collides with an ingest input")
    counts = {
        "inputs": len(candidates),
        "talks": 0,
        "segments": 0,
        "duplicates": 0,
        "unchanged": 0,
        "blocked": 0,
        "failed": 0,
    }
    refs = []
    with Store(db) as store:
        db.chmod(0o600)
        existing = [
            Talk.model_validate_json(row[0])
            for row in store.connection.execute("SELECT payload FROM talks")
        ]
        planned = []
        if not issues:
            try:
                planned = _plan(candidates, existing, issues, counts)
            except (ValueError, OSError) as exc:
                issues.append(Issue(StageOutcome.FAIL, f"ingest planning: {exc}"))
        if not issues:
            _check_source_history(existing, issues)
        if not issues:
            try:
                _preserve_sources(
                    planned,
                    candidates,
                    objects or db.with_suffix(db.suffix + ".objects"),
                )
            except (ValueError, OSError) as exc:
                issues.append(Issue(StageOutcome.FAIL, f"source preservation: {exc}"))
        outcome = (
            StageOutcome.FAIL
            if any(i.outcome == StageOutcome.FAIL for i in issues)
            else StageOutcome.BLOCKED
            if issues
            else StageOutcome.PASS
        )
        counts["blocked"] = sum(i.outcome == StageOutcome.BLOCKED for i in issues)
        counts["failed"] = sum(i.outcome == StageOutcome.FAIL for i in issues)
        if outcome == StageOutcome.PASS:
            counts["talks"] = sum(isinstance(entity, Talk) for entity in planned)
            counts["segments"] = sum(isinstance(entity, Segment) for entity in planned)
            refs = [entity.ref for entity in planned]
        errors = [f"{issue.outcome.value}: {issue.message}" for issue in issues]
        with store.stage_transaction(
            Stage.INGEST, config_version=CONFIG_VERSION, input_hashes=hashes
        ) as attempt:
            if outcome == StageOutcome.PASS:
                for entity in planned:
                    store.put(entity)
            attempt.finish(
                outcome,
                output_refs=refs,
                error=_json({"counts": counts, "errors": errors}) if errors else None,
            )
        receipt = {
            "stage": "ingest",
            "id": attempt.id,
            "config_version": CONFIG_VERSION,
            "input_hashes": hashes,
            "counts": counts,
            "outcome": outcome.value,
            "errors": errors,
            "output_refs": [ref.model_dump() for ref in refs],
            "finished_at": datetime.now(UTC).isoformat(),
        }
    _write_receipt(receipt_path, receipt)
    return IngestResult(outcome, counts, errors, receipt_path, hashes)
