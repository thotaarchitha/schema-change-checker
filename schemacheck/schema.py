"""Work out the output columns of every model, in dependency order.

Each model is qualified against the columns of everything upstream of it,
which is also what expands SELECT *. That's why the order matters: you can't
expand `select * from stg_products` until you know what stg_products returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from schemacheck.manifest import CheckerError, Project

DIALECT = "postgres"
# Types don't affect qualification, and we don't know them without a catalog.
UNKNOWN_TYPE = "unknown"


@dataclass
class SchemaState:
    project: Project
    schema: MappingSchema
    # None means "can't tell", e.g. SELECT * over a source with no documented columns
    columns: dict[str, list[str] | None] = field(default_factory=dict)
    qualified: dict[str, exp.Query] = field(default_factory=dict)
    # Normalised select expression per output column, used to spot renames
    expressions: dict[str, dict[str, str]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)


def _register(schema: MappingSchema, relation: tuple[str, str, str], columns: list[str]) -> None:
    table = exp.table_(relation[2], db=relation[1], catalog=relation[0], quoted=True)
    schema.add_table(table, {c: UNKNOWN_TYPE for c in columns}, dialect=DIALECT)


def parse_model(name: str, sql: str) -> exp.Query:
    try:
        parsed = sqlglot.parse_one(sql, read=DIALECT)
    except ParseError as e:
        first = str(e).splitlines()[0]
        raise CheckerError(f"couldn't parse the compiled SQL of {name}: {first}") from e
    if not isinstance(parsed, exp.Query):
        raise CheckerError(f"{name} compiled to a {parsed.key.upper()} statement, expected a SELECT")
    return parsed


def build_schema(project: Project) -> SchemaState:
    state = SchemaState(project=project, schema=MappingSchema(dialect=DIALECT))

    for uid in project.build_order():
        node = project.nodes[uid]
        if node.compiled_code is None:
            cols = node.known_columns
            if cols is None and node.resource_type != "model":
                state.notes[uid] = f"no known columns for {node.resource_type} {node.name}"
        else:
            cols = _model_columns(state, uid)

        state.columns[uid] = cols
        if cols is not None and node.relation is not None:
            _register(state.schema, node.relation, cols)
    return state


def _model_columns(state: SchemaState, uid: str) -> list[str] | None:
    node = state.project.nodes[uid]
    parsed = parse_model(node.name, node.compiled_code)
    try:
        # Both flags on purpose: a broken reference shouldn't stop us from
        # learning the model's output columns. impact.py hunts the breaks down.
        # (Without allow_partial_qualification, `c.email` on a table that lost
        # `email` raises even with validation off.)
        q = qualify(
            parsed,
            schema=state.schema,
            dialect=DIALECT,
            validate_qualify_columns=False,
            allow_partial_qualification=True,
        )
    except OptimizeError as e:
        state.notes[uid] = f"couldn't qualify {node.name}: {e}"
        return None

    state.qualified[uid] = q
    names = [s.alias_or_name for s in q.selects]
    if "*" in names or any(isinstance(s, exp.Star) for s in q.selects):
        state.notes[uid] = f"{node.name} selects * from something with unknown columns"
        return None
    state.expressions[uid] = {s.alias_or_name: s.unalias().sql(DIALECT) for s in q.selects}
    return names
