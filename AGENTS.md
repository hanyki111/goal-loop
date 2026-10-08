# Shared Project Memory

This project can use `memory_tool` (github.com/hanyki111/memory_tool) as the shared persistent memory for
Claude Code and Codex. The knowledge folder is local and never published; skip the memory steps when it is absent.

Personal operating notes for this checkout (the current Aim and Goal state, local folder layout, judge
connections) live in `AGENTS.local.md` when it exists; read it after this file. It is not published.

## Session start

1. Run `./.venv/Scripts/python.exe -m memory_tool context` from the project root.
2. Read `.claude/memory-context.md` for the current timeline, modules, and plans.
3. Read `.claude/guidelines.md` when deciding whether to record or search memory.

## Commands

Use the project virtual environment; do not rely on global aliases.

```powershell
.\.venv\Scripts\python.exe -m memory_tool record "message"
.\.venv\Scripts\python.exe -m memory_tool search "query"
.\.venv\Scripts\python.exe -m memory_tool context
.\.venv\Scripts\python.exe -m memory_tool module tree
.\.venv\Scripts\python.exe -m memory_tool status
```

## Memory workflow

- Record the start and completion of substantial work, important decisions, verified fixes, and material changes of direction.
- Do not record routine questions, tentative ideas, or unverified work-in-progress.
- Search memory before answering questions about prior decisions or implementation history.
- After meaningful work, update the relevant module under `.memory/modules/` and rebuild context.
- Timeline entries must be created through `memory_tool record`; do not edit timeline files directly.

## Initial module

The root module is `.memory/modules/goal-loop-tool/goal-loop-tool.md`. Keep its purpose, current state, decisions, interfaces, and related files current as the implementation begins.

## Development tracking with Goal Loop

This repository's own development is tracked with Goal Loop (dogfooding). The journal lives in
`.goal-loop/` at the project root and is excluded from version control by its own `.gitignore`.

- At session start, after the memory context, run `.\.venv\Scripts\goal-loop.exe where` (expect
  `source: project`), then `next` and `status`, and summarize open Aims, running Attempts, and pending
  verifications.
- Follow the "Developing with Goal Loop" protocol in the `goal-loop` skill: the agent records through
  the CLI. Verdicts and Aim evaluations are delegated to a separate agent (Decision 6, below); the user
  makes closure decisions, pause/resume, and Holds in the desktop app and may override any judgment.
- Attach the output you relied on to Evidence with `--attach`, and link design notes or memory modules
  with `reference-add`. Put judgment criteria in the Contract, not in a linked document.
- The memory timeline records milestones and decisions and cites Goal Loop IDs instead of repeating them.

- Write "what counts as achieved" in the first Proposal of each Aim, its first line as a one-sentence
  summary, then close that Proposal with `proposal-close --kind criteria` so it stays the Aim's criteria
  instead of an open approach; write the stop condition in each Goal's criterion.

Names recorded in the journal:

- `user`: the human. Review resolutions, closures, pause/resume, and Holds are recorded by `user`, normally
  from the desktop app. The user may also submit or supersede Verdicts and Aim evaluations.
- `claude` / `codex`: the agent. Use it as `--actor` for Proposals and references and as the executor and
  observer of Attempts and Evidence. Never record anything under `user` yourself.

Delegated judging (Decision 6, 2026-10-04): Verdicts and Aim evaluations are recorded from the judgment of a
separate agent that did not execute the work, normally `codex` when `claude` executed (and vice versa).

- Use the command, after `verification-begin`:
  `.\.venv\Scripts\python.exe -m goal_loop_tool.delegated_judging goal <goal-id> [--attachments-only] [--note "..."]`
  and, for an Aim, `... delegated_judging aim <aim-id>`. Add `--no-record` to see the judgment without recording it.
- For a Goal that changed another repository (an AO lane), add `--tree NAME=PATH` before `goal` so the judge gets
  its own copy of that working tree (without `.git`, node_modules linked) and reruns the tests there, never in the original.
- Pick the judge with `--judge codex|claude|glm` before `goal`/`aim` (default `codex`). `claude` runs a fresh Claude CLI
  session (`claude -p --json-schema`) in the copy and is recorded as `claude-judge`; `glm` is the same CLI pointed at GLM
  and is recorded as `glm`. GLM's connection comes from `.goal-loop/judges.json` (for example
  `{"glm": {"env_from_ps1": "<path>/set-glm.ps1"}}`), is passed only to that process, and is
  scrubbed from the log. A judge named like the Attempt's executor is refused before it runs. Use `claude`/`glm` when Codex
  executed the work or Codex is over its usage limit.
- The command copies the project (without `.venv`, `node_modules`, build output, or the live journal; `ui/node_modules`
  is linked), backs up the journal into the copy, sets TEMP/TMP outside the copy and `GOAL_LOOP_CEILING_DIRECTORIES`
  to the copy, and runs `codex exec` there with the Contract (or Aim criteria), the Evidence, and the Decision 5 rules,
  its answer fixed by a JSON Schema. Windows sandboxes did not keep writes out of the project consistently, so the
  copy, not the sandbox, protects the project; the copy stays in the OS temp folder for audit.
- It records the answer unchanged: the report as Evidence under the agent's name (answer, prompt, and log attached),
  then `verdict-submit ... --reason` (superseding a previous `inconclusive` on the same Attempt), or `aim-evaluate`
  only when the Aim is judged achieved with acceptable citations. If you disagree with a judgment, record your view
  as Evidence and tell the user; do not change or re-run it to get a different result.

Desktop app: `cd ui; npm run tauri dev` (the user starts it). To let an agent inspect the window, start it
with `$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--remote-debugging-port=9222'` first, then use
`node ui/scripts/cdp.mjs shot|eval|click|clicktext|type|key` (screenshots go to the OS temp dir). The app reloads
the journal when its window regains focus.
