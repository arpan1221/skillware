# skillware architecture

Status: C01 design for review, implementing the future contract in
[SPEC.md](SPEC.md). C01 writes only these two documents. The seed at
`a075291dac541547faaade9fb32cadebc4f4a4a3` has README and Apache-2.0 LICENSE;
the package, tests, corpus, and automation described below do not exist yet.

## Scope and runtime

Use **Python 3.12**, **uv** for the environment and locked dependencies, and
**Typer** for a local CLI. The first output is **The AI Engineer Playbook**:
reusable Agent Skills grounded in AI Engineer talks. MVP inputs are local Markdown
transcripts plus operator-provided metadata and optional repository snapshots.
Later “paste any channel” acquisition needs separate authorization and design.

There is no hosted YouTube scraper, web UI in the MVP, paired-mode HTTP app, or
hosted database. C01 adds no implementation, C02 scaffolding, dependency files,
workflow files, network ingestion, or live LLM calls. This design assumes existing
CLI subscriptions only: **no paid API budget and no Anthropic credits**.

## Package boundaries

Planned paths, not files created by C01:

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
| `ingest` | Local Markdown/metadata parsing, stable source identity, source offsets/timing, and the SQLite persistence adapter shared through typed operations. No network source acquisition. |
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

`src/skillware/schema.py` will implement the entity fields in SPEC. The following
mapping constrains storage; it does not claim alignment with code that exists now.

| Entity | Storage and invariants |
| --- | --- |
| `Talk` | Source identity, title/speaker/nullable company, source URL/nullable video ID, local transcript path/hash. Deduplicate the same talk across filenames, URLs, and transcript versions before counting support. |
| `Segment` | Foreign key to Talk, ordinal, private text, source offsets, nullable start/end seconds, and source-backed speaker override. Offsets resolve within the hashed source; ordered timing must be nonnegative. Never invent missing times. |
| `Claim` | Foreign key to Segment, atomic statement/applicability, quote and exact offsets, citation status, repository links, and extraction receipt. Quote text must match the referenced span after whitespace-only normalization. |
| `RepoRef` | Canonical URL/host/owner/name; many-to-many claim links; inspection state; pinned commit; license/unknown and evidence; claimed/observed facts with file paths, lines, and blob hashes. |
| `Cluster` | Scoped proposition; member, support, and opposition links; evidence relations; deduplicated distinct-talk count; label/rationale; assessment state; configuration hash. A changed claim invalidates dependent assessments. |
| `Skill` | Pack/name/description/body; cluster and citation links; version; validation and eval states. All cited claim paths must resolve through Segment to Talk. |

**ASSUMPTION A1 — storage:** local SQLite at `.skillware/claims.sqlite3` is
authoritative. JSONL is optional versioned import/export only. Use foreign-key
enforcement, typed writes, schema versions, and transactions at stage boundaries.
Join tables represent claim/repository and cluster/skill membership; internal
run/report tables retain attempts, statuses, errors, and input/output hashes.
These support the six product entities rather than expanding that entity list.

Hash source content and configuration. Keep stable source IDs separate from
version IDs so retries do not manufacture new talks or inflate consensus.
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

## Stage orchestration and failure propagation

The command contract is **ingest → extract → cluster → synthesize → validate → eval**.
Every stage receives typed references to committed inputs and writes a receipt
with stage/configuration version, source hashes, artifact references, and outcome.

| Stage | Output and prerequisite |
| --- | --- |
| ingest | Talks/Segments from readable, declared local files. Invalid metadata is flagged; missing inputs block work. Retain source offsets and timing rather than estimating them. |
| extract | Claims and repository mentions from eligible segments. Accept only schema-valid responses with exact source spans. Missing metadata can leave research records ineligible for publication. |
| cluster | Candidate groups and reviewed semantic relations from valid claims. Zero claims blocks; unsupported or unresolved evidence becomes insufficient. |
| synthesize | Draft Skills from reviewed consensus/contested clusters with eligible citations. Contested output preserves both sides; insufficient clusters stay internal. |
| validate | A structural/provenance/release-content report over the entire candidate pack, including aggregate quote counts. Invalid drafts cannot proceed as validated artifacts. |
| eval | Versioned metric/task reports over validated artifacts and reviewed reference data. Missing labels, criteria, or runnable tasks are BLOCKED, with no fabricated score. |

Use `PASS`, `FAIL`, and `BLOCKED` consistently with SPEC. Failures and blocked
required checks produce non-success CLI status and remain visible in the final
run report. Partial diagnostic output is permitted but cannot be labeled a passing
release. A resume uses verified input hashes and stage receipts; it cannot reuse
stale reports. C01 has no runtime evidence for this future behavior.

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
subscription can run offline. No such transport runs in C01 or fixture CI.

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

These settings are an explicit starting assumption, not a measured quality claim.
Semantic relation errors and missed opposition require eval coverage. Do not
claim reproducible CLI text merely because local vector operations record a seed.

## Citations, repository evidence, and release validation

`links` traverses `Skill → Cluster → Claim → Segment → Talk`. Format speaker,
company (or `company unknown`), and talk title with a YouTube timestamp link:
`https://www.youtube.com/watch?v=VIDEO_ID&t=SECONDS`. Use a verified video ID
and source segment start; floor only the URL offset for fractional times. Retain
the precise source value and label it as a segment anchor. Missing time never
becomes zero. Missing speaker/video/time/span makes the claim non-publishable;
source-backed zero is valid. Paraphrases and both sides of a dispute also cite.

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

The code's Apache-2.0 license does not relicense talks. Source content remains
with speakers and AI Engineer. Publish short quotes and links, never full
transcripts. Arpan must contact AI Engineer and record acceptable reuse/launch
terms before launch; neither model output nor an optional SKILL `license` field
constitutes clearance.

## Evals and CI outline

`evals` covers extraction precision/recall, citation/span correctness, accuracy of
consensus/contested/insufficient labels, repository grounding, and skill usefulness
on defined engineering tasks. Reports include per-check outcomes, failures,
counts/denominators, corpus and artifact hashes, prompt/model/adapter versions,
rubric versions, and human adjudications. Separate tuning examples from reviewed
evaluation examples. Compare variants only on the same versioned inputs and
criteria. Missing gold labels or unrun metrics remain BLOCKED.

The seed has no fixtures, baseline, or human-approved quality thresholds. Defining
them is future human-gated work, not a reason to remove evals or invent passing
scores. Gold labels, rubrics, metrics, thresholds, and baselines are protected by
the evaluation-change gate. Never weaken tests to make a result pass.

Proposed CI, to be implemented after C01:

1. Use Python 3.12 and uv with a reviewed lockfile; dependency preparation is
   separate from network-free test execution and does not need model secrets.
2. Run `git diff --check` on the PR diff, plus agreed formatting/type checks.
3. Run offline fixture tests for entity serialization/references, duplicate talk
   counts, timing/quote boundaries, malformed frontmatter, and release leakage.
4. Test opposing evidence across candidate groups, insufficient inputs, repository
   unknown/mismatch states, path/symlink containment, adapter denial/timeouts/bad
   JSON, and propagation of failed/blocked/stale stage outcomes.
5. Run available approved eval fixtures, reporting missing cases/criteria as
   BLOCKED; fixture tests alone cannot establish real corpus or task quality.

C01 validation is document review, section/word-count and SHA-256 evidence, and
whitespace checks including new files. No workflow, tests, or scaffolding is
created here. Runtime execution, live-model evaluation, and corpus quality checks
are BLOCKED in this slice.

## Protected paths and trust boundaries

These are required implementation/review controls, not a claim that repository
settings or enforcement already exist:

| Paths or artifacts | Protection |
| --- | --- |
| `.env*`, credentials, tokens, `.aws/`, secret stores | Do not read, write, commit, or include in model payloads/release artifacts. Secrets work needs the human gate. |
| `.cursor/`, `.codex/`, `.agents/` | Local agent configuration/instructions are outside C01 writes. Do not commit `.cursor/` or change permissions/settings. |
| Local transcript roots and `.skillware/` | Private source/work data; exclude from commits and release exports. No bulk publication of database, cache, or raw responses. |
| Future gold labels, eval fixtures/rubrics/thresholds/baselines, including those under `tests/` or `src/skillware/evals/` | Human evaluation-change gate; preserve review history and never weaken criteria to force a pass. |
| `LICENSE`, `.github/`, package/lock files, `src/`, repository settings, Git internals | Outside C01 edits; leave them unchanged. Later changes require their own scope and any applicable human gate. |
| `docs/SPEC.md`, `docs/ARCHITECTURE.md` | The sole C01 deliverables. Coordination artifacts such as REQUIREMENTS are inputs, not part of the deliverable diff. |

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
mandatory before merge. Without that recorded sign-off, the merge gate remains
BLOCKED. Independent reviews and a developer submission are evidence, not human
acceptance or authorization to publish, push, or merge.
