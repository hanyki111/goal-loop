# Delegated judging in this repository

Moved from `CLAUDE.md` unchanged on 2026-11-13 (goal-160), so the instruction files keep only the rules that
always apply. The rule itself (a separate agent judges; never re-run a judgment to get a different result) stays
in `AGENTS.md`. The `goal-loop` skill describes `goal-loop-judge`, the installed form of the same command.

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
- A project can set its own judging in `.goal-loop/judging.json` (goal-140): `copy_exclude` (patterns left out of the
  copy; one with `/` is matched from the project root, one without matches any name), `attachments_only` (the default
  for `goal`; `--attachments-only`/`--no-attachments-only` override it), and `rules` (`code`, or `notes` for work that is
  not code: the judge reads Evidence, attachments, references and the notes in the copy instead of rerunning tests).
  An unknown key or a wrong value stops the run before a copy is made; the result reports the settings it applied.
- A judge still running 60 seconds after its answer is complete is ended with its process tree, and processes whose
  command line names the judge's copy (servers it left) are ended after each run (goal-78). Codex's sandbox cannot list
  or end other Windows processes; those tests skip there with a reason, so a Goal that depends on them is judged with
  `--judge glm`.
- It records the answer unchanged: the report as Evidence under the agent's name (answer, prompt, and log attached),
  then `verdict-submit ... --reason` (superseding a previous `inconclusive` on the same Attempt), or `aim-evaluate`
  only when the Aim is judged achieved with acceptable citations. If you disagree with a judgment, record your view
  as Evidence and tell the user; do not change or re-run it to get a different result.
