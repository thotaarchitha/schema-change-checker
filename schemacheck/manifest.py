"""Load a dbt manifest.json into the handful of things the checker cares about."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
from sqlglot import exp

# (database, schema, table), lowercased. dbt quotes every identifier, but our
# names are all lowercase, and folding case saves a class of silly mismatches
# between relation_name and what sqlglot pulls out of the SQL.
RelationKey = tuple[str, str, str]

TRACKED_TYPES = ("model", "seed", "snapshot")


class CheckerError(Exception):
    """The checker can't give a trustworthy answer. Maps to exit code 2."""


def relation_key(table: exp.Table | str) -> RelationKey:
    if isinstance(table, str):
        table = exp.to_table(table, dialect="postgres")
    return (table.catalog.lower(), table.db.lower(), table.name.lower())


@dataclass
class Node:
    unique_id: str
    name: str
    resource_type: str
    relation: RelationKey | None
    compiled_code: str | None
    depends_on: list[str]
    # Only used for seeds and sources, where there's no SQL to derive columns from
    known_columns: list[str] | None = None


@dataclass
class Exposure:
    unique_id: str
    label: str
    depends_on: list[str]


@dataclass
class Project:
    nodes: dict[str, Node]
    exposures: dict[str, Exposure]
    graph: nx.DiGraph = field(repr=False)
    by_relation: dict[RelationKey, str] = field(repr=False)

    def name(self, unique_id: str) -> str:
        node = self.nodes.get(unique_id)
        return node.name if node else unique_id.split(".")[-1]

    def build_order(self) -> list[str]:
        try:
            return list(nx.topological_sort(self.graph))
        except nx.NetworkXUnfeasible as e:
            raise CheckerError("the model graph has a cycle, which dbt itself shouldn't allow") from e

    def exposures_on(self, unique_ids: set[str]) -> list[Exposure]:
        return [e for e in self.exposures.values() if unique_ids.intersection(e.depends_on)]


def _seed_columns(node: dict, roots: list[Path]) -> list[str] | None:
    # The CSV header is the truth for a seed. Documented columns are a fallback,
    # which is also what lets test fixtures work without shipping CSVs.
    rel_path = node.get("original_file_path")
    for root in roots:
        path = root / rel_path if rel_path else None
        if path and path.is_file():
            with path.open(newline="", encoding="utf-8-sig") as f:
                header = next(csv.reader(f), None)
            return [h.strip() for h in header] if header else None
    documented = list((node.get("columns") or {}).keys())
    return documented or None


def load_manifest(path: str | Path) -> Project:
    path = Path(path)
    if not path.is_file():
        raise CheckerError(f"manifest not found: {path} (run `dbt compile` first)")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CheckerError(f"{path} is not valid JSON: {e}") from e

    nodes: dict[str, Node] = {}
    default_root = path.resolve().parent.parent  # <project>/target/manifest.json

    for uid, n in raw.get("nodes", {}).items():
        rtype = n.get("resource_type")
        if rtype not in TRACKED_TYPES:
            continue
        relation = relation_key(n["relation_name"]) if n.get("relation_name") else None
        node = Node(
            unique_id=uid,
            name=n["name"],
            resource_type=rtype,
            relation=relation,
            compiled_code=n.get("compiled_code"),
            depends_on=list(n.get("depends_on", {}).get("nodes", [])),
        )
        if rtype == "seed":
            roots = [Path(n["root_path"])] if n.get("root_path") else []
            node.known_columns = _seed_columns(n, roots + [default_root])
        elif rtype == "model" and node.compiled_code is None and relation is not None:
            # Ephemeral models have no relation; anything else without SQL means
            # the manifest came from `dbt parse`, which doesn't compile anything.
            raise CheckerError(
                f"model {node.name} has no compiled_code. Generate the manifest with "
                "`dbt compile`, not `dbt parse`."
            )
        nodes[uid] = node

    for uid, s in raw.get("sources", {}).items():
        nodes[uid] = Node(
            unique_id=uid,
            name=f"{s['source_name']}.{s['name']}",
            resource_type="source",
            relation=relation_key(s["relation_name"]),
            compiled_code=None,
            depends_on=[],
            known_columns=list((s.get("columns") or {}).keys()) or None,
        )

    exposures = {
        uid: Exposure(
            unique_id=uid,
            label=e.get("label") or e["name"],
            depends_on=list(e.get("depends_on", {}).get("nodes", [])),
        )
        for uid, e in raw.get("exposures", {}).items()
    }

    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    for uid, node in nodes.items():
        for dep in node.depends_on:
            if dep in nodes:  # skip macros, metrics and anything else we don't model
                graph.add_edge(dep, uid)

    by_relation = {n.relation: uid for uid, n in nodes.items() if n.relation}
    return Project(nodes=nodes, exposures=exposures, graph=graph, by_relation=by_relation)
