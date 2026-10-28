"""Edge cases the sample warehouse doesn't naturally contain."""

import json

import pytest

from schemacheck.check import run_check
from schemacheck.diff import Kind, Severity
from schemacheck.manifest import CheckerError, load_manifest
from schemacheck.report import COMMENT_MARKER, render_json, render_markdown

from .conftest import write_manifest

SEEDS = {"raw": ["id", "a", "b"]}


def check(tmp_path, base_models, head_models, seeds=SEEDS, exposures=None):
    base = write_manifest(tmp_path / "base.json", base_models, seeds, exposures)
    head = write_manifest(tmp_path / "head.json", head_models, seeds, exposures)
    return run_check(base, head)


def test_ambiguous_rename_is_not_guessed(tmp_path):
    # `a` disappears and two new columns are built from exactly the same input.
    # Either could be the rename; reporting a confident wrong answer is worse.
    result = check(
        tmp_path,
        {"m": "select id, a from {raw}"},
        {"m": "select id, a as a1, a as a2 from {raw}"},
    )
    kinds = sorted(c.kind.value for c in result.changes)
    assert kinds == ["column_added", "column_added", "column_removed"]


def test_rename_wins_tie_on_identical_expression(tmp_path):
    result = check(
        tmp_path,
        {"m": "select id, upper(a) as a_up from {raw}"},
        {"m": "select id, upper(a) as a_upper, lower(a) as a_low from {raw}"},
    )
    renamed = [c for c in result.changes if c.kind is Kind.COLUMN_RENAMED]
    assert [(c.column, c.new_column) for c in renamed] == [("a_up", "a_upper")]


def test_reference_used_only_in_a_filter_is_still_caught(tmp_path):
    # Lineage only follows selected columns; a WHERE clause needs the head-side check
    result = check(
        tmp_path,
        {"s": "select id, a, b from {raw}", "m": "select id from {s} where b > 0"},
        {"s": "select id, a from {raw}", "m": "select id from {s} where b > 0"},
    )
    (ch,) = result.changes
    assert ch.severity is Severity.BREAKING
    assert [b.via for b in ch.breaks] == ["references s.b in a join, filter or group by"]


def test_typo_in_a_new_reference_is_breaking_on_its_own(tmp_path):
    result = check(
        tmp_path,
        {"m": "select id, a from {raw}"},
        {"m": "select r.id, r.aa from {raw} as r"},
    )
    broken = [c for c in result.changes if c.kind is Kind.BROKEN_REFERENCE]
    assert len(broken) == 1 and broken[0].column == "aa"
    assert result.exit_code == 1


def test_removing_a_column_that_feeds_a_dashboard_is_breaking(tmp_path):
    # Nothing in dbt reads `b`, but dbt can't see inside the dashboard either
    result = check(
        tmp_path,
        {"mart": "select id, a, b from {raw}"},
        {"mart": "select id, a from {raw}"},
        exposures={"exec_dashboard": ["mart"]},
    )
    (ch,) = result.changes
    assert ch.severity is Severity.BREAKING
    assert "Exec Dashboard" in ch.reason


def test_unused_model_removed_is_a_warning(tmp_path):
    result = check(
        tmp_path,
        {"m": "select id from {raw}", "orphan": "select a from {raw}"},
        {"m": "select id from {raw}"},
    )
    (ch,) = result.changes
    assert (ch.kind, ch.severity) == (Kind.MODEL_REMOVED, Severity.WARNING)


def test_select_star_over_unknown_columns_is_a_note_not_a_crash(tmp_path):
    result = check(
        tmp_path,
        {"m": "select * from {raw}"},
        {"m": "select * from {raw}"},
        seeds={"raw": []},  # no CSV and no documented columns
    )
    assert result.changes == []
    assert any("unknown columns" in n for n in result.notes)


def test_missing_manifest_is_a_checker_error(tmp_path):
    with pytest.raises(CheckerError, match="run `dbt compile` first"):
        load_manifest(tmp_path / "nope.json")


def test_parse_only_manifest_is_rejected(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.shop.m": {
                        "name": "m",
                        "resource_type": "model",
                        "relation_name": '"shop"."analytics"."m"',
                        "depends_on": {"nodes": []},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CheckerError, match="not `dbt parse`"):
        load_manifest(path)


def test_report_shapes(fixture_case):
    result = fixture_case("01_drop_used_column")
    md = render_markdown(result)
    assert md.startswith(COMMENT_MARKER)
    assert "**stg_orders**: column `discount` was REMOVED" in md
    assert "breaking-change-approved" in md
    payload = json.loads(render_json(result))
    assert payload["summary"] == {"BREAKING": 1, "WARNING": 0, "INFO": 0}
    assert payload["changes"][0]["model"] == "stg_orders"
