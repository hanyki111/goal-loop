# Goal Loop Tool 영속성 계약

**상태:** 설계 합의 · **기준일:** 2026-09-16 · **적용 단계:** 단일 PC SQLite 영속 MVP

이 문서는 현행 in-memory concept-validation MVP를 append-only SQLite 저장과 재시작 복원으로 확장하기 위한 계약이다. 현재 의미론의 정본은 [`MVP-SPEC.md`](MVP-SPEC.md)이며, 이 문서는 그 전이·불변조건을 바꾸지 않는다. 코드 구현, HTTP 동기화, Obsidian 플러그인은 이 문서의 다음 단계다.

## 1. 범위와 경계

첫 구현은 한 대의 PC에서 동작하는 SQLite 파일을 canonical store로 사용한다. SQLite는 Vault 동기화 대상이 아니며, Obsidian은 향후 HTTP adapter를 통하는 UI가 될 수 있다. Core는 SQLite, 파일 경로, HTTP, Obsidian API를 직접 알지 않는다.

```text
GoalLoop Core
  ↕ repository transaction boundary
  ├─ InMemoryRepository  (기존 테스트와 CLI)
  └─ SqliteRepository    (첫 영속 구현)
       └─ future HttpRepository (동기화/모바일 adapter)
```

외부 Runtime 호출·취소와 DB 기록은 같은 transaction에 넣지 않는다. Runtime 요청의 enqueue만
Attempt 시작과 같은 transaction으로 확정할 수 있으며, 실제 dispatch와 재시도는 그 밖에서
명시적으로 수행한다.

## 2. 정본 기록과 파생 projection

`event_journal`의 승인된 불변 사실이 정본이다. journal에는 Aim, Proposal, Proposal supersession, Goal Identity, immutable Contract 각 버전, Attempt 시작·완료, Evidence와 Correction, Verdict와 supersede, Aim Evaluation과 closure invalidation, Decision, Impact Review/Resolution, dependency, grant/revoke, Hold add/clear를 기록한다. 모든 record에는 actor, 사유, operation, observation, conclusion 및 안정적인 대상 참조를 보존한다.

Aim/Goal phase, current Contract version, verifying Attempt, Attempt outcome, Verdict/Aim Evaluation validity, Decision 소비 여부, pending re-verification, pause, Hold, grant 및 dependency admission 결과는 journal을 재생해 계산하는 현재 상태다. 빠른 조회용 projection에는 저장할 수 있으나 정본은 아니다.

projection은 journal checkpoint와 일치할 때만 신뢰하며 언제든 재생성할 수 있어야 한다. 비정상 종료는 domain 전이가 아니므로 `running` Attempt나 Goal 상태를 자동으로 `failed` 또는 `stopped`로 바꾸지 않는다.

## 3. SQLite 최소 구조

| 저장소 | 역할 |
|---|---|
| `event_journal` | 승인된 domain 사실의 append-only 정본 |
| `command_receipts` | 재전송 결과와 거절 요청의 immutable receipt |
| `*_current` projection | 현재 Aim, Goal, Attempt, Verdict 등 빠른 조회용 상태 |
| `projection_checkpoint` | 마지막으로 적용한 journal sequence |
| `activity_projection` | journal/receipt에서 만든 사람용 cache 또는 view |
| `system_runs` | 복원·rebuild 기술 진단 기록; domain activity와 분리 |
| `runtime_outbox` | Runtime adapter가 claim/ack하는 기술적 실행 요청; journal 정본과 분리 |

`event_journal` envelope:

```text
sequence, event_id, command_id, event_type, event_schema_version,
recorded_at, actor_id, subject_kind, subject_id, payload_json
```

새 domain record는 entity prefix를 붙인 ULID 또는 UUIDv7 계열 canonical ID를 쓴다. 관계·외래 참조는 이 ID를 사용한다. 사람에게는 별도로 영속된 짧은 불변 표시 번호를 보인다. 예: `Goal #12`, `Attempt #31`.

`command_receipts`는 command ID, command type, request fingerprint, status (`committed` 또는 `rejected`), 성공 event sequence 범위, 결과 또는 error code/message, 기록 시각을 가진다. 같은 command ID에 다른 fingerprint를 보내면 conflict다.

## 4. Transaction과 idempotency

모든 상태 변경 command는 한 SQLite transaction으로 처리한다.

```text
현재 projection 로드 및 불변조건 검사
→ domain event 생성
→ journal append
→ command receipt 기록
→ current projection 갱신
→ 모두 commit 또는 모두 rollback
```

모든 변경 요청에는 내부 `command_id`를 둔다. 현재 CLI와 `GoalLoop` public method는 이를 직접 요구하지 않아도 되며 adapter가 생성한다. 동일 command ID와 동일 요청은 저장된 결과 또는 거절 receipt를 반환한다.

`accept_proposal`의 `idempotency_key`는 별도 domain 계약으로 유지한다. 같은 key와 동일 canonical fingerprint는 같은 Goal을 반환하고, 다른 fingerprint는 conflict다. Proposal acceptance는 `ProposalAccepted`, `GoalCommitted`, `ContractVersionAdded`와 idempotency 기록을 한 transaction으로 확정한다.

`revise_proposal`은 `previous_proposal_id`, 새 `direction`, `actor_id`, 비어 있지 않은
`reason`을 request fingerprint에 포함한다. 한 transaction에서 새 Proposal ID를 생성하고
`ProposalSuperseded(previous_id, replacement_id, direction, actor_id, reason)` event를
append하며 receipt는 새 Proposal ID를 결과로 보관한다. replay는 이 event로 후속 Proposal을
재구성한다. 동일 command ID의 재전송은 그
후속 Proposal을 반환하고, 다른 fingerprint는 conflict다. 재시작 replay는 이전 Proposal을
`superseded`, 후속 Proposal을 `open`으로 복원한다.

상태 변경이 거절된 요청도 domain event는 만들지 않지만 `rejected` receipt로 남긴다. 이는 pause, Hold, Authority, stale Contract 등 admission 차단의 원인을 재시작 뒤에도 설명하고 동일 요청을 안정적으로 재응답하기 위함이다.

## 5. 민감한 의미론의 기록 규칙

### Proposal supersession

새 대안을 추가하는 `create_proposal`은 기존 Proposal의 상태를 바꾸지 않는다. 반면
`revise_proposal`은 정확히 하나의 `open` Proposal만 교체할 수 있다. 이전 Proposal의
본문을 갱신하지 않으며, 같은 Aim을 가진 새 Proposal과 replacement 관계를 기록한다.
`accepted` 또는 이미 `superseded`인 Proposal을 교체하려는 요청은 rejected receipt로
남긴다. acceptance는 `open` Proposal만 허용한다. activity projection은 “새 대안 추가”와
“Proposal 교체”를 구분하여, 기존 문구·새 문구·이유·Actor·후속 Proposal을 보여 준다.

### Contract revision과 늦은 사실

revision은 `DecisionRecorded(kind=revise_contract)`와 `ContractVersionAdded(goal_id, next_version, draft)`를 함께 기록한다. 기존 Attempt와 Evidence의 Contract version은 절대 바꾸지 않는다. v2가 생성된 뒤 늦게 도착한 v1 Attempt 완료와 Evidence는 v1 사실로 보존할 수 있으나 v2 verification/Verdict 근거가 될 수 없다.

### Decision 소비

retry/re-verification Decision은 `DecisionRecorded`로 생성하고, 새 Attempt 시작 시 `AttemptStarted`와 `DecisionConsumed(decision_id, attempt_id)`를 같은 transaction에 기록한다. `DecisionConsumed`가 있으면 재시작 뒤에도 다시 소비할 수 없다.

### Correction과 review validity

Correction은 `EvidenceCorrected`와 영향받은 유효 Verdict별 `ImpactReviewOpened(trigger_verdict_id, correction_id, affected_goal_ids, affected_aim_evaluation_ids)`를 같은 transaction에 기록한다. 영향 범위는 당시의 사실로 고정한다. 미해결 review가 있으면 Verdict/Aim Evaluation은 projection에서 `under_review`다. `ReviewResolved(reaffirm|invalidate)`를 재생해 validity를 계산하며, 하나라도 invalidate면 terminal `invalidated`다. 모든 관련 review가 reaffirm되어야 Verdict가 `valid`로 돌아올 수 있다.

## 6. Runtime 결과 ingress

Runtime 결과에는 Runtime이 부여한 `runtime_delivery_id`를 포함한다. 이는 Core 요청의 `command_id`와 다르다. 전자는 외부 실행 결과 배달의 identity이고, 후자는 Core command 재전송의 receipt identity다.

| 수신 상태 | 처리 |
|---|---|
| `running` Attempt에 새 delivery | `AttemptCompleted` 기록 |
| 동일 delivery와 동일 payload | 기존 결과 반환 |
| 동일 delivery와 다른 payload | conflict receipt |
| 완료된 Attempt에 다른 delivery | rejected receipt |
| 없는 Attempt 또는 시작 전 결과 | rejected receipt |
| 재시작 뒤 원래 running Attempt 결과 | 정상 수용 |

Runtime 결과는 Attempt outcome만 확정한다. Evidence와 Verdict는 현행 계약처럼 별도 command로 기록한다. `cancelled`, `unknown`, `stopped` 전이는 현 단계에 도입하지 않는다.

### Local subprocess adapter profile

공통 subprocess adapter는 shell 문자열이 아닌 `argv`, working directory, timeout, optional
stdin만 받는다. 종료 코드 0은 `succeeded`, non-zero 또는 timeout은 `failed` Runtime
delivery 후보가 되며 stdout·stderr·exit code·timeout 여부는 adapter result에 보존한다.
실제 process 실행은 SQLite transaction 밖에서 끝난 뒤 `runtime_delivery_id`로 ingress한다.

### Durable Runtime request outbox

`PersistentGoalLoop.start_runtime_attempt(...)`는 기존 `AttemptStarted` domain event와 하나의
`runtime_outbox` row를 같은 SQLite transaction에서 확정한다. outbox에는 안정적인
`request_id`, `attempt_id`, adapter가 해석할 `request_kind`와 JSON payload, 그리고
`pending → claimed → completed` 기술 상태만 저장한다. 이는 새 domain event나 Attempt 상태가
아니다.

worker는 pending request를 명시적으로 claim하고, 자기 claim만 acknowledge하거나 pending으로
되돌릴 수 있다. claim은 다른 worker의 claim을 덮어쓰지 않으며, DB 재시작 뒤에도 남는다.
claim/ack/requeue는 subprocess를 실행하거나 Attempt 결과를 기록하지 않는다. 실행 결과는 계속
별도 `runtime_delivery_id` ingress로만 완료한다. adapter는 재전송 안전성이 필요하면 안정적인
`command_id`와 `request_id`를 함께 제공해야 한다.

공용 `RuntimeOutboxWorker` helper는 caller가 제공한 executor를 한 번만 호출하고, 성공적으로
반환된 결과를 delivery한 뒤 ack한다. executor·delivery·ack 중 어느 단계든 실패하면 worker는
자동 retry 또는 requeue하지 않고 claim을 남긴다. 따라서 이 단계의 external dispatch는 보수적인
at-most-once 시도다. 작업을 다시 실행할지, 이미 실행된 결과를 수동으로 ingress/ack할지는
operator가 claim을 점검한 뒤 명시적으로 결정한다.

현재 built-in payload contract는 `codex_exec.v1`과 `claude_print.v1`뿐이다.
`codex_exec.v1`은 `prompt`, 기존 `workdir`와 선택적인 positive `timeout_seconds`,
`read-only|workspace-write` sandbox, executable, 안전한 extra args만 받으며 기존
`CodexExecProfile`을 사용한다. `claude_print.v1`은 `prompt`, 기존 `workdir`, 명시적인 model,
양의 max_turns와 max_budget_usd, 선택적 timeout/executable만 받으며 기존 restricted
`ClaudePrintProfile`을 사용한다. 알 수 없는 kind/field와 금지된 Codex 인자는 process 시작 전
거절되고 request는 claimed로 남는다. `runtime-dispatch <request> <worker>`는 이 profile을
명시적으로 한 번 실행하는 CLI surface이며 background scheduler가 아니다.

worker가 process 완료 뒤 delivery/ack 전에 중단됐다면 operator는 requeue로 재실행하지 않고
`runtime-deliver <request> <worker> <succeeded|failed> <runtime_delivery_id>`를 사용해 기존
claim에 관측된 결과를 ingress할 수 있다. 이 명령은 executor를 호출하지 않으며, delivery 성공 뒤
ack한다. delivery가 이미 완료됐는데 ack만 실패한 경우에는 `runtime-ack`만 수행한다.

Codex profile은 공식 `codex exec`의 non-interactive 형식을 사용한다. 기본 sandbox는
`read-only`이고 필요 시 명시적으로 `workspace-write`만 허용한다. `danger-full-access`,
기존의 deprecated `--full-auto`, credential 주입, project rule 무시는 adapter가 자동으로
선택하지 않는다.

Claude Code profile은 `claude -p --restricted`를 사용한다. 이 첫 profile은 명령/코드 실행
도구와 권한 prompt를 허용하지 않는 bounded read-only investigation이며 JSON 결과만 수집한다.
`--no-session-persistence`로 Claude session 저장도 끈다. 모든 `claude-run`은 model, 양의
`max_turns`, 양의 USD `max_budget_usd`를 명시해야 한다. adapter는 기본 모델이나 무제한 비용을
선택하지 않는다. Claude의 파일 수정이나 command 실행을 허용하는 profile은 별도 권한·artifact
계약을 설계할 때까지 도입하지 않는다.

## 7. 사람용 활동 타임라인

activity projection은 domain journal과 command receipt를 `command_id`로 묶어 한국어 활동 카드로 보여준다. card에는 시각, actor, 행동, 결과, 이유·관측·결론 원문, 관련 Aim/Goal/Attempt/Evidence/Verdict, 영향받은 record와 상태 변화를 포함한다.

Correction card는 정정 근거, quarantine된 Verdict, 차단된 dependent Goal, under-review가 된 Aim Evaluation, 생성된 Impact Review를 보여 준다. 화면용 문구와 지역화는 projection의 책임이다. actor가 입력한 reason/observation/conclusion 및 구조화된 사실은 journal payload에 보존하며, 화면 문구가 정본이 되지 않는다.

## 8. 복원과 최소 acceptance 기준

시작 시 schema version과 projection checkpoint를 검사한다. checkpoint가 journal 끝과 일치하면 projection을 사용한다. 불일치·손상·명시적 rebuild가 있으면 마지막 안전 snapshot 또는 journal 시작부터 sequence 순서로 재생해 모든 projection을 재구축한다. 복원은 domain activity event가 아니라 `system_runs` 기술 진단 기록이다.

기존 18개 unittest와 7개 CLI 시나리오에 더해 최소 다음을 검증한다.

1. 재시작 뒤 Proposal acceptance idempotency와 fingerprint conflict
2. acceptance 중 장애 주입 시 부분 event·projection·receipt가 남지 않음
3. retry 및 re-verification Decision의 단회 소비 복원
4. v2 revision 뒤 재시작하고 늦은 v1 결과를 보존하되 v2 verification 차단
5. Correction/restart 뒤 dependent Goal quarantine과 복수 review reaffirm 규칙
6. Aim Evaluation invalidation 및 명시적 closure invalidation 복원
7. pause/Hold/Authority 거절 receipt와 활동 카드
8. running Attempt 재시작 뒤 Runtime 결과 수용 및 duplicate/conflict 판정
9. projection 삭제·손상 뒤 journal replay가 동일 current state를 만듦
10. InMemoryRepository와 SqliteRepository의 repository contract parity

## 9. 보류 범위

## 10. SQLite MVP 운영 구현 상태

현재 SQLite MVP는 schema v3를 지원한다. v1 metadata DB는 v2와 v3으로 순차 migration하며, 더 높은
미지원 버전은 열지 않는다. Snapshot은 journal prefix의 event payload, sequence, SHA-256
digest를 함께 저장하는 선택적 replay cache다. 현재 journal prefix가 digest와 일치할 때만
snapshot payload를 복원하고, 뒤에 추가된 journal event는 suffix로 replay한다. 불일치한
snapshot은 폐기하고 전체 journal replay를 사용한다.

`projection_checkpoint`가 journal 끝보다 뒤처진 상태로 시작하면 persistent facade는
journal을 replay한 뒤 checkpoint를 최신 sequence로 갱신한다. `PRAGMA integrity_check`는
repository의 corruption diagnostic으로 제공하며, `backup_to()`는 SQLite online backup으로
journal과 receipt를 일관된 사본으로 저장한다.

Snapshot은 아직 domain current-state를 독자 직렬화한 materialized projection이 아니다.
따라서 cache가 없어도 정확성에는 영향이 없고, journal replay가 언제나 정본이다.

- HTTP server, 계정/인증, 다중 사용자 conflict resolution, Obsidian plugin
- offline queue와 sync protocol, Runtime dispatch/cancel retry
- Attempt `cancelled`/`unknown`, Goal `stopped`의 전이
- Goal Tree, Frame, Gap, Hypothesis, Lesson, 완전한 Authority Policy
