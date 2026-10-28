"""Wire the steps together: load, schema, lineage, diff, impact."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from schemacheck.diff import Change, Severity, diff_schemas
from schemacheck.impact import assess, find_broken_refs
from schemacheck.lineage import build_column_graph
from schemacheck.manifest import Project, load_manifest
from schemacheck.schema import build_schema


@dataclass
class CheckResult:
    changes: list[Change]
    notes: list[str] = field(default_factory=list)
    base: Project | None = field(default=None, repr=False)
    head: Project | None = field(default=None, repr=False)

    def by_severity(self, severity: Severity) -> list[Change]:
        return [c for c in self.changes if c.severity is severity]

    @property
    def exit_code(self) -> int:
        return 1 if self.by_severity(Severity.BREAKING) else 0


def run_check(base_manifest: str | Path, head_manifest: str | Path) -> CheckResult:
    base = build_schema(load_manifest(base_manifest))
    head = build_schema(load_manifest(head_manifest))
    base_graph = build_column_graph(base)
    head_graph = build_column_graph(head)

    changes, notes = diff_schemas(base, head, base_graph, head_graph)
    broken = find_broken_refs(head, base)
    assess(changes, broken, base, head, base_graph)

    # Only surface notes about the head branch; base problems aren't this PR's fault
    notes += sorted(set(head.notes.values()))
    return CheckResult(changes=changes, notes=notes, base=base.project, head=head.project)
