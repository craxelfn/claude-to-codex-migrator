from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .models import MigrationResult
from .planner import build_plan
from .reporting import write_reports
from .source import inventory_source, stage_source
from .transform import build_package
from .validation import validate_package


@dataclass(slots=True)
class MigrationOptions:
    target: str = "auto"
    name: str | None = None
    strict: bool = False
    force: bool = False
    stdin_text: str | None = None
    stdin_name: str = "SOURCE.md"


class MigrationError(RuntimeError):
    def __init__(self, message: str, result: MigrationResult | None = None):
        super().__init__(message)
        self.result = result


def _validate_output_path(output: Path) -> None:
    resolved = output.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("Refusing to use a filesystem root as migration output")


def migrate(
    source: str | Path, output: str | Path, options: MigrationOptions | None = None
) -> MigrationResult:
    selected = options or MigrationOptions()
    if selected.target not in {"auto", "skill", "plugin"}:
        raise ValueError("target must be auto, skill, or plugin")
    output_root = Path(output).expanduser().resolve()
    _validate_output_path(output_root)
    output_root.parent.mkdir(parents=True, exist_ok=True)
    if output_root.exists() and not selected.force:
        raise FileExistsError(
            f"Output already exists: {output_root}. Use force=True to replace it."
        )

    workspace = Path(
        tempfile.mkdtemp(
            prefix="claude-to-codex-migrator-", dir=output_root.parent
        )
    )
    try:
        staged_root, source_kind = stage_source(
            source,
            workspace / "staging",
            stdin_text=selected.stdin_text,
            stdin_name=selected.stdin_name,
        )
        inventory = inventory_source(staged_root, source_kind)
        plan = build_plan(
            inventory, requested_target=selected.target, requested_name=selected.name
        )
        result_root = workspace / "result"
        package_root = result_root / "package" / plan.target_name
        reports_root = result_root / "reports"
        build_package(inventory, plan, package_root, reports_root)
        validation = validate_package(package_root, plan.decision.target, plan)
        strict_failed = selected.strict and (
            bool(plan.manual_items) or not validation.ok
        )
        write_reports(reports_root, inventory, plan, validation)

        if output_root.exists():
            if output_root.is_dir():
                shutil.rmtree(output_root)
            else:
                output_root.unlink()
        os.replace(result_root, output_root)
        final_package = output_root / "package" / plan.target_name
        final_reports = output_root / "reports"
        result = MigrationResult(
            output_root=output_root,
            package_root=final_package,
            reports_root=final_reports,
            plan=plan,
            validation=validation,
            strict_failed=strict_failed,
        )
        if strict_failed:
            raise MigrationError(
                "Strict migration failed because manual items or validation failures remain. Reports were preserved.",
                result,
            )
        return result
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
