"""SNOMED CT grounding via the NCI EVS REST API.

The "research" decision (per the brief): we don't ingest SNOMED wholesale — we
pull a small, well-justified subset (our 9 joints + key sub-structure and the
clinical conditions in the seed data) and attach the official concept code +
preferred term to the graph as a SKOS `exactMatch`-style grounding.

`build_cache()` fetches from NCI EVS and writes data/snomed-cache.json; that file
is committed, so the local `docker compose up` run is fully offline. No app
imports here, so the cache can be (re)built standalone with stdlib only.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api-evsrest.nci.nih.gov/api/v1/concept/snomedct_us"

# our concept name -> SNOMED CT search term
JOINT_TERMS = {
    "knee": "knee joint structure",
    "hip": "hip joint structure",
    "shoulder": "shoulder joint structure",
    "ankle": "ankle joint structure",
    "elbow": "elbow joint structure",
    "wrist": "wrist joint structure",
    "cervical spine": "cervical spine structure",
    "thoracic spine": "thoracic spine structure",
    "lumbar spine": "lumbar spine structure",
    "patellofemoral joint": "patellofemoral joint structure",
}

# clinical conditions present in the seed members (matched onto injuries by note)
CONDITION_TERMS = {
    "patellofemoral": "patellofemoral pain syndrome",
    "rotator cuff": "rotator cuff syndrome",
}


def _search(term: str) -> dict | None:
    url = f"{BASE}/search?term={urllib.parse.quote(term)}&pageSize=1"
    with urllib.request.urlopen(url, timeout=25) as resp:
        data = json.load(resp)
    concepts = data.get("concepts", [])
    if concepts:
        return {"code": concepts[0]["code"], "name": concepts[0]["name"]}
    return None


def build_cache(out_path: str) -> dict:
    cache: dict = {"joints": {}, "conditions": {}}
    for name, term in JOINT_TERMS.items():
        hit = _search(term)
        if hit:
            cache["joints"][name] = hit
    for key, term in CONDITION_TERMS.items():
        hit = _search(term)
        if hit:
            cache["conditions"][key] = hit
    Path(out_path).write_text(json.dumps(cache, indent=2))
    return cache


def load_cache(data_dir: str) -> dict:
    p = Path(data_dir) / "snomed-cache.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"joints": {}, "conditions": {}}
