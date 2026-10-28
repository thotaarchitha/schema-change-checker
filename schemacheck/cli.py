"""schemacheck --base base/manifest.json --head head/manifest.json

Exit codes: 0 no breaking changes, 1 breaking changes, 2 the checker failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from schemacheck.check import run_check
from schemacheck.manifest import CheckerError
from schemacheck.report import render_failure, render_json, render_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="schemacheck", description="Find dbt schema changes that break downstream models."
    )
    parser.add_argument("--base", required=True, help="manifest.json from the base branch")
    parser.add_argument("--head", required=True, help="manifest.json from the PR branch")
    parser.add_argument("--out", type=Path, help="write the markdown report here")
    parser.add_argument("--json", type=Path, help="write the JSON report here")
    parser.add_argument("--quiet", action="store_true", help="don't print the report")
    args = parser.parse_args(argv)

    try:
        result = run_check(args.base, args.head)
    except CheckerError as e:
        markdown, code = render_failure(str(e)), 2
        print(f"schemacheck: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - a crash still has to produce a report
        markdown, code = render_failure(f"unexpected {type(e).__name__}: {e}"), 2
        print(f"schemacheck crashed: {type(e).__name__}: {e}", file=sys.stderr)
    else:
        markdown, code = render_markdown(result), result.exit_code
        if args.json:
            args.json.write_text(render_json(result), encoding="utf-8")

    if args.out:
        args.out.write_text(markdown, encoding="utf-8")
    if not args.quiet:
        print(markdown)
    return code


if __name__ == "__main__":
    sys.exit(main())
