import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { actionTitle, friendlyError, AttachmentList, ReferenceSection, AimInspector, AimNavigator, AttemptTimeline, GoalInspector, NextActionSummary, ProposalInspector, RuntimeScreen, scopedNextActions, verdictRelations } from './Workspace';

describe('Goal Loop workspace components', () => {
  afterEach(cleanup);

  it('maps lifecycle command keys to Korean user actions', () => {
    expect(actionTitle('accept_proposal')).toBe('이 접근안을 과제로 확정');
    expect(actionTitle('start_retry_attempt')).toBe('재시도 시작');
    expect(actionTitle('resolve_verdict_review')).toBe('검증 판단 검토 종결');
    expect(actionTitle('unrecognized')).toBe('다음 행동');
  });

  it('shows a Proposal closed as criteria as the Aim criteria, not as an open approach', () => {
    const onSelect = vi.fn();
    const data = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [
        { id: 'proposal-1', aim_id: 'aim-1', direction: '무엇을 이루면 달성인가\n(1) 동작한다',status: 'closed', closure_kind: 'criteria', closed_by_actor_id: 'claude', closure_reason: '달성 기준', accepted_goal_id: null },
        { id: 'proposal-2', aim_id: 'aim-1', direction: '검토 중 접근', status: 'open', accepted_goal_id: null },
        { id: 'proposal-3', aim_id: 'aim-1', direction: '확정한 접근', status: 'accepted', accepted_goal_id: 'goal-1' },
      ],
      goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'committed', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }],
      attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    const aim = render(<AimInspector aim={data.aims[0]} data={data} busy={false} onSelect={onSelect} submit={() => vi.fn()} />);
    expect(screen.getByText(/검토 중 1개/)).toBeTruthy();
    const criteria = screen.getByRole('region', { name: '달성 기준' });
    fireEvent.click(criteria.querySelector('button')!);
    expect(onSelect).toHaveBeenCalledWith({ kind: 'proposal', id: 'proposal-1' });
    fireEvent.click(screen.getByRole('tab', { name: /1\. 접근안/ }));
    expect(screen.getAllByRole('button', { name: /무엇을 이루면 달성인가/ }).some(row => row.textContent?.includes('달성 기준'))).toBe(true);
    aim.unmount();
    const inspector = render(<ProposalInspector proposal={data.proposals[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    expect(screen.getByText(/접근안 · 목표의 달성 기준/)).toBeTruthy();
    expect(screen.getByText(/목표의 달성 기준으로 채택/)).toBeTruthy();
    inspector.unmount();
    render(<ProposalInspector proposal={data.proposals[1]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    expect(screen.getByRole('option', { name: /달성 기준 — / })).toBeTruthy();
  });

  it('groups Aim records and selects an Aim without mixing in Goal records', () => {
    const onSelect = vi.fn();
    render(<AimNavigator
      aims={[
        { id: 'aim-1', description: 'Open Aim', phase: 'open', paused: false, currently_achieved: false },
        { id: 'aim-2', description: 'Completed Aim', phase: 'achieved', paused: false, currently_achieved: true },
      ]}
      goals={[{ id: 'goal-1', aim_id: 'aim-1', phase: 'accepted', target: 'A Goal', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }]}
      selected={null}
      onSelect={onSelect}
    />);
    fireEvent.click(screen.getByRole('button', { name: /Open Aim/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'aim', id: 'aim-1' });
    expect(screen.getByText('과제 1개')).toBeTruthy();
    expect(screen.getByText('Completed Aim')).toBeTruthy();
  });

  it('summarizes global next actions when nothing is selected and opens the action context', () => {
    const data = { aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'committed', target: '첫 안내 제공', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5 };
    const start = { kind: 'start_attempt', summary: 'start', subject: { id: 'goal-1', kind: 'goal' }, command_argv: ['goal-loop', 'attempt-start', 'goal-1'] };
    const onSelect = vi.fn();
    const onOpenInbox = vi.fn();
    render(<NextActionSummary actions={[start]} data={data} onSelect={onSelect} onOpenInbox={onOpenInbox} />);
    expect(screen.getByText('과제 — 첫 안내 제공')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /실행 시작/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'goal', id: 'goal-1' });
    fireEvent.click(screen.getByRole('button', { name: /전체 작업함에서 모두 보기/ }));
    expect(onOpenInbox).toHaveBeenCalledOnce();
  });

  it('explains how to start when there are no next actions', () => {
    const data = { aims: [], proposals: [], goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5 };
    render(<NextActionSummary actions={[]} data={data} onSelect={vi.fn()} />);
    expect(screen.getByText(/지금 기다리는 작업이 없습니다/)).toBeTruthy();
  });

  it('scopes Aim evaluation to the Aim even when it cites a Goal Verdict', () => {
    const data = { aims: [{ id: 'aim-1', description: 'Open', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'verified', target: 'Done task', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }], attempts: [], verdicts: [{ id: 'verdict-1', goal_id: 'goal-1', attempt_id: 'attempt-1', result: 'satisfied', validity: 'valid' }], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5 } as never;
    const evaluate = { kind: 'evaluate_aim', summary: 'evaluate', subject: { id: 'aim-1', kind: 'aim' }, command_argv: ['goal-loop', 'aim-evaluate', 'aim-1', '--verdict', 'verdict-1'] };
    expect(scopedNextActions(data, [evaluate], { kind: 'goal', id: 'goal-1' })).toEqual([]);
    expect(scopedNextActions(data, [evaluate], { kind: 'aim', id: 'aim-1' })).toEqual([evaluate]);
  });

  it('shows task criteria from the stored contract and hides hold and new links once verified', () => {
    const goal = (phase: string) => ({ id: 'goal-1', aim_id: 'aim-1', phase, target: '안내 카드 추가', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: { operational_scope: '시작 화면', success_criterion: '다음 행동을 찾는다', verification_specification: '수동 QA' } });
    const data = (phase: string) => ({ aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [goal(phase), { ...goal('committed'), id: 'goal-2', target: '다른 과제' }], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5 });
    const props = { activity: [], admission: {}, busy: false, onSelect: vi.fn(), submit: () => vi.fn(), run: async () => undefined, onArchive: vi.fn(), actions: [] };
    const active = render(<GoalInspector goal={goal('active')} data={data('active')} {...props} />);
    expect(screen.getByText('다음 행동을 찾는다')).toBeTruthy();
    expect(screen.getByRole('button', { name: '실행 보류 추가' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '선행 과제 연결' })).toBeTruthy();
    active.unmount();
    render(<GoalInspector goal={goal('verified')} data={data('verified')} {...props} />);
    expect(screen.queryByRole('button', { name: '실행 보류 추가' })).toBeNull();
    expect(screen.queryByRole('button', { name: '선행 과제 연결' })).toBeNull();
    expect(screen.queryByRole('button', { name: '기준 바꾸기' })).toBeNull();
  });

  it('offers only a new approach once the accepted approach has a verified task', () => {
    const onSelect = vi.fn();
    const data = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '관찰 흐름을 단순화한다', status: 'accepted', accepted_goal_id: 'goal-1' }],
      goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'verified', target: '안내 카드 추가', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }],
      attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    render(<ProposalInspector proposal={data.proposals[0]} data={data} busy={false} onSelect={onSelect} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    expect(screen.queryByRole('button', { name: /과제의 완료 기준 바꾸기/ })).toBeNull();
    expect(screen.queryByText('확정된 과제 열기 →')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /다른 접근안 추가/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'aim', id: 'aim-1', focus: 'add-proposal' });
  });

  it('shows an achieved Aim evaluation with its conclusion and marks every stage done', () => {
    const data = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'achieved', paused: false, currently_achieved: true }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '관찰 흐름을 단순화한다', status: 'accepted', accepted_goal_id: 'goal-1' }],
      goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'verified', target: '안내 카드 추가', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }],
      attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], schema_version: 5,
      aim_evaluations: [{ id: 'aim-evaluation-1', aim_id: 'aim-1', validity: 'valid', conclusion: '안내 카드로 목표를 이루었다', evaluator_actor_id: 'owner', verdict_ids: ['verdict-1'] }],
    };
    render(<AimInspector aim={data.aims[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} />);
    expect(screen.getByText('안내 카드로 목표를 이루었다')).toBeTruthy();
    expect(screen.getByText('유효')).toBeTruthy();
    expect(screen.queryByText(/지금 단계/)).toBeNull();
  });

  it('offers a new Aim from an achieved Aim and marks open approaches as still under review', () => {
    const onCreateAim = vi.fn();
    const achieved = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'achieved', paused: false, currently_achieved: true }],
      proposals: [], goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    const view = render(<AimInspector aim={achieved.aims[0]} data={achieved} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} onCreateAim={onCreateAim} />);
    fireEvent.click(screen.getByRole('button', { name: /새 목표 만들기/ }));
    expect(onCreateAim).toHaveBeenCalledOnce();
    view.unmount();
    const mixed = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '확정한 접근', status: 'accepted', accepted_goal_id: 'goal-1' }, { id: 'proposal-2', aim_id: 'aim-1', direction: '검토 중 접근', status: 'open', accepted_goal_id: null }],
      goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'committed', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }],
      attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    render(<AimInspector aim={mixed.aims[0]} data={mixed} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} />);
    expect(screen.getByRole('tab', { name: /1\. 접근안/ }).className).toContain('pending');
    expect(screen.getByText(/검토 중 1개/)).toBeTruthy();
  });

  it('turns a Core traceback into a short Korean reason and keeps the raw text as detail', () => {
    const raw = ['Traceback (most recent call last):', '  File "x.py", line 1', 'goal_loop_tool.service.GoalLoopError: The executing Actor cannot issue the independent final Verdict.'].join('\n');
    const result = friendlyError(raw);
    expect(result.message).toContain('실행한 사람은 같은 과제의 검증 판단을 내릴 수 없습니다');
    expect(result.detail).toBe(raw);
    expect(friendlyError('GoalLoopError: Something new.').message).toContain('Something new.');
  });

  it('lets a run started under an older contract be tidied without implying it verifies the new one', () => {
    const run = vi.fn(async () => undefined);
    render(<AttemptTimeline
      attempts={[{ id: 'attempt-1', goal_id: 'goal-1', outcome: 'running', operation: 'v1 run', executor: 'user', contract_version: 1 }]}
      evidence={[]}
      verdicts={[]}
      busy={false}
      onSelect={vi.fn()}
      run={run}
      currentVersion={2}
    />);
    expect(screen.getByText(/v2 검증에는 쓰이지 않습니다/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '실패로 정리' }));
    expect(run).toHaveBeenCalledWith('attempt-complete', ['attempt-1', 'failed'], expect.stringContaining('이전 기준(v1)'));
  });

  it('offers three choices after a failed run and retries in one form', async () => {
    const onRetry = vi.fn(async () => true);
    const onSelect = vi.fn();
    const goal = { id: 'goal-1', aim_id: 'aim-1', phase: 'active', target: '과제', responsibility: 'produce', current_contract_version: 2, verifying_attempt_id: null, held: false, contract: {} };
    const data = { aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [goal], attempts: [{ id: 'attempt-1', goal_id: 'goal-1', outcome: 'failed', operation: 'op', executor: 'user', contract_version: 2 }], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 6 };
    const decide = { kind: 'decide_retry', summary: 'retry', subject: { id: 'goal-1', kind: 'goal' }, command_argv: ['goal-loop', 'retry-decide', 'goal-1', 'attempt-1'] };
    render(<GoalInspector goal={goal} data={data} activity={[]} admission={{}} busy={false} onSelect={onSelect} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[decide]} onRetry={onRetry} />);
    fireEvent.click(screen.getByRole('button', { name: /기준을 바꾸고 다시 하기/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'goal', id: 'goal-1', focus: 'contract' });
    fireEvent.click(screen.getByRole('button', { name: /다른 접근안 시도/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'aim', id: 'aim-1', focus: 'add-proposal' });
    fireEvent.click(screen.getByRole('button', { name: /이 과제 그만두기/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'goal', id: 'goal-1', focus: 'stop' });
    fireEvent.change(screen.getByLabelText('실행자'), { target: { value: 'me' } });
    fireEvent.change(screen.getByLabelText('왜 다시 하나요?'), { target: { value: '순서 변경' } });
    fireEvent.change(screen.getByLabelText('이번에 할 작업'), { target: { value: '다시 실행' } });
    fireEvent.click(screen.getByRole('button', { name: '재시도 시작' }));
    expect(onRetry).toHaveBeenCalledWith('goal-1', 'attempt-1', 2, 'me', '순서 변경', '다시 실행');
  });

  it('keeps hidden children out of the Aim counts and restores them in place', () => {
    const onRestore = vi.fn();
    const data = {
      aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '보이는 접근안', status: 'open', accepted_goal_id: null }, { id: 'proposal-2', aim_id: 'aim-1', direction: '숨긴 접근안', status: 'open', accepted_goal_id: null }],
      goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 7,
    };
    render(<AimInspector aim={data.aims[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} hiddenIds={new Set(['proposal-2'])} onRestore={onRestore} />);
    expect(screen.getByRole('tab', { name: /1\. 접근안/ }).textContent).toContain('1개');
    fireEvent.click(screen.getByRole('button', { name: '다시 보기' }));
    expect(onRestore).toHaveBeenCalledWith('proposal-2');
  });

  it('shows a waiting card instead of a raw CLI command while a prerequisite is unverified, and never offers an ancestor as prerequisite', () => {
    const onSelect = vi.fn();
    const base = { aim_id: 'aim-1', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} };
    const goals = [{ ...base, id: 'goal-1', phase: 'committed', target: '선행' }, { ...base, id: 'goal-2', phase: 'committed', target: '후행' }, { ...base, id: 'goal-3', phase: 'committed', target: '다른 과제' }];
    const data = { aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals, attempts: [], verdicts: [], evidence: [], aim_evaluations: [], schema_version: 7,
      nodes: [{ id: 'goal-2', kind: 'goal', label: '후행', phase: 'committed', aim_id: 'aim-1', dependency_blocked: true }],
      edges: [{ id: 'parent:goal-1:goal-2', source: 'goal-1', target: 'goal-2', kind: 'parent' }, { id: 'dependency:goal-1:goal-2', source: 'goal-1', target: 'goal-2', kind: 'dependency' }] };
    const wait = { kind: 'inspect_goal_dependency', summary: 'wait', subject: { id: 'goal-2', kind: 'goal' }, command_argv: ['goal-loop', '--database', 'C:\very\long\path.sqlite3', 'goal-graph'] };
    const view = render(<GoalInspector goal={goals[1]} data={data as never} activity={[]} admission={{}} busy={false} onSelect={onSelect} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[wait]} />);
    expect(screen.getByText('선행 과제를 기다리는 중입니다')).toBeTruthy();
    expect(view.container.querySelector('.dependency-card')?.textContent).not.toContain('goal-graph');
    fireEvent.click(screen.getByRole('button', { name: /선행 →/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'goal', id: 'goal-1' });
    const options = Array.from(view.container.querySelectorAll('select[name="other"] option')).map(option => option.textContent);
    expect(options).toContain('다른 과제');
    expect(options).not.toContain('선행');
  });

  it('lists hidden items at the bottom of the navigator with a restore action', () => {
    const onRestore = vi.fn();
    render(<AimNavigator aims={[]} goals={[]} selected={null} onSelect={vi.fn()} hidden={[{ id: 'goal-9', label: '과제 · 숨긴 과제' }]} onRestore={onRestore} />);
    expect(screen.getByText('숨긴 항목 1개')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '다시 보기' }));
    expect(onRestore).toHaveBeenCalledWith('goal-9');
  });

  it('maps a Verdict review to the reviewed task and shows the correction on its evidence', () => {
    const goal = { id: 'goal-1', aim_id: 'aim-1', phase: 'verified', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} };
    const data = { aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [goal], nodes: [], edges: [], aim_evaluations: [], schema_version: 8,
      attempts: [{ id: 'attempt-1', goal_id: 'goal-1', outcome: 'succeeded', operation: 'op', executor: 'user', contract_version: 1 }],
      evidence: [{ id: 'evidence-1', goal_id: 'goal-1', attempt_id: 'attempt-1', observation: '원래 관찰', actor: 'observer' }],
      verdicts: [{ id: 'verdict-1', goal_id: 'goal-1', attempt_id: 'attempt-1', result: 'satisfied', validity: 'under_review', evidence_ids: ['evidence-1'], verifier_actor_id: 'reviewer' }],
      evidence_corrections: [{ id: 'correction-1', corrected_evidence_id: 'evidence-1', submitted_by_actor_id: 'observer', observation: '바로잡은 관찰' }],
      impact_reviews: [{ id: 'impact-review-1', trigger_verdict_id: 'verdict-1', correction_id: 'correction-1', affected_goal_ids: [], affected_aim_evaluation_ids: [] }] };
    const review = { kind: 'resolve_verdict_review', summary: 'review', subject: { id: 'impact-review-1', kind: 'impact_review' }, command_argv: ['goal-loop', 'verdict-review-resolve', 'impact-review-1'] };
    expect(scopedNextActions(data as never, [review], { kind: 'goal', id: 'goal-1' })).toEqual([review]);
    render(<GoalInspector goal={goal} data={data as never} activity={[]} admission={{}} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[review]} />);
    expect(screen.getByText(/검증 판단을 다시 확인해야 합니다/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '검토 결론 기록' })).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: /2\. 관찰 근거/ }));
    expect(screen.getByText(/정정: 바로잡은 관찰/)).toBeTruthy();
  });

  it('keeps a single place to clear an execution hold', () => {
    const goal = { id: 'goal-1', aim_id: 'aim-1', phase: 'committed', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: true, contract: {} };
    const data = { aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [goal], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 8 };
    const clear = { kind: 'clear_goal_hold', summary: 'clear', subject: { id: 'goal-1', kind: 'goal' }, command_argv: ['goal-loop', 'goal-hold-clear', 'goal-1'] };
    render(<GoalInspector goal={goal} data={data} activity={[]} admission={{}} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[clear]} />);
    expect(screen.getAllByRole('button', { name: '실행 보류 해제' })).toHaveLength(1);
    expect(screen.queryByText('실행 보류 상태라서 새 실행을 시작할 수 없습니다.')).toBeNull();
  });

  it('shows the proposer, offers closing an open approach, and explains a closed one', () => {
    const base = { aim_id: 'aim-1', accepted_goal_id: null };
    const data = { aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 9,
      proposals: [{ ...base, id: 'proposal-1', direction: '열린 접근안', status: 'open', proposed_by_actor_id: 'agent-a' }, { ...base, id: 'proposal-2', direction: '닫힌 접근안', status: 'closed', closure_kind: 'declined', closed_by_actor_id: 'owner', closure_reason: '다른 안을 골랐다' }] };
    const commands: string[] = [];
    const submit = (command: string) => { commands.push(command); return vi.fn(); };
    const open = render(<ProposalInspector proposal={data.proposals[0]} data={data} busy={false} onSelect={vi.fn()} submit={submit} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    expect(screen.getByText('agent-a')).toBeTruthy();
    expect(screen.getByText('과제로 만들지 않고 닫기')).toBeTruthy();
    expect(commands).toContain('proposal-close');
    open.unmount();
    render(<ProposalInspector proposal={data.proposals[1]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    expect(screen.getByText(/이 접근안은 닫혔습니다 · 채택 안 함/)).toBeTruthy();
    expect(screen.queryByText('과제로 만들지 않고 닫기')).toBeNull();
  });

  it('shows a stopped task with its reason and no further hold or stop controls', () => {
    const goal = { id: 'goal-1', aim_id: 'aim-1', phase: 'stopped', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, stopped_by_actor_id: 'owner', stop_reason: '요구가 바뀜', contract: {} };
    const data = { aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [goal], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 9 };
    render(<GoalInspector goal={goal} data={data} activity={[]} admission={{}} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    expect(screen.getByText('이 과제는 그만두었습니다')).toBeTruthy();
    expect(screen.getByText(/요구가 바뀜 — owner/)).toBeTruthy();
    expect(screen.queryByText('과제 그만두기')).toBeNull();
    expect(screen.queryByText(/실행 보류/)).toBeNull();
  });

  it('shows an abandoned Aim as finished, without management controls or a current stage', () => {
    const aim = { id: 'aim-1', description: '목표', phase: 'abandoned', paused: false, currently_achieved: false, abandoned_by_actor_id: 'owner', abandon_reason: '범위에서 제외' };
    const data = { aims: [aim], proposals: [], goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 9 };
    render(<AimInspector aim={aim} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} />);
    expect(screen.getByText('이 목표는 그만두었습니다')).toBeTruthy();
    expect(screen.queryByText('목표 관리')).toBeNull();
    expect(screen.queryByText(/지금 단계/)).toBeNull();
  });

  it('records a hold with a reason and shows it while held', () => {
    const goal = (held: boolean) => ({ id: 'goal-1', aim_id: 'aim-1', phase: 'committed', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held, hold_actor_id: held ? 'owner' : null, hold_reason: held ? '승인 대기' : null, contract: {} });
    const data = (held: boolean) => ({ aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [goal(held)], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 9 });
    const commands: string[] = [];
    const free = render(<GoalInspector goal={goal(false)} data={data(false)} activity={[]} admission={{}} busy={false} onSelect={vi.fn()} submit={(command: string) => { commands.push(command); return vi.fn(); }} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    expect(commands).toContain('goal-hold-add');
    expect(commands).toContain('goal-stop');
    free.unmount();
    render(<GoalInspector goal={goal(true)} data={data(true)} activity={[]} admission={{}} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    expect(screen.getByText(/사유: 승인 대기 — owner/)).toBeTruthy();
  });

  it('keeps a global new-Aim entry point in the goal navigator', () => {
    const onCreate = vi.fn();
    const view = render(<AimNavigator aims={[]} goals={[]} selected={null} onSelect={vi.fn()} onCreate={onCreate} />);
    fireEvent.click(view.container.querySelector('.navigator-create')!);
    expect(onCreate).toHaveBeenCalledOnce();
  });

  it('shows execution records in the timeline and leaves completion to the single next-action box', () => {
    render(<AttemptTimeline
      attempts={[{ id: 'attempt-1', goal_id: 'goal-1', outcome: 'succeeded', operation: 'Inspect the workspace', executor: 'user', contract_version: 1 }]}
      evidence={[{ id: 'evidence-1', goal_id: 'goal-1', attempt_id: 'attempt-1', observation: 'Observed UI', actor: 'observer' }]}
      verdicts={[{ id: 'verdict-1', goal_id: 'goal-1', attempt_id: 'attempt-1', result: 'satisfied', validity: 'valid', evidence_ids: ['evidence-1'], verifier_actor_id: 'reviewer' }]}
      busy={false}
      onSelect={vi.fn()}
      run={vi.fn(async () => undefined)}
    />);
    expect(screen.getByText('관찰 근거 1')).toBeTruthy();
    expect(screen.getByText('충족됨 · 유효')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '성공으로 기록' })).toBeNull();
  });

  it('labels Runtime requests as delivery state rather than goal completion', () => {
    render(<RuntimeScreen runtime={{ requests: [{ request_id: 'request-1', attempt_id: 'attempt-1', request_kind: 'codex_exec.v1', status: 'pending' }] }} />);
    expect(screen.getByText(/AI 에이전트에게 맡긴 실행 요청의 전달 상태/)).toBeTruthy();
    expect(screen.getByText('실행 요청 1')).toBeTruthy();
    expect(screen.getByText(/전달 상태: pending/)).toBeTruthy();
  });

  it('shows AO runs that wait for a person with the question and the CLI commands to answer and resume', () => {
    const ao = [
      { request_id: 'runtime-3', request_kind: 'ao_cli.v1', status: 'claimed', claimed_by: 'worker-a', attempt_id: 'attempt-9', ao_goal: 'ai-orchestration/s3-docs', ao_goal_status: 'ready', ao_session: 'run-1', ao_session_status: 'stopped', waiting_for_human: { kind: 'approval', question: 'Stage "impl" failed. Continue anyway?' }, next: ['ao-respond runtime-3 --approve | --reject | --action <a> [--feedback "…"]', 'ao-resume runtime-3 "worker-a"'] },
      { request_id: 'runtime-1', request_kind: 'ao_session.v1', status: 'completed', claimed_by: 'claude', attempt_id: 'attempt-1', ao_goal: 'ai-orchestration/old', ao_goal_status: 'completed', ao_session: 'run-0', ao_session_status: 'completed', waiting_for_human: null },
    ];
    render(<RuntimeScreen runtime={{ requests: [] }} ao={ao} />);
    expect(screen.getByText('AO Goal ai-orchestration/s3-docs')).toBeTruthy();
    expect(screen.getByText(/사람 승인을 기다립니다: Stage "impl" failed/)).toBeTruthy();
    expect(screen.getByText('goal-loop ao-resume runtime-3 "worker-a"')).toBeTruthy();
    expect(screen.queryByText('AO Goal ai-orchestration/old')).toBeNull();
    expect(screen.getByText('끝난 AO 실행 1건')).toBeTruthy();
  });

  it('does not put a different Aim retry into the selected achieved Aim action panel', () => {
    const data = {
      aims: [
        { id: 'aim-1', description: 'Achieved', phase: 'achieved', paused: false, currently_achieved: true },
        { id: 'aim-2', description: 'Open', phase: 'open', paused: false, currently_achieved: false },
      ],
      proposals: [], goals: [{ id: 'goal-2', aim_id: 'aim-2', phase: 'accepted', target: 'Retry target', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }],
      attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    const retry = { kind: 'start_retry_attempt', summary: 'retry', subject: { id: 'decision-1', kind: 'decision' }, command_argv: ['goal-loop', 'attempt-start', 'goal-2', '1', '--retry-decision', 'decision-1'] };
    expect(scopedNextActions(data, [retry], { kind: 'aim', id: 'aim-1' })).toEqual([]);
    expect(scopedNextActions(data, [retry], { kind: 'aim', id: 'aim-2' })).toEqual([retry]);
  });

  it('keeps a Goal Hold clear action in that Goal context', () => {
    const data = { aims: [{ id: 'aim-1', description: 'Open', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'active', target: 'Held goal', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: true, contract: {} }], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5 };
    const clear = { kind: 'clear_goal_hold', summary: 'clear', subject: { id: 'goal-1', kind: 'goal' }, command_argv: ['goal-loop', 'goal-hold-clear', 'goal-1'] };
    expect(scopedNextActions(data, [clear], { kind: 'goal', id: 'goal-1' })).toEqual([clear]);
  });

  it('shows approaches and tasks in separate Aim tabs with their relationship', () => {
    const onSelect = vi.fn();
    const data = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '관찰 흐름을 단순화한다', status: 'accepted', accepted_goal_id: 'goal-1' }],
      goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'committed', target: '안내 카드 추가', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }],
      attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    render(<AimInspector aim={data.aims[0]} data={data} busy={false} onSelect={onSelect} submit={() => vi.fn()} />);
    expect(screen.getByRole('tab', { name: /2\. 과제/ }).getAttribute('aria-selected')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: /← 접근안: 관찰 흐름을 단순화한다/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'proposal', id: 'proposal-1' });
    fireEvent.click(screen.getByRole('tab', { name: /1\. 접근안/ }));
    expect(screen.getByText('과제로 확정됨')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /→ 과제: 안내 카드 추가/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'goal', id: 'goal-1' });
  });

  it('keeps Proposal acceptance out of the parent Aim detail and links to the Proposal instead', () => {
    const onSelect = vi.fn();
    const data = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '관찰 흐름을 단순화한다', status: 'open', accepted_goal_id: null }],
      goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    render(<AimInspector aim={data.aims[0]} data={data} busy={false} onSelect={onSelect} submit={() => vi.fn()} actions={[{ kind: 'accept_proposal', summary: 'accept', subject: { id: 'proposal-1', kind: 'proposal' }, command_argv: ['goal-loop', 'goal-accept', 'proposal-1'] }]} />);
    expect(screen.queryByLabelText('과제 이름 — 끝났을 때 확인할 결과')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /이 접근안을 과제로 확정/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'proposal', id: 'proposal-1' });
  });

  it('opens a recorded approach from the Aim approach tab instead of treating it as inert text', () => {
    const onSelect = vi.fn();
    const data = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '관찰 흐름을 단순화한다', status: 'open', accepted_goal_id: null }],
      goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    render(<AimInspector aim={data.aims[0]} data={data} busy={false} onSelect={onSelect} submit={() => vi.fn()} />);
    fireEvent.click(screen.getByRole('tab', { name: /1\. 접근안/ }));
    fireEvent.click(screen.getByRole('button', { name: /관찰 흐름을 단순화한다/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'proposal', id: 'proposal-1' });
  });

  it('keeps proposal acceptance only in the selected Proposal context', () => {
    const data = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '관찰 흐름을 단순화한다', status: 'proposed', accepted_goal_id: null }],
      goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    const accept = { kind: 'accept_proposal', summary: '과제로 구체화하세요.', subject: { id: 'proposal-1', kind: 'proposal' }, command_argv: ['goal-loop', 'goal-accept', 'proposal-1'] };
    expect(scopedNextActions(data, [accept], { kind: 'aim', id: 'aim-1' })).toEqual([{ ...accept }]);
    expect(scopedNextActions(data, [accept], { kind: 'proposal', id: 'proposal-1' })).toEqual([accept]);
  });

  it('prefills task confirmation from an open approach and keeps local hide without a revise control', () => {
    const archive = vi.fn();
    const data = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '관찰 흐름을 단순화한다', status: 'open', accepted_goal_id: null }],
      goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    const accept = { kind: 'accept_proposal', summary: 'accept', subject: { id: 'proposal-1', kind: 'proposal' }, command_argv: ['goal-loop', 'goal-accept', 'proposal-1'] };
    render(<ProposalInspector proposal={data.proposals[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={archive} actions={[accept]} />);
    expect((screen.getByLabelText('과제 이름 — 끝났을 때 확인할 결과') as HTMLInputElement).value).toBe('관찰 흐름을 단순화한다');
    expect(screen.queryByRole('button', { name: '새 버전으로 바꾸기' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '이 접근안 숨기기' }));
    expect(archive).toHaveBeenCalledWith('proposal-1');
  });

  it('keeps line breaks of a multi-line observation in the verdict form', () => {
    const goal = { id: 'goal-1', aim_id: 'aim-1', phase: 'verifying', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: 'attempt-1', held: false, contract: {} };
    const observation = '(1) 문서를 썼다.\n(2) 테스트 138개가 통과했다.';
    const data = { aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [goal],
      attempts: [{ id: 'attempt-1', goal_id: 'goal-1', outcome: 'succeeded', operation: 'run', executor: 'claude', contract_version: 1 }], verdicts: [], nodes: [], edges: [],
      evidence: [{ id: 'evidence-1', goal_id: 'goal-1', attempt_id: 'attempt-1', observation, actor: 'claude' }], aim_evaluations: [], schema_version: 11 };
    const verdict = { kind: 'submit_verdict', summary: 'verdict', subject: { id: 'goal-1', kind: 'goal' }, command_argv: ['goal-loop', 'verdict-submit', 'goal-1', 'attempt-1'] };
    render(<GoalInspector goal={goal} data={data} activity={[]} admission={{}} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[verdict]} />);
    const cited = screen.getAllByText((_, element) => element?.classList.contains('observation-text') === true && element.textContent === observation);
    expect(cited.length).toBeGreaterThan(0);
  });

  it('sends the verdict reason and shows it with line breaks on the recorded verdict', () => {
    const goal = { id: 'goal-1', aim_id: 'aim-1', phase: 'verifying', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: 'attempt-1', held: false, contract: {} };
    const base = { aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [goal],
      attempts: [{ id: 'attempt-1', goal_id: 'goal-1', outcome: 'succeeded', operation: 'run', executor: 'claude', contract_version: 1 }], nodes: [], edges: [],
      evidence: [{ id: 'evidence-1', goal_id: 'goal-1', attempt_id: 'attempt-1', observation: '관찰', actor: 'claude' }], aim_evaluations: [], schema_version: 12 };
    let sent: string[] = [];
    const submit = (_command: string, args: (form: HTMLFormElement) => string[]) => async (event: React.FormEvent<HTMLFormElement>) => { event.preventDefault(); sent = args(event.currentTarget); };
    const verdict = { kind: 'submit_verdict', summary: 'verdict', subject: { id: 'goal-1', kind: 'goal' }, command_argv: ['goal-loop', 'verdict-submit', 'goal-1', 'attempt-1'] };
    const form = render(<GoalInspector goal={goal} data={{ ...base, verdicts: [] }} activity={[]} admission={{}} busy={false} onSelect={vi.fn()} submit={submit} run={async () => undefined} onArchive={vi.fn()} actions={[verdict]} />);
    fireEvent.change(screen.getByLabelText(/독립 검증자/), { target: { value: 'user' } });
    expect((screen.getByLabelText(/판단 이유/) as HTMLTextAreaElement).required).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '검증 판단 기록' }));
    expect(sent).not.toContain('--reason');
    fireEvent.change(screen.getByLabelText(/판단 이유/), { target: { value: '(1) 충족\n(2) 미충족' } });
    fireEvent.click(screen.getByRole('button', { name: '검증 판단 기록' }));
    expect(sent.slice(-2)).toEqual(['--reason', '(1) 충족\n(2) 미충족']);
    form.unmount();
    const recorded = { ...goal, phase: 'active', verifying_attempt_id: null };
    render(<GoalInspector goal={recorded} data={{ ...base, goals: [recorded], verdicts: [{ id: 'verdict-1', goal_id: 'goal-1', attempt_id: 'attempt-1', result: 'not_satisfied', validity: 'valid', evidence_ids: ['evidence-1'], verifier_actor_id: 'user', reason: '(1) 충족\n(2) 미충족' }] }} activity={[]} admission={{}} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    const shown = screen.getAllByText((_, element) => element?.classList.contains('verdict-reason') === true);
    expect(shown[0].textContent).toBe('판단 이유: (1) 충족\n(2) 미충족');
  });

  it('folds evidence and verdicts into cards that show a one-line preview and open to the full record', () => {
    const goal = { id: 'goal-1', aim_id: 'aim-1', phase: 'verified', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} };
    const data = { aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [goal],
      attempts: [{ id: 'attempt-1', goal_id: 'goal-1', outcome: 'succeeded', operation: 'run', executor: 'claude', contract_version: 1 }], nodes: [], edges: [],
      evidence: [{ id: 'evidence-1', goal_id: 'goal-1', attempt_id: 'attempt-1', observation: '첫 줄 요약\n둘째 줄 세부 내용', actor: 'claude',
        attachments: [{ name: 'log.txt', sha256: 'a'.repeat(64), size: 10, path: null, status: 'preserved' as const }] },
        { id: 'evidence-2', goal_id: 'goal-1', attempt_id: 'attempt-1', observation: '첨부 없는 관찰', actor: 'claude' }],
      verdicts: [{ id: 'verdict-1', goal_id: 'goal-1', attempt_id: 'attempt-1', result: 'satisfied', validity: 'valid', evidence_ids: ['evidence-1'], verifier_actor_id: 'codex', reason: '(1) 충족: 첫 이유\n(2) 충족: 둘째 이유' }],
      aim_evaluations: [], schema_version: 12 };
    render(<GoalInspector goal={goal} data={data} activity={[]} admission={{}} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    fireEvent.click(screen.getByRole('tab', { name: /관찰 근거/ }));
    const evidenceCard = document.querySelector('details.evidence-card') as HTMLDetailsElement;
    expect(evidenceCard.open).toBe(false);
    const evidenceSummary = evidenceCard.querySelector('summary')!;
    expect(evidenceSummary.textContent).toContain('1번째 실행의 관찰 근거');
    expect(evidenceSummary.textContent).toContain('첨부 1');
    expect(evidenceSummary.textContent).toContain('인용됨');
    expect(evidenceSummary.querySelector('.fold-preview')!.textContent).toBe('첫 줄 요약');
    expect(evidenceCard.querySelector('.fold-body .observation-text')!.textContent).toBe('첫 줄 요약\n둘째 줄 세부 내용');
    const bare = document.querySelectorAll('details.evidence-card')[1];
    expect(bare.querySelector('summary .fold-chip.empty')!.textContent).toBe('첨부 0');  // the count is shown even when nothing is attached
    expect(bare.querySelector('summary')!.textContent).not.toContain('인용됨');
    fireEvent.click(screen.getByRole('tab', { name: /검증/ }));
    const verdictCard = document.querySelector('details.verdict-card') as HTMLDetailsElement;
    expect(verdictCard.open).toBe(false);
    expect(verdictCard.querySelector('summary')!.textContent).toContain('인용 근거 1개');
    expect(verdictCard.querySelector('.fold-preview')!.textContent).toBe('(1) 충족: 첫 이유');
    expect(verdictCard.querySelector('.verdict-reason')!.textContent).toBe('판단 이유: (1) 충족: 첫 이유\n(2) 충족: 둘째 이유');
    const cited = verdictCard.querySelector('[aria-label="인용한 관찰 근거"]')!.textContent;
    expect(cited).toBe('evidence-1 · 첫 줄 요약');
  });

  it('puts the current verdict first and folds superseded and earlier-attempt verdicts into a labelled history', () => {
    const base = { goal_id: 'goal-1', evidence_ids: ['evidence-1'], verifier_actor_id: 'codex' };
    const verdicts = [
      { ...base, id: 'verdict-3', attempt_id: 'attempt-1', result: 'inconclusive', validity: 'valid' },
      { ...base, id: 'verdict-4', attempt_id: 'attempt-1', result: 'not_satisfied', validity: 'under_review', supersedes_verdict_id: 'verdict-3' },
      { ...base, id: 'verdict-6', attempt_id: 'attempt-2', result: 'satisfied', validity: 'valid', reason: '(1) 충족' },
    ];
    const relations = verdictRelations(verdicts, ['attempt-1', 'attempt-2']);
    expect(relations.current?.id).toBe('verdict-6');
    expect(relations.history.map(item => [item.verdict.id, item.label])).toEqual([['verdict-3', '대체됨 → verdict-4'], ['verdict-4', '이전 실행의 판정']]);
    // A later Attempt without a Verdict yet leaves no current Verdict: everything is history.
    expect(verdictRelations(verdicts.slice(0, 2), ['attempt-1', 'attempt-2']).current).toBeUndefined();

    const goal = { id: 'goal-1', aim_id: 'aim-1', phase: 'verified', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} };
    const attempts = ['attempt-1', 'attempt-2'].map(id => ({ id, goal_id: 'goal-1', outcome: 'succeeded', operation: 'run', executor: 'claude', contract_version: 1 }));
    const data = { aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [goal], attempts, nodes: [], edges: [],
      evidence: [{ id: 'evidence-1', goal_id: 'goal-1', attempt_id: 'attempt-1', observation: '관찰', actor: 'claude' }], verdicts, aim_evaluations: [], schema_version: 12 };
    render(<GoalInspector goal={goal} data={data} activity={[]} admission={{}} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    fireEvent.click(screen.getByRole('tab', { name: /검증/ }));
    const panel = document.querySelector('[role=tabpanel]')!;
    const first = panel.querySelector('.record-cards > details.verdict-card')!;
    expect(first.classList.contains('current')).toBe(true);
    expect(first.querySelector('.fold-chip.current')!.textContent).toBe('현재 판정');
    expect(first.querySelector('.state-badge')).toBeNull();  // a valid Verdict shows no validity badge
    const history = panel.querySelector('details.verdict-history') as HTMLDetailsElement;
    expect(history.open).toBe(false);
    expect(history.querySelector('summary')!.textContent).toBe('이전 판정 2건');
    const older = Array.from(history.querySelectorAll('details.verdict-card.history'));
    expect(older.map(card => card.querySelector('.fold-chip.relation')!.textContent)).toEqual(['대체됨 → verdict-4', '이전 실행의 판정']);
    expect(older[1].querySelector('.state-badge')!.textContent).toBe('재검토 중');
  });

  it('titles a long multi-paragraph approach by its first sentence and keeps the full text with line breaks', () => {
    const direction = '대응 표를 확정한다. 그다음 import 명령을 만든다.\n\n달성 기준: (1) 문서가 있다.\n\nGap: 읽는 방법 / Hypothesis: 파일 읽기';
    const data = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction, status: 'open', accepted_goal_id: null }],
      goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    const accept = { kind: 'accept_proposal', summary: 'accept', subject: { id: 'proposal-1', kind: 'proposal' }, command_argv: ['goal-loop', 'goal-accept', 'proposal-1'] };
    render(<ProposalInspector proposal={data.proposals[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[accept]} />);
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('대응 표를 확정한다.');
    expect(screen.getByRole('region', { name: '접근안 전체 내용' }).textContent).toBe(direction);
    expect((screen.getByLabelText('과제 이름 — 끝났을 때 확인할 결과') as HTMLInputElement).value).toBe('대응 표를 확정한다.');
  });

  it('routes changes of an accepted approach to the task criteria or a new approach', () => {
    const onSelect = vi.fn();
    const data = {
      aims: [{ id: 'aim-1', description: '사용성 개선', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '관찰 흐름을 단순화한다', status: 'accepted', accepted_goal_id: 'goal-1' }],
      goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'committed', target: '안내 카드 추가', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }],
      attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5,
    };
    render(<ProposalInspector proposal={data.proposals[0]} data={data} busy={false} onSelect={onSelect} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    fireEvent.click(screen.getByRole('button', { name: /과제의 완료 기준 바꾸기/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'goal', id: 'goal-1', focus: 'contract' });
    fireEvent.click(screen.getByRole('button', { name: /다른 접근안 추가/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'aim', id: 'aim-1', focus: 'add-proposal' });
  });

  it('offers a reversible local archive action from the Aim inspector', () => {
    const archive = vi.fn();
    const data = { aims: [{ id: 'aim-1', description: '보관할 목표', phase: 'open', paused: false, currently_achieved: false }], proposals: [], goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 5 };
    render(<AimInspector aim={data.aims[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} onArchive={archive} />);
    fireEvent.click(screen.getAllByRole('button', { name: '이 목표 숨기기' }).at(-1)!);
    expect(archive).toHaveBeenCalledWith('aim-1');
  });

  it('lists live references, flags missing files, and records add and removal with the acting person', () => {
    const calls: Array<[string, string[]]> = [];
    const submit = (command: string, args: (form: HTMLFormElement) => string[]) => async (event: React.FormEvent<HTMLFormElement>) => { event.preventDefault(); calls.push([command, args(event.currentTarget)]); };
    const references = [
      { id: 'reference-1', location: 'docs/design.md', label: '설계', actor: 'owner', resolved: 'C:/repo/docs/design.md', exists: true },
      { id: 'reference-2', location: 'docs/old.md', label: null, actor: 'claude', resolved: 'C:/repo/docs/old.md', exists: false },
    ];
    const { container } = render(<ReferenceSection subjectId="goal-1" references={references} projectRoot="C:/repo" busy={false} submit={submit} />);
    expect(screen.getByText('2개')).toBeTruthy();
    expect(screen.getByRole('button', { name: '설계' })).toBeTruthy();
    expect(screen.getAllByText('찾을 수 없음')).toHaveLength(1);
    const addForm = container.querySelector('form.reference-form') as HTMLFormElement;
    fireEvent.change(addForm.querySelector('input[name="location"]')!, { target: { value: 'docs/new.md' } });
    fireEvent.change(addForm.querySelector('input[name="actor"]')!, { target: { value: 'reviewer' } });
    fireEvent.submit(addForm);
    expect(calls.at(-1)).toEqual(['reference-add', ['goal-1', 'docs/new.md', '--actor', 'reviewer']]);
    const removeForm = container.querySelector('.reference-list li .sub-choice form') as HTMLFormElement;
    fireEvent.change(removeForm.querySelector('input[name="actor"]')!, { target: { value: 'reviewer' } });
    fireEvent.submit(removeForm);
    // The person removing the link is recorded, not the person who added it.
    expect(calls.at(-1)).toEqual(['reference-remove', ['reference-1', '--actor', 'reviewer']]);
  });

  it('shows whether each attachment copy is preserved', () => {
    render(<AttachmentList items={[
      { name: 'pytest.log', sha256: 'a'.repeat(64), size: 2048, path: 'C:/repo/.goal-loop/artifacts/a/pytest.log', status: 'preserved' },
      { name: 'gone.log', sha256: 'b'.repeat(64), size: 10, path: null, status: 'missing' },
    ]} />);
    expect(screen.getByText('2.0KB · 보존됨')).toBeTruthy();
    expect(screen.getByText('10B · 없음')).toBeTruthy();
    expect((screen.getByRole('button', { name: /gone.log/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('nests sub-Aims under their parent in the navigator and links back from the sub-Aim screen', () => {
    const aims = [
      { id: 'aim-1', description: '게임을 만든다', phase: 'open', paused: false, currently_achieved: false },
      { id: 'aim-2', description: '전투가 재미있다', phase: 'open', paused: false, currently_achieved: false, parent_aim_id: 'aim-1' },
      { id: 'aim-3', description: '보스전', phase: 'achieved', paused: false, currently_achieved: true, parent_aim_id: 'aim-2' },
      { id: 'aim-4', description: '다른 목표', phase: 'open', paused: false, currently_achieved: false },
    ];
    const onSelect = vi.fn();
    const { container } = render(<AimNavigator aims={aims} goals={[]} selected={null} onSelect={onSelect} />);
    const openRows = Array.from(Array.from(container.querySelectorAll('section'))[0].querySelectorAll('.aim-row'));
    expect(openRows.map(row => row.querySelector('span')?.textContent)).toEqual(['게임을 만든다', '↳ 전투가 재미있다', '다른 목표']);
    expect(screen.getAllByText('하위 목표 1개 · 과제 0개')).toHaveLength(2);
    // A sub-Aim in another phase group names its parent instead of being indented.
    expect(screen.getByText('과제 0개 · 상위: 전투가 재미있다')).toBeTruthy();
    cleanup();
    const data = { aims, proposals: [], goals: [], attempts: [], verdicts: [], nodes: [], edges: [], evidence: [], aim_evaluations: [], schema_version: 11 };
    render(<AimInspector aim={aims[1]} data={data} busy={false} onSelect={onSelect} submit={() => vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /상위 목표: 게임을 만든다/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'aim', id: 'aim-1' });
    expect(screen.getByText('하위 목표 · 진행 중')).toBeTruthy();
    expect(screen.getByRole('button', { name: /보스전/ })).toBeTruthy();
  });
});
