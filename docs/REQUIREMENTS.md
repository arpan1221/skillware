# C01 requirements

Requirements seat `4fd3ca28-676b-4788-8c7d-84f4eddb69cc` (read-only, accepted, attempt 2, not BLOCKED). This file is the requirements artifact and an input for the architect and developer. Per Arpan's rework brief, it stays in the C01 deliverable alongside `docs/SPEC.md` and `docs/ARCHITECTURE.md`; revised bytes require fresh broker hashes.

Architect `7ca2baa9-f918-407e-a742-ff34b0bc57e2` stays a separate read-only seat and depends on this submission. Developer `2ca2f64d-5de8-4382-bdab-e90267746802` (Codex gpt-6-astra, workspace-write) is the only writer of the three C01 documents under Arpan's rework brief. Requirements and architect seats remain read-only.

Worktree: branch `aw/c01-spec`. The seed base (`origin/main`) is `a075291dac541547faaade9fb32cadebc4f4a4a3` (README + Apache-2.0 LICENSE); it is not the current branch tip. The C01 documentation commit is `22f9fefe6fdf9cf3765e91ddf8d003253aa5dfc7`, the HEAD observed before this uncommitted rework. The seed has no due-diligence document and no transcripts. The Page gap is the three topics named in the objective: cross-talk consensus/contested, repo mining, and evals. `.cursor/` is local and must not be committed.

An earlier requirements file in this worktree used seat IDs from a different run. Those IDs are void. Use the IDs in this file.

## Requirements

Taken from the objective, README.md, LICENSE, and the Agent Skills spec at https://agentskills.io/specification. Both product docs carry these.

### Product

- Goal: turn AI Engineer talks into reusable Agent Skills a coding agent can load.
- MVP pack name is exactly `The AI Engineer Playbook`.
- Later, outside the MVP: paste any channel.
- MVP ingest reads local Markdown transcripts. C01 does not add a download or scrape step. The seed repo does not contain the corpus.
- Pipeline order is fixed: ingest → extract → cluster → synthesize → validate → eval.
- Entities, later implemented in `src/skillware/schema.py`, are exactly: Talk, Segment, Claim, RepoRef, Cluster, Skill. The architect may add fields. The six entity names stay fixed.

### Minimum fields

Forced by citations and the Page gap:

- Talk: speaker, company, title, local transcript path, source URL, optional YouTube video id. Company unknown stays explicit and is never invented.
- Segment: talk id, order, text, and start/end seconds when the transcript has them. Segment text stays inside the pipeline.
- Claim: one practice statement, one short quote, and a resolvable citation.
- RepoRef: canonical URL plus host/owner/name, linked to the claim.
- Cluster: member claim ids and a cross-talk label.
- Skill: pack name, Agent Skills `name` and `description`, body, supporting clusters, and citations.

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
- Code license stays Apache-2.0, matching LICENSE and README.md. The project claims no rights in talk content; rights remain with their respective rights holders. Publish short quotes and links. Do not republish full transcripts. Ask AI Engineer before launch.
- SKILL.md YAML frontmatter follows https://agentskills.io/specification. Required: `name` (1–64 chars, lowercase letters, numbers, hyphens; no leading, trailing, or consecutive hyphens; matches the skill directory) and `description` (1–1024 chars, what it does and when to use it). Optional: `license`, `compatibility` (max 500 characters if present), `metadata` (string-to-string map), `allowed-tools` (experimental). Cursor-only keys such as `disable-model-invocation` are not required.

### Architecture

- Python 3.12, uv, typer. Package modules are exactly `src/skillware/{cli,schema,ingest,extract,cluster,synth,links,evals}`.
- Claim store is local, one of jsonl, SQLite, or DuckDB. ARCHITECTURE picks one, labels it an assumption, and both docs use that pick. It supports claim, cluster, and eval queries. No hosted database.
- LLM layer is provider-agnostic (provider id, model id, CLI-backed adapter). Extraction uses existing CLI subscriptions only: no paid API budget and no Anthropic credits. `grok-4.7-high` is not locked.
- Embeddings and clustering do not require a paid API key. Clustering records its seed and emits `consensus`, `contested`, or `insufficient`.
- CI is an outline only. No workflow file in C01. The outline is Python 3.12, uv, `git diff --check`, tests that do not call the network or a paid LLM, and no secrets.
- Protected paths: secrets and env files; gold-label and eval fixtures once they exist (human gate `evaluation-change`); never weaken tests; do not commit `.cursor/`.

## Constraints

- C01 delivers `docs/SPEC.md`, `docs/ARCHITECTURE.md`, and `docs/REQUIREMENTS.md`. These are the only editable files under Arpan's rework brief; no implementation or scaffolding is included.
- Requirements and architect seats are read-only. The developer is the only writer of the three C01 documents for this rework.
- Developer evidence is the three doc paths, a section/word-count checklist covering criteria 1–5, broker file hashes, and results from `git diff --check` and `git diff --stat`. `git diff --check` not run means BLOCKED.
- SPEC and ARCHITECTURE use the same entity names, stage names, module names, storage assumption, license stance, and Page-gap labels.
- Claims in a broker submission are truncated at 500 characters, with at most 30 claims. The file is the durable record.

## Non-goals

- No hosted YouTube scraper.
- No web UI in the MVP.
- No paired-mode HTTP app.
- No C01 implementation, no C02 scaffolding, no network ingestion, and no live LLM calls.
- No push, merge, repo-setting changes, or secrets.
- Criterion 6 (Arpan's PR approval or explicit comment) is a human merge gate. Agents cannot satisfy it. It is not one of the four SPEC open questions.

## Decisions

### Arpan-only open questions

SPEC Open questions lists only these items, and no team decisions:

- License email to AI Engineer before launch.
- Whether any LLM API budget or key will exist. Until answered, CLI subscriptions only.
- Whether `grok-4.7-high` is the extraction and synthesis model.
- Whether Arpan will commit a gold-label set for evals.

### Architect-owned assumptions

The architect records each choice in ARCHITECTURE and SPEC uses the same choice:

- Which local claim store: jsonl, SQLite, or DuckDB.
- The numeric short-quote cap.
- How embeddings run without a paid API key, and the clustering seed policy, within the label rules above.
