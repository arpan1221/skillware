# skillware product specification

Status: C01 design for review. This document and [ARCHITECTURE.md](ARCHITECTURE.md)
define the future implementation; the seed contains only README and LICENSE.
No pipeline, corpus, quality results, or launch permission is implied.

## Product goal and scope

Turn AI Engineer conference talks into reusable Agent Skills that coding agents
can load to perform engineering work. The MVP pack is **The AI Engineer Playbook**.
It should translate concrete practices into instructions with applicability,
limitations, evidence, and evaluation results. It should help a reader distinguish
agreement across talks from disagreement and from evidence that is too thin.

MVP inputs are operator-provided local Markdown transcripts from AI Engineer,
source metadata, and optional local snapshots of referenced repositories. The
seed repository includes none of that corpus. Ingestion reads those local inputs;
it does not download videos, transcripts, or repositories. Later, **paste any
channel** can expand source selection and acquisition under a separate design.

The due-diligence Page gaps named in the C01 objective become explicit product
requirements: cross-talk consensus/contested evidence, repository mining, and
evals. The original Page was not available for direct comparison. Fidelity to its
full contents remains **BLOCKED** until the source is accessible; coverage here is
limited to the three gaps supplied in the objective.

## Data model

The following six entities are the contract for later `src/skillware/schema.py`.
No schema implementation exists in C01. Identifiers and references must survive
serialization; changing a source or extraction creates a version with provenance
rather than silently rewriting evidence used by an existing skill.

| Entity | Required information and relationships |
| --- | --- |
| `Talk` | `id`, `title`, `speaker`, nullable `company`, `source_url`, nullable `youtube_video_id`, `transcript_path`, `transcript_sha256`. Preserve unresolved metadata explicitly. Source identity, not filename, determines duplicate talks. |
| `Segment` | `id`, `talk_id`, `ordinal`, private `text`, source character offsets, nullable `start_seconds`/`end_seconds`, and a source-backed speaker override for multi-speaker talks when needed. Preserve the original timing precision. |
| `Claim` | `id`, `segment_id`, one atomic `statement`, its `applicability`, one short supporting `quote`, quote character offsets within the segment, `citation_status`, `repo_ref_ids`, and extraction provenance. Provenance includes input/prompt/schema hashes, adapter/provider/model identifiers, run receipt, and review status. |
| `RepoRef` | `id`, canonical `url`, `host`, `owner`, `name`, linked `claim_ids`, `inspection_status`, nullable pinned `commit`, license value or explicit unknown with evidence, and claimed-versus-observed facts. Observations carry file path, line range, and blob hash. |
| `Cluster` | `id`, scoped `proposition`, member `claim_ids`, supporting/opposing claim IDs, reviewed evidence relations, distinct-talk count, `label`, rationale, assessment status, and clustering configuration hash. Labels are `consensus`, `contested`, or `insufficient`. |
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
| ingest | Read declared local Markdown inputs; normalize metadata; retain source offsets and real timing; identify duplicate talks; emit `Talk` and `Segment` records. Flag missing or ambiguous attribution. |
| extract | Use a configured existing CLI subscription to propose atomic practices, applicability, exact short quotes, and repository mentions. Validate response structure and quote spans before accepting `Claim`/`RepoRef` records. Reject invented evidence. |
| cluster | Form candidates with local vectors, then assess scoped agreement and opposition using source evidence. Emit labeled `Cluster` records with rationale, unresolved relations, and provenance. Similarity alone never establishes consensus. |
| synthesize | Turn eligible reviewed clusters into draft `Skill` instructions. Cite procedural claims, preserve caveats, and present contested alternatives with both sources. Keep insufficient evidence as research notes, outside the release pack. |
| validate | Check schema, relationships, frontmatter, attribution, timestamp construction, source spans, quote budgets, repository evidence status, and release file allowlist. Emit a report; invalid output cannot advance as validated. |
| eval | Measure extraction, grounding, cross-talk judgments, repository observations, and task usefulness against reviewed labels and a versioned rubric. Emit denominators, scores, failures, and blocked checks. Missing gold labels remain BLOCKED. |

The provider/model/CLI choice is configuration, not product identity. Extraction
and synthesis assume **existing CLI subscriptions only: no paid API budget and
no Anthropic credits**. An unavailable subscription or unsupported structured
output blocks the stage; there is no automatic paid API fallback. C01 performs
no live LLM calls. `grok-4.7-high` remains an Arpan decision, not a dependency.

## Cross-talk evidence

Deduplicate sources before counting distinct talks. Assess the same proposition
under comparable conditions; different workloads or constraints may explain an
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
company renders as `company unknown`; never invent an affiliation. Unresolved
speaker identity blocks publication. A multi-speaker segment uses its verified
speaker attribution rather than attributing every remark to the headline speaker.

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

The project code remains Apache-2.0 under [LICENSE](../LICENSE). Talk content
belongs to its speakers and AI Engineer. Publish original synthesis, short quotes,
and links; **full transcripts are not republished**, nor are raw extraction dumps
or repository source. Public availability and a skill's optional `license` field
do not grant rights to source content. **Ask AI Engineer before launch:** Arpan
must contact them and resolve acceptable reuse, attribution, and launch terms.
Launch remains blocked until those terms are recorded and satisfied.

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

C01 acceptance evidence consists of these two docs, a section/word-count checklist,
their SHA-256 hashes, the base revision, and a clean `git diff --check`. New files
must also be checked directly because ordinary Git diffs omit untracked files.
Independent tester/reviewer checks and any unresolved Page comparison are reported
separately from the developer's claim. Runtime and quality checks are BLOCKED in
this documentation-only slice, not reported as passed.

Before merge, **Arpan sign-off must be recorded in the PR** as an approving review
or an explicit approval comment applying to the final diff. Agent verdicts do not
satisfy that gate. Acceptance does not itself authorize this worker to push,
publish, or merge. Missing PR approval leaves the merge gate BLOCKED.

## Non-goals

- No hosted YouTube scraper, web UI in the MVP, or paired-mode HTTP app.
- No general channel ingestion in this MVP; “paste any channel” is later work.
- No C01 implementation beyond these two docs, C02 scaffolding, network ingestion,
  live LLM calls, or paid API dependency.
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
