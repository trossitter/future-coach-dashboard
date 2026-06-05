"""Thin Neo4j driver wrapper — one process-wide driver, simple query helpers."""
from __future__ import annotations

from functools import lru_cache

from neo4j import Driver, GraphDatabase

from .config import settings


@lru_cache(maxsize=1)
def get_driver() -> Driver:
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


def run(cypher: str, **params) -> list[dict]:
    """Run a read/write query and return rows as plain dicts."""
    with get_driver().session() as session:
        result = session.run(cypher, **params)
        return [record.data() for record in result]
