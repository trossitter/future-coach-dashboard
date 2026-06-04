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

## Scaling (e.g. +50,000 exercises)

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
