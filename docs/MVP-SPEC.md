# Goal Loop Tool — 규범 mock MVP 명세 (개념 검증 프로필)

**상태:** SQLite 영속 MVP와 CLI에 적용된 규범 의미론 · **기준일:** 2026-09-28 · **범위:** Python Core, append-only SQLite facade, local CLI

이 문서는 현재 개념 검증 구현의 **규범적 계약**이다. 이 프로필은 제품 MVP 이전의
mock MVP이며, 제한된 시나리오와 append-only SQLite journal로 핵심 상태 의미론을 반례까지
검증한다. Core는 저장소를 직접 알지 않으며 SQLite facade가 Core command와 journal replay를
연결한다. AI_ORCHESTRATION 연동, Goal Tree, Frame은 이 프로필의 구현 범위 밖이며, 이 문서가
그것들을 이미 제공한다고 주장하지 않는다.

Core command 호출자는 이 프로필에서 신뢰된 경계 안에 있다. Actor ID는 기록과
시나리오 검증을 위한 식별자이며, Core는 Correction·review·Decision 권한을 인증하거나
위임 검증하지 않는다. 비신뢰 adapter를 붙일 때의 인증·인가와 완전한 Authority Policy는
후속 범위다.

## 1. 목적과 경계

이 프로필은 자연어 Intent를 완전 자동으로 해결하는 제품이 아니다. 다음 다섯 가지
의미론적 경계가 함께 작동하는지를 검증한다.

1. Proposal 수락은 Goal과 불변 Contract v1을 원자적으로 만든다.
2. 실행 결과, 검증 판정, Goal 단계, Aim 달성은 서로 다른 사실이다.
3. Contract가 물질적으로 바뀌면 새 버전을 만들며 과거 사실을 새 기준에 전용하지 않는다.
4. Evidence 정정은 과거 이력을 지우지 않고 Verdict의 현재 유효성을 바꾼다.
5. 새 실행은 권한·Hold·Aim pause를 통과해야 하며, Aim은 명시적 Evaluation으로만 달성된다.

## 2. 용어와 최소 개체

| 개체 | 역할 | 최소 필드 |
|---|---|---|
| Aim | Intent에서 정규화한, 평가 가능한 지향점 | id, description, phase |
| Goal Proposal | Aim에 기여할 Goal 후보 또는 기존 후보의 교체본 | id, aim_id, direction, status, accepted_goal_id, superseded_by_proposal_id |
| Goal | 하나의 주 Aim에 책임지는 실행 약속 | id, Goal Identity, phase, current_contract_version |
| Goal Tree link | 같은 Aim 안의 immutable parent-child Goal 관계 | child_goal_id, parent_goal_id |
| Goal dependency | target Attempt 전에 source Goal의 current valid satisfied Verdict를 요구하는 DAG 관계 | dependent_goal_id, required_goal_id |
| Goal Identity | Goal이 같은 문제를 계속 말하는지 판별하는 고정 정체성 | primary_aim_id, target_and_desired_result, responsibility_kind |
| Goal Contract | 특정 버전에서 무엇을 성공으로 볼지 정한 불변 계약 | goal_id, version, operational_scope, success_criterion, verification_specification |
| Execution Attempt | 특정 Contract 버전으로 실제 실행한 한 번의 시도 | id, goal_id, contract_version, executor_actor_id, operation, outcome |
| Evidence | 완료된 Attempt에서 수집한 관측 사실 | id, goal_id, contract_version, attempt_id, submitted_by_actor_id, observation |
| Verification Verdict | 한 성공 Attempt의 Evidence를 근거로 Contract 충족 여부를 판정한 기록 | id, goal_id, contract_version, attempt_id, verifier_actor_id, result, evidence_ids, supersedes_verdict_id, reason(선택, 판단 이유. 2026-10-04 추가, 이전 journal에는 없음) |
| Aim Evaluation | 유효한 Verdict가 Aim 달성에 충분한지 판단한 기록 | id, aim_id, evaluator_actor_id, verdict_ids, rationale |
| Evidence Correction | 기존 Evidence가 잘못되었음을 기록한 정정 사실 | id, corrected_evidence_id, submitted_by_actor_id, observation |
| Impact Review | Correction이 Verdict·의존 Goal·Aim Evaluation에 미치는 영향을 기록 | id, trigger_verdict_id, correction_id, affected_goal_ids, affected_aim_evaluation_ids, level |
| Review Resolution | Impact Review를 reaffirm 또는 invalidate로 종결한 기록 | id, impact_review_id, disposition, decided_by_actor_id, reason |
| Aim Closure Invalidation | 무효화된 Evaluation 뒤 Aim을 다시 열기로 한 명시적 판단 | id, aim_id, evaluation_id, triggering_record_id, decided_by_actor_id, reason |

## 3. 상태는 서로 섞지 않는다

### 3.1 Aim phase

- `open`: 아직 종결되지 않은 Aim.
- `achieved`: 명시적 Aim Evaluation으로 종결 판정한 이력 상태.

Goal이 `verified`여도 Aim은 자동으로 `achieved`가 되지 않는다.

`achieved`는 종결형 Aim에만 사용한다. 지속적으로 관리할 Aim은 `open`으로 유지하고
반복 Goal·Evaluation으로 현재 상태를 평가한다. Aim Evaluation에도 `valid`,
`under_review`, `invalidated`의 현재 유효성이 있으며, `currently_achieved`는 현재
`valid`인 Aim Evaluation이 하나 이상 있는지로 계산한다. `achieved` 이력은 근거가
무효화되어도 자동으로 `open`으로 덮어쓰지 않는다.

### 3.2 Proposal status와 교체

- `open`: Goal 후보로 검토하거나 수락할 수 있는 Proposal.
- `accepted`: Goal과 Contract v1을 만들기 위해 수락된 이력 상태.
- `superseded`: 같은 실행 방향의 더 적합한 후속 Proposal로 명시적으로 교체된 이력 상태.

`create_proposal`은 기존 Proposal과 경쟁하거나 보완하는 새 대안을 만든다. 따라서
같은 Aim 아래 여러 `open` Proposal이 공존할 수 있다. `revise_proposal`은 기존
`open` Proposal의 `direction`을 수정하지 않는다. 대신 새 `open` Proposal을 만들고,
이전 Proposal을 `superseded`로 전이하며 `superseded_by_proposal_id`와 변경 이유·Actor를
기록한다. 이 둘은 하나의 논리적 command다.

`accepted` Proposal은 다시 수정하거나 supersede하지 않는다. 수락 뒤 같은 Goal의
범위·성공 기준·검증 방법을 바꾸는 일은 `revise_contract`의 책임이며, 실행 방향 자체를
다시 선택하려면 별도 새 Proposal을 만든다.

### 3.3 Goal phase

`committed → active → verifying → verified`

- `committed`: Proposal 수락과 Contract v1 생성이 끝난 상태.
- `active`: 새 Attempt를 시작할 수 있거나 실행·보완 중인 상태.
- `verifying`: 성공한 현재 Contract Attempt가 검증 대기 중인 상태.
- `verified`: 현재 Contract를 만족한다는 `satisfied` Verdict가 기록된 이력 상태.
- `stopped`: 모델에는 예약되어 있지만, 이 개념 검증 프로필에서는 전이 command를 아직 제공하지 않는다.

`not_satisfied` Verdict는 Goal을 `active`로 되돌린다. `inconclusive` Verdict는 Goal을
`verifying`에 유지한다.

### 3.4 Attempt outcome

이 프로필은 `running`, `succeeded`, `failed`만 구현한다. Attempt 실패는 Goal 실패가
아니다. 실패 Attempt 뒤 같은 Contract의 다음 Attempt는 대상 실패 Attempt와 Contract
버전을 가리키고 한 번만 소비되는 명시적 retry Decision 뒤에 시작한다. `not_satisfied`
Verdict 뒤의 보완 실행은 이 retry Decision 규칙과 별개다.

### 3.5 Verdict result와 validity

- 결과: `satisfied`, `not_satisfied`, `inconclusive`
- 현재 유효성: `valid`, `under_review`, `invalidated`

결과는 당시 검증의 결론이며, 유효성은 지금도 그 결론을 신뢰할 수 있는지다.
`invalidated`는 과거 `verified` Goal 이력을 지우지 않는다. 허용 전이는
`valid → under_review`, `under_review → valid | invalidated`다. `invalidated`는
terminal이며, 여러 Correction review가 열려 있으면 모두 reaffirm될 때까지 `valid`로
복귀하지 않는다.

## 4. Command와 전이 계약

1. `create_aim`은 `open` Aim을 만든다.
2. `create_proposal`은 기존 Aim 아래에 `open` Proposal을 만든다.
   이는 기존 Proposal을 대체하지 않는 새 대안 추가다.
3. `revise_proposal`은 `open` Proposal 하나에만 적용한다. 새 `open` Proposal과
   `ProposalSuperseded(previous_id, replacement_id, actor_id, reason)` 사실을 원자적으로
   만들며, 이유는 비어 있을 수 없다. 같은 command ID와 같은 요청은 같은 후속
   Proposal을 반환하고, 다른 요청은 conflict다.
4. `accept_proposal`은 `open` Proposal만 수락할 수 있으며, idempotency key를 사용해 Proposal 수락, Goal 생성, Contract v1 생성을 한 논리 작업으로 처리한다. 같은 key와 같은 요청만 같은 Goal을 돌려주며, 다른 요청의 key 재사용은 conflict다. 현재 `achieved`인 Aim 아래 Proposal은 수락할 수 없다.
5. `start_attempt`는 Goal이 `committed` 또는 `active`이고, 주 Aim이 `open`이며, 요청 Contract가 현재 버전이고 Admission을 통과할 때만 허용된다. 실패 Attempt 뒤에는 대상·버전 고정 retry Decision이 필요하다. 시작 뒤 Goal은 `active`다.
6. `complete_attempt`는 실행 중 Attempt를 `succeeded` 또는 `failed`로 한 번만 전이한다.
7. `record_evidence`는 완료된 Attempt에만 연결되며 Attempt의 Goal·Contract 버전을 그대로 가진다.
8. `begin_verification`은 성공한 **현재 Contract** Attempt 하나를 현재 검증 대상으로 고정하고 Goal을 `verifying`으로 바꾼다.
9. `submit_verdict`는 고정된 성공 Attempt의 실행자와 다른 Actor가, 그 Attempt에 속한 현재 Contract Evidence를 하나 이상 인용할 때만 허용된다. `satisfied`와 `not_satisfied`는 해당 검증 흐름의 최종 Verdict다. `inconclusive` 뒤에는 새 Evidence를 인용하고 이전 Verdict를 `supersedes`하는 Verdict만 제출할 수 있다. `satisfied`는 `verified`, `not_satisfied`는 `active`, `inconclusive`는 `verifying`을 유지한다.
10. `revise_contract`는 `committed` 또는 `active` Goal에만 새 불변 Contract 버전을 추가한다. 기존 Attempt·Evidence는 원래 버전을 유지한다.
11. `correct_evidence`는 원 Evidence를 보존한 채 Correction을 기록하고, 그 Evidence를 인용한 모든 `valid` Verdict를 즉시 `under_review`로 바꾸며 Impact Review를 만든다. 정정된 Evidence는 새 Verdict의 근거로 인용할 수 없다. `resolve_verdict_review`는 review를 `reaffirm` 또는 `invalidate`로 종결한다.
12. `evaluate_aim_as_achieved`는 `open` Aim에 속하고 현재 `valid`인 `satisfied` Verdict를 하나 이상 인용할 때만 Aim을 `achieved`로 전이한다. 인용 Verdict가 흔들리면 Evaluation도 `under_review`가 되며, 별도 review로 reaffirm 또는 invalidate한다. 현재 `valid` Evaluation이 하나도 없고 Evaluation이 invalidated된 뒤에만 `closure_invalidated` 기록으로 Aim을 `open`으로 재개할 수 있다.
13. `pause_aim`과 `resume_aim`은 Aim phase를 바꾸지 않고 새 Attempt Admission만 각각 차단·재허용한다. 둘 다 actor와 reason을 남기며 resume은 Attempt를 자동 시작하지 않는다.
14. `decide_reverification_requires_new_attempt`는 `inconclusive`인 검증 흐름에서 새 Runtime 작업이 필요하다고 판단할 때만 Goal을 `active`로 되돌린다.

## 5. 핵심 불변 조건

- Goal은 하나의 `primary_aim_id`만 가진다.
- Proposal 수락은 Goal을 중복 생성하지 않는다.
- `superseded` Proposal은 수락·재수정할 수 없고, `superseded_by_proposal_id`는 같은 Aim의
  존재하는 후속 Proposal을 가리킨다. 순환 supersession은 허용하지 않는다.
- Contract는 수정하지 않는다. 물질적 변경은 다음 버전 생성이다.
- 늦게 도착한 v1 결과는 v2 Goal의 검증 또는 성공 근거가 될 수 없다.
- 실행 Actor는 자신의 Attempt에 대한 최종 독립 Verdict를 제출할 수 없다. 이 프로필의 독립성은 Actor ID 상이성만 검사하며 실제 주체·신원·관측 방법은 보장하지 않는다.
- Evidence와 Verdict는 대상 Goal·Contract 버전·고정된 검증 Attempt를 일치시켜야 한다.
- Verdict가 `under_review` 또는 `invalidated`이면 그 Verdict에 의존한 Goal의 새 Attempt는 시작할 수 없다.
- Verdict dependency는 요구하는 Verdict result를 명시하며 Admission은 result와 validity를 모두 확인한다.
- Goal `verified`와 Aim `achieved`는 별개의 전이이며 후자는 명시적 Evaluation이 필요하다.

## 6. Execution Admission

이 프로필의 Admission은 새 Attempt 시작 시점에 계산한다. 화면용 `ready` 값은
실행 허가의 정본이 아니다.

현재 최소 구현은 다음을 확인한다.

- Goal phase와 현재 Contract version
- Authority 검사를 활성화한 구성에서의 `execute` Authority Grant
- Goal 범위의 미해소 Hold
- 주 Aim의 pause
- 의존 Verdict의 요구 result와 현재 유효성
- 실패 Attempt 뒤 retry Decision, 또는 inconclusive 뒤 새 Runtime 작업을 위한 re-verification Decision

현재 Grant와 Hold는 Goal 범위의 단순 in-memory 집합이다. Authority 검사가 꺼진 구성은
Grant가 자동 부여됐다는 뜻이 아니다. Authority Policy, 위임,
세부 action/stage 범위, 이미 실행 중인 작업의 취소는 후속 구현 범위다.

## 7. 책임 경계

| 계층 | 이 프로필의 정본 책임 |
|---|---|
| Goal Loop Core | Aim·Proposal·Goal Identity/Contract, Attempt/Evidence/Verdict의 의미론, Evaluation, Admission 판정 |
| Runtime adapter | 실제 작업 수행, 관측 반환, 실행 실패 사실 전달 |
| LocalFileRuntime | 파일 생성·읽기라는 실제 I/O 예제 |
| AI_ORCHESTRATION adapter | 아직 미구현. 향후 Core command를 AO workflow 요청으로 전달하고 실행 사실을 Core 형식으로 반환 |

Runtime의 실제 취소 성공 여부, 도구 강제 권한 검사, 영속 트랜잭션은 아직 Core가
소유하거나 구현한 기능이 아니다. AI_ORCHESTRATION을 통합할 때는 이를 명시적
adapter contract로 나누어야 한다.

## 8. 수용 시나리오

다음 CLI와 테스트는 이 프로필의 실행 가능한 수용 기준이다.

| 시나리오 | 명령 | 확인하는 계약 |
|---|---|---|
| 실패 후 같은 Contract 재시도 및 독립 검증 | `.\.venv\Scripts\python.exe -m goal_loop_tool demo` | Attempt 실패와 Goal 실패의 분리, Goal verified/Aim open |
| 검증 미충족 | `.\.venv\Scripts\python.exe -m goal_loop_tool not-satisfied` | `not_satisfied → active`, 같은 v1 보완 실행 |
| 판정 불충분 | `.\.venv\Scripts\python.exe -m goal_loop_tool inconclusive` | `inconclusive`은 `verifying` 유지 |
| Contract 버전 | `.\.venv\Scripts\python.exe -m goal_loop_tool contract-revision` | 늦은 v1 사실은 v2 성공으로 전용 불가 |
| Evidence 정정 | `.\.venv\Scripts\python.exe -m goal_loop_tool evidence-invalidation` | `verified` 이력 보존, Verdict 무효화, 의존 Goal 차단 |
| 실행 허가 | `.\.venv\Scripts\python.exe -m goal_loop_tool execution-admission` | Authority·Hold·Aim pause의 독립 Admission 검사 |
| Aim 평가 | `.\.venv\Scripts\python.exe -m goal_loop_tool aim-evaluation` | 명시적 Evaluation만 Aim achieved |

전체 회귀 검사는 다음 명령으로 실행한다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 9. 명시적으로 다음 단계로 미룬 것

- 다중 process 동시 쓰기 정책과 분산 conflict resolution
- Runtime 요청/취소/늦은 결과의 durable adapter outbox 계약
- AI_ORCHESTRATION adapter와 AO 용어의 통합·개명
- Goal Tree, execution dependency graph, Frame, Gap, Hypothesis, Lesson의 저장·전이
- Attempt `cancelled`·`unknown`, Goal `stopped`, Proposal `closed`의 완전한 전이
- invalidated terminal Goal에서 파생한 후속 재검증 Proposal의 provenance 모델
- Authority Policy, 위임, scope 계층, 외부 강제 안전 게이트
- Aim의 `abandoned`·`superseded`, Outcome/Inquiry 전용 평가 기준
