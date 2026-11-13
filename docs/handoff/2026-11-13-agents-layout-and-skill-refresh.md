# 인수인계: `@AGENTS.md` 구성 대응과 스킬 사본 갱신

- 작성일: 2026-11-13
- 출처: `memory_notion` 저장소에서 agent-workbench의 `project-setup`(0.1.4)을 처음 적용한 세션
- 상태: 문제와 개선 방향만 정리했으며, 구현은 시작하지 않았습니다. 같은 날의 Memory Tool 쪽 메모는 `memory_tool/docs/handoff/2026-11-13-record-exception-and-template-target.md`에 있습니다.

## 1. 배경

agent-workbench(`github.com/hanyki111/agent-workbench`)는 Goal Loop와 Memory Tool을 함께 쓰는 프로젝트를 설정하는 Claude Code plugin입니다. 이 plugin은 지침 파일을 다음과 같이 구성합니다. 이 저장소가 `36c8c4f`에서 택한 방식과 같습니다.

- `AGENTS.md`가 원본입니다. Codex는 이 파일을 바로 읽습니다.
- `CLAUDE.md`는 `@AGENTS.md`로 그 내용을 가져오고, Claude에만 해당하는 차이만 적습니다.

이 구성에서 `project-init`을 쓰다가 아래 문제가 드러났습니다.

## 2. 요청하는 작업

1. **`project-init`이 `@AGENTS.md` 구성을 알게 하기.** 지금은 `--agents claude,codex`를 실행하면 `AGENTS.md`에 "이 에이전트의 이름은 `codex`" 절이 들어가고, `CLAUDE.md`에도 "이 에이전트의 이름은 `claude`" 절이 덧붙습니다(`goal_loop_tool/agent_setup.py`의 `instruction_section`). 그런데 Claude는 `@AGENTS.md`를 통해 `AGENTS.md`의 절도 읽기 때문에, 서로 다른 이름과 판정자를 적은 절 두 개를 함께 읽게 됩니다. 그래서 Claude가 자기 이름과 판정자를 혼동할 수 있습니다. `CLAUDE.md`에 `@AGENTS.md` 줄이 있으면 다음 가운데 하나를 하도록 바꿔 주십시오.
   - (가) `AGENTS.md`에는 두 에이전트를 함께 적은 공통 절을 씁니다. 예를 들어 "Claude Code는 `claude`, Codex는 `codex`라는 이름으로 기록하고, 판정은 실행하지 않은 쪽(`claude`가 실행하면 `codex`, 그 반대도 같음)에 맡긴다"처럼 씁니다. `CLAUDE.md`에는 "`claude`로 기록한다" 한 줄만 둡니다. Codex는 `AGENTS.md`만 읽으므로 Codex의 이름과 판정자는 반드시 `AGENTS.md`에 있어야 하고, Claude도 그 파일을 읽으므로 Codex 전용 절을 따로 둘 수 없습니다. 이 저장소의 `AGENTS.md`(Development tracking 절)와 `CLAUDE.md`가 이미 이 형태입니다.
   - (나) 절을 쓰지 않고 경고하며 `--skills-only`를 권합니다.

   agent-workbench는 지금 이 문제를 피하려고 언제나 `--skills-only`를 쓰고, 공통 규칙은 자체 문안으로 씁니다. (가)가 구현되면 agent-workbench는 그 문안을 버리고 `project-init`의 절을 쓰겠습니다.

2. **직접 쓴 Goal Loop 지침이 있을 때 경고하기.** `memory_notion`에는 거시 저널용으로 직접 쓴 Goal Loop 절이 있습니다. 그런데 `--skills-only` 없이 실행하자, 생성 절이 하나 더 추가되어 세션 시작 절차와 판정 규칙이 두 번 들어갔습니다. 표지 밖에 이미 `goal-loop` 명령이나 Goal Loop 절이 있으면, 절을 추가하기 전에 경고해 주십시오.

3. **고치지 않은 예전 템플릿 사본을 교체하기.** 지금은 내용이 다른 스킬 파일이 모두 `kept`로 남습니다. 그래서 아무도 고치지 않은 예전 템플릿 사본도 교체되지 않습니다. `memory_notion`에서는 결함이 있던 예전 `goal-loop` 스킬을 손으로 지운 뒤 다시 실행해야 했습니다. 설치한 템플릿의 해시나 버전을 기록해 두고, 고쳐지지 않은 사본이면 자동으로 교체하는 방식을 검토해 주십시오. 사용자가 고친 사본은 지금처럼 `kept`로 남깁니다.
   - 검토할 때 설치 기록을 어디에 둘지도 정해야 합니다. 이 저장소처럼 `.claude/skills/`와 `.agents/skills/`의 사본을 git으로 추적하는 프로젝트에서는 스킬 폴더 안에 기록 파일을 두면 그 파일도 커밋 대상이 됩니다. `.goal-loop/`(버전 관리에서 제외됨)에 두면 다른 체크아웃에서는 기록이 없어 모든 사본을 `kept`로 보게 됩니다. 두 방식의 장단점을 비교해서 고르십시오.

4. **이 저장소의 Memory Tool 설정 갱신.** Memory Tool 1.10.0에서 `guidelines.md`의 내용이 `mt-record` 스킬로 옮겨졌습니다. 그런데 이 저장소는 아직 갱신되지 않았습니다.
   - `.claude/guidelines.md`가 남아 있고, `mt-record`가 설치되어 있지 않습니다. `.\.venv\Scripts\python.exe -m memory_tool init --update-all`로 갱신합니다. 이 명령은 `guidelines.md`를 `.backup`으로 이름을 바꿔 남깁니다.
   - `AGENTS.md`의 15행("Read `.claude/guidelines.md` …")과 24행("Where `.claude/guidelines.md` writes `m`, `ms` …")을 `mt-record` 기준으로 고칩니다.
   - `docs/GUIDE-THREE-TOOLS.ko.md` 95행은 `minit`이 `.claude/guidelines.md`를 만든다고 안내합니다. 1.10.0부터는 만들지 않으므로 `mt-record` 스킬 기준으로 고칩니다.
   - `.gitignore` 15행의 `.claude/guidelines.md`는 남겨 두어도 해가 없습니다. 다만 갱신 뒤에는 `guidelines.md.backup`이 생기므로, 이 줄을 그 파일 이름으로 바꾸거나 함께 적을지 정합니다.

5. **어제 메모의 상태 문구 갱신.** `docs/handoff/2026-11-12-skills-over-instructions.md`의 상태는 "구현은 시작하지 않았습니다"로 되어 있습니다. 하지만 실제로는 커밋 `8bca3ac`(aim-20, goal-158)에서 대부분 반영되었습니다. 결함 수정(스킬 템플릿의 첫 지시를 PATH의 `goal-loop`로 바꿈), 생성 절 줄이기(13줄), `--skills-only`, README의 `pipx reinstall` 안내가 여기에 해당합니다. 이 커밋을 적어서 상태를 고쳐 주십시오.

6. **`goal-loop-planning`의 참고 파일 이름 유지.** agent-workbench의 `grill-me`는 `goal-loop-planning`을 불러서 그 질문을 씁니다. 처음에는 절 번호로 가리켰는데, `cb39e2c`에서 본문과 참고 파일이 나뉘면서 번호가 바뀌어 잘못된 절을 가리키게 되었습니다. 그래서 지금은 주제(Aim을 정할 때와 묻는 방법, 접근 고르기와 나누기)로 가리킵니다. 참고 파일의 이름이나 다루는 주제가 바뀌면 그 사실을 변경 기록에 적어 주십시오.

## 3. 참고

- `memory_notion`에서 정리한 결과는 커밋 `d576f33`에 있습니다. `CLAUDE.md` 5줄, `AGENTS.md` 113줄입니다.
- agent-workbench 0.1.4의 진단은 이 저장소에서 4번 문제만 보고합니다. 지침 구성은 이미 맞습니다.
