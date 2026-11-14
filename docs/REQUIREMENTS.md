# Skillware requirements

C03 extends the historical C01 design and C02 scaffold with AI Engineer Markdown
fetching and local ingest. The assigned base is
`main@89375493eb8de2d446288b413652881c5c04b5bb`; the branch is `aw/c03-ingest`.
The old C01 documentation-only delivery limits and seat identities do not define
this contract. C03 explicitly requires an ingest delta in this document,
`docs/SPEC.md`, and `docs/ARCHITECTURE.md`.

The original due-diligence Page was unavailable in C01. Its three supplied topics
remain cross-talk consensus/contested evidence, repository mining, and evals;
this ingest work does not claim a new comparison against the missing Page.

## Requirements

Taken from the objective, README.md, LICENSE, and the Agent Skills spec at https://agentskills.io/specification. Both product docs carry these.

### Product

- Goal: turn AI Engineer talks into reusable Agent Skills a coding agent can load.
- MVP pack name is exactly `The AI Engineer Playbook`.
- Later, outside the MVP: paste any channel.
- MVP ingest politely fetches Markdown transcripts from AI Engineer into a private external cache and also accepts local Markdown plus source metadata. The repository and release pack contain no real full-talk corpus.
- Pipeline order is fixed: ingest → extract → cluster → synthesize → validate → eval.
- Entities in `src/skillware/schema.py` are exactly: Talk, Segment, Claim, RepoRef, Cluster, Skill. The architect may add fields. The six entity names stay fixed.

### Minimum fields

Forced by citations and the Page gap:

- Talk: speaker, nullable company, title, transcript path and SHA-256, source URL, nullable YouTube video ID, and nullable fetch provenance containing source URL, UTC fetch date, and exact content hash. Unknown company stays explicit and is never invented.
- Segment: Talk ID and pinned version, order, private text, exact source character offsets, nullable start/end seconds parsed from transcript timestamps with original precision, and source-backed speaker overrides. Missing timing stays null; it is never invented.
- Claim: one practice statement, one short quote, and a resolvable citation.
- RepoRef: canonical URL plus host/owner/name, linked to the claim.
- Cluster: member claim ids and a cross-talk label.
- Skill: pack name, Agent Skills `name` and `description`, body, supporting clusters, and citations.

### C03 ingest acceptance

- `uv run skillware ingest fetch --out <cache>` retrieves AI Engineer Markdown with an identifiable User-Agent, robots compliance, rate limits, ETag/Last-Modified conditional caching, and resumable per-transcript progress. Robots groups use the longest case-insensitive agent prefix, with wildcard path rules as fallback when no specific path rule applies; pacing-only groups cannot bypass wildcard restrictions. Repeated `--url` can select AI Engineer discovery or transcript URLs; arbitrary channel/video scraping is out of scope.
- Raw Markdown and its source URL, UTC fetch date, and content SHA-256 persist together in a private cache outside the repository and release pack. A 304 or same-byte 200 at the same source URL reuses the original fetch date/version, even if ETag/Last-Modified validators change. Changed content, source URL, or source fetch date is versioned at ingest.
- `uv run skillware ingest --cache <dir> --db <path> [--manifest <jsonl>] [--objects <dir>]` parses cached or local Markdown into Talk/Segment rows. Metadata comes from scalar transcript/frontmatter or the local UTF-8 JSONL manifest, never guessed filenames or model output. The fetch provenance URL records acquisition only and cannot fill missing source attribution. Manifest `path` is relative to its parent directory; SPEC defines supported fields and syntax.
- `--cache` or fetch `--out` declares its root by default; optional `--root <root>` constrains paths to an explicit enclosing root. Inputs, manifests, and cache artifacts outside the declared root are rejected, as are all supplied symlinks including root aliases; use the real non-symlink root spelling. The database path is an explicit output declaration.
- Duplicate checks use video ID, canonical URL, and transcript hash across filenames and URL variants. Known duplicates share a stable identity; undecided duplicates are recorded as BLOCKED and never create two independent Talks. Matching title/speaker text or matching host/path with distinct retained query parameters alone is not an identity match.
- URL identity preserves reserved path escapes and explicit non-default ports, rejects invalid query percent escapes and UTF-8 encodings, and retains repeated query parameter order. Ingest `c03-ingest-v3` blocks unverifiable aliases from earlier lossy normalization. Cross-run manifest changes cannot silently contradict unchanged source attribution; duplicate copies must retain fetch provenance or BLOCK conflicting records.
- Explicit `Name:`, `**Name:**`, `**Name**:`, or `[Speaker: Name]` labels provide source-backed speaker overrides; plain names include Unicode names such as `José García:`. Colon-less bold prose is not a speaker label.
- Missing inputs and missing/ambiguous speaker/source attribution are BLOCKED. Contradictory attribution is FAIL. Both appear in the stage receipt and return a nonzero CLI status. Directory enumeration errors fail the attempt rather than silently omitting unreadable inputs. Missing inputs with a safe database destination get a persisted receipt; malformed declarations without a safe output destination emit a BLOCKED JSON receipt on stdout.
- Unchanged reruns reuse the same IDs and versions. Changed transcripts or fetch provenance create new immutable versions with provenance, retaining earlier evidence.
- Every new Talk pins exact source bytes in a private content-addressed object store before its rows commit. Local snapshots default to `<db>.objects`; optional `--objects <dir>` declares another root, outside repositories/release packs and subject to path/symlink containment. Existing immutable fetch `.objects` are reused. Before a PASS rerun all stored history paths and hashes are checked, so edited/renamed inputs cannot invalidate previous offsets. Missing/corrupt objects FAIL; legacy mutable paths BLOCK and require re-ingest from available originals into a fresh database without rewriting history.
- The JSON receipt beside the database contains configuration version, input hashes, counts, outcome, and per-input diagnostics. SQLite stage receipts retain configuration, hashes, outcome, errors, and exact output references. Every PASS stage lists every written Talk/Segment version in `output_refs`; FAIL/BLOCKED cannot commit partial passing entity output.
- Offline pytest uses at least 20 recorded synthetic/redacted Markdown transcripts and recorded HTTP fixtures under `tests/fixtures/`, never real full-talk content or a live site/model. Cover timing, offsets, deduplication including undecided cases, attribution, idempotency/versioning, containment, robots/rate limits/cache/resume, and fetch URL/date/hash persistence.
- Required checks: `uv sync && uv run pytest -q`, `uv run ruff check . && uv run ruff format --check .`, CLI checks, `git diff --check`, and `git diff --stat`. Include untracked files. Use `UV_CACHE_DIR=/tmp/skillware-c03-uv-cache UV_OFFLINE=1`; a missing dependency or unrun check is BLOCKED.

### Page gap

Both docs use the same labels.

- Cross-talk: consensus requires support from at least two distinct talks and no contradiction among them. Contested means the talks disagree, and the skill cites both sides. A single talk, or support too thin to call, is not consensus; label that `insufficient`. Do not average a disagreement into a fake consensus.
- An undecided duplicate check, including unresolved talk identity or possibly-same URLs, makes the affected cluster assessment BLOCKED until resolved. Uncertain identities never count as independent talks or inflate consensus.
- After any claim is dropped, including span check failure or missing attribution, validate recomputes distinct supporting and opposing talk counts from remaining publishable claims and re-derives labels. Excluded opposition must not erase known contradictions or unresolved relations to manufacture consensus; block the affected assessment until resolved. Changed membership or labels invalidate dependent drafts, which must be re-synthesized and revalidated before eval.
- Repo mining: RepoRef is first-class. Mining records identity, license, and what was claimed versus what is present, tied back to the Claim. Third-party source is not vendored into the pack.
- Evals: the eval stage exists in the spec and in `src/skillware/evals`. Missing gold labels do not delete the stage.

### Citation, license, frontmatter

- Every published quote cites speaker, company, and talk, plus a YouTube timestamp link built from the transcript's video id and start time (`t=` seconds). `src/skillware/links` owns that formatting. A quote with no timestamp is not published with a timestamp link.
- Missing or ambiguous speaker/source attribution makes the claim BLOCKED, not merely flagged; contradicted attribution or a failed span check makes it FAIL. Neither can be published or counted as supporting/opposing publication evidence. Unknown company remains explicitly unknown.
- SPEC states a numeric short-quote cap so validate can reject transcript dumps. The number is an architect assumption, written the same way in both docs, and is not an Arpan question.
- Code license stays Apache-2.0, matching LICENSE and README.md. The project claims no rights in talk content; rights remain with their respective rights holders. Publish short quotes and links. Do not republish full transcripts. Ask AI Engineer before launch: this is the C12 launch gate, not a C03 acceptance prerequisite. A2 quote limits stay unchanged.
- SKILL.md YAML frontmatter follows https://agentskills.io/specification. Required: `name` (1–64 chars, lowercase letters, numbers, hyphens; no leading, trailing, or consecutive hyphens; matches the skill directory) and `description` (1–1024 chars, what it does and when to use it). Optional: `license`, `compatibility` (max 500 characters if present), `metadata` (string-to-string map), `allowed-tools` (experimental). Cursor-only keys such as `disable-model-invocation` are not required.

### Architecture

- Python 3.12, uv, Typer, Pydantic v2, Ruff, and pytest. Package modules are exactly `src/skillware/{cli,schema,ingest,extract,cluster,synth,links,evals}`.
- Claim store is local SQLite (A1); JSONL is optional interchange. It supports versioned evidence and claim, cluster, and eval queries. No hosted database.
- LLM layer is provider-agnostic (provider id, model id, CLI-backed adapter). Extraction uses existing CLI subscriptions only: no paid API budget, API keys, Anthropic credits, or automatic provider fallback. `grok-4.7-high` is not locked.
- Embeddings and clustering do not require a paid API key. Clustering records its seed and emits `consensus`, `contested`, or `insufficient`.
- Tests/CI use Python 3.12, uv, fixture adapters and recorded HTTP, without network, live models, or secrets. C03 does not modify workflows.
- Protected paths: secrets and env files; gold-label and eval fixtures once they exist (human gate `evaluation-change`); never weaken tests; do not commit `.cursor/`.

## Constraints

- C03 writes only the assigned ingest implementation, supporting fixtures/tests, sanitized verification evidence under `evidence/`, and the required delta in these three documents. Evidence may record commands, outcomes and hashes, but must use neutral placeholders for private host paths. Do not edit LICENSE, workflows, secrets, local agent artifacts, or reviewed eval gold/rubrics/thresholds/baselines.
- Developer evidence records assignment/attempt, exact base and branch, changed files, exact commands and exit codes, pass/fail/skip counts, diff checks, assumptions, deviations, and blockers. Include untracked files and mark every unrun check BLOCKED.
- Use Agent World scoped messages and evidence. A developer submission is a claim, never completion; independent testers rerun checks and record a review. Command-free reviewers cite tester evidence.
- SPEC and ARCHITECTURE use the same entity names, stage names, module names, storage assumption, license stance, and Page-gap labels.
- Claims in a broker submission are truncated at 500 characters, with at most 30 claims. The file is the durable record.

## Non-goals

- No hosted YouTube scraper.
- No web UI in the MVP.
- No paired-mode HTTP app.
- No video downloads, arbitrary source acquisition, or live LLM calls in C03/tests.
- No commit, push, PR, merge, repo-setting changes, publication, or secrets.
- Arpan's PR approval or explicit comment is a post-run human merge gate, not a C03 success criterion. Agents cannot satisfy it; it is not one of the four SPEC open questions.

## Decisions

### Arpan-only open questions

SPEC Open questions lists only these items, and no team decisions:

- License email to AI Engineer before launch.
- Whether any LLM API budget or key will exist. Until answered, CLI subscriptions only.
- Whether `grok-4.7-high` is the extraction and synthesis model.
- Whether Arpan will commit a gold-label set for evals.

### Architect-owned assumptions

The architect records each choice in ARCHITECTURE and SPEC uses the same choice:

- A1: local SQLite is the authoritative claim store; JSONL is optional interchange.
- The numeric short-quote cap.
- How embeddings run without a paid API key, and the clustering seed policy, within the label rules above.
