# Shared Project Memory

This project can use `memory_tool` (github.com/hanyki111/memory_tool) as the shared persistent memory for
Claude Code and Codex. The knowledge folder is local and never published; skip the memory steps when it is absent.

This file holds the instructions for both agents; Claude Code reads it through `@AGENTS.md` in `CLAUDE.md`.
Personal operating notes for this checkout (the current Aim and Goal state, local folder layout, judge
connections) are not published: Claude Code loads `CLAUDE.local.md` automatically, and Codex reads
`AGENTS.local.md` after this file when it exists.

## Session start

1. Run `./.venv/Scripts/python.exe -m memory_tool context` from the project root.
2. Read `.claude/memory-context.md` for the current timeline, modules, and plans.
3. Read `.claude/guidelines.md` when deciding whether to record or search memory.
4. Run `.\.venv\Scripts\goal-loop.exe where` (expect `source: project`), then `next` and `status`, and summarize
   open Aims, running Attempts, and pending verifications.

## Commands

Use the project virtual environment; do not rely on global aliases.
This includes Goal Loop itself: where the `goal-loop` skill writes `goal-loop` or `goal-loop-judge`, run
`.\.venv\Scripts\goal-loop.exe` or `.\.venv\Scripts\goal-loop-judge.exe` in this repository, so commands use the
working tree instead of an installed copy on PATH. Where `.claude/guidelines.md` writes `m`, `ms` and the like, run
`.\.venv\Scripts\python.exe -m memory_tool record|search|context|module tree|status`.

## Memory

- Timeline entries must be created through `memory_tool record`; do not edit timeline files directly.
- The root module is `.memory/modules/goal-loop-tool/goal-loop-tool.md`; keep it current after meaningful work.
- The memory timeline records milestones and decisions and cites Goal Loop IDs instead of repeating them.
  Cite IDs as full tokens or `·` lists (`verdict-3·5`), not `goal-60(132)`.

## Development tracking with Goal Loop

This repository's own development is tracked with Goal Loop (dogfooding). The journal lives in
`.goal-loop/` at the project root and is excluded from version control by its own `.gitignore`.
Follow the `goal-loop` and `goal-loop-planning` skills (`.claude/skills/` for Claude Code, `.agents/skills/` for Codex).

- `user`: the human. Review resolutions, closures, pause/resume, and Holds are recorded by `user`, normally
  from the desktop app. The user may also submit or supersede Verdicts and Aim evaluations.
- `claude` / `codex`: the agent. Use it as `--actor` for Proposals and references and as the executor and
  observer of Attempts and Evidence. Never record anything under `user` yourself.
- Delegated judging (Decision 6, 2026-10-04): Verdicts and Aim evaluations are recorded from the judgment of a
  separate agent that did not execute the work, normally `codex` when `claude` executed (and vice versa; Codex's own
  work is judged with `--judge claude` or `--judge glm`). After `verification-begin`, run
  `.\.venv\Scripts\python.exe -m goal_loop_tool.delegated_judging [--judge codex|claude|glm] goal <goal-id>`
  (or `aim <aim-id>`); options and how it works are in `docs/DELEGATED-JUDGING.md`.
  If you disagree with a judgment, record your view as Evidence and tell the user; do not change or re-run it
  to get a different result.

Desktop app: `cd ui; npm run tauri dev` (the user starts it). How an agent inspects its window is in
`docs/DESKTOP-APP-INSPECTION.md`.
