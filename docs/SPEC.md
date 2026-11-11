# skillware product specification

Status: C03 ingest contract, extending the C01 design and C02 scaffold.
This document, [ARCHITECTURE.md](ARCHITECTURE.md), and
[REQUIREMENTS.md](REQUIREMENTS.md) describe the AI Engineer Markdown fetcher
and local ingest authorized by the C03 assignment. C01 delivery limits are historical. No corpus
quality result, downstream implementation, or launch permission is implied.

## Product goal and scope

Turn AI Engineer conference talks into reusable Agent Skills that coding agents
can load to perform engineering work. The MVP pack is **The AI Engineer Playbook**.
It should translate concrete practices into instructions with applicability,
limitations, evidence, and evaluation results. It should help a reader distinguish
agreement across talks from disagreement and from evidence that is too thin.

MVP ingest retrieves Markdown transcripts from the AI Engineer site with a
polite fetcher and also accepts operator-provided local Markdown transcripts and
metadata. Fetched raw Markdown lives in a private cache outside the repository
and release pack. Optional repository snapshots remain operator-provided local
inputs. This is transcript retrieval, not permission to redistribute full talks.
A hosted YouTube scraper and **paste any channel** remain out of scope.

The due-diligence Page gaps named in the C01 objective become explicit product
requirements: cross-talk consensus/contested evidence, repository mining, and
evals. The original Page was not available for direct comparison. Fidelity to its
full contents remains **BLOCKED** until the source is accessible; coverage here is
limited to the three gaps supplied in the objective.

## Data model

The following six entities are the contract for `src/skillware/schema.py`.
Identifiers and references must survive serialization; changing a source or extraction creates a version with provenance
rather than silently rewriting evidence used by an existing skill.

| Entity | Required information and relationships |
| --- | --- |
| `Talk` | `id`, `title`, `speaker`, nullable `company`, `source_url`, nullable `youtube_video_id`, `transcript_path`, `transcript_sha256`, and nullable fetch provenance containing source URL, UTC fetch date, and content SHA-256. Preserve unresolved metadata explicitly. Source identity, not filename, determines duplicate talks. |
| `Segment` | `id`, `talk_id`, `ordinal`, private `text`, source character offsets, nullable `start_seconds`/`end_seconds`, and a source-backed speaker override for multi-speaker talks when needed. Preserve the original timing precision. |
| `Claim` | `id`, `segment_id`, one atomic `statement`, its `applicability`, one short supporting `quote`, quote character offsets within the segment, `citation_status`, `repo_ref_ids`, and extraction provenance. Provenance includes input/prompt/schema hashes, adapter/provider/model identifiers, run receipt, and review status. |
| `RepoRef` | `id`, canonical `url`, `host`, `owner`, `name`, linked `claim_ids`, `inspection_status`, nullable pinned `commit`, license value or explicit unknown with evidence, and claimed-versus-observed facts. Observations carry file path, line range, and blob hash. |
| `Cluster` | `id`, scoped `proposition`, member `claim_ids`, supporting/opposing claim IDs, reviewed evidence relations, distinct-talk count plus separate distinct supporting/opposing talk counts, `label`, rationale, assessment status, and clustering configuration hash. Validation derives publication counts and labels from publishable claims. Labels are `consensus`, `contested`, or `insufficient`. |
| `Skill` | `id`, `pack_name`, Agent Skills `name` and `description`, Markdown `body`, supporting `cluster_ids`, citations mapped to claims, version, validation status, and eval status. The MVP `pack_name` is `The AI Engineer Playbook`. |

The evidence chain is `Skill → Cluster → Claim → Segment → Talk`. `RepoRef`
supplements a claim with implementation evidence; it does not replace the talk
citation. Dangling references fail validation. Null means unknown, not a value to
guess. Internal run receipts and reports support these entities without becoming
additional product entities.

**ASSUMPTION A1 — storage:** local SQLite at `.skillware/claims.sqlite3` is the
authoritative claim store. JSONL is optional interchange, not a second authority.
Transcripts, segment text, the database, caches, and raw model output remain
private working data and are excluded from the release pack.

## Pipeline and stage outcomes

The order is **ingest → extract → cluster → synthesize → validate → eval**.
Each stage records its inputs, configuration/version, output references, and
outcome. `PASS` means its stated checks ran and passed; `FAIL` means a check ran
and rejected the result; `BLOCKED` means a prerequisite or check is missing or
unrun. A later successful stage cannot erase an earlier failure or blockage.

| Stage | Contract |
| --- | --- |
| ingest | Fetch AI Engineer Markdown into a private cache, or read declared cached/local inputs; normalize source-backed metadata; retain exact source offsets and real timing; deduplicate source identity; emit versioned `Talk` and `Segment` records with provenance. Missing inputs or missing/ambiguous speaker/source attribution are BLOCKED; contradictory attribution is FAIL. Undecided duplicates block ingest and the affected cluster's distinct-talk assessment and never create two independent Talks. |
| extract | Use a configured existing CLI subscription to propose atomic practices, applicability, exact short quotes, and repository mentions. Validate response structure and quote spans before accepting `Claim`/`RepoRef` records. Reject invented evidence. |
| cluster | Form candidates with local vectors, then assess scoped agreement and opposition using source evidence. Emit labeled `Cluster` records with rationale, unresolved relations, and provenance. Similarity alone never establishes consensus. |
| synthesize | Turn eligible reviewed clusters into draft `Skill` instructions. Cite procedural claims, preserve caveats, and present contested alternatives with both sources. Keep insufficient evidence as research notes, outside the release pack. |
| validate | Check schema, relationships, frontmatter, attribution, timestamp construction, source spans, quote budgets, repository evidence status, and release file allowlist. After any claim is dropped, recompute distinct supporting/opposing talk counts from remaining publishable claims and re-derive labels. Invalidate affected drafts and emit a report; invalid output cannot advance as validated. |
| eval | Measure extraction, grounding, cross-talk judgments, repository observations, and task usefulness against reviewed labels and a versioned rubric. Emit denominators, scores, failures, and blocked checks. Missing gold labels remain BLOCKED. |

The provider/model/CLI choice is configuration, not product identity. Extraction
and synthesis assume **existing CLI subscriptions only: no paid API budget and
no Anthropic credits**. An unavailable subscription or unsupported structured
output blocks the stage; there is no automatic provider fallback. C03 and
fixture tests perform no live LLM calls. `grok-4.7-high` remains an Arpan decision,
not a dependency.

## Ingest acquisition and parsing

`uv run skillware ingest fetch --out <cache>` retrieves AI Engineer Markdown.
`--out` declares the private cache root by default; optional `--root <root>`
constrains it to a broader operator-declared private root. An optional repeated
`--url <url>` supplies AI Engineer transcript or discovery URLs. Requests identify
Skillware in the User-Agent, respect `robots.txt`, and enforce rate limits,
including source-provided crawl delays and retry delays. ETag and Last-Modified
validators enable conditional requests. Unchanged content is skipped; completed
cache entries survive interruption so a rerun resumes without losing progress.
Robots agent groups match the longest case-insensitive prefix of the Skillware
product token, not arbitrary substrings. If no applicable specific path rule
matches, wildcard rules remain the fallback, including when the specific group
contains only pacing directives. Applicable pacing limits are retained.
Robots denials, unavailable required resources, and incomplete retrieval remain
visible in the fetch receipt rather than appearing as successful empty work.

Each cached transcript has raw Markdown and fetch provenance: source URL, fetch
date in UTC, and SHA-256 of the exact content bytes. A 304 response, or a 200 with
the same bytes at the same source URL, preserves the original fetch date and
ingest version. Updated ETag/Last-Modified validators alone are cache bookkeeping,
not a new source representation. Changed content, source URL, or source fetch date
creates a new ingest version; prior evidence is retained.
The cache is private working data, never a repository or release-pack artifact.
Retrieval does not waive A2 quote limits or the C12 AI Engineer launch-terms gate.

`uv run skillware ingest --cache <dir> --db <path> [--manifest <jsonl>] [--objects <dir>]`
parses cached or local Markdown without network access. `--cache` declares the
input root by default; optional `--root <root>` constrains all input paths,
including the manifest. The explicit database path declares its output location.
Reject paths outside declared roots and all supplied symlinks, including root
aliases, nested input files, and cache artifacts. Use the real non-symlink spelling
of a root. Missing inputs with a safe database destination produce a persisted
BLOCKED receipt and nonzero exit status. Malformed declarations with no safe
output destination emit the BLOCKED receipt as JSON on stdout. An unreadable
input directory fails the attempt and appears in its receipt; it cannot silently
produce a partial passing corpus.

Before committing Talk rows, preserve the exact parsed source bytes in a private
content-addressed store. Local source snapshots default to `<db>.objects`;
`--objects <dir>` declares an alternate object root. The object root must be
outside repositories and release packs and obey the same symlink containment
rules. Existing immutable fetch-cache `.objects` are reused when available.
Each Talk's `transcript_path` pins its `<transcript_sha256>.txt` object, retaining
the bytes needed to resolve every historical Segment offset even after input
files are renamed or edited. Before a PASS rerun, revalidate every stored source
path and hash. Missing or corrupt history is FAIL. Legacy mutable transcript
paths are BLOCKED and require re-ingest from available originals into a fresh
database; existing versions are never rewritten to imply preserved history.

Title, speaker, nullable company, source URL, and nullable YouTube video ID come
from transcript/frontmatter metadata or a local JSONL manifest. The fetch
provenance source URL records acquisition only and never fills missing source
attribution. Metadata is not inferred from filenames or generated by a model.
Conflicting attribution is FAIL; missing or ambiguous speaker/source attribution
is BLOCKED and recorded in the
stage receipt. A speaker override needs evidence in the source itself. Segment
text resolves exactly to the recorded character offsets in the hashed transcript.
Parse only source timestamps, preserving fractional precision; absent start or
end times remain null, never invented zero or estimated durations.

Inputs are UTF-8. Supported frontmatter is a scalar YAML-style mapping of the
metadata fields; JSON-quoted and single-quoted scalar values are accepted.
Collections and complex YAML remain BLOCKED. A Markdown `# title` and a metadata
preamble with `Speaker:`, `Source:`, `Company:`, and `YouTube Video ID:` are also
supported. A manifest row supplies the same fields, with `path` relative to the
manifest's directory and contained under the declared root. For example, this
synthetic JSONL row describes a local file without inventing its company or video:

```json
{"path":"talk.md","title":"Synthetic example","speaker":"Fixture Speaker","company":null,"source_url":"https://www.ai.engineer/synthetic-example.md","youtube_video_id":null}
```

Recognized timestamp lines use `MM:SS.fraction` or `HH:MM:SS.fraction`, optionally
in brackets, with an optional explicit `--> END` timestamp; fractional parts are
optional. Explicit `Name:`, `**Name:**`, `**Name**:`, or `[Speaker: Name]` line
labels provide speaker-override evidence; plain names include Unicode names such
as `José García:`. Bold prose without a colon is never a speaker label.
Character offsets refer to the original decoded source, retaining its newline
characters.

Duplicate checks use video ID, canonical source URL, and transcript hash across
filenames and URL variants. Known duplicates share a stable Talk identity;
unresolved identity matches are BLOCKED and cannot create two independent Talks.
Matching title and speaker alone, or matching host/path with distinct retained
query parameters alone, does not establish a duplicate or an identity conflict.
Canonical paths decode only percent-encoded unreserved characters and normalize
escape case; reserved escapes such as `%2F` remain distinct from `/`. Explicit
non-default ports retain their endpoint identity. Malformed query percent escapes
and invalid UTF-8 query encodings are rejected; repeated parameter values retain
their order. Ingest configuration `c03-ingest-v3` does not trust alternate URL
aliases produced by older lossy normalization: matches relying on those aliases
are BLOCKED pending resolution.
Unchanged source bytes cannot silently acquire conflicting attribution from a
later manifest. Duplicate copies retain available fetch provenance; conflicting
fetch records are BLOCKED rather than choosing one by filename.
Unchanged input is idempotent: the same IDs and evidence versions are reused.
Changed transcript or fetch provenance creates a new version of that identity.

The JSON ingest receipt beside the database records configuration version, input
hashes, counts, outcome, and per-input problems. The SQLite stage receipt retains
configuration, hashes, outcome, errors, and exact output references. Every PASS
stage lists every written Talk and Segment version in `output_refs`. FAIL and BLOCKED return nonzero and cannot commit partial
entity output as a passing stage. Fetch progress may remain cached for resume;
it is not a successful ingest receipt.

## Cross-talk evidence

Deduplicate sources before counting distinct talks. An undecided duplicate check,
including unresolved talk identity or possibly-same URLs, makes the affected
cluster assessment BLOCKED until resolved. Such records must never count as
independent talks or inflate consensus. Assess the same proposition under
comparable conditions; different workloads or constraints may explain an
apparent disagreement and must remain visible.

- **consensus:** at least two distinct, deduplicated talks support the same scoped
  proposition, with no contradictory reviewed evidence and no unresolved
  relevant relation in the evaluated corpus. This is corpus agreement, not a
  universal truth or a claim about the whole industry.
- **contested:** reviewed evidence from different talks takes opposing positions
  on the same scoped proposition. Preserve both positions, context, and citations;
  do not average them into one recommendation or hide the minority position.
- **insufficient:** one talk, too little support, ambiguous scope, or unresolved
  relations prevent either conclusion. Do not promote it to consensus.

Validation must recompute distinct supporting and opposing talk counts from the
claims that remain publishable after every exclusion, including a failed span
check or missing attribution. It must re-derive `consensus`, `contested`, or
`insufficient` using those counts and the reviewed relations, never retain a
cached label from the original membership. Losing one of two supporting talks
therefore removes consensus eligibility. Preserve excluded claims and reasons in
the internal report; dropping an opposing claim must not erase a known
contradiction or unresolved relation and manufacture consensus. Block the affected
assessment until that evidence is resolved. Any membership or label change
invalidates dependent drafts; re-synthesize and revalidate them before eval.

**ASSUMPTION A3 — candidate clustering:** use local TF-IDF unigrams/bigrams with
negation retained, optional truncated SVD with seed `0`, L2 normalization, and
average-link clustering at cosine distance `0.35`. SVD dimension is
`min(128, n_claims - 1, n_features - 1)` when at least `2`; otherwise record a raw
TF-IDF fallback. No paid key or downloaded embedding model is required. Record
input order/hash, preprocessing, dependency versions, transform hash, dimension,
threshold, and seed. Singleton or zero-vector evidence is insufficient; no claims
blocks clustering. These are provisional candidate-generation settings, not
measured semantic accuracy. Review related candidates for opposition even when
they land in different clusters.

## Repository mining

Repository mentions create first-class `RepoRef` records. Mining inspects only
operator-provided local snapshots, records a pinned revision and license evidence,
and compares the speaker's claim with what the snapshot contains. Use explicit
states such as `mentioned`, `unavailable`, `inspected`, and `mismatch`; a mention
does not prove behavior. Unknown license, missing revision, or absent snapshot
remains visible. Static inspection proves only the recorded observation, never
runtime correctness.

Repository content is untrusted data. Do not execute its code, hooks, or embedded
instructions. Do not vendor third-party source into the pack. A skill may link an
uninspected repository as an attributed mention, but must not call it verified or
base an implementation claim on it. An implementation claim needs a traceable
observation at the pinned revision; preserve mismatches rather than silently
reconciling them.

## Agent Skills output

Each skill directory contains `SKILL.md` with YAML frontmatter followed by
Markdown. Required `name` is 1–64 characters, uses lowercase letters, digits, and
hyphens, has no leading/trailing/repeated hyphen, and matches the directory.
Required `description` is 1–1024 characters explaining the task and when to use
it. MVP names use portable ASCII. Optional fields are `license`, `compatibility`
(1–500 characters), `metadata` (string-to-string map), and experimental
`allowed-tools` (space-separated tools). These rules follow the
[Agent Skills specification](https://agentskills.io/specification), checked
2026-11-10. Client-specific keys such as `disable-model-invocation` are not required.

Illustrative frontmatter, not an evaluated skill:

```yaml
---
name: assess-retrieval-quality
description: Assess retrieval changes against reviewed examples before deciding whether to adopt them.
metadata:
  pack: The AI Engineer Playbook
  version: "0.1"
---
```

The body supplies applicability, inputs, actionable steps, expected outputs,
failure cases, evidence labels, citations, repository observations when relevant,
and an eval result or explicit BLOCKED status. Supporting references may hold
short evidence notes. MVP exports authored Markdown and an evidence manifest;
it does not generate executable scripts from transcript instructions.

## Citations and licensing

Every published quote and substantive paraphrased practice identifies the
speaker, company, and talk, linked to the source YouTube timestamp. Unknown
company renders as `company unknown`; never invent an affiliation. Missing or
ambiguous speaker/source attribution makes the claim BLOCKED, not merely flagged;
contradicted attribution or a failed span check makes it FAIL. Neither outcome is
publishable or counts as supporting/opposing publication evidence. A multi-speaker
segment uses its verified speaker attribution rather than attributing every remark
to the headline speaker.

`src/skillware/links` constructs links of the form
`https://www.youtube.com/watch?v=VIDEO_ID&t=SECONDS` from a verified video ID and
source-backed segment start. Display citation text as
`Speaker (Company), “Talk title,” MM:SS` (or `HH:MM:SS` for longer talks). For
fractional timing, floor only the link's second offset and retain original
precision internally. A real source-backed zero is valid. Missing timing must
never become `t=0`; missing video ID, timing, speaker, or a resolvable evidence
span blocks that claim from publication. Segment anchors must not be described
as exact word-level timing. Both sides of a contested practice need citations.

**ASSUMPTION A2 — quote budget:** at most **25 whitespace-delimited words per
verbatim quote** and **100 quoted words per Talk across an entire release pack**,
counting repeated occurrences. Quotes must be contiguous source spans; only
whitespace normalization is permitted. Apply the pack-wide budget to all emitted
Markdown and evidence notes, not just individual `SKILL.md` files. Splitting a
transcript among files or reconstructing it as a sequence of excerpts is rejected.
These are editorial limits, not a finding that reuse is licensed.

The project code remains Apache-2.0 under [LICENSE](../LICENSE). The project claims
no rights in talk content; rights remain with their respective rights holders.
Publish original synthesis, short quotes, and links; **full transcripts are not
republished**, nor are raw extraction dumps or repository source. Public
availability and a skill's optional `license` field
do not grant rights to source content. **Ask AI Engineer before launch:** Arpan
must contact them and resolve acceptable reuse, attribution, and launch terms.
Launch remains blocked until those terms are recorded and satisfied. This is the
**C12 launch gate**, not a prerequisite for C03 fetcher/ingest acceptance.

## Evaluation and acceptance

Evals must report extraction precision/recall against labeled practices, citation
and span correctness, consensus/contested/insufficient accuracy, repository
grounding, and skill usefulness on defined engineering tasks. Record the corpus,
model/adapter, prompt/schema versions, rubric, sample counts, denominators,
failures, and reviewer adjudications. Keep reviewed evaluation examples separate
from examples used to tune prompts or clustering. No scores are inferred from
well-formed Markdown or passing structural checks.

No gold-label set, measured baseline, or quality threshold exists in the seed.
Human-approved fixtures and criteria must precede a quality acceptance claim;
missing labels, unrun metrics, or undecided criteria mean BLOCKED. Never weaken
tests or change evaluation criteria to make output pass. Changes to future gold
labels, rubrics, thresholds, or baselines require the human evaluation-change gate.

C03 acceptance uses at least 20 recorded synthetic or redacted Markdown fixture
transcripts and recorded HTTP fixtures under `tests/fixtures/`, with no real
full-talk content and no live site or model. Offline pytest covers timing,
character offsets, deduplication including undecided cases, attribution,
idempotency/versioning, root/symlink containment, robots, rate limiting,
conditional caching/resume, and fetch URL/date/hash persistence. Required checks
are `uv sync && uv run pytest -q`, `uv run ruff check . && uv run ruff format
--check .`, CLI checks, `git diff --check`, and `git diff --stat`. Verify untracked
files too. Record exact commands, exit codes, pass/fail/skip counts, assignment
and attempt, exact base/branch, changed files, assumptions, deviations, and
blockers. Unrun checks are BLOCKED. Developer submission is a claim; independent
testers rerun checks and reviewers assess that evidence.

Arpan's PR approval is a post-run merge gate, not a C03 success criterion.
Agent verdicts do not satisfy that gate. This run authorizes no commit, push,
PR, merge, settings change, or publication.

## Non-goals

- No hosted YouTube scraper, web UI in the MVP, or paired-mode HTTP app.
- No general channel ingestion in this MVP; “paste any channel” is later work.
- No acquisition beyond AI Engineer Markdown, video downloads, live LLM calls
  in C03/tests, or paid API dependency.
- No automatic endorsement of a practice, fabricated consensus, full transcript
  redistribution, or execution/vendoring of referenced repositories.
- No publishing, pushing/merging without human acceptance and authorization,
  repository settings changes, secrets access, or permission widening.

## Open questions

Only Arpan can resolve these; storage, quote budgets, and clustering defaults are
documented design assumptions above.

1. Will you send the license email to AI Engineer and record the permitted reuse
   and launch terms?
2. Will a future LLM API budget/key be authorized? Until then, only existing CLI
   subscriptions are available; no paid API budget or Anthropic credits is assumed.
3. Should `grok-4.7-high` be the extraction/synthesis model, and is it available
   through your authorized CLI subscription?
4. Will you commit a reviewed gold-label set and the time to adjudicate evals?
