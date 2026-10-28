# How it works

This walks through one run, from two manifests to a PR comment. The code for each step
lives in its own module, so each heading names the file.

## 0. Getting two manifests (the workflow)

The action checks out the PR with full history, then runs
`git worktree add ../base origin/<base branch>`. That gives me both branches side by side
without a second clone. `dbt compile` runs in each `warehouse/` folder, and each one writes
its own `target/manifest.json`.

`dbt compile` needs a database connection even though it builds nothing, so the job runs a
Postgres service container. If either branch fails to compile, the comment shows the
compile error and the job fails. That's almost always a `ref()` to a model the PR deleted.

## 1. Load the manifest (`manifest.py`)

From `manifest.json` I keep the nodes whose `resource_type` is model, seed or snapshot,
plus sources and exposures. For each one I keep:

- `relation_name`, the real table, e.g. `"shop"."analytics"."stg_orders"`, turned into a
  `(database, schema, table)` key
- `compiled_code`, the SQL with every `ref()` already resolved
- `depends_on.nodes`

Those go into a networkx `DiGraph` with edges pointing upstream → downstream, and
`topological_sort` gives me the build order.

Two things are deliberately rejected with a clear error: a model with no `compiled_code`
(the manifest came from `dbt parse`), and a cycle in the graph.

## 2. Output columns of every model (`schema.py`)

Seeds: the CSV header. If the CSV isn't next to the manifest, I fall back to documented
columns, which is also how the test fixtures work without shipping CSVs.

Models, in build order:

1. `sqlglot.parse_one(compiled_code, read="postgres")`
2. `qualify(...)` against a `MappingSchema` holding every upstream relation's columns so
   far. This is what turns `select *` into a real list of columns, and why the order
   matters.
3. The output columns are `[s.alias_or_name for s in expr.selects]`.
4. Register them in the schema before moving to the next model.

I call `qualify` with `validate_qualify_columns=False` and
`allow_partial_qualification=True`. Without those, a model with a broken reference raises
an error, and I'd lose its output columns along with everything downstream of it. Finding
the breaks is step 3's job, so step 2 is lenient on purpose.

This runs once for base and once for head.

## 3. Broken references on head (`impact.py`, first half)

For every head model I go through `traverse_scope` on the qualified SQL. A scope is the
main query, a CTE or a subquery. For each one I know its sources: a real table (whose
columns I learned in step 2) or another scope (whose columns are its select list). Then
for every column in the scope:

- `alias.column` where the alias is a known source without that column: **broken**
- an unqualified column that `qualify` couldn't place anywhere, and that isn't a select
  alias used in ORDER BY or GROUP BY: **broken**
- a table that existed on base but is gone on head: **broken**, a removed model still
  being read

I don't use sqlglot's `validate_qualify_columns=True` here because it raises on the
*first* bad column and only gives a message string. Walking the scopes myself finds all of
them and tells me which upstream relation each one was looked up in, which step 6 needs.

This step is also why a PR that renames a column *and* updates everything downstream
passes: on head, nothing references the old name.

## 4. Classify the changes (`diff.py`)

For every model on both sides, I compare the two column lists as sets, so reordering or
reformatting is not a change:

| Change | How it's detected | Severity |
|---|---|---|
| Column removed | in base, not in head | BREAKING if something on head still references it, or the model feeds an exposure; otherwise WARNING |
| Column renamed | a removed + added pair with the same upstream inputs (see below) | same rule as removed |
| Column added | in head, not in base | INFO |
| Model removed | node gone from head | BREAKING if head still reads it, otherwise WARNING |
| Model added | new node | INFO |
| Broken reference | a head reference that isn't explained by any change above (typos, mostly) | BREAKING |

**Renames.** For a removed column `old` and an added column `new` in the same model, I
compare the upstream columns each one is built from (base lineage for `old`, head lineage
for `new`). If they match, it's probably a rename. If two added columns match, an
identical SQL expression decides. If it's still a tie, I report a drop plus an add.

**Cascades.** If `stg_products.category` is dropped, `dim_products.category` disappears
too, because `dim_products` is `select *`. Both look like removals, but only one of them
is a decision somebody made. Any removal whose base lineage parent was also removed gets
folded into that parent's change.

## 5. Column-level lineage (`lineage.py`)

`sqlglot.lineage.lineage(None, sql, schema=...)` returns a lineage tree for every output
column in one call. I walk each tree down to its leaves. Every leaf whose source is a real
table becomes an edge `(upstream model, column) → (this model, column)`.

So `int_order_totals.discount_amount` gets an edge from `stg_orders.discount`, even though
inside the model it travels through a CTE called `orders` under the name `discount_rate`.

## 6. Walk the impact (`impact.py`, second half)

For each BREAKING change I do a breadth-first walk down the **base** column graph from the
lost column, recording which column led to which. That gives the "via" text in the comment.
Head can't answer this question, because on head the column is already gone and nothing
points at it.

Lineage only follows selected columns. A model that uses the column in a `WHERE` or a join
is invisible to it, but step 3 still found that model, so it gets added as "references X
in a join, filter or group by".

Then every exposure whose `depends_on` touches an affected model goes on the list.

## 7. Report (`report.py`, `cli.py`)

Breaking changes first, then warnings, then safe changes, then a collapsed list of things
the checker couldn't see (for example, a `select *` over a source with no known columns).
The comment starts with an HTML marker, which is how the workflow finds and edits its own
comment instead of adding a new one on every push.

`report.json` has the same content for other tools.

Exit codes: `0` nothing breaks, `1` something breaks, `2` the checker failed. A failure
still writes a comment, so a broken checker can never look like a pass.
