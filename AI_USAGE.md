# AI Usage

AI assisted with scaffolding, Cypher, LangGraph wiring, UI iteration, synthetic
member expansion, and documentation.

The architecture choices were kept explicit: Neo4j owns truth, graph traversal
owns safety, vectors are fallback retrieval, and LLM output is validated or
treated as narration only.

Critical behaviors are covered by tests and the evaluation harness rather than
trusted because a model wrote them.
