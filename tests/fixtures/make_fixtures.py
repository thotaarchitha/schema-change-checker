"""Build the base/head manifest pairs the tests run against.

Each fixture starts from the real compiled manifest of the sample warehouse,
trimmed down to the fields the checker reads, so the tests exercise the SQL
dbt actually produces, not hand-written approximations of it. Edits are plain
string replacements, and every one asserts that its target text exists, so if
a model changes the fixture build fails instead of quietly testing nothing.

    cd warehouse && dbt compile && cd ..
    python tests/fixtures/make_fixtures.py [path/to/manifest.json]
"""

from __future__ import annotations

import copy
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "warehouse" / "target" / "manifest.json"
OUT = Path(__file__).resolve().parent

KEEP_NODE_FIELDS = ("name", "resource_type", "relation_name", "compiled_code", "original_file_path")


def trim(raw: dict) -> dict:
    nodes = {}
    for uid, n in raw["nodes"].items():
        if n["resource_type"] not in ("model", "seed"):
            continue
        slim = {k: n.get(k) for k in KEEP_NODE_FIELDS}
        slim["depends_on"] = (
            {"nodes": n["depends_on"].get("nodes", [])} if "depends_on" in n else {"nodes": []}
        )
        if n["resource_type"] == "seed":
            # Fixtures don't ship the CSVs, so record the header as documented columns
            with (ROOT / "warehouse" / n["original_file_path"]).open(newline="") as f:
                header = next(csv.reader(f))
            slim["columns"] = {c: {"name": c} for c in header}
        nodes[uid] = slim
    exposures = {
        uid: {"name": e["name"], "label": e.get("label"), "depends_on": {"nodes": e["depends_on"]["nodes"]}}
        for uid, e in raw["exposures"].items()
    }
    return {
        "metadata": {"note": "trimmed by tests/fixtures/make_fixtures.py"},
        "nodes": nodes,
        "exposures": exposures,
        "sources": {},
    }


def model(m: dict, name: str) -> dict:
    return m["nodes"][f"model.shop.{name}"]


def edit(m: dict, name: str, old: str, new: str) -> None:
    node = model(m, name)
    if old not in node["compiled_code"]:
        raise SystemExit(f"fixture edit on {name} didn't match, the model changed:\n{old!r}")
    node["compiled_code"] = node["compiled_code"].replace(old, new, 1)


# --- the ten seeded changes ------------------------------------------------


def case_01_drop_used_column(head):
    edit(head, "stg_orders", "end as status,\n    coalesce(discount, 0) as discount", "end as status")


def case_02_drop_unused_column(head):
    edit(head, "stg_payments", "method as payment_method,\n    ", "")


def case_03_rename_column(head):
    edit(head, "stg_customers", "lower(email) as email", "lower(email) as email_address")


def case_04_rename_and_fix_downstream(head):
    case_03_rename_column(head)
    edit(head, "dim_customers", "c.email,", "c.email_address as email,")


def case_05_add_column(head):
    edit(
        head,
        "stg_customers",
        "    last_name,\n",
        "    last_name,\n    first_name || ' ' || last_name as full_name,\n",
    )


def case_06_remove_model(head):
    # dbt won't compile a ref() to a deleted model, so the realistic way this
    # reaches the checker is a hard-coded relation name left behind downstream.
    del head["nodes"]["model.shop.int_customer_orders"]
    dim = model(head, "dim_customers")
    dim["depends_on"]["nodes"].remove("model.shop.int_customer_orders")


def case_07_column_inside_cte(head):
    edit(head, "stg_order_items", "    quantity\nfrom", "    quantity as qty\nfrom")


def case_08_change_behind_select_star(head):
    edit(head, "stg_products", "    category,\n", "")


def case_09_reorder_and_reformat(head):
    edit(
        head,
        "stg_customers",
        "    first_name,\n    last_name,\n",
        "    last_name,   -- moved up, no other change\n    FIRST_NAME,\n",
    )
    edit(head, "fct_orders", "select\n    t.order_id,", "-- formatting only\nSELECT t.order_id,")


def case_10_unparseable_sql(head):
    # A dropped paren. (sqlglot shrugs off a doubled comma, so that doesn't work as a test.)
    edit(head, "stg_orders", "coalesce(discount, 0) as discount", "coalesce(discount, 0 as discount")


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_")]


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE
    if not source.exists():
        sys.exit(f"{source} not found; run `dbt compile` in warehouse/ first")
    base = trim(json.loads(source.read_text(encoding="utf-8")))
    for fn in CASES:
        head = copy.deepcopy(base)
        fn(head)
        case_dir = OUT / fn.__name__.removeprefix("case_")
        for side, manifest in (("base", base), ("head", head)):
            (case_dir / side).mkdir(parents=True, exist_ok=True)
            (case_dir / side / "manifest.json").write_text(
                json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",  # Windows would write CRLF and every fixture would show as changed
            )
        print(f"wrote {case_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
