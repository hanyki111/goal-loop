"""Korean, step-by-step demonstration for the first Goal Loop Tool slice."""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import uuid4

from .model import AttemptOutcome, GoalIdentity, VerdictResult, VerdictValidity
from .activity import activity_cards
from .repository import SqliteEventRepository
from .service import ContractDraft, GoalLoop, GoalLoopError
from .local_file_runtime import LocalFileRuntime


def _step(number: int, title: str, detail: str) -> None:
    print(f"\n[{number}] {title}\n{detail}")


def run_demo(output: Path) -> None:
    loop = GoalLoop()
    runtime = LocalFileRuntime()
    _step(1, "Aim 생성", "Intent를 해석해 '검증 가능한 텍스트 산출물을 만든다' Aim을 open 상태로 만듭니다.")
    aim = loop.create_aim("검증 가능한 텍스트 산출물을 만든다.")
    proposal = loop.create_proposal(aim.id, "hello.txt를 만든다.")
    identity = GoalIdentity(aim.id, "hello.txt에 지정한 텍스트를 만든다.", "산출물을 생성·검증한다.")
    contract = ContractDraft("로컬 artifacts", "내용이 hello-goal-loop", "다른 verifier Actor가 내용을 직접 확인")
    _step(2, "Proposal 원자 수락", "Proposal, Goal, 불변 Contract v1을 한 논리 작업으로 생성합니다.")
    goal = loop.accept_proposal(proposal.id, identity, contract, idempotency_key="cli-demo")
    print(f"  결과: {proposal.id}=accepted, {goal.id}=committed, Contract v1 생성")
    first = loop.start_attempt(goal.id, 1, "executor-agent", "파일 작성")
    _step(3, "첫 Attempt 실패", "실행 실패는 Goal 실패가 아닙니다. Goal은 active로 유지됩니다.")
    failed_result = runtime.write_new_text(output.parent / "__missing_parent__" / output.name, "hello-goal-loop")
    loop.complete_attempt(first.id, AttemptOutcome.SUCCEEDED if failed_result.succeeded else AttemptOutcome.FAILED)
    if failed_result.succeeded:
        raise RuntimeError("첫 Runtime 작업은 실패 시나리오여야 합니다.")
    evidence = loop.record_evidence(first.id, "executor-agent", failed_result.observation)
    print(f"  실제 Runtime: {failed_result.observation}")
    print(f"  결과: {first.id}=failed, {evidence.id} 기록, Goal={goal.phase}")
    decision = loop.decide_same_contract_retry(goal.id, first.id, "policy-admin", "경로를 수정해 같은 Contract로 재시도")
    _step(4, "같은 Contract 재시도 결정", "성공 기준·범위·검증 방식은 바뀌지 않았으므로 v2가 아닌 v1을 유지합니다.")
    print(f"  결과: {decision.id}={decision.kind}, 현재 Contract=v{goal.current_contract_version}")
    second = loop.start_attempt(goal.id, 1, "executor-agent", "파일 재작성", retry_decision_id=decision.id)
    if output.exists():
        raise FileExistsError(f"기존 파일을 덮어쓰지 않습니다: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    result = runtime.write_new_text(output, "hello-goal-loop")
    loop.complete_attempt(second.id, AttemptOutcome.SUCCEEDED if result.succeeded else AttemptOutcome.FAILED)
    if not result.succeeded:
        raise RuntimeError(result.observation)
    proof = loop.record_evidence(second.id, "verifier-agent", f"{result.observation}; verifier 읽기: {runtime.read_text(output)}")
    _step(5, "두 번째 Attempt 성공", "Runtime 성공은 Goal 성공이 아닙니다. 독립 검증 단계로 이동합니다.")
    loop.begin_verification(goal.id, second.id)
    print(f"  실제 Runtime: {result.observation}")
    print(f"  verifier 실제 읽기: {runtime.read_text(output)}")
    print(f"  결과: {second.id}=succeeded, Goal={goal.phase}")
    verdict = loop.submit_verdict(goal.id, second.id, "verifier-agent", VerdictResult.SATISFIED, (proof.id,))
    _step(6, "독립 검증 Verdict", "실행자와 다른 verifier Actor가 Evidence를 인용해 satisfied를 확정합니다.")
    print(f"  결과: {verdict.id}=satisfied, Goal={goal.phase}, Aim={aim.phase}")
    _step(7, "핵심 확인", "Goal은 verified지만 Aim은 자동 achieved가 아닙니다. Aim은 별도 Evaluation이 필요합니다.")


def run_verdict_demo(result: VerdictResult) -> None:
    """Show the two non-success verification paths without mock runtime work."""
    loop = GoalLoop()
    aim = loop.create_aim("산출물이 성공 기준을 만족하는지 판단한다.")
    proposal = loop.create_proposal(aim.id, "검증할 산출물을 준비한다.")
    identity = GoalIdentity(aim.id, "검증 대상 산출물을 준비한다.", "산출물을 생성·검증한다.")
    contract = ContractDraft("개념 검증", "정해진 기준을 만족", "별도 verifier Actor가 Evidence를 평가")
    goal = loop.accept_proposal(proposal.id, identity, contract, idempotency_key=f"cli-{result.value}")

    _step(1, "Goal과 Attempt 준비", "같은 Contract v1 아래 실행 성공과 검증 판정은 서로 다른 사실임을 확인합니다.")
    attempt = loop.start_attempt(goal.id, 1, "executor-agent", "검증 대상 산출물 준비")
    loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
    evidence = loop.record_evidence(attempt.id, "executor-agent", "산출물이 준비되었다는 실행 기록")
    loop.begin_verification(goal.id, attempt.id)
    print(f"  결과: {attempt.id}=succeeded, Goal={goal.phase}, Evidence={evidence.id}")

    if result is VerdictResult.NOT_SATISFIED:
        _step(2, "검증 미충족", "실행은 성공했지만 Evidence가 성공 기준을 충족하지 못했다고 verifier가 판정합니다.")
        verdict = loop.submit_verdict(goal.id, attempt.id, "verifier-agent", result, (evidence.id,))
        print(f"  결과: {verdict.id}=not_satisfied, Goal={goal.phase}")
        _step(3, "같은 Contract 보완 실행", "Goal은 failed나 stopped가 되지 않고 active로 돌아갑니다. 같은 v1로 보완 Attempt를 시작할 수 있습니다.")
        repair = loop.start_attempt(goal.id, 1, "executor-agent", "성공 기준을 충족하도록 보완")
        print(f"  결과: {repair.id}=running, Contract=v{repair.contract_version}, Goal={goal.phase}")
        return

    _step(2, "판정 불충분", "Evidence만으로는 충족·미충족 어느 쪽도 확정할 수 없다고 verifier가 판정합니다.")
    verdict = loop.submit_verdict(goal.id, attempt.id, "verifier-agent", result, (evidence.id,))
    print(f"  결과: {verdict.id}=inconclusive, Goal={goal.phase}")
    _step(3, "추가 Evidence 수집", "Goal은 verifying에 머뭅니다. 실행을 되돌리거나 성공으로 처리하지 않고, 필요한 관측을 더 수집합니다.")
    extra = loop.record_evidence(attempt.id, "verifier-agent", "추가 측정이 필요하다는 검증 기록")
    print(f"  결과: {extra.id} 기록, Goal={goal.phase}")

def run_contract_revision_demo() -> None:
    """Show why late results belong to their original contract version."""
    loop = GoalLoop()
    aim = loop.create_aim("정확한 텍스트 산출물을 만든다.")
    proposal = loop.create_proposal(aim.id, "텍스트 산출물을 만든다.")
    identity = GoalIdentity(aim.id, "정확한 텍스트 산출물을 만든다.", "산출물을 생성·검증한다.")
    v1_draft = ContractDraft("로컬 artifacts", "내용이 hello-goal-loop", "별도 verifier Actor가 내용을 직접 확인")
    goal = loop.accept_proposal(proposal.id, identity, v1_draft, idempotency_key="cli-contract-revision")

    _step(1, "Contract v1 실행 시작", "Attempt는 시작 당시의 Contract 버전에 묶입니다. 아직 실행 중인 v1 Attempt를 만듭니다.")
    v1_attempt = loop.start_attempt(goal.id, 1, "executor-agent", "v1 텍스트 작성")
    print(f"  결과: {v1_attempt.id}=running, Attempt Contract=v{v1_attempt.contract_version}, Goal 현재 Contract=v{goal.current_contract_version}")

    _step(2, "물질적 계약 변경", "성공 기준이 달라지면 같은 Goal이더라도 새 Contract 버전이 필요합니다. v1은 덮어쓰지 않습니다.")
    v2 = loop.revise_contract(
        goal.id,
        ContractDraft("로컬 artifacts", "내용이 hello-goal-loop-v2", "별도 verifier Actor가 내용을 직접 확인"),
        decided_by_actor_id="policy-admin",
        reason="성공 기준의 텍스트가 변경됨",
    )
    print(f"  결과: Contract v{v2.version} 생성, Goal 현재 Contract=v{goal.current_contract_version}")

    _step(3, "늦게 도착한 v1 결과", "이미 시작한 v1 Attempt는 완료될 수 있습니다. 그러나 그 결과는 v1 사실로만 기록됩니다.")
    loop.complete_attempt(v1_attempt.id, AttemptOutcome.SUCCEEDED)
    v1_evidence = loop.record_evidence(v1_attempt.id, "executor-agent", "v1 산출물 내용: hello-goal-loop")
    print(f"  결과: {v1_attempt.id}=succeeded, {v1_evidence.id}=Contract v{v1_evidence.contract_version}")

    _step(4, "v2 성공으로의 자동 전용 차단", "현재 Goal은 v2를 요구합니다. 따라서 v1 Attempt는 현재 Goal의 검증을 시작할 수 없습니다.")
    try:
        loop.begin_verification(goal.id, v1_attempt.id)
    except GoalLoopError as error:
        print(f"  차단됨: {error}")

    _step(5, "v2 Attempt로 새 검증", "v2 성공은 v2 Contract로 시작한 Attempt와 그 Evidence로만 판정할 수 있습니다.")
    v2_attempt = loop.start_attempt(goal.id, 2, "executor-agent", "v2 텍스트 작성")
    loop.complete_attempt(v2_attempt.id, AttemptOutcome.SUCCEEDED)
    v2_evidence = loop.record_evidence(v2_attempt.id, "verifier-agent", "v2 산출물 내용: hello-goal-loop-v2")
    loop.begin_verification(goal.id, v2_attempt.id)
    verdict = loop.submit_verdict(goal.id, v2_attempt.id, "verifier-agent", VerdictResult.SATISFIED, (v2_evidence.id,))
    print(f"  결과: {verdict.id}=satisfied, Verdict Contract=v{verdict.contract_version}, Goal={goal.phase}")

def run_evidence_invalidation_demo() -> None:
    """Show that invalidating a verdict does not erase verified history."""
    loop = GoalLoop()
    aim = loop.create_aim("검증된 산출물을 후속 작업에 사용한다.")
    source_proposal = loop.create_proposal(aim.id, "원본 산출물을 만든다.")
    identity = GoalIdentity(aim.id, "원본 산출물을 만든다.", "산출물을 생성·검증한다.")
    contract = ContractDraft("개념 검증", "원본 산출물이 기준을 충족", "별도 verifier Actor가 Evidence를 평가")
    source = loop.accept_proposal(source_proposal.id, identity, contract, idempotency_key="cli-invalidation-source")

    _step(1, "검증 완료 이력 생성", "먼저 원본 Goal을 정상적으로 verified로 만듭니다. 이 단계는 과거의 판단 기록입니다.")
    attempt = loop.start_attempt(source.id, 1, "executor-agent", "원본 산출물 작성")
    loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
    evidence = loop.record_evidence(attempt.id, "verifier-agent", "측정 결과: 원본 산출물이 기준을 충족")
    loop.begin_verification(source.id, attempt.id)
    verdict = loop.submit_verdict(source.id, attempt.id, "verifier-agent", VerdictResult.SATISFIED, (evidence.id,))
    print(f"  결과: Source Goal={source.phase}, Verdict={verdict.id}, Validity={loop.verdict_validity[verdict.id]}")

    _step(2, "후속 Goal의 의존성 연결", "후속 Goal은 원본의 '유효한 검증 판정'을 전제로 새 Attempt를 시작합니다.")
    dependent_proposal = loop.create_proposal(aim.id, "검증된 원본 산출물을 사용한다.")
    dependent = loop.accept_proposal(
        dependent_proposal.id,
        GoalIdentity(aim.id, "검증된 원본 산출물을 사용한다.", "검증된 산출물을 소비한다."),
        contract,
        idempotency_key="cli-invalidation-dependent",
    )
    loop.add_verdict_dependency(dependent.id, verdict.id)
    print(f"  결과: {dependent.id}가 {verdict.id}에 의존")

    _step(3, "Evidence 정정과 영향 검토", "Evidence의 측정 출처가 잘못되었다면, 과거 Goal을 지우지 않고 Verdict의 현재 유효성을 under_review로 바꿉니다.")
    correction = loop.correct_evidence(evidence.id, "auditor-agent", "측정 출처가 잘못되어 결과를 신뢰할 수 없음")
    review = loop.review_verdict_after_correction(verdict.id, correction.id)
    print(f"  결과: Source Goal={source.phase}, Verdict Validity={loop.verdict_validity[verdict.id]}, 영향 Goal={review.affected_goal_ids}")
    try:
        loop.start_attempt(dependent.id, 1, "executor-agent", "원본 산출물 사용")
    except GoalLoopError as error:
        print(f"  후속 실행 차단: {error}")

    _step(4, "Verdict 무효화", "검토 결과 판정이 잘못되었다면 invalidated로 확정합니다. 그래도 Source Goal의 과거 verified 이력은 남습니다.")
    loop.invalidate_verdict(verdict.id)
    print(f"  결과: Source Goal={source.phase}, Verdict Validity={loop.verdict_validity[verdict.id]}")
    assert loop.verdict_validity[verdict.id] is VerdictValidity.INVALIDATED

def run_execution_admission_demo() -> None:
    """Show that execution admission is checked for every new Attempt."""
    loop = GoalLoop(enforce_authority=True)
    aim = loop.create_aim("권한 있는 Actor만 산출물을 실행한다.")
    proposal = loop.create_proposal(aim.id, "제어된 산출물을 만든다.")
    goal = loop.accept_proposal(
        proposal.id,
        GoalIdentity(aim.id, "제어된 산출물을 만든다.", "권한을 확인한 뒤 실행한다."),
        ContractDraft("개념 검증", "산출물이 생성됨", "실행 기록을 확인"),
        idempotency_key="cli-execution-admission",
    )

    _step(1, "권한 없는 실행 요청", "Actor 형식이 agent라는 사실만으로 실행 권한이 생기지 않습니다. 실행 직전에 Grant를 확인합니다.")
    try:
        loop.start_attempt(goal.id, 1, "executor-agent", "산출물 생성")
    except GoalLoopError as error:
        print(f"  차단됨: {error}")

    _step(2, "Authority Grant 부여", "정책 관리자가 이 Actor에게 이 Goal의 execute 권한을 부여합니다.")
    loop.grant_execute("executor-agent", goal.id)
    print("  결과: executor-agent에 Goal 범위 execute Grant 부여")

    _step(3, "해소되지 않은 Hold", "권한이 있어도 Hold는 별도의 차단 사실입니다. Hold 부재와 권한 검사는 서로 대체되지 않습니다.")
    loop.add_goal_hold(goal.id)
    try:
        loop.start_attempt(goal.id, 1, "executor-agent", "산출물 생성")
    except GoalLoopError as error:
        print(f"  차단됨: {error}")

    _step(4, "Hold 해소 뒤 실행 허가", "Grant가 유효하고 Hold도 해소된 현재 문맥에서만 새 Attempt가 허용됩니다.")
    loop.clear_goal_hold(goal.id)
    attempt = loop.start_attempt(goal.id, 1, "executor-agent", "산출물 생성")
    loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
    print(f"  결과: {attempt.id}=succeeded, Goal={goal.phase}")

    _step(5, "Aim pause", "Aim pause는 Goal 생명주기를 바꾸지 않지만, 그 Aim 아래의 새 Attempt 시작은 다시 차단합니다.")
    loop.pause_aim(aim.id)
    try:
        loop.start_attempt(goal.id, 1, "executor-agent", "추가 산출물 생성")
    except GoalLoopError as error:
        print(f"  차단됨: {error}")

def run_aim_evaluation_demo() -> None:
    """Show that a verified Goal needs a separate Aim evaluation."""
    loop = GoalLoop()
    aim = loop.create_aim("검증 가능한 텍스트 산출물을 만든다.")
    proposal = loop.create_proposal(aim.id, "텍스트 산출물을 만들고 검증한다.")
    goal = loop.accept_proposal(
        proposal.id,
        GoalIdentity(aim.id, "검증 가능한 텍스트 산출물을 만든다.", "산출물을 생성·검증한다."),
        ContractDraft("개념 검증", "텍스트 내용이 기준을 충족", "별도 verifier Actor가 내용을 확인"),
        idempotency_key="cli-aim-evaluation",
    )

    _step(1, "Goal 검증 완료", "Goal Contract의 성공 기준은 충족시켜 Goal을 verified로 만듭니다. 이 사실만으로 Aim은 종결되지 않습니다.")
    attempt = loop.start_attempt(goal.id, 1, "executor-agent", "텍스트 산출물 작성")
    loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
    evidence = loop.record_evidence(attempt.id, "verifier-agent", "텍스트 내용이 Goal Contract의 기준을 충족")
    loop.begin_verification(goal.id, attempt.id)
    verdict = loop.submit_verdict(goal.id, attempt.id, "verifier-agent", VerdictResult.SATISFIED, (evidence.id,))
    print(f"  결과: Goal={goal.phase}, Verdict={verdict.id}=satisfied, Aim={aim.phase}")

    _step(2, "Aim Evaluation", "평가자는 Aim 자체의 의도와 현재 유효한 Verdict를 함께 검토합니다. 이 명시적 판단이 있어야 Aim을 achieved로 바꿉니다.")
    evaluation = loop.evaluate_aim_as_achieved(
        aim.id,
        "aim-evaluator",
        (verdict.id,),
        "이 Aim에 필요한 산출물과 유효한 검증 판정이 확보되었다.",
    )
    print(f"  결과: {evaluation.id} 기록, 근거 Verdict={evaluation.verdict_ids}, Aim={aim.phase}")

    _step(3, "핵심 확인", "Goal verified는 Goal Contract가 충족되었다는 뜻입니다. Aim achieved는 별도 Evaluation이 그 Goal의 기여가 Aim을 충분히 달성했다고 판단한 뜻입니다.")

def main() -> None:
    parser = argparse.ArgumentParser(description="Goal Loop Tool 한국어 데모")
    parser.add_argument("command", choices=("demo", "not-satisfied", "inconclusive", "contract-revision", "evidence-invalidation", "execution-admission", "aim-evaluation", "activity"), help="실행할 데모 또는 활동 로그 조회")
    parser.add_argument("--database", type=Path, help="activity 조회용 SQLite 파일")
    parser.add_argument("--output", type=Path, help="새로 만들 실제 출력 파일")
    args = parser.parse_args()
    if args.command == "activity":
        if args.database is None:
            parser.error("activity에는 --database <sqlite 경로>가 필요합니다.")
        for card in activity_cards(SqliteEventRepository(args.database)):
            print(f"[{card.recorded_at}] {card.title} ({card.status})")
            print(f"  {card.detail}")
            if card.error_code:
                print(f"  error_code={card.error_code}")
        return
    if args.command == "not-satisfied":
        run_verdict_demo(VerdictResult.NOT_SATISFIED)
    elif args.command == "inconclusive":
        run_verdict_demo(VerdictResult.INCONCLUSIVE)
    elif args.command == "contract-revision":
        run_contract_revision_demo()
    elif args.command == "evidence-invalidation":
        run_evidence_invalidation_demo()
    elif args.command == "execution-admission":
        run_execution_admission_demo()
    elif args.command == "aim-evaluation":
        run_aim_evaluation_demo()
    else:
        output = args.output or Path("artifacts") / f"hello-goal-loop-demo-{uuid4().hex[:8]}.txt"
        try:
            run_demo(output)
        except FileExistsError as error:
            parser.error(f"출력 파일이 이미 존재합니다. 다른 --output 경로를 지정하세요. ({error})")







