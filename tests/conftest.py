from __future__ import annotations

import json
from pathlib import Path

import pytest

from schemacheck.check import run_check

FIXTURES = Path(__file__).parent / "fixtures"


def rel(name: str) -> str:
    return f'"shop"."analytics"."{name}"'


def write_manifest(
    path: Path,
    models: dict[str, str],
    seeds: dict[str, list[str]] | None = None,
    exposures: dict[str, list[str]] | None = None,
) -> Path:
    """A minimal manifest for edge cases the real warehouse doesn't have.

    Model SQL refers to other nodes as {name}; that's swapped for the relation
    name, and depends_on is filled in from which names appear.
    """
    seeds = seeds or {}
    all_names = list(seeds) + list(models)
    uid = {n: f"{'seed' if n in seeds else 'model'}.shop.{n}" for n in all_names}
    nodes = {}
    for name, cols in seeds.items():
        nodes[uid[name]] = {
            "name": name,
            "resource_type": "seed",
            "relation_name": rel(name),
            "original_file_path": f"seeds/{name}.csv",
            "columns": {c: {"name": c} for c in cols},
        }
    for name, sql in models.items():
        deps = [uid[n] for n in all_names if "{" + n + "}" in sql]
        compiled = sql.format(**{n: rel(n) for n in all_names})
        nodes[uid[name]] = {
            "name": name,
            "resource_type": "model",
            "relation_name": rel(name),
            "compiled_code": compiled,
            "depends_on": {"nodes": deps},
        }
    exp = {
        f"exposure.shop.{k}": {
            "name": k,
            "label": k.replace("_", " ").title(),
            "depends_on": {"nodes": [uid[d] for d in v]},
        }
        for k, v in (exposures or {}).items()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"nodes": nodes, "exposures": exp, "sources": {}}), encoding="utf-8")
    return path


@pytest.fixture
def fixture_case():
    def load(case: str):
        return run_check(
            FIXTURES / case / "base" / "manifest.json", FIXTURES / case / "head" / "manifest.json"
        )

    return load
