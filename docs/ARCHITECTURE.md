# skillware architecture

Status: C03 ingest architecture, extending the C01 design and C02 scaffold
under [SPEC.md](SPEC.md). C01 delivery restrictions are historical; C03 adds the
AI Engineer Markdown fetcher, private cache, local parsing, and their offline
fixtures. Downstream stages remain separate contracts.

## Scope and runtime

Use **Python 3.12**, **uv** for the environment and locked dependencies, and
**Typer** for a local CLI. The first output is **The AI Engineer Playbook**:
reusable Agent Skills grounded in AI Engineer talks. MVP inputs are AI Engineer
Markdown retrieved by the polite fetcher or supplied locally, source metadata,
and optional local repository snapshots. Fetched transcripts stay in an
operator-declared private cache outside the repository and release pack.
Later “paste any channel” acquisition needs separate authorization and design.

There is no hosted YouTube scraper, web UI in the MVP, paired-mode HTTP app, or
hosted database. C03 retrieves Markdown from AI Engineer; it does not download
videos or clone repositories. Tests and CI use recorded fixtures with no network
or live LLM calls. Production model work assumes existing CLI subscriptions
only: **no paid API budget and no Anthropic credits**.

## Package boundaries

Package boundaries (later stages retain their separate implementation scope):

```text
src/skillware/
  cli.py
  schema.py
  ingest/
  extract/
  cluster/
  synth/
  links/
  evals/
```

| Boundary | Responsibility |
| --- | --- |
| `cli` | Typer stage commands and local orchestration; explicit input/output roots, configuration, run receipts, and exit status. Expose ingest, extract, cluster, synthesize, validate, and eval separately and in that order. |
| `schema` | The six typed entities, serialization versions, reference constraints, stage outcomes, and report contracts. It contains no provider-specific logic. |
| `ingest` | Polite AI Engineer Markdown fetching into a private cache; cached/local metadata parsing, stable source identity, exact source offsets/timing, and versioned SQLite writes through the shared store. Injectable HTTP transport keeps tests offline. |
| `extract` | Atomic claim/repository-mention extraction, schema/span checks, and the shared provider-neutral LLM protocol with CLI and fixture adapters. |
| `cluster` | Local embeddings, candidate groups, evidence relation assessment, cross-talk labels, and reproducibility metadata. |
| `synth` | Grounded skill drafting and explicit `validate` orchestration over schema, citations, quotes, and artifact contents. Both cluster assessment and synthesis reuse the neutral LLM protocol. |
| `links` | Citation/timestamp formatting, canonical repository references, and read-only inspection of local snapshots. No repository cloning or execution. |
| `evals` | Reviewed fixtures, metric computation, rubric/version tracking, task outcome reports, and explicit blocked checks. |

This keeps the required `src/skillware/{cli,schema,ingest,extract,cluster,synth,links,evals}`
layout. Validation is a distinct stage owned by `synth`, not an omitted stage or
an extra top-level package. Imports use typed contracts; stage workers do not
reach into each other's internal tables or adapter implementations.

## Shared schema and storage

`src/skillware/schema.py` defines the entity fields in SPEC. The following
mapping constrains storage and versioned references.

| Entity | Storage and invariants |
| --- | --- |
| `Talk` | Source identity, title/speaker/nullable company, source URL/nullable video ID, local transcript path/hash, and nullable fetch provenance (source URL, UTC fetch date, exact content SHA-256). Deduplicate the same talk across filenames, URLs, and transcript versions before counting support. An undecided duplicate check blocks the affected cluster assessment; uncertain identities never count independently. |
| `Segment` | Foreign key to Talk, ordinal, private text, source offsets, nullable start/end seconds, and source-backed speaker override. Offsets resolve within the hashed source; ordered timing must be nonnegative. Never invent missing times. |
| `Claim` | Foreign key to Segment, atomic statement/applicability, quote and exact offsets, citation status, repository links, and extraction receipt. Quote text must match the referenced span after whitespace-only normalization. |
| `RepoRef` | Canonical URL/host/owner/name; many-to-many claim links; inspection state; pinned commit; license/unknown and evidence; claimed/observed facts with file paths, lines, and blob hashes. |
| `Cluster` | Scoped proposition; member, support, and opposition links; evidence relations; deduplicated distinct-talk count plus separate distinct supporting/opposing talk counts; label/rationale; assessment state; configuration hash. Validation derives publication counts and labels from publishable claims. A changed claim invalidates dependent assessments. |
| `Skill` | Pack/name/description/body; cluster and citation links; version; validation and eval states. All cited claim paths must resolve through Segment to Talk. |

**ASSUMPTION A1 — storage:** local SQLite at `.skillware/claims.sqlite3` is
authoritative. JSONL is optional versioned import/export only. Use foreign-key
enforcement, typed writes, schema versions, and transactions at stage boundaries.
Join tables represent claim/repository and cluster/skill membership; internal
run/report tables retain attempts, statuses, errors, and input/output hashes.
These support the six product entities rather than expanding that entity list.

Hash source content and configuration. Keep stable source IDs separate from
version IDs so retries do not manufacture new talks or inflate consensus.
Unchanged transcript/metadata/fetch provenance reuses existing versions; changed
transcript or fetch provenance produces a new immutable evidence version.
Pin an output to its exact evidence versions; changed inputs mark downstream
artifacts stale and require revalidation/evaluation. Stage output and completion
receipts commit together. Record a failed attempt separately without overwriting
the last successful version. Never interpret an empty response as successful work.

Transcripts, full segment text, database files, vectors, caches, and raw adapter
responses live in operator-controlled private working storage. They are not
release artifacts. Export through an explicit allowlist of generated Markdown and
an evidence manifest, using paths rooted under a declared output directory.
The manifest carries IDs/hashes, source links, revisions, and report references;
it excludes transcript bodies, raw responses, credentials, and private local paths.

## AI Engineer acquisition and local ingest

`ingest fetch --out <cache>` uses an identifiable User-Agent, `robots.txt`,
rate limiting, conditional ETag/Last-Modified requests, and atomic per-transcript
cache updates. A completed entry stores Markdown plus source URL, UTC fetch date,
content hash, and HTTP validators. A 304 or same-byte 200 at the same source URL
retains the original fetch date and ingest version, even when validators change.
Changed content, source URL, or source fetch date creates a new ingest version.
Interrupted fetches retain completed entries and resume on rerun.
Robots groups use the longest case-insensitive agent prefix of the Skillware
product token rather than a substring match. Wildcard rules remain available
when no specific path rule applies, including for a specific group containing
only pacing directives; applicable crawl delays and request rates still apply.
Discovery stays on the AI Engineer site; explicit repeated `--url` arguments may
select transcript or index URLs. HTTP redirects must obey the same source and
robots restrictions as initial requests. Denials and unavailable prerequisites
produce a non-success receipt; they are not silently treated as an empty corpus.

`ingest --cache <dir> --db <path> [--manifest <jsonl>] [--objects <dir>]`
parses cached/local files without network requests; acquisition is a separate
fetch subcommand. The input directory defaults to its own declared root;
`--root <root>` permits an
explicit enclosing root. The manifest and each discovered file must stay within
that root. All supplied symlink components, including root aliases, are rejected;
operators use the real non-symlink spelling of a root. Fetch has the same `--root` option,
with `--out` declaring the root by default; a fetched cache may not reside in the
repository or release pack. The explicit database path declares its write target.
Metadata comes from scalar transcript/frontmatter fields or a UTF-8 JSONL
manifest as described in SPEC; manifest paths are relative to its parent directory.
The fetch provenance URL records acquisition and cannot supply missing source
attribution. Directory enumeration errors propagate into a non-PASS receipt;
unreadable subtrees cannot silently yield a partial passing corpus.
Contradictions are rejected rather than resolved through a guessed priority.

Before any Talk rows commit, source preservation pins the exact parsed bytes in
a private content-addressed object store outside repositories and release packs.
The default root for local snapshots is `<db>.objects`; `--objects <dir>` declares
an alternate root, with path and symlink containment enforced. Existing immutable
fetch-cache `.objects` are reused. Talk paths reference `<transcript_sha256>.txt`
objects; a source rename or edit cannot invalidate earlier Segment offsets.
Objects are never replaced. Every otherwise-passing rerun checks all stored
source paths and hashes, including versions not changed by the current inputs.
Missing or corrupt history fails the stage. Legacy mutable source paths block
it and require re-ingest from available originals into a fresh database;
historical rows are never rewritten to claim preservation retrospectively.

Parsing retains exact Unicode character offsets against the hashed original
source, nullable Decimal timestamps only when present, and evidence for any
speaker override. Explicit `Name:`, `**Name:**`, `**Name**:`, and
`[Speaker: Name]` labels supply speaker evidence; plain names support Unicode,
including `José García:`. Colon-less bold prose cannot supply a speaker override.
Identity matching uses video ID, canonical URL, and content hash across filenames
and URL variants. Unresolved matches block the attempt;
they cannot become two independent Talks. Equal title/speaker text or equal
host/path with distinct retained query parameters alone does not establish an
identity match. The `c03-ingest-v3` identity normalization preserves reserved
percent-encoded path characters and non-default ports as distinct identities. Only unreserved path
escapes are decoded. Invalid query percent escapes and UTF-8 encodings are
rejected, and repeated query parameter values keep their original order.
Unverifiable legacy URL aliases block a match instead of silently linking another
source. Cross-run manifest attribution conflicts are
checked against unchanged source hashes. A fetched duplicate is preferred to a
copy without fetch provenance; conflicting duplicate fetch provenance blocks the
attempt.
The store commits Talk and Segment versions only for PASS, preserving older
versions and their pinned references.
The JSON receipt beside the database includes counts, input hashes, configuration
version, outcome, and diagnostic reasons. The SQLite stage attempt records hashes,
configuration, outcome, errors, and exact output references. PASS `output_refs`
lists every written row, including each Segment version. Missing inputs with a
safe database destination get a persisted BLOCKED receipt; malformed declarations
with no safe output destination instead emit a BLOCKED JSON receipt on stdout.

HTTP tests inject recorded responses, a clock, and a sleep function. No test or
CI run contacts AI Engineer, a live model, or any other network service. Fixture
transcripts are synthetic or redacted and live under `tests/fixtures/`; raw
real-world transcripts remain private external working data.

## Stage orchestration and failure propagation

The command contract is **ingest → extract → cluster → synthesize → validate → eval**.
Every stage receives typed references to committed inputs and writes a receipt
with stage/configuration version, source hashes, artifact references, and outcome.

| Stage | Output and prerequisite |
| --- | --- |
| ingest | Talks/Segments from declared cached/local Markdown, with fetch provenance when retrieved. Missing inputs, missing/ambiguous speaker/source attribution, or undecided duplicates are BLOCKED; contradictory attribution is FAIL. Retain exact source offsets and actual nullable timing. Fetch and ingest receipts expose counts and failures. |
| extract | Claims and repository mentions from eligible segments. Accept only schema-valid responses with exact source spans. Missing metadata can leave research records ineligible for publication. |
| cluster | Candidate groups and reviewed semantic relations from valid claims. Zero claims blocks; unsupported or unresolved evidence becomes insufficient. |
| synthesize | Draft Skills from reviewed consensus/contested clusters with eligible citations. Contested output preserves both sides; insufficient clusters stay internal. |
| validate | A structural/provenance/release-content report over the entire candidate pack, including aggregate quote counts. After any claim is dropped, recompute distinct supporting/opposing talk counts from remaining publishable claims and re-derive labels. Invalidate affected drafts; invalid drafts cannot proceed as validated artifacts. |
| eval | Versioned metric/task reports over validated artifacts and reviewed reference data. Missing labels, criteria, or runnable tasks are BLOCKED, with no fabricated score. |

Use `PASS`, `FAIL`, and `BLOCKED` consistently with SPEC. Failures and blocked
required checks produce non-success CLI status and remain visible in the final
run report. Partial diagnostic output is permitted but cannot be labeled a passing
release. A resume uses verified input hashes and stage receipts; it cannot reuse
stale reports. Offline fixture evidence establishes C03 behavior, not downstream
quality or live-site compatibility.

## Provider-agnostic LLM boundary

Define a neutral operation such as
`generate_json(task, payload, response_schema, limits, run_context)` returning a
typed result plus receipt, or a typed error. Configure `provider_id`, `model_id`,
and CLI adapter independently. Extraction, evidence relation proposals, and skill
synthesis share the interface; domain entities do not embed SDK response types.

The production path uses an already authorized CLI subscription. No API key,
paid API budget, Anthropic credits, billing setup, or automatic provider fallback
is assumed. The `grok-4.7-high` choice and availability are unresolved; record the
actual configured model identity and its receipt, not an inferred alias.

Use configured executable paths and argument arrays, with source data on stdin
or in declared input files. Never interpolate transcript/repository/model text
into shell commands. Limit duration, output size, concurrency, and retries; record
attempts and malformed responses. Missing executables, subscription limits,
authorization denial, timeout, invalid JSON, or unsupported response schemas
surface as typed errors and block or fail the affected stage as appropriate.

Constrain tool access, file access, working directory, and network behavior to
the approved model interaction. A CLI that cannot be constrained to data-only
processing is an unsupported adapter, not permission to broaden access. Do not
read credentials or capture them in prompts/logs. Model transport is distinct
from transcript ingestion; these constraints do not imply the existing CLI
subscription can run offline. No such transport runs in C03 or fixture CI.

For CI, a fixture adapter supplies fixed responses and errors. It verifies
integration contracts without a subscription or network connection; it does not
prove extraction quality. Validate all generated claims against actual input
spans, and require review of semantic judgments before release.

## Embeddings, clustering, and semantic assessment

**ASSUMPTION A3 — candidate clustering:** form TF-IDF unigram/bigram vectors
locally, retaining negation. Optionally apply truncated SVD with seed `0` and
dimension `min(128, n_claims - 1, n_features - 1)` if that value is at least `2`;
otherwise record a raw TF-IDF fallback. L2-normalize, then use average-link
agglomerative clustering at cosine distance `0.35`. This lexical embedding
baseline needs no paid API key, model service, or downloaded embedding model.
Dependencies will be pinned through uv in later implementation work.

Stable-sort inputs by claim ID, define deterministic tie handling, and record the
ordered input hash, preprocessing/configuration, dependency versions, vocabulary
and transform hash, actual dimension/fallback, threshold, and seed. Zero claims
means BLOCKED. Singleton and zero-vector claims are insufficient; keep them out
of cosine comparisons that would be undefined. Changing these settings creates
a new run rather than silently altering an existing evaluation.

Candidate proximity does not establish support or contradiction. Assess the
scoped proposition in context, recording support, opposition, or unresolved
relations with claim IDs and rationale. Search related candidates for opposing
claims even if they clustered separately. An adapter may propose relations;
review must settle them before publication. Preserve unresolved evidence.

Label rules are shared with SPEC: `consensus` needs at least two deduplicated
distinct talks supporting the same scoped proposition, no contradictory reviewed
evidence, and no unresolved relevant relation. `contested` needs comparable
cross-talk opposition and retains both citations. `insufficient` covers single,
thin, or unresolved evidence. Counts of quotes, chunks, or retries cannot stand
in for distinct talks. Consensus is limited to the reviewed corpus.

An undecided duplicate check, including unresolved talk identity or possibly-same
URLs, makes the affected cluster assessment BLOCKED until resolved. Such records
must never count as independent talks. Validation repeats identity checks on the
remaining publishable evidence before recomputing separate distinct supporting
and opposing talk counts; cached counts are not authoritative.

These settings are an explicit starting assumption, not a measured quality claim.
Semantic relation errors and missed opposition require eval coverage. Do not
claim reproducible CLI text merely because local vector operations record a seed.

## Citations, repository evidence, and release validation

`links` traverses `Skill → Cluster → Claim → Segment → Talk`. Format speaker,
company (or `company unknown`), and talk title with a YouTube timestamp link:
`https://www.youtube.com/watch?v=VIDEO_ID&t=SECONDS`. Use a verified video ID
and source segment start; floor only the URL offset for fractional times. Retain
the precise source value and label it as a segment anchor. Missing time never
becomes zero. Missing or ambiguous speaker/source attribution, video, time, or
span makes the claim BLOCKED; contradicted attribution or a failed span check
makes it FAIL. These claims are non-publishable and cannot count as supporting
or opposing publication evidence. Unknown company stays explicit; source-backed
zero is valid. Paraphrases and both sides of a dispute also cite.

**ASSUMPTION A2 — quote budget:** at most **25 whitespace-delimited words per
verbatim quote** and **100 quoted words per Talk across an entire release pack**,
including repeated occurrences and all evidence notes. Require contiguous spans
with whitespace-only normalization. Validate the aggregate export, and reject
transcript reconstruction or raw-data dumps regardless of how files are split.
These editorial caps confer no licensing clearance.

`RepoRef` creation records a mention, not verification. Inspect only declared local
snapshots, with path/symlink containment and no code or hook execution. Record
canonical identity, pinned commit, license/unknown and its evidence, and
claimed-versus-observed facts with path/line/blob hashes. Keep `mentioned`,
`unavailable`, `inspected`, and `mismatch` states explicit. Static observations
never imply runtime correctness. A revision mismatch or missing snapshot leaves
implementation claims unverified. A skill may include a clearly marked mention;
assertions about repository behavior require grounded observations.

`synth` owns whole-pack validation: six-entity references and versions, source
spans, citation completeness, cluster eligibility, repository assertion grounding,
quote budgets, output paths/allowlist, and the Agent Skills frontmatter contract
in SPEC. Parse YAML safely and validate the skill name against its directory.
MVP output is authored Markdown and an evidence manifest, with no generated
executable scripts. Raw transcripts, DB files, prompts/responses, repository
source, and local environment files cannot pass the export allowlist.

After every claim exclusion, including span check failure or missing attribution,
`validate` recomputes distinct supporting and opposing talk counts from claims
that remain publishable and re-derives labels from those counts and reviewed
relations. Losing one of two supporting talks removes consensus eligibility.
Record excluded claims and reasons internally. Dropping opposition must not erase
a known contradiction or unresolved relation and manufacture consensus; block
the affected assessment until that evidence is resolved. Membership or label
changes invalidate dependent drafts, which must be re-synthesized and revalidated
before eval. Persist revised memberships, counts, labels, and validation receipts
together so a stale label cannot survive filtering.

The code's Apache-2.0 license does not relicense talks. The project claims no rights
in talk content; rights remain with their respective rights holders. Publish short
quotes and links, never full transcripts. Arpan must contact AI Engineer and
record acceptable reuse/launch terms before launch. This remains the C12 launch
gate, not a C03 fetcher/ingest criterion; neither model output nor an optional
SKILL `license` field constitutes clearance.

## Evals and CI outline

`evals` covers extraction precision/recall, citation/span correctness, accuracy of
consensus/contested/insufficient labels, repository grounding, and skill usefulness
on defined engineering tasks. Reports include per-check outcomes, failures,
counts/denominators, corpus and artifact hashes, prompt/model/adapter versions,
rubric versions, and human adjudications. Separate tuning examples from reviewed
evaluation examples. Compare variants only on the same versioned inputs and
criteria. Missing gold labels or unrun metrics remain BLOCKED.

C03 does not supply reviewed corpus-quality baselines or human-approved quality
thresholds. Defining them is future human-gated work, not a reason to remove
evals or invent passing scores. Gold labels, rubrics, metrics, thresholds, and baselines are protected by
the evaluation-change gate. Never weaken tests to make a result pass.

CI and verification requirements:

1. Use Python 3.12 and uv with a reviewed lockfile; dependency preparation is
   separate from network-free test execution and does not need model secrets.
2. Run `git diff --check` on the PR diff, plus agreed formatting/type checks.
3. Run offline fixture tests for entity serialization/references, unresolved
   duplicate identities, missing/ambiguous attribution, supporting/opposing talk
   recounts and relabeling after claim exclusion, timing/quote boundaries,
   malformed frontmatter, and release leakage.
4. Test opposing evidence across candidate groups, insufficient inputs, repository
   unknown/mismatch states, path/symlink containment, adapter denial/timeouts/bad
   JSON, and propagation of failed/blocked/stale stage outcomes.
5. Run available approved eval fixtures, reporting missing cases/criteria as
   BLOCKED; fixture tests alone cannot establish real corpus or task quality.

C03 runs at least 20 recorded synthetic/redacted transcript fixtures and recorded
HTTP fixtures covering timing/offsets, identity/attribution, versioning, containment,
robots/rate limits, conditional caching/resume, and fetch URL/date/hash
persistence. Run pytest, Ruff check/format, CLI checks, and diff checks including
untracked files; record commands, exit codes, counts, and blockers. Missing
dependencies in the prescribed offline environment are BLOCKED. These tests are
implementation checks, not changes to reviewed evaluation labels or criteria.
Live-site checks and live-model evaluation are not run in C03.

## Protected paths and trust boundaries

These are required implementation/review controls, not a claim that repository
settings or enforcement already exist:

| Paths or artifacts | Protection |
| --- | --- |
| `.env*`, credentials, tokens, `.aws/`, secret stores | Do not read, write, commit, or include in model payloads/release artifacts. Secrets work needs the human gate. |
| `.cursor/`, `.grok/`, `.codex/`, `.agents/` | Local agent configuration/instructions are outside C03 writes. Do not commit local agent artifacts or change permissions/settings. |
| Local transcript roots, fetched caches, source object stores, `.skillware/`, and `data/` | Private source/work data; exclude from commits and release exports. Fetched raw Markdown/cache and preserved source objects must be outside repositories and release packs. No bulk publication of database, cache, or raw responses. |
| Future gold labels, eval fixtures/rubrics/thresholds/baselines, including those under `tests/` or `src/skillware/evals/` | Human evaluation-change gate; preserve review history and never weaken criteria to force a pass. |
| `LICENSE`, `.github/`, repository settings, Git internals | Outside C03 edits. Source changes are limited to the assigned ingest contract; no commit, push, PR, merge, or settings changes. |
| `docs/SPEC.md`, `docs/ARCHITECTURE.md`, `docs/REQUIREMENTS.md` | C03 explicitly requires the ingest acquisition/provenance delta in these three documents. Other protected documentation is outside the assignment. |

Treat transcripts, repository contents, CLI responses, and worker messages as
untrusted data, never instructions or approval. Validate declared-root containment
for input/output paths and symlinks; derive safe artifact names from validated
skill names. Restrict adapter capabilities and inspect all publication contents.
Human gates cover publishing, destructive actions, secrets, permission widening,
and evaluation changes. This architecture grants none of those permissions.

## Traceability and outstanding gates

The objective's Page gaps are carried end-to-end: cross-talk claims become labeled
clusters with opposition checks; repository mentions become pinned, qualified
observations; evals measure grounding and task usefulness. The original
due-diligence Page was unavailable, so direct comparison remains BLOCKED; these
docs cover the three gaps stated in the assignment.

SPEC's four Arpan-only questions concern the AI Engineer license email/terms,
future LLM budget/key, `grok-4.7-high` choice/availability, and commitment to gold
labels. A1 storage, A2 quote caps, and A3 candidate clustering are explicit design
assumptions shared by both documents, not unresolved questions for Arpan.

Arpan's approving PR review or explicit approval comment on the final diff is
mandatory before merge as a post-run human gate, not a C03 success criterion. Independent reviews and a developer submission are evidence, not human
acceptance or authorization to publish, push, or merge.
