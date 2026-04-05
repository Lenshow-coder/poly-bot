---
name: update-docs
description: Update CLAUDE.md and notes/plan.md to reflect the current state of the codebase
user_invocable: true
---

You are updating the project's two documentation files to match the current state of the codebase. You must ONLY edit `CLAUDE.md` and `notes/plan.md` — do not modify any source code, config files, or other files.

## Steps

1. **Read all source files** to understand current project state:
   - `main.py`, `config.yaml`, `pyproject.toml`
   - All files in `core/`, `scrapers/`, `markets/`, `tests/`
   - All YAML configs in `markets/configs/`
   - `notes/plan.md` (the full file)
   - `CLAUDE.md`

2. **Read recent git history** for context on what changed:
   - Run `git log --oneline -20` to see recent commits
   - Run `git diff HEAD~5 --stat` to see which files changed recently

3. **Update `CLAUDE.md`** to reflect reality. This file is Claude Code's primary context for working on the project. Keep it concise (aim for under 50 lines).

   **CLAUDE.md INCLUDE — only these categories belong:**
   - Bash commands Claude can't guess (how to run, test, build)
   - Code style rules that differ from defaults (e.g., the config rule, `from_config()` pattern)
   - Testing instructions and preferred test runners
   - Architectural decisions specific to this project (plugin system, order types, odds format)
   - Developer environment quirks (required env vars, `.venv` path, `uv` not pip, USDC decimals)
   - Common gotchas or non-obvious behaviors (Polymarket API traps)

   **CLAUDE.md EXCLUDE — never add these:**
   - Anything Claude can figure out by reading code (file-by-file descriptions, project structure trees, import patterns)
   - Standard Python conventions Claude already knows
   - Detailed API documentation (reference py-clob-client or Polymarket docs instead)
   - Information that changes frequently (implementation status — that belongs in plan.md)
   - Long explanations or tutorials
   - Self-evident practices like "write clean code"

4. **Update `notes/plan.md`** to reflect reality. This is the detailed design document. Update:
   - Implementation phase statuses (what's done, what's next)
   - Project structure section if files were added/removed/renamed
   - Any design sections that no longer match the code
   - Keep future-phase plans intact unless the code clearly supersedes them

5. **Show a brief summary** of what you changed in each file and why.

## Rules

- Only edit `CLAUDE.md` and `notes/plan.md`. Nothing else.
- Don't invent features that don't exist in the code. If a file or function doesn't exist, don't document it.
- Don't remove API gotchas or conventions unless you've confirmed they're no longer relevant.
- Keep `CLAUDE.md` concise. Keep `notes/plan.md` detailed.
- Preserve the existing tone and structure of each file — update content, don't restructure.
