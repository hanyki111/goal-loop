# 인수인계: 스킬 템플릿 결함과 생성 절 줄이기

- 작성일: 2026-11-12
- 출처: `memory_notion` 저장소에서 진행한 프로젝트 설정 점검 세션 (`project-setup` 스킬 시험)
- 상태: 결함을 확인하고 방향을 정했으며, 구현은 시작하지 않았습니다. 같은 방향의 Memory Tool 작업은 `memory_tool/docs/handoff/2026-11-12-skills-over-instructions.md`에 있습니다.

## 1. 결함: 다른 프로젝트에 복사되는 스킬의 첫 지시

`project-init --agents`는 `goal_loop_tool/agent_files/goal-loop-SKILL.md`를 내용을 바꾸지 않고 `.claude/skills/goal-loop/`와 `.agents/skills/goal-loop/`에 복사합니다. 그런데 이 템플릿의 첫 지시는 "In this repository, invoke `& .\.venv\Scripts\goal-loop.exe`"입니다. 이 문장은 이 개발 저장소 안에서만 맞습니다. 다른 프로젝트에는 `.venv`가 없기 때문에, 에이전트가 처음에 잘못된 경로를 시도하게 됩니다. 바로 다음 줄에 PATH의 `goal-loop`를 쓰라는 대안이 있으므로 결국에는 작동합니다.

- **고칠 방향:** 템플릿에는 PATH의 `goal-loop`를 기본으로 적습니다. `.venv`를 쓰라는 지시는 이 저장소의 `CLAUDE.md`나 `CLAUDE.local.md`로 옮깁니다.
- **영향받은 곳:** `memory_notion`에는 커밋 `59a553e` 기준의 템플릿을 그대로 복사해 두었습니다. 결함을 고친 뒤 다시 복사해야 합니다.

## 2. 결정: 사용법은 스킬로, 지침 파일에는 항상 적용할 규칙만

사용자는 `CLAUDE.md`와 `AGENTS.md`를 최소화하기를 원합니다. 그래서 내용을 세 가지 층으로 나누기로 했습니다.

| 층 | 담는 내용 | 위치 |
| :--- | :--- | :--- |
| 항상 적용되는 규칙 | 에이전트의 기록 이름, `user` 이름으로는 기록하지 않는다는 규칙, 판정을 실행자와 분리한다는 규칙 | `CLAUDE.md`, `AGENTS.md` (규칙마다 한 줄) |
| 사용법과 절차 | 명령 문법, 달성 기준을 쓰는 방법, 하위 Aim으로 묶는 방법, `goal-follow-add`, Evidence 첨부 | `goal-loop` 스킬 |
| 매 세션 반드시 실행할 명령 | `where`, `next`, `status` | SessionStart hook (검토 단계) |

## 3. 요청하는 작업

1. **결함 수정:** 1절의 결함을 고칩니다. 이 저장소에 있는 `.claude/skills/goal-loop/SKILL.md`와 `.agents/skills/goal-loop/SKILL.md`도 같은 문장을 쓰는지 확인합니다.
2. **생성 절 줄이기:** `agent_setup.instruction_section`이 만드는 절은 지금 35줄 정도입니다. 2절의 첫 번째 층에 해당하는 규칙만 남기고, 나머지는 스킬로 옮깁니다. 다만 판정 명령의 `--judge` 값은 에이전트에 따라 달라집니다(`judge_for`). 그래서 이 값은 생성 절에 남겨 두는 편이 맞습니다.
3. **스킬만 설치하는 선택지:** `memory_notion`처럼 거시 저널을 둔 폴더에는 직접 쓴 Goal Loop 절이 이미 있습니다. 이런 폴더에서는 생성 절이 같은 지시를 두 번 넣게 됩니다. 그래서 이번에는 템플릿을 손으로 복사했습니다. 스킬만 설치하고 지침 파일은 건드리지 않는 선택지(예: `--skills-only`)를 검토해 주십시오.
4. **업데이트 안내:** 버전이 계속 `0.1.0`이어서 `pipx upgrade`를 실행해도 새 커밋이 반영되지 않을 수 있습니다. 버전을 올리는 규칙을 정하거나, README에 `pipx reinstall goal-loop-tool`을 안내해 주십시오.

## 4. 참고: 이 PC의 설치 상태 (2026-11-12)

PATH의 `goal-loop`는 이제 GitHub에서 pipx로 설치한 버전입니다(`%LOCALAPPDATA%\pipx\pipx\venvs\goal-loop-tool`). 원래 10월 11일에 pipx로 설치했습니다. 하지만 pipx의 기본 폴더가 `~/pipx`에서 바뀐 탓에 `pipx list`에 나타나지 않았고, 11월 7일쯤 `uv tool`로 로컬 폴더에서 다시 설치하면서 실행 파일이 덮어써졌습니다. 이번 세션에서 uv 설치와 예전 `~/pipx` 폴더를 지우고 pipx로 다시 설치했습니다. 그래서 커밋하지 않은 goal-155 변경(`_report_excerpt`)은 PATH의 `goal-loop`에 들어 있지 않습니다.
