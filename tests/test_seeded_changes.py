"""The ten seeded changes from the project plan, run against real compiled SQL."""

from schemacheck.cli import main
from schemacheck.diff import Kind, Severity

from .conftest import FIXTURES


def only_change(result):
    assert len(result.changes) == 1, result.changes
    return result.changes[0]


def broken_columns(change):
    return {f"{b.model}.{b.column}" for b in change.breaks}


def test_01_dropping_a_used_column_is_breaking(fixture_case):
    result = fixture_case("01_drop_used_column")
    ch = only_change(result)
    assert (ch.kind, ch.severity, ch.column) == (Kind.COLUMN_REMOVED, Severity.BREAKING, "discount")
    assert {b.model for b in ch.breaks} == {"int_order_totals", "fct_orders", "mart_daily_revenue"}
    assert {
        "int_order_totals.discount_amount",
        "fct_orders.net_revenue",
        "mart_daily_revenue.net_revenue",
    } <= broken_columns(ch)
    # LTV is based on payments, so it must not get dragged in
    assert not any(b.model == "mart_customer_ltv" for b in ch.breaks)
    assert ch.exposures == ["Revenue Dashboard"]
    assert result.exit_code == 1


def test_02_dropping_an_unused_column_is_only_a_warning(fixture_case):
    result = fixture_case("02_drop_unused_column")
    ch = only_change(result)
    assert (ch.kind, ch.severity, ch.column) == (Kind.COLUMN_REMOVED, Severity.WARNING, "payment_method")
    assert result.exit_code == 0


def test_03_rename_is_detected_and_breaking(fixture_case):
    ch = only_change(fixture_case("03_rename_column"))
    assert ch.kind is Kind.COLUMN_RENAMED
    assert (ch.column, ch.new_column) == ("email", "email_address")
    assert ch.severity is Severity.BREAKING
    assert "dim_customers.email" in broken_columns(ch)
    assert ch.exposures == ["Customer Health Report"]


def test_04_rename_with_downstream_fixed_passes(fixture_case):
    result = fixture_case("04_rename_and_fix_downstream")
    ch = only_change(result)
    assert ch.kind is Kind.COLUMN_RENAMED and ch.severity is Severity.WARNING
    assert result.exit_code == 0


def test_05_new_column_is_info(fixture_case):
    result = fixture_case("05_add_column")
    ch = only_change(result)
    assert (ch.kind, ch.severity, ch.column) == (Kind.COLUMN_ADDED, Severity.INFO, "full_name")
    assert result.exit_code == 0


def test_06_removed_model_still_read_downstream(fixture_case):
    ch = only_change(fixture_case("06_remove_model"))
    assert ch.kind is Kind.MODEL_REMOVED and ch.severity is Severity.BREAKING
    assert "dim_customers.order_count" in broken_columns(ch)
    assert ch.exposures == ["Customer Health Report"]


def test_07_reference_inside_a_cte_is_found(fixture_case):
    ch = only_change(fixture_case("07_column_inside_cte"))
    assert ch.kind is Kind.COLUMN_RENAMED and ch.severity is Severity.BREAKING
    # gross_amount is computed inside the `items` CTE of int_order_totals
    gross = next(b for b in ch.breaks if (b.model, b.column) == ("int_order_totals", "gross_amount"))
    assert gross.via == "uses stg_order_items.quantity"


def test_08_change_behind_select_star_is_traced(fixture_case):
    result = fixture_case("08_change_behind_select_star")
    ch = only_change(result)  # dim_products' silent loss is folded in, not reported twice
    assert (ch.model, ch.column, ch.severity) == ("model.shop.stg_products", "category", Severity.BREAKING)
    assert ch.cascade == [("model.shop.dim_products", "category")]
    assert "mart_daily_revenue.category" in broken_columns(ch)
    star = next(b for b in ch.breaks if b.model == "dim_products")
    assert "SELECT *" in star.via


def test_09_reorder_and_reformat_reports_nothing(fixture_case):
    result = fixture_case("09_reorder_and_reformat")
    assert result.changes == []
    assert result.exit_code == 0


def test_10_unparseable_sql_exits_2_with_a_clear_message(tmp_path, capsys):
    case = FIXTURES / "10_unparseable_sql"
    out = tmp_path / "report.md"
    code = main(
        [
            "--base",
            str(case / "base/manifest.json"),
            "--head",
            str(case / "head/manifest.json"),
            "--out",
            str(out),
            "--quiet",
        ]
    )
    assert code == 2
    assert "couldn't parse the compiled SQL of stg_orders" in capsys.readouterr().err
    # The failure still produces a comment, so CI never reads silence as a pass
    assert "couldn't run" in out.read_text(encoding="utf-8")
