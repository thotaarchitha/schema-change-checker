"""Compare base and head schemas and say what changed.

This module only describes changes. Whether a change is BREAKING depends on
who uses it, and that's impact.py's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import networkx as nx

from schemacheck.lineage import Col
from schemacheck.schema import SchemaState


class Kind(StrEnum):
    COLUMN_REMOVED = "column_removed"
    COLUMN_RENAMED = "column_renamed"
    COLUMN_ADDED = "column_added"
    MODEL_REMOVED = "model_removed"
    MODEL_ADDED = "model_added"
    BROKEN_REFERENCE = "broken_reference"


class Severity(StrEnum):
    BREAKING = "BREAKING"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class Impact:
    model: str
    column: str | None
    via: str  # human-readable "why", e.g. "uses stg_orders.discount"


@dataclass
class Change:
    kind: Kind
    model: str  # unique_id
    column: str | None = None
    new_column: str | None = None
    severity: Severity = Severity.INFO
    reason: str = ""
    # Downstream removals that only happened because of this one, e.g. a column
    # that vanished from a SELECT * model. Folded in rather than reported twice.
    cascade: list[Col] = field(default_factory=list)
    breaks: list[Impact] = field(default_factory=list)
    exposures: list[str] = field(default_factory=list)

    @property
    def lost_columns(self) -> list[Col]:
        """The base-side columns this change takes away."""
        if self.kind in (Kind.COLUMN_REMOVED, Kind.COLUMN_RENAMED):
            return [(self.model, self.column)]
        return []


def diff_schemas(
    base: SchemaState, head: SchemaState, base_graph: nx.DiGraph, head_graph: nx.DiGraph
) -> tuple[list[Change], list[str]]:
    changes: list[Change] = []
    notes: list[str] = []
    bp, hp = base.project, head.project

    for uid in base.columns:
        if uid not in head.columns:
            if bp.nodes[uid].resource_type != "source":
                changes.append(Change(Kind.MODEL_REMOVED, uid))
            continue
        before, after = base.columns[uid], head.columns[uid]
        if before is None or after is None:
            if before != after:
                notes.append(f"couldn't compare columns of {bp.name(uid)}: columns unknown on one side")
            continue

        # Sets on purpose: reordering or reformatting a select list isn't a change.
        removed = [c for c in before if c not in after]
        added = [c for c in after if c not in before]
        renames = _match_renames(uid, removed, added, base, head, base_graph, head_graph)
        for old, new in renames.items():
            changes.append(Change(Kind.COLUMN_RENAMED, uid, column=old, new_column=new))
        changes += [Change(Kind.COLUMN_REMOVED, uid, column=c) for c in removed if c not in renames]
        changes += [Change(Kind.COLUMN_ADDED, uid, column=c) for c in added if c not in renames.values()]

    for uid in head.columns:
        if uid not in base.columns and hp.nodes[uid].resource_type != "source":
            changes.append(Change(Kind.MODEL_ADDED, uid))

    _fold_cascades(changes, base_graph)
    return changes, notes


def _match_renames(uid, removed, added, base, head, base_graph, head_graph) -> dict[str, str]:
    """Pair a removed column with an added one when they're built from the same inputs.

    Same upstream columns is the main signal. When two added columns share the
    same inputs, the identical SQL expression breaks the tie; if that still
    doesn't single one out, we refuse to guess and call it a drop plus an add.
    """
    if not removed or not added:
        return {}
    base_expr = base.expressions.get(uid, {})
    head_expr = head.expressions.get(uid, {})
    renames: dict[str, str] = {}
    taken: set[str] = set()

    for old in removed:
        old_inputs = set(base_graph.predecessors((uid, old))) if (uid, old) in base_graph else set()
        candidates = []
        for new in added:
            if new in taken:
                continue
            new_inputs = set(head_graph.predecessors((uid, new))) if (uid, new) in head_graph else set()
            same_inputs = bool(old_inputs) and old_inputs == new_inputs
            same_expr = base_expr.get(old) is not None and base_expr.get(old) == head_expr.get(new)
            if same_inputs or same_expr:
                candidates.append((same_inputs and same_expr, same_inputs, new))
        if not candidates:
            continue
        candidates.sort(reverse=True)
        best = candidates[0]
        # A tie at the top means we genuinely can't tell which column it became
        if len(candidates) > 1 and candidates[1][:2] == best[:2]:
            continue
        renames[old] = best[2]
        taken.add(best[2])
    return renames


def _fold_cascades(changes: list[Change], base_graph: nx.DiGraph) -> None:
    """Report a removal once, at the column where it started.

    Dropping stg_products.category also silently drops dim_products.category
    (that model does SELECT *). Two separate "removed" lines would bury the
    actual cause, so the downstream removal becomes part of the root's cascade.
    """
    lost: dict[Col, Change] = {}
    for ch in changes:
        for col in ch.lost_columns:
            lost[col] = ch

    def roots_of(col: Col, seen: set[Col]) -> set[Col]:
        parents = [p for p in base_graph.predecessors(col) if p in lost] if col in base_graph else []
        if not parents:
            return {col}
        out: set[Col] = set()
        for p in parents:
            if p not in seen:
                out |= roots_of(p, seen | {p})
        return out or {col}

    folded: set[int] = set()
    for col, ch in lost.items():
        roots = roots_of(col, {col})
        if roots == {col}:
            continue
        for r in roots:
            lost[r].cascade.append(col)
        folded.add(id(ch))
    changes[:] = [c for c in changes if id(c) not in folded]
