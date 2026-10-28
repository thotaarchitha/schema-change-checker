"""Turn a CheckResult into the PR comment and the JSON report."""

from __future__ import annotations

import json
from dataclasses import asdict

from schemacheck.check import CheckResult
from schemacheck.diff import Change, Kind, Severity

# The workflow finds its previous comment by this marker and edits it in place
COMMENT_MARKER = "<!-- schema-change-checker -->"
OVERRIDE_LABEL = "breaking-change-approved"


def _headline(ch: Change, name) -> str:
    model = f"**{name(ch.model)}**"
    match ch.kind:
        case Kind.COLUMN_REMOVED:
            return f"{model}: column `{ch.column}` was REMOVED"
        case Kind.COLUMN_RENAMED:
            return f"{model}: column `{ch.column}` was RENAMED to `{ch.new_column}`"
        case Kind.COLUMN_ADDED:
            return f"{model}: column `{ch.column}` was ADDED"
        case Kind.MODEL_REMOVED:
            return f"{model}: model was REMOVED"
        case Kind.MODEL_ADDED:
            return f"{model}: new model"
        case Kind.BROKEN_REFERENCE:
            return f"{model}: broken reference to `{ch.column}`"


def _block(ch: Change, name) -> list[str]:
    lines = [_headline(ch, name)]
    if ch.breaks:
        lines.append("- Breaks:")
        for b in ch.breaks:
            target = f"{b.model}.{b.column}" if b.column else b.model
            lines.append(f"  - `{target}` ({b.via})")
    elif ch.reason:
        lines.append(f"- {ch.reason[0].upper()}{ch.reason[1:]}")
    if ch.exposures:
        lines.append(f"- Dashboards affected: {', '.join(ch.exposures)} (exposure)")
    return lines


def render_markdown(result: CheckResult) -> str:
    name = _namer(result)
    breaking = result.by_severity(Severity.BREAKING)
    warnings = result.by_severity(Severity.WARNING)
    info = result.by_severity(Severity.INFO)

    if breaking:
        n = len(breaking)
        title = f"{n} breaking change{'s' if n > 1 else ''}"
    elif warnings or info:
        title = "no breaking changes"
    else:
        title = "no schema changes"

    out = [COMMENT_MARKER, f"### Schema Change Checker: {title}", ""]
    for ch in breaking:
        out += _block(ch, name) + [""]
    if warnings:
        out.append("**Warnings**")
        out.append("")
        for ch in warnings:
            out += _block(ch, name) + [""]
    if info:
        out.append("**Safe changes**")
        out.append("")
        out += [f"- {_headline(ch, name).replace('**', '')} (safe)" for ch in info]
        out.append("")
    if result.notes:
        out.append("<details><summary>Things the checker couldn't see</summary>")
        out.append("")
        out += [f"- {n}" for n in result.notes]
        out += ["", "</details>", ""]

    if breaking:
        out.append(f"**Result: FAILED.** Add the label `{OVERRIDE_LABEL}` to merge anyway.")
    else:
        out.append("**Result: PASSED.**")
    return "\n".join(out) + "\n"


def render_failure(message: str) -> str:
    """What gets posted when the checker itself falls over. Silence would read as a pass."""
    return (
        f"{COMMENT_MARKER}\n### Schema Change Checker: couldn't run\n\n"
        f"```\n{message}\n```\n\n"
        "**Result: ERROR.** This is a problem with the check, not necessarily with your change, "
        "but the PR can't be verified until it's fixed.\n"
    )


def render_json(result: CheckResult) -> str:
    name = _namer(result)
    payload = {
        "exit_code": result.exit_code,
        "summary": {s.value: len(result.by_severity(s)) for s in Severity},
        "changes": [
            {
                **asdict(ch),
                "model": name(ch.model),
                "unique_id": ch.model,
                "cascade": [f"{name(m)}.{c}" for m, c in ch.cascade],
            }
            for ch in result.changes
        ],
        "notes": result.notes,
    }
    return json.dumps(payload, indent=2, default=lambda o: o.value) + "\n"


def _namer(result: CheckResult):
    def name(uid: str) -> str:
        for project in (result.head, result.base):
            if project and uid in project.nodes:
                return project.name(uid)
        return uid.split(".")[-1]

    return name
