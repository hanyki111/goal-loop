# 변경 기록

이 파일은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고, 버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 따릅니다.

## [0.1.0] - 미발행

첫 공개 준비 버전입니다.

### 추가

- 라이선스: Apache License 2.0(`LICENSE`, `NOTICE`).
- 기록 모델과 CLI(`goal-loop`): 목표(Aim)·하위 목표, 접근안(Proposal)과 달성 기준 접근안(`proposal-close --kind criteria`), 과제(Goal)와 완료 기준(Contract)·개정,
  실행(Attempt), 관찰 근거(Evidence)와 첨부·정정, 검증 판단(Verdict)과 재검토(Impact Review), 목표 달성 평가(Aim Evaluation), 보류·중단·포기 기록.
- 프로젝트별 기록: `project-init`으로 프로젝트 폴더 안 `.goal-loop/`에 기록하고, `where`로 어떤 기록을 쓰는지 확인합니다.
- 읽기 전용 투영: `next`, `status`, `activity`, `reviews`, `admission`, `ui-projection`(schema 16), `memory-draft`.
- 과제 연결: 상위 과제(`goal-parent-add`), 선행 과제(`goal-dependency-add`), 실행을 막지 않고 흐름만 남기는 이어받음(`goal-follow-add`, 목표가 달라도 연결 가능).
- 위임 판정(`goal-loop-judge`): 실행하지 않은 에이전트(Codex CLI, Claude CLI, Claude CLI로 연결한 다른 모델)가 프로젝트 사본에서 판정하고, 그 답을 그대로 기록합니다.
- 데스크톱 앱(Tauri, 한국어 화면): 목표 → 접근안 → 과제 그래프와 배치·필터, ID 검색(Ctrl+K), 지금 할 일 안내, 검토함과 실행 대기함, 창이 다시 활성화될 때 기록 다시 읽기.
- 선택 연결:
  - AI_ORCHESTRATION(AO): 과제를 AO에서 실행하고(`ao-attempt-start`, `runtime-worker`) 결과를 근거로 가져오며(`ao-import`), 앱에서 AO 실행 내역을 봅니다.
  - Memory Tool: 판정이 생기면 타임라인 기록 초안을 제안하고, 기록을 인용한 타임라인 줄과 참조 문서를 앱에서 봅니다(Memory Tool 기본 폴더 설정을 따름).
- 실행 대기함(Runtime outbox): `codex_exec.v1`, `claude_print.v1`, `ao_cli.v1` 요청의 명시적 전달·확인.
