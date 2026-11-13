# Goal Loop 데스크톱 앱

README에 있던 데스크톱 앱 설명을 2026-11-13에 옮긴 문서입니다(goal-161). 설치 방법은 [README](../README.md#데스크톱-앱)에 있습니다.

## React / Tauri desktop preview

`ui/` contains the local-first React workspace shared by the future desktop, mobile, and
Obsidian adapters. It reads the same default SQLite database as `goal-loop`; it does not create
a second application model or bypass Core validation.

The desktop UI uses Korean display terms decided on 2026-10-03: **목표** (Aim), **접근안** (Proposal),
**과제** (Goal), **완료 기준** (Contract), **실행** (Attempt), **관찰 근거** (Evidence), **검증 판단**
(Verdict), and **목표 달성 평가** (Aim Evaluation). These are display labels only; Core, CLI,
`ui-projection`, and code identifiers keep the English names.

The workspace has three resizable panes: the 목표 navigator on the left, a graph in the middle that
draws 목표 → 접근안 → 과제 (approaches can be toggled off), and a detail pane on the right. Every
detail screen follows the same rules: one **지금 할 일** box driven by read-only `goal-loop next`
(the same action never appears in two places), a one-line hint with concept explanations behind
`ⓘ`, stage tabs (목표: 접근안 · 과제 · 달성 평가; 과제: 실행 · 관찰 근거 · 검증), collapsible
sections for criteria, relations, execution hold, and activity, and internal IDs and CLI commands
only under **기술 정보**. Recording an action shows a short confirmation; when Core rejects a record
the detail pane shows the reason in Korean and keeps the raw CLI output folded.

Notable flows: confirming an approach prefills the task name; after a failed run the detail pane
offers retrying with the same criteria (one form, recorded as a retry Decision plus a new Attempt),
revising the criteria, or trying another approach; runs started under an older criteria version can
be tidied without counting toward the new version; evidence cited by a Verdict can be corrected,
which opens a review shown on the task, the 목표, and **검토함** (with a count in the header). The
**전체 작업함** lists every next action as links grouped by 목표, and **실행 대기함** shows only Runtime
delivery state; neither treats technical delivery as completion.

`ui-projection` is now schema version 18. Besides graph nodes and records it exposes per-Goal
`contract_history`, retry and re-verification `decisions`, `evidence_corrections`, the
`impact_reviews` they opened, closure and attribution facts (proposer, closure kind and reason,
stop reason, abandon reason, hold actor and reason), `project_root`, live `references` on Aims,
Proposals and Goals (with resolved path and existence), Evidence `attachments` (with copy path
and `preserved`/`changed`/`missing` status), each Aim's `parent_aim_id` with `aim-parent` graph edges,
and each Aim Evaluation's `child_evaluation_ids`. All additions are read-only.

The app opens a **project folder** (`프로젝트 관리 → 프로젝트 폴더 열기`) and keeps its journal in that
folder's `.goal-loop/`; removing such a project from the app only forgets the registration. Each
detail screen has a **참고 문서** section, and the Evidence form can attach files. Files open with their
default program; executables are revealed in Explorer instead of being run.

UI component checks are available with `cd ui; npm run test:ui`. Production builds remain available
with `npm run build` and `npm run tauri build -- --debug`. The [UI redesign specification](UI-REDESIGN-SPEC.ko.md)
records the 2026-09-29 design; its vocabulary and several screens were superseded by the 2026-10 polish described above.

### Independent workspaces and hiding

The desktop workspace selector creates a separate SQLite journal for each project. Selecting a
workspace passes its database path to the existing read-only projections and allow-listed commands;
it does not add a `Project` entity to the Goal Loop Core. A fresh installation starts with the same
workspace-creation screen shown after the last workspace is deleted. Existing shared default data
is presented once as **기존 작업 공간** so it can be renamed, retained, or explicitly deleted.
The **숨기기** (hide) controls for 목표, 접근안, and 과제 are reversible local display
preferences: they hide records from the navigator, graph, next-action summary, and work inbox on
this PC, but never delete, pause, close, or alter the append-only journal. Restore them from
**숨긴 항목** at the bottom of the navigator or from the parent 목표 screen. Every workspace can be renamed or deleted from the selector after explicit confirmation.
Deletion removes only its verified SQLite file and SQLite sidecars; deleting the last workspace
returns to the common creation screen rather than silently creating another database.

From the project root:

```powershell
cd ui
npm install
npm run tauri dev
```

The wrapper supplies the project virtual-environment and Rust paths, uses port `5174`, and ignores
Rust build output in Vite's watcher. Select an Aim to record a Proposal, then select that Proposal
to make its execution goal or record a replacement version. Select a Goal node to add an immutable
parent link or a valid-satisfied-verdict dependency. The right Inspector covers
Aim → Proposal → Goal Contract → Attempt → Evidence → Verification → Verdict, Contract revision,
Evidence correction, retry/reverification decisions, Aim evaluation, and correction-driven review
resolutions. Buttons record facts only
after the person using the UI chooses the action; a successful Attempt remains distinct from a
verified Goal, and a verified Goal remains distinct from an achieved Aim.

For a distributable debug build:

```powershell
npm run tauri build -- --debug
```

Use the terminal `goal-loop guide`, `goal-loop guided`, and `goal-loop help --all` for the
complete self-service CLI guide. The graph shell is a local UI preview, not a replacement for the
CLI's Runtime outbox or an authorization boundary.

To work inside VS Code, including over Remote SSH, build and install the extension in [`vscode/`](../vscode/README.md).
Clicking `.goal-loop/journal.sqlite3` then opens the desktop app's screen in an editor tab, with the same changes.
