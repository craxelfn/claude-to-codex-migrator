from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


MAX_TEXT_BYTES = 4 * 1024 * 1024
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("source-product-name", re.compile(r"claude", re.IGNORECASE)),
    ("source-vendor-name", re.compile(r"anthropic", re.IGNORECASE)),
    ("source-plugin-directory", re.compile(r"\.claude(?:-plugin)?", re.IGNORECASE)),
    ("source-environment-variable", re.compile(r"CLAUDE_[A-Z0-9_]+")),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_binary_bytes(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def read_text_if_safe(path: Path) -> tuple[bool, str | None]:
    if path.stat().st_size > MAX_TEXT_BYTES:
        return True, None
    data = path.read_bytes()
    if is_binary_bytes(data):
        return True, None
    return False, data.decode("utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def validate_relative_path(value: str) -> PurePosixPath:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if candidate.is_absolute() or not candidate.parts:
        raise ValueError(f"Path must be relative: {value}")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"Path contains unsafe traversal: {value}")
    return candidate


def _replacement_for_case(match: re.Match[str], replacement: str) -> str:
    value = match.group(0)
    if value.isupper():
        return replacement.upper()
    if value.islower():
        return replacement.lower()
    return replacement


def rewrite_source_terms(value: str) -> str:
    replacements: tuple[tuple[str, str], ...] = (
        (r"CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT"),
        (r"CLAUDE_PLUGIN_DATA", "PLUGIN_DATA"),
        (r"\.claude-plugin", ".codex-plugin"),
        (r"~[/\\]\.claude", "~/.codex"),
        (r"/reload-plugins\b", "restart Codex"),
        (r"\breload-plugins\b", "restart-codex"),
        (r"\bplugin-dir\b", "plugin-root"),
        (r"\bClaude Code\b", "Codex"),
        (r"\bAnthropic\b", "provider"),
        (r"\bClaude\b", "Codex"),
    )
    result = value
    for pattern, replacement in replacements:
        result = re.sub(
            pattern,
            lambda match, word=replacement: _replacement_for_case(match, word),
            result,
            flags=re.IGNORECASE,
        )
    return result


def clean_path_part(value: str) -> str:
    cleaned = rewrite_source_terms(value)
    cleaned = cleaned.replace(" ", "-")
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned


def normalize_name(value: str, fallback: str = "migrated-workflow") -> str:
    cleaned = rewrite_source_terms(value).strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if not cleaned:
        cleaned = fallback
    return cleaned[:64].rstrip("-") or fallback


def humanize(value: str) -> str:
    return " ".join(part.capitalize() for part in normalize_name(value).split("-"))


def clean_description(value: str | None, name: str, *, trigger: bool = False) -> str:
    cleaned = rewrite_source_terms(value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip("'\"")
    if not cleaned:
        cleaned = f"Run the {humanize(name)} workflow."
    if trigger and "use when" not in cleaned.lower():
        cleaned = (
            cleaned.rstrip(".") + ". Use when this workflow matches the user's request."
        )
    return cleaned[:500]


def scan_text_forbidden(value: str) -> list[str]:
    labels: list[str] = []
    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(value):
            labels.append(label)
    return sorted(set(labels))


def split_frontmatter(value: str) -> tuple[dict[str, Any], str]:
    normalized = value.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized.strip()
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}, normalized.strip()
    header = normalized[4:end]
    body = normalized[end + 5 :].strip()
    result: dict[str, Any] = {}
    lines = header.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not match:
            index += 1
            continue
        key, raw = match.groups()
        raw = raw.strip()
        if raw in {"|", ">"}:
            index += 1
            buffer: list[str] = []
            while index < len(lines) and (
                lines[index].startswith("  ") or not lines[index].strip()
            ):
                buffer.append(lines[index][2:] if lines[index].startswith("  ") else "")
                index += 1
            result[key] = "\n".join(buffer).strip()
            continue
        if (
            not raw
            and index + 1 < len(lines)
            and re.match(r"^\s+-\s+", lines[index + 1])
        ):
            index += 1
            values: list[str] = []
            while index < len(lines):
                item = re.match(r"^\s+-\s+(.*)$", lines[index])
                if not item:
                    break
                values.append(item.group(1).strip().strip("'\""))
                index += 1
            result[key] = values
            continue
        if raw.startswith("[") and raw.endswith("]"):
            result[key] = [
                item.strip().strip("'\"")
                for item in raw[1:-1].split(",")
                if item.strip()
            ]
        elif raw.lower() in {"true", "false"}:
            result[key] = raw.lower() == "true"
        else:
            result[key] = raw.strip("'\"")
        index += 1
    return result, body


def render_skill(name: str, description: str, body: str) -> str:
    safe_description = clean_description(description, name, trigger=True).replace(
        "\n", " "
    )
    return (
        "---\n"
        f"name: {normalize_name(name)}\n"
        f"description: {safe_description}\n"
        "---\n\n"
        f"{rewrite_source_terms(body).strip()}\n"
    )


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_openai_yaml(name: str, description: str) -> str:
    display = humanize(name)
    short = re.sub(r"\s+", " ", rewrite_source_terms(description)).strip()
    if len(short) < 25:
        short = f"Run the {display} migration workflow"
    short = short[:64].rstrip()
    prompt = f"Use ${normalize_name(name)} to run this workflow."
    return (
        "interface:\n"
        f"  display_name: {yaml_quote(display)}\n"
        f"  short_description: {yaml_quote(short)}\n"
        f"  default_prompt: {yaml_quote(prompt)}\n"
    )
