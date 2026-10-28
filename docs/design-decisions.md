# Design decisions

These are the calls I made and why. Each one is something I'd expect to be asked about.

## What happens when someone opens a PR?

The workflow checks out the PR branch, puts the base branch next to it with
`git worktree`, runs `dbt compile` in both, and passes the two `manifest.json` files to
`schemacheck`. The checker works out every model's columns on both sides, diffs them,
looks for references on head that point at nothing, and for each real break walks base
lineage to list the affected columns and exposures. The comment is posted, or the existing
one edited. The job fails on exit code 1 unless the PR has the `breaking-change-approved`
label. Labeling re-triggers the workflow, so adding the label flips the check without a
new push.

## Why parse compiled SQL instead of the raw model files?

Raw dbt models are Jinja templates, not SQL. `{{ ref('stg_orders') }}` means nothing to a
SQL parser, and macros can generate whole clauses. `compiled_code` in the manifest is what
dbt would actually send to the warehouse, with every `ref()` resolved to a real relation
name. That relation name is also how I map a table in the SQL back to a dbt node.

The price is that I need `dbt compile`, which needs a database connection. In CI that's a
Postgres service container and about 10 seconds.

## How does it handle `SELECT *`?

Models are qualified in dependency order against a schema that holds every upstream
model's columns so far. When `dim_products` does `select * from stg_products`, sqlglot
already knows what `stg_products` returns and expands the star into real columns. So a
column dropped from `stg_products` really does disappear from `dim_products` on head, and
`mart_daily_revenue`'s `dp.category` shows up as broken.

If the upstream columns are unknown (a source with no documented columns, and no CSV), the
star can't be expanded. I mark that model's columns as unknown, skip checking references
into it, and list it under "things the checker couldn't see". Pretending to know would be
worse.

## Rename vs. drop + add: how do you tell, and when is it wrong?

A rename is a removed column and an added column in the same model whose upstream inputs
are the same set of columns. `lower(email) as email` → `lower(email) as email_address`:
both come from `customers.email`, so it's a rename. When two new columns have the same
inputs, an identical SQL expression breaks the tie. If it's still a tie, I don't guess.

Where it goes wrong:

- **A rename that also changes the logic** (`email` → `lower(trim(email)) as email_clean`)
  is still detected, because the inputs are the same. Whether that counts as a rename is
  arguable, but the downstream impact is identical either way.
- **Columns with no upstream** (literals, `current_date`) can only match on identical
  expressions, so renaming `1 as is_active` works but other cases don't.
- **Seeds** have no lineage, so a renamed CSV header shows up as a drop plus an add.
- **A coincidence**: dropping `a` and adding an unrelated `b` that happens to be built
  from the same columns would be called a rename. The severity comes out the same, only
  the wording is wrong.

The severity doesn't depend on getting the rename right. Both a drop and a rename are
breaking if anything still references the old name. Rename detection only makes the
comment easier to read.

## Why check head for breaks but use base lineage for the chain?

They answer different questions.

"Is anything broken?" has to be asked of head. If the PR renames `email` and also updates
`dim_customers`, nothing is broken, and the check should pass. Base would wrongly say
everything that used `email` is affected.

"What does it break?" can only be answered from base. On head the column is gone, so there
are no lineage edges out of it. Base still has the full chain:
`stg_orders.discount → int_order_totals.discount_amount → fct_orders.discount_amount`.

## Why sqlglot instead of regex, or asking the database?

Regex can't tell `o.discount` inside a CTE from a string literal or a comment, doesn't
know what `select *` expands to, and has no idea that `discount_rate` in the outer query
*is* `discount` from two scopes down. Every rename-inside-a-CTE case would be a false
negative.

The database (running `EXPLAIN` or building into a scratch schema) would catch broken
references for real, but it needs data, it's slow, and it tells you *that* something
failed, not *which upstream column* caused it. sqlglot gives me scope resolution,
star expansion and column lineage offline in under a second, which is what lets the tests
run without a database.

## How is this different from `state:modified+` or Datafold?

`dbt ls --select state:modified+` is model-level. It says every model downstream of
`stg_orders` *might* be affected. Here that includes `mart_customer_ltv`, which isn't,
because LTV is built from payments. On a big project that list is so long people stop
reading it.

Datafold and similar tools do column-level lineage too, plus data diffs (comparing actual
rows between branches), which this doesn't do at all. They're products with a warehouse
connection. This is a small, free, offline check focused on one question: does this PR
remove something that's still used?

## What doesn't it catch?

- Dynamic SQL from macros that differs between CI and production
- BI tools reading tables directly, unless they're declared as exposures
- Columns whose *meaning* changes while the name stays (tax suddenly included in `net_revenue`)
- Type changes (planned: diff `catalog.json` from `dbt docs generate`)
- Anything outside Postgres SQL, for now

## How would it scale to 2,000 models?

The full run here takes about 0.8 seconds for 12 models on both branches. Most of that is
`qualify` and `lineage` per model, so it grows roughly linearly. At 2,000 models that's
somewhere around two minutes, which is too slow to run on every push without changes.
What I'd change:

1. **Only diff models whose `checksum` changed** in the manifest. A schema change can only
   start there.
2. **Only validate models downstream of those** for broken references.
3. **Cache base lineage** keyed by the model's checksum, since base usually equals the last
   main build, and most of it is already known.
4. Qualifying still needs every upstream schema, but that part is cheap compared to lineage.

## Why does removing a column from a dashboard-facing model count as breaking?

Exposures say "this dashboard depends on `fct_orders`", not which columns it reads. If a
PR drops `fct_orders.discount_amount` and no dbt model uses it, I have two choices: assume
the dashboard doesn't read it (and maybe let a break through) or assume it does (and
sometimes make someone add a label). The label costs ten seconds. A blank dashboard costs a
lot more, so I went with breaking.

## Hardest bug

See [bug-journal.md](bug-journal.md).
