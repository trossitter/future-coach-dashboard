# Future — Knowledge Graph Coaching Platform

AI coaching assistant that ingests a member's context into a **knowledge graph**,
retrieves the relevant slice via **GraphRAG** (graph traversal + vector search),
and generates **injury-aware, explainable** workout recommendations.

The differentiator is reasoning over relationships: safety is enforced
**deterministically by graph traversal**, and every recommendation can answer
*"why?"* by pointing at the path that produced it — not an LLM rationalization.

> Take-home track 2 of 2. Synthetic data only — no real member data is used.

## Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Graph + vectors | **Neo4j 5** | Spec-preferred; native vector index keeps GraphRAG's traversal + semantic halves in one store |
| Backend | Python · FastAPI · LangGraph | typed API, agentic generation |
| Embeddings | local `sentence-transformers` (bge-small) | token-efficient, no per-lookup API cost |
| LLM | Claude | generation + explanation phrasing only — never safety decisions |
| Frontend | React + Vite | dashboard / chat demo |

## Run

```bash
docker compose up --build        # brings up Neo4j + backend
curl -X POST localhost:8000/ingest                       # load graph
curl localhost:8000/members/M-001/contraindicated        # safety filter
curl localhost:8000/members/M-001/exercises/<id>/why     # explainability
```

Neo4j Browser: http://localhost:7474 (`neo4j` / `futurepassword`).

## Status

- [x] Infra (Docker), Neo4j schema + constraints + vector index
- [x] Ingestion pipeline (exercises + synthetic member → nodes/edges)
- [x] Deterministic safety traversal + explainability query
- [ ] GraphRAG retrieval (traversal + vector search)
- [ ] LangGraph generation runtime
- [ ] Coach copilot
- [ ] Frontend
- [ ] Critical-path tests · README: production evaluation section

See [`docs/SCHEMA.md`](docs/SCHEMA.md) for the graph contract.
