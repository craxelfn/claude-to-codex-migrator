from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .common import (
    FORBIDDEN_PATTERNS,
    NAME_RE,
    SEMVER_RE,
    read_text_if_safe,
    split_frontmatter,
)
from .models import MigrationPlan, TargetKind, ValidationResult


MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PLUGIN_ROOT_PATH_RE = re.compile(
    r"\$(?:\{PLUGIN_ROOT\}|PLUGIN_ROOT)/([A-Za-z0-9_./-]+)"
)


def scan_leftovers(package_root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in sorted(
        candidate for candidate in package_root.rglob("*") if candidate.is_file()
    ):
        relative = path.relative_to(package_root).as_posix()
        for label, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(relative):
                findings.append(
                    {"path": relative, "kind": "filename", "pattern": label}
                )
        binary, text = read_text_if_safe(path)
        if binary or text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in FORBIDDEN_PATTERNS:
                match = pattern.search(line)
                if match:
                    findings.append(
                        {
                            "path": relative,
                            "line": line_number,
                            "kind": "content",
                            "pattern": label,
                            "match": match.group(0),
                        }
                    )
    return findings


def _validate_markdown_links(skill_root: Path, errors: list[str]) -> None:
    for path in sorted(skill_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for raw in MARKDOWN_LINK_RE.findall(text):
            target = raw.strip().strip("<>").split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / target).resolve()
            root = skill_root.resolve()
            if resolved != root and root not in resolved.parents:
                errors.append(
                    f"Markdown link escapes Skill root in {path.relative_to(skill_root)}: {raw}"
                )
            elif not resolved.exists():
                errors.append(
                    f"Broken Markdown link in {path.relative_to(skill_root)}: {raw}"
                )


def _validate_skill(skill_root: Path, errors: list[str], warnings: list[str]) -> None:
    skill_file = skill_root / "SKILL.md"
    if not skill_file.is_file():
        errors.append(f"Missing SKILL.md in {skill_root}")
        return
    metadata, body = split_frontmatter(skill_file.read_text(encoding="utf-8"))
    unknown = sorted(set(metadata) - {"name", "description"})
    if unknown:
        errors.append(
            f"SKILL.md contains unsupported frontmatter fields in {skill_root.name}: {', '.join(unknown)}"
        )
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        errors.append(f"Skill name must be lowercase hyphen-case in {skill_file}")
    elif name != skill_root.name:
        errors.append(
            f"Skill folder {skill_root.name} must match frontmatter name {name}"
        )
    if not isinstance(description, str) or not description.strip():
        errors.append(f"Skill description must be non-empty in {skill_file}")
    if not body.strip():
        errors.append(f"Skill body must be non-empty in {skill_file}")
    openai_yaml = skill_root / "agents" / "openai.yaml"
    if not openai_yaml.is_file():
        warnings.append(f"Recommended agents/openai.yaml is missing in {skill_root}")
    elif isinstance(name, str) and f"${name}" not in openai_yaml.read_text(
        encoding="utf-8"
    ):
        errors.append(
            f"agents/openai.yaml default_prompt must mention ${name} in {skill_root}"
        )
    _validate_markdown_links(skill_root, errors)


def _validate_mcp(plugin_root: Path, errors: list[str]) -> None:
    mcp_path = plugin_root / ".mcp.json"
    if not mcp_path.exists():
        return
    try:
        payload = json.loads(mcp_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        errors.append(f"Invalid .mcp.json: {error}")
        return
    if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), dict):
        errors.append(".mcp.json must contain an mcpServers object")
        return
    known_suffixes = {".js", ".mjs", ".cjs", ".py", ".sh", ".ts", ".rb", ".php", ".jar"}
    for server_name, config in payload["mcpServers"].items():
        if not isinstance(config, dict):
            errors.append(f"MCP server {server_name} must be an object")
            continue
        command = config.get("command")
        if isinstance(command, str) and command.startswith(("./", "../")):
            candidate = (plugin_root / command).resolve()
            if plugin_root.resolve() not in candidate.parents or not candidate.exists():
                errors.append(
                    f"MCP server {server_name} command does not resolve inside the package: {command}"
                )
        args = config.get("args", [])
        if isinstance(args, list):
            for argument in args:
                if (
                    not isinstance(argument, str)
                    or argument.startswith("-")
                    or "://" in argument
                ):
                    continue
                candidate_path = PurePosixPath(argument)
                if candidate_path.suffix.lower() in known_suffixes or "/" in argument:
                    candidate = (plugin_root / Path(*candidate_path.parts)).resolve()
                    if (
                        plugin_root.resolve() not in candidate.parents
                        or not candidate.exists()
                    ):
                        errors.append(
                            f"MCP server {server_name} references a missing local dependency: {argument}"
                        )


def _validate_plugin(plugin_root: Path, errors: list[str], warnings: list[str]) -> None:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    if not manifest_path.is_file():
        errors.append("Missing .codex-plugin/plugin.json")
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        errors.append(f"Invalid plugin.json: {error}")
        return
    if not isinstance(manifest, dict):
        errors.append("plugin.json must contain an object")
        return
    for field in ("name", "version", "description"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            errors.append(f"plugin.json field {field} must be a non-empty string")
    name = manifest.get("name")
    if isinstance(name, str):
        if not NAME_RE.fullmatch(name):
            errors.append("plugin.json name must be lowercase hyphen-case")
        if name != plugin_root.name:
            errors.append(
                f"Plugin folder {plugin_root.name} must match manifest name {name}"
            )
    version = manifest.get("version")
    if isinstance(version, str) and not SEMVER_RE.fullmatch(version):
        errors.append("plugin.json version must be strict semver")
    author = manifest.get("author")
    if (
        not isinstance(author, dict)
        or not isinstance(author.get("name"), str)
        or not author["name"].strip()
    ):
        errors.append("plugin.json author.name must be a non-empty string")
    interface = manifest.get("interface")
    required_interface = {
        "displayName": str,
        "shortDescription": str,
        "longDescription": str,
        "developerName": str,
        "category": str,
        "capabilities": list,
        "defaultPrompt": list,
    }
    if not isinstance(interface, dict):
        errors.append("plugin.json interface must be an object")
    else:
        for field, expected in required_interface.items():
            value = interface.get(field)
            if not isinstance(value, expected) or (
                hasattr(value, "__len__") and len(value) == 0
            ):
                errors.append(
                    f"plugin.json interface.{field} has an invalid or empty value"
                )
    for field in ("skills", "mcpServers", "apps"):
        value = manifest.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.startswith("./"):
            errors.append(f"plugin.json field {field} must be a ./-prefixed path")
            continue
        if not (plugin_root / value[2:]).exists():
            errors.append(
                f"plugin.json field {field} points to a missing path: {value}"
            )
    skills_root = plugin_root / "skills"
    if skills_root.exists():
        for child in sorted(path for path in skills_root.iterdir() if path.is_dir()):
            _validate_skill(child, errors, warnings)
    hooks = plugin_root / "hooks" / "hooks.json"
    if hooks.exists():
        try:
            payload = json.loads(hooks.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(
                payload.get("hooks"), dict
            ):
                errors.append("hooks/hooks.json must contain a hooks object")
            else:

                def inspect_hook_value(value: Any) -> None:
                    if isinstance(value, dict):
                        for key, nested in value.items():
                            if key == "command" and isinstance(nested, str):
                                for match in PLUGIN_ROOT_PATH_RE.finditer(nested):
                                    dependency = (
                                        plugin_root / match.group(1)
                                    ).resolve()
                                    if (
                                        plugin_root.resolve() not in dependency.parents
                                        or not dependency.exists()
                                    ):
                                        errors.append(
                                            f"Hook command references a missing package dependency: {match.group(1)}"
                                        )
                                    elif not os.access(dependency, os.X_OK):
                                        errors.append(
                                            f"Hook command dependency is not executable: {match.group(1)}"
                                        )
                            inspect_hook_value(nested)
                    elif isinstance(value, list):
                        for nested in value:
                            inspect_hook_value(nested)

                inspect_hook_value(payload)
        except json.JSONDecodeError as error:
            errors.append(f"Invalid hooks/hooks.json: {error}")
    _validate_mcp(plugin_root, errors)


def validate_package(
    package_root: Path, target: TargetKind, plan: MigrationPlan | None = None
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if target == "skill":
        _validate_skill(package_root, errors, warnings)
    else:
        _validate_plugin(package_root, errors, warnings)
    if plan is not None:
        for item in plan.items:
            if item.operation in {
                "keep-as-is",
                "rename",
                "rewrite",
                "split-into-reference",
            }:
                if not item.target_path:
                    errors.append(f"Plan item lacks a target path: {item.source_path}")
                elif not (
                    package_root / Path(*PurePosixPath(item.target_path).parts)
                ).exists():
                    errors.append(
                        f"Planned target was not generated: {item.target_path} from {item.source_path}"
                    )
    cleanup = scan_leftovers(package_root)
    return ValidationResult(
        target=target,
        errors=sorted(set(errors)),
        warnings=sorted(set(warnings)),
        cleanup_findings=cleanup,
    )
