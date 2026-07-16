# Claude to Codex Migrator

`claude-to-codex-migrator` is a skills-only Codex plugin that converts Claude skills, plugins, commands, agents, and integration bundles into validated Codex Skills or plugins.

It inventories every source file, chooses the simplest compatible Codex architecture, performs deterministic transformations, scans the generated package for source-platform leftovers, and produces decision, migration, cleanup, and validation reports.

Published by [Oussama Lakrafi](https://github.com/craxelfn).

## Requirements

- A current Codex CLI, IDE extension, or Codex desktop app with plugin support
- Git
- Python 3.10 or newer available as `python3`

The plugin has no MCP server, connector, authentication flow, or external runtime service. Migration runs locally against paths you explicitly provide.

## Install from GitHub

Add this repository as a Codex marketplace source:

```bash
codex plugin marketplace add craxelfn/claude-to-codex-migrator --ref main
```

Install the plugin from that marketplace:

```bash
codex plugin add claude-to-codex-migrator@oussama-lakrafi
```

Start a new Codex session after installation so the bundled Skill is discovered.

You can also open Codex and enter `/plugins`, select the **Oussama Lakrafi** marketplace, then install **Claude to Codex Migrator** from the plugin browser.

## Use the plugin

Describe the migration naturally or invoke the Skill explicitly with `$claude-to-codex-migrator`.

Examples:

```text
$claude-to-codex-migrator migrate ./legacy-plugin into a clean Codex package.

$claude-to-codex-migrator convert ./assistant-package.zip and put the result in ./migration-output.

$claude-to-codex-migrator inspect this repository, decide Skill versus plugin, migrate it, and report unresolved items.

$claude-to-codex-migrator audit ./generated-package for Claude-specific filenames, metadata, and instructions.
```

The migrator accepts:

- A local folder
- A ZIP archive
- A single file
- Pasted content
- A JSON multi-file bundle through standard input
- A locally installed source package
- A repository tree or implementation document supplied in the conversation

By default, instruction-driven sources become Codex Skills. Sources that require MCP, apps, authenticated tools, hooks, or runtime integration become Codex plugins.

## Generated output

A migration produces two separate trees:

```text
migration-output/
├── package/
│   └── <generated-skill-or-plugin>/
└── reports/
    ├── decision.md
    ├── source-inventory.json
    ├── migration-plan.json
    ├── migration-report.md
    ├── cleanup-report.json
    ├── validation-report.md
    └── unresolved/
```

Strict mode exits unsuccessfully when manual work or validation failures remain, while preserving the reports and unresolved source snapshots for review.

## Run the migration engine directly

When working from a clone of this repository, you can run the deterministic engine without installing the plugin:

```bash
python3 skills/claude-to-codex-migrator/scripts/migrate.py ./source-package \
  --out ./migration-output \
  --strict
```

Inventory a package without generating output:

```bash
python3 skills/claude-to-codex-migrator/scripts/inventory_source.py ./source-package
```

Validate or scan a generated package:

```bash
python3 skills/claude-to-codex-migrator/scripts/validate_output.py \
  ./migration-output/package/<name> \
  --target auto

python3 skills/claude-to-codex-migrator/scripts/scan_leftovers.py \
  ./migration-output/package/<name>
```

## Plugin layout

```text
.codex-plugin/plugin.json
.agents/plugins/marketplace.json
skills/claude-to-codex-migrator/
├── SKILL.md
├── agents/openai.yaml
├── references/
└── scripts/
```

## Development

Run the test suite from the repository root:

```bash
python3 -m unittest -v
```

Run lint checks:

```bash
ruff check skills/claude-to-codex-migrator/scripts tests
```

Before releasing a new version, update the semantic version in `.codex-plugin/plugin.json`, rerun the Skill and plugin validators, and test installation from the GitHub marketplace source.

## Safety

- ZIP traversal, absolute archive paths, encrypted entries, and symlinks are rejected.
- Existing output is not replaced unless `--force` is explicit.
- Migrated hooks and MCP executables are treated as untrusted and are never enabled automatically.
- Unknown components are reported and preserved outside the distributable package instead of being silently discarded.
- The migrator does not publish, install, or enable a generated package unless separately requested.

## Public directory submission

This repository is ready for Git-backed marketplace distribution. A later submission to the public plugin directory will additionally require verified publisher details, production visual assets, support and legal URLs, submission test cases, and OpenAI review.
