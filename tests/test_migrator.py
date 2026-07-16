from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "migrate-to-codex"
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from migrate_to_codex import (  # noqa: E402
    MigrationError,
    MigrationOptions,
    discover_installed,
    migrate,
    resolve_installed,
    scan_leftovers,
    validate_package,
)


FIXTURES = Path(__file__).parent / "fixtures"


class MigratorTests(unittest.TestCase):
    def test_repository_is_a_skills_only_plugin(self) -> None:
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["name"], ROOT.name)
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["author"]["name"], "Oussama Lakrafi")
        self.assertEqual(
            manifest["author"]["email"], "oussama.lakrafi@oracle.com"
        )
        self.assertNotIn("apps", manifest)
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("hooks", manifest)
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())
        self.assertTrue((SKILL_ROOT / "agents" / "openai.yaml").is_file())

        marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marketplace["name"], "oussama-lakrafi")
        self.assertEqual(len(marketplace["plugins"]), 1)
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], manifest["name"])
        self.assertEqual(entry["source"]["source"], "url")
        self.assertEqual(
            entry["source"]["url"],
            "https://github.com/craxelfn/migrate-to-codex.git",
        )
        self.assertEqual(entry["policy"]["installation"], "AVAILABLE")
        self.assertEqual(entry["policy"]["authentication"], "ON_INSTALL")

    def test_instruction_source_builds_clean_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            result = migrate(
                FIXTURES / "instruction-source",
                output,
                MigrationOptions(strict=True),
            )

            self.assertEqual(result.plan.decision.target, "skill")
            self.assertTrue((result.package_root / "SKILL.md").is_file())
            self.assertTrue((result.package_root / "agents" / "openai.yaml").is_file())
            self.assertTrue(
                (result.package_root / "references" / "release.md").is_file()
            )
            self.assertTrue(
                (result.package_root / "references" / "release-checklist.md").is_file()
            )
            self.assertTrue(
                (result.package_root / "references" / "risk-reviewer.md").is_file()
            )
            self.assertTrue(result.validation.ok)
            self.assertEqual(scan_leftovers(result.package_root), [])
            risk = (result.package_root / "references" / "risk-reviewer.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("release risk reviewer", risk)
            self.assertIn("missing tests", risk)
            self.assertNotIn("Claude", risk)
            release = (result.package_root / "references" / "release.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("](release-checklist.md)", release)
            plan = json.loads(
                (result.reports_root / "migration-plan.json").read_text(
                    encoding="utf-8"
                )
            )
            source_file_count = sum(
                1
                for path in (FIXTURES / "instruction-source").rglob("*")
                if path.is_file()
            )
            self.assertEqual(len(plan["items"]), source_file_count)
            self.assertTrue(all(item["operation"] for item in plan["items"]))

    def test_runtime_source_builds_valid_clean_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            result = migrate(
                FIXTURES / "integration-source",
                output,
                MigrationOptions(strict=True),
            )

            self.assertEqual(result.plan.decision.target, "plugin")
            self.assertTrue(
                (result.package_root / ".codex-plugin" / "plugin.json").is_file()
            )
            self.assertTrue(
                (result.package_root / "skills" / "sync" / "SKILL.md").is_file()
            )
            self.assertTrue((result.package_root / "scripts" / "server.js").is_file())
            self.assertTrue((result.package_root / "hooks" / "hooks.json").is_file())
            plugin_reference = (
                result.package_root / "skills" / "sync" / "references" / "operations.md"
            )
            self.assertTrue(plugin_reference.is_file())
            sync_skill = (
                result.package_root / "skills" / "sync" / "SKILL.md"
            ).read_text(encoding="utf-8")
            self.assertIn("](references/operations.md)", sync_skill)
            hook_text = (result.package_root / "hooks" / "hooks.json").read_text(
                encoding="utf-8"
            )
            self.assertIn("PLUGIN_ROOT", hook_text)
            self.assertNotIn("CLAUDE_PLUGIN_ROOT", hook_text)
            self.assertTrue(result.validation.ok)
            self.assertEqual(scan_leftovers(result.package_root), [])
            validated = validate_package(result.package_root, "plugin")
            self.assertTrue(validated.ok, validated.errors)

    def test_zip_input_is_safely_staged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            archive_path = temporary_path / "source.zip"
            source = FIXTURES / "instruction-source"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for path in sorted(
                    candidate for candidate in source.rglob("*") if candidate.is_file()
                ):
                    archive.write(path, Path("wrapped") / path.relative_to(source))
            result = migrate(
                archive_path, temporary_path / "result", MigrationOptions(strict=True)
            )
            self.assertEqual(result.plan.source_kind, "zip")
            self.assertTrue(result.validation.ok)

    def test_zip_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.md", "unsafe")
            with self.assertRaises(ValueError):
                migrate(archive_path, Path(temporary) / "result")

    def test_stdin_json_bundle_builds_skill(self) -> None:
        bundle = json.dumps(
            {
                "files": {
                    "SKILL.md": (
                        "---\n"
                        "name: claude-helper\n"
                        "description: Help with source workflows. Use when Claude is requested.\n"
                        "---\n\n"
                        "Ask Claude Code to inspect the current repository.\n"
                    ),
                    "references/details.md": "# Details\n\nPreserve the Claude workflow intent.\n",
                }
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = migrate(
                "-",
                Path(temporary) / "result",
                MigrationOptions(name="bundle-helper", strict=True, stdin_text=bundle),
            )
            self.assertEqual(result.plan.source_kind, "stdin")
            self.assertEqual(result.package_root.name, "bundle-helper")
            self.assertTrue(result.validation.ok)
            self.assertEqual(scan_leftovers(result.package_root), [])

    def test_strict_mode_preserves_reports_when_manual_work_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            (source / ".claude-plugin").mkdir(parents=True)
            (source / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "manual-case", "version": "1.0.0"}),
                encoding="utf-8",
            )
            (source / "settings.json").write_text(
                '{"agent":"reviewer"}', encoding="utf-8"
            )
            output = Path(temporary) / "result"
            with self.assertRaises(MigrationError) as captured:
                migrate(source, output, MigrationOptions(strict=True))
            self.assertIsNotNone(captured.exception.result)
            self.assertTrue(
                (output / "reports" / "unresolved" / "settings.json").is_file()
            )
            self.assertTrue((output / "reports" / "migration-report.md").is_file())

    def test_missing_mcp_dependency_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            shutil.copytree(FIXTURES / "integration-source", source)
            (source / ".mcp.json").write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "broken": {
                                "command": "node",
                                "args": ["scripts/missing.js"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(MigrationError) as captured:
                migrate(
                    source, Path(temporary) / "result", MigrationOptions(strict=True)
                )
            self.assertIsNotNone(captured.exception.result)
            errors = captured.exception.result.validation.errors  # type: ignore[union-attr]
            self.assertTrue(
                any("missing local dependency" in error for error in errors)
            )

    def test_existing_output_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                migrate(FIXTURES / "instruction-source", output)
            result = migrate(
                FIXTURES / "instruction-source",
                output,
                MigrationOptions(force=True, strict=True),
            )
            self.assertTrue(result.validation.ok)

    def test_installed_source_discovery_and_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "cache" / "demo" / "1.0.0"
            (install / ".claude-plugin").mkdir(parents=True)
            (install / ".claude-plugin" / "plugin.json").write_text(
                "{}", encoding="utf-8"
            )
            (root / "installed_plugins.json").write_text(
                json.dumps(
                    {
                        "plugins": {
                            "demo@local": [
                                {
                                    "installPath": str(install),
                                    "version": "1.0.0",
                                    "scope": "user",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            discovered = discover_installed(root)
            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["kind"], "plugin")
            self.assertEqual(resolve_installed("demo@local", root), install.resolve())


if __name__ == "__main__":
    unittest.main()
