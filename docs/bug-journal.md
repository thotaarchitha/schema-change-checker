# Bug journal

Things that broke while building this, newest at the bottom.

## `qualify` raises even with validation turned off

**Symptom:** dropping `stg_products.category` came back as a WARNING ("nothing uses it"),
even though `mart_daily_revenue` clearly uses `dp.category`. The notes section said
`couldn't qualify mart_daily_revenue: Unknown column: category`.

**Cause:** I'd passed `validate_qualify_columns=False` and assumed that meant "never
raise about columns". It doesn't. sqlglot still raises `Unknown column` for a *qualified*
reference (`dp.category`) when it knows the source table's columns and the name isn't
among them. Only unqualified columns are left alone. So the broken model was never
qualified, never got scanned for broken references, and the change looked unused.

**Fix:** also pass `allow_partial_qualification=True`, both in `schema.py` and in the
`lineage()` call. That's the flag that actually skips the check.

**Lesson:** a lenient pass and a strict pass are two separate jobs. The bug was the
lenient pass failing loudly in a place where it needed to just keep going.

## sqlglot parses SQL that Postgres wouldn't

**Symptom:** test case 10 ("SQL the checker can't parse") exited 0.

**Cause:** the fixture introduced a doubled comma (`order_date,,`). Postgres would reject
it, but sqlglot's parser is forgiving and just carried on.

**Fix:** the fixture now drops a closing paren instead, which sqlglot does reject. Worth
knowing in general: "sqlglot parsed it" doesn't mean "the warehouse will accept it". This
tool checks references, not syntax. `dbt compile` doesn't catch syntax errors either. Only
actually running the model does.

## Windows path length

**Symptom:** `pip install` failed inside the venv with `No such file or directory` on a
file it had just extracted.

**Cause:** the first working folder was nested deep enough that `.venv/Lib/site-packages/...`
went past Windows' 260-character limit, and long-path support was off.

**Fix:** moved the project to a short path and keep the venv outside the repo. Not a
code bug, but it cost time, and dbt's `target/compiled/...` paths would have hit the same
limit.
