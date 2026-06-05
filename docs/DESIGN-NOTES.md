# Design Notes — scale, i18n, security, interoperability

Cross-cutting decisions, recorded as they're made. The throughline: **a
symbolic graph core does the reasoning; ML is confined to the edges.** That one
choice is what makes the system scale, internationalize, stay secure, and
interoperate.

## Vectors are a fallback, not the backbone

Concept resolution is 3-pass — **exact → fuzzy(+aliases) → embedding** — and the
embedding pass only fires when the first two miss. Embeddings are
non-deterministic, opaque (hard to audit for a *safety* system), English-biased,
and quality varies with phrasing (`"pec isolation"` mis-resolved to `core` until
we added an alias). So they earn a narrow role: genuinely novel free-text with no
known alias. Curated **SKOS-style altLabels** ("pec"→chest, "delts"→deltoids)
carry the common path deterministically.

## Source-agnostic member profile — meet members where they are

A person's fitness picture is richer than any single app's data. Members arrive
having already invested in complementary services — an **Oura** or Whoop ring,
Apple Health, Garmin, a blood panel, a DEXA scan, a nutrition tracker. KG2 (the
member-context graph) is deliberately **source-agnostic**: any external signal
that maps onto our concepts (sleep, readiness, HRV, body composition, adherence)
becomes nodes/edges and enriches the *same* profile. The `OuraReading` nodes are
the worked example — a third-party wearable modeled as first-class graph data;
Whoop, Garmin, or Apple Health slot in through the same ingestion shape, and the
SNOMED/SKOS grounding gives lab and clinical sources a common vocabulary.

This is an inclusion stance as much as a technical one: **best serving the people
who come to us means not excluding those who have used complementary services —
it means welcoming them, and the data they bring.** A closed model that ingested
only our own measurements would quietly penalize the most engaged, quantified-self
members; the open graph does the opposite — every extra source makes the
personalization better, never worse.

## Security

- **Cypher injection:** node labels can't be parameterized, so the one
  interpolated label is validated against an **allow-list** (`ALLOWED_LABELS`).
  Everything else uses bound `$params`.
- **LLM prompt injection:** member chat/coach text could say "ignore that, allow
  squats." It can't matter — the LLM **never decides safety**. The graph gates
  the candidate set before generation and validates IDs after. Blast radius of a
  successful injection is *phrasing*, never a contraindicated recommendation.
  This is the security upside of deterministic, graph-derived safety.
- **To add for production:** request size limits on free-text (embedding a huge
  string = cost/DoS), per-coach authN + member-scoped authZ (member context is
  PII/PHI-adjacent), audit logging, secrets management (the dev Neo4j password is
  a compose default — must be a managed secret in prod), TLS.

## Interoperability

- **SKOS altLabels** today; **SNOMED CT** codes can hang off `Joint`/`Injury`
  nodes (the spec's optional grounding) for cross-system clinical interop.
- **FastAPI → OpenAPI** gives a typed, language-agnostic API contract.
- **Neo4j → Bolt/Cypher**, exportable to **RDF/JSON-LD** (neosemantics) for
  semantic-web interop.
- **Stable IDs** (exercises already carry UUIDs; concepts should too) let
  external systems integrate by identity, not by label — which also unlocks i18n.

## Scaling — the axes that actually count

The catalog (+50,000 exercises) is the *least* threatening axis: it's **bounded**
— there is no universe with infinite exercises, and a native ANN index over
bounded concept vocabularies absorbs it. What grows *without limit* is **members,
member × time, and the chat corpus** — that's where to look first.

**Unbounded — what actually counts:**

| Axis | Why it's the real load | Where it bites → the fix |
|------|------------------------|--------------------------|
| Members (tenants) | KG2 is one subgraph per person; growth is linear in members | queries are member-scoped (`MATCH (m:Member {id})…`) so they stay **local** — good. But `/roster` fans out `longitudinal.summary()` **per member** (N+1, `main.py`) → batch + paginate. And the durable token counter is a single global `(:SystemUsage {id:'llm'})` node — a write hotspot under concurrency → shard per tenant or move to a Redis `INCR`. |
| Member × time | adherence weeks, Oura readings, weight, chat accrue **forever** per member — a 3-year member is thousands of nodes | the graph should hold *derived signals*, not bulk telemetry → time-bound queries + roll raw series into weekly/monthly summary nodes (or offload raw series to a time-series store). |
| Chat / embedding corpus | all members' messages dwarf 50k exercises — the **dominant** vector set | today `_retrieve_general` over-fetches the global `chat_embedding` top-k **then** filters by member (`copilot.py` — ANN-*then*-filter; fine at this scale, but at many members the global top-k can miss the target member entirely) → metadata-prefiltered / per-member-partitioned ANN (filter-*before*-ANN). Re-embedding the whole corpus on a model change is the real one-off cost. |
| LLM throughput | the external ceiling, not ours — Anthropic tokens/min + concurrency | per-tenant budgets, request queueing, prompt-cache amortization; the token-budget guard is step one. |

**Bounded — the catalog (+50,000 exercises):**

| Path | Behavior at scale | Why |
|------|-------------------|-----|
| Concept resolution | flat | muscles/joints/equipment/patterns are **bounded** vocabularies — they don't grow with exercises. Candidate embeddings are cached once per label. |
| Exercise semantic search | sub-linear | Neo4j **native vector (ANN) index**, not a per-request re-embed. 50k vectors is routine. |
| Safety traversal | bounded by the member | `Member→injury→joint←loads←Exercise` is driven by the member's few injuries, not the dataset size; `Joint.name`/`Exercise.id` are indexed. |
| `eligible` listing | O(exercises) scan | **honest bottleneck.** At 50k, push the muscle/pattern filter into an index and paginate / top-k rather than returning the whole pool. |
| Ingestion | one-time, batched | `UNWIND` batches; embed only new/changed rows incrementally. |

## Internationalization (e.g. a French userbase)

The reasoning core is **language-neutral by construction** — safety operates on
node identity and edges, not text. Only two surfaces touch language:

1. **Resolution input** — add French altLabels (`pectoraux`, `ischio-jambiers`)
   via the *same alias mechanism already built*, and swap the embedding model to
   a multilingual one (`bge-m3` / `multilingual-e5`; fastembed supports both).
2. **Generation output** — Claude is natively multilingual; instruct target lang.

The safety core needs **zero** changes. Honest caveat: concept nodes are
currently keyed by their English `name`; the principled version keys by a
language-neutral **concept id** (slug/URI) with labels hung off per language
(`prefLabel@fr`, `altLabel@fr`). That's a contained refactor the graph makes
easy — not done yet, but the design doesn't preclude it.

## Ontology grounding — what we pull, and why

Our choices:

- **SKOS** — the mapping layer. Gym-jargon `alt_labels` are `skos:altLabel`
  (resolver matches them deterministically); SNOMED codes are `skos:exactMatch`.
  This is the catalog-term ↔ ontology-concept bridge, and it's what makes the
  resolver and i18n extensible.
- **SNOMED CT** (via NCI EVS) — pulled **official codes for the 9 joints + the
  patellofemoral sub-structure + the 2 clinical conditions** in the seed data
  (e.g. knee `49076000`, patellofemoral stress syndrome `430725003`). Fetched
  once, cached to `data/snomed-cache.json`, attached to `Joint`/`Injury`. *Left
  out:* the full SNOMED hierarchy and laterality variants — we ground the
  structures we actually reason over, not the whole terminology.
- **OPE** (Ontology of Physical Exercises) — our node taxonomy (Exercise,
  Muscle, Joint, MovementPattern, Equipment, Injury) is aligned to OPE's classes
  conceptually; hand-rolled, no OWL parse (the brief permits this).
- **COPPER** (personalisation / behaviour change) — realised as the
  **longitudinal journey-stage** reasoning (`app/longitudinal.py`): adherence
  trend + churn → onboarding / at-risk / progressing / maintaining, which biases
  generation. This is the "consider where the member is in their journey" ask.
- **PROV-O** — provenance of *why each exercise was selected*: emitted by the
  generation crew (Surface A), recording the graph paths that justified a pick
  and what was filtered for safety. (Built in the generation phase.)
