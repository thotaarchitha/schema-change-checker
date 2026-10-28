"""Column-level lineage: which upstream columns each output column is built from."""

from __future__ import annotations

import networkx as nx
from sqlglot import exp
from sqlglot.lineage import lineage

from schemacheck.manifest import relation_key
from schemacheck.schema import DIALECT, SchemaState

# Graph nodes are (unique_id, column) pairs; edges point upstream -> downstream.
Col = tuple[str, str]


def build_column_graph(state: SchemaState) -> nx.DiGraph:
    project = state.project
    graph = nx.DiGraph()
    for uid, cols in state.columns.items():
        for c in cols or []:
            graph.add_node((uid, c))

    for uid, q in state.qualified.items():
        if state.columns.get(uid) is None:
            continue
        try:
            # column=None returns every output column in one pass, so the model
            # is qualified once instead of once per column.
            roots = lineage(
                None,
                q,
                schema=state.schema,
                dialect=DIALECT,
                # Same leniency as schema.py, so a head model with a broken
                # reference still gets lineage for the columns that are fine
                validate_qualify_columns=False,
                allow_partial_qualification=True,
            )
        except Exception as e:  # noqa: BLE001 - sqlglot raises a zoo of types here
            state.notes.setdefault(uid, f"lineage failed for {project.name(uid)}: {e}")
            continue
        if not isinstance(roots, dict):
            roots = {roots.name: roots}

        for col, root in roots.items():
            for leaf in root.walk():
                if leaf.downstream or not isinstance(leaf.source, exp.Table):
                    continue
                upstream = project.by_relation.get(relation_key(leaf.source))
                if upstream is None:
                    continue
                source_col = leaf.name.split(".")[-1].strip('"')
                graph.add_edge((upstream, source_col), (uid, col))
    return graph
