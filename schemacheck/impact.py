"""Decide what actually breaks.

Two different questions, answered from two different sides:

* "Is anything broken?" is asked of the HEAD branch. If the PR also updated
  the downstream models, nothing references the old column any more and
  nothing is broken, no matter how scary the diff looks.
* "What does it break?" is answered from BASE lineage, because that's the
  graph that still contains the column. On head it's already gone.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import networkx as nx
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from schemacheck.diff import Change, Impact, Kind, Severity
from schemacheck.lineage import Col
from schemacheck.manifest import relation_key
from schemacheck.schema import SchemaState


@dataclass(frozen=True)
class BrokenRef:
    model: str  # the model holding the bad reference
    column: str | None  # None when a whole relation is missing
    source: str | None  # unique_id it was looked up in, if we could tell


def find_broken_refs(head: SchemaState, base: SchemaState) -> list[BrokenRef]:
    """Every column or relation a head model references that doesn't exist.

    sqlglot's own validate_qualify_columns stops at the first bad column and
    reports it as a string. Walking the scopes ourselves finds all of them and
    tells us which upstream relation each one was looked up in.
    """
    found: dict[BrokenRef, None] = {}  # dict as an ordered set
    for uid, q in head.qualified.items():
        for scope in traverse_scope(q):
            for ref in _check_scope(uid, scope, head, base):
                found[ref] = None
    return list(found)


def _check_scope(uid: str, scope: Scope, head: SchemaState, base: SchemaState):
    # alias -> (unique_id or None, set of columns or None if unknown)
    sources: dict[str, tuple[str | None, set[str] | None]] = {}
    for alias, src in scope.sources.items():
        if isinstance(src, exp.Table):
            key = relation_key(src)
            up = head.project.by_relation.get(key)
            if up is None:
                gone = base.project.by_relation.get(key)
                if gone is not None:
                    # Still reading a model this PR deleted (hard-coded relation name)
                    yield BrokenRef(uid, None, gone)
                sources[alias] = (None, None)
            else:
                cols = head.columns.get(up)
                sources[alias] = (up, set(cols) if cols is not None else None)
        elif isinstance(src, Scope):
            names = src.expression.named_selects
            sources[alias] = (None, None if "*" in names else set(names))
        else:
            sources[alias] = (None, None)

    select_aliases = (
        set(scope.expression.named_selects) if isinstance(scope.expression, exp.Select) else set()
    )

    for col in scope.columns:
        if isinstance(col.this, exp.Star):
            continue
        if col.table:
            if col.table not in sources:
                continue  # correlated reference to an outer scope
            up, cols = sources[col.table]
            if cols is not None and col.name not in cols:
                yield BrokenRef(uid, col.name, up)
            continue

        # Unqualified after qualify() means sqlglot couldn't place it anywhere.
        if col.name in select_aliases:
            continue  # ORDER BY / GROUP BY on an output alias
        if any(cols is None for _, cols in sources.values()):
            continue  # it might live in the source we know nothing about
        if any(col.name in cols for _, cols in sources.values()):
            continue  # ambiguous, not missing; postgres would complain, not us
        only = next(iter(sources.values()))[0] if len(sources) == 1 else None
        yield BrokenRef(uid, col.name, only)


def assess(
    changes: list[Change],
    broken: list[BrokenRef],
    base: SchemaState,
    head: SchemaState,
    base_graph: nx.DiGraph,
) -> list[Change]:
    """Set severity on each change and fill in what it breaks."""
    bp, hp = base.project, head.project

    # Map every lost column (including folded cascades) back to its change
    owner: dict[Col, Change] = {}
    for ch in changes:
        for col in ch.lost_columns + ch.cascade:
            owner[col] = ch
    removed_models = {ch.model: ch for ch in changes if ch.kind is Kind.MODEL_REMOVED}

    refs_by_change: dict[int, list[BrokenRef]] = {}
    orphans: list[BrokenRef] = []
    for ref in broken:
        ch = _attribute(ref, owner, removed_models, hp)
        if ch is None:
            orphans.append(ref)
        else:
            refs_by_change.setdefault(id(ch), []).append(ref)

    for ch in changes:
        refs = refs_by_change.get(id(ch), [])
        if ch.kind in (Kind.COLUMN_ADDED, Kind.MODEL_ADDED):
            ch.severity = Severity.INFO
            continue

        if ch.kind is Kind.MODEL_REMOVED:
            still_used = refs or [n for n in hp.nodes.values() if ch.model in n.depends_on]
            if still_used:
                ch.severity = Severity.BREAKING
                ch.reason = "still referenced on this branch"
                start = [(ch.model, c) for c in base.columns.get(ch.model) or []]
                _fill_impact(ch, start, refs, base, base_graph, extra_models={ch.model})
            else:
                ch.severity = Severity.WARNING
                ch.reason = "nothing on this branch reads it any more"
            continue

        # Column removed or renamed
        touched = {m for m, _ in ch.lost_columns + ch.cascade}
        exposed = [e.label for e in hp.exposures_on(touched)]
        if refs:
            ch.severity = Severity.BREAKING
            _fill_impact(ch, ch.lost_columns, refs, base, base_graph)
        elif exposed:
            # dbt can't see which columns a dashboard reads, so assume it reads this one
            ch.severity = Severity.BREAKING
            ch.reason = f"{bp.name(ch.model)} feeds {', '.join(exposed)}, which may read this column"
            ch.exposures = exposed
        else:
            ch.severity = Severity.WARNING
            what = "the old name" if ch.kind is Kind.COLUMN_RENAMED else "it"
            ch.reason = f"nothing in this project uses {what} any more; check consumers outside dbt"

    for ref in orphans:
        ch = Change(Kind.BROKEN_REFERENCE, ref.model, column=ref.column, severity=Severity.BREAKING)
        where = f" in {hp.name(ref.source)}" if ref.source else ""
        ch.reason = f"references `{ref.column}`, which doesn't exist{where}"
        ch.exposures = [e.label for e in hp.exposures_on({ref.model})]
        changes.append(ch)
    return changes


def _attribute(ref: BrokenRef, owner, removed_models, head_project) -> Change | None:
    if ref.column is None:
        return removed_models.get(ref.source)
    if ref.source is not None:
        return owner.get((ref.source, ref.column))
    # Unqualified or via a CTE: try each direct upstream of the broken model
    for up in head_project.graph.predecessors(ref.model):
        if (up, ref.column) in owner:
            return owner[(up, ref.column)]
    return None


def _fill_impact(
    ch: Change,
    start: list[Col],
    refs: list[BrokenRef],
    base: SchemaState,
    base_graph: nx.DiGraph,
    extra_models: set[str] | None = None,
) -> None:
    """Breadth-first walk down base lineage, remembering how we got to each column."""
    name = base.project.name
    cascade = set(ch.cascade)
    parent: dict[Col, Col] = {}
    queue = deque(c for c in start if c in base_graph)
    seen = set(queue)
    while queue:
        cur = queue.popleft()
        for nxt in base_graph.successors(cur):
            if nxt not in seen:
                seen.add(nxt)
                parent[nxt] = cur
                queue.append(nxt)

    start_set = set(start)
    rows: list[tuple[str, str | None, str]] = []
    for col, via in parent.items():
        verb = "uses" if via in start_set else "via"
        note = ", disappears via SELECT *" if col in cascade else ""
        rows.append((col[0], col[1], f"{verb} {name(via[0])}.{via[1]}{note}"))

    # References lineage can't see: filters, joins, group-bys
    reached = {m for m, _ in parent}
    for ref in refs:
        if ref.model in reached:
            continue
        reached.add(ref.model)
        what = f"{name(ref.source)}.{ref.column}" if ref.column else name(ref.source)
        rows.append((ref.model, None, f"references {what} in a join, filter or group by"))

    order = {uid: i for i, uid in enumerate(base.project.build_order())}
    rows.sort(key=lambda r: (order.get(r[0], len(order)), r[1] or ""))
    ch.breaks = [Impact(name(uid), col, via) for uid, col, via in rows]
    affected = reached | (extra_models or set())
    ch.exposures = [e.label for e in base.project.exposures_on(affected)]
