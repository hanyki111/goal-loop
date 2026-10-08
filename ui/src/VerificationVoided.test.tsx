import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { GoalInspector, verificationVoided, Workspace } from './Workspace';

const projection = (validity: string) => ({
  aims: [{ id: 'aim-1', description: '도구를 쓸 만하게 만든다', phase: 'open', paused: false, currently_achieved: false }],
  proposals: [],
  goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'verified', target: '가드가 막지 않음', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }],
  attempts: [{ id: 'attempt-1', goal_id: 'goal-1', outcome: 'succeeded', operation: '구현', executor: 'claude', contract_version: 1 }],
  evidence: [],
  verdicts: [{ id: 'verdict-1', goal_id: 'goal-1', attempt_id: 'attempt-1', result: 'satisfied', validity, evidence_ids: [], verifier_actor_id: 'codex' }],
  aim_evaluations: [],
  nodes: [{ id: 'aim-1', kind: 'aim', label: '도구를 쓸 만하게 만든다', phase: 'open', aim_id: 'aim-1' }, { id: 'goal-1', kind: 'goal', label: '가드가 막지 않음', phase: 'verified', aim_id: 'aim-1' }],
  edges: [{ id: 'aim-1:goal-1', source: 'aim-1', target: 'goal-1', kind: 'aim' }], schema_version: 11,
}) as never;

const inspect = (data: never, onSelect = vi.fn()) => {
  const goal = (data as { goals: never[] }).goals[0];
  render(<GoalInspector goal={goal} data={data} busy={false} onSelect={onSelect} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} admission={{}} activity={[]} />);
  return onSelect;
};

describe('a verified Goal whose Verdict was invalidated', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('is voided only when no satisfied Verdict is still valid', () => {
    const goal = { id: 'goal-1', phase: 'verified' };
    const verdict = (id: string, result: string, validity: string) => ({ id, goal_id: 'goal-1', result, validity }) as never;
    expect(verificationVoided(goal, { verdicts: [verdict('v1', 'satisfied', 'invalidated')] })).toBe(true);
    expect(verificationVoided(goal, { verdicts: [verdict('v1', 'satisfied', 'invalidated'), verdict('v2', 'satisfied', 'valid')] })).toBe(false);
    expect(verificationVoided(goal, { verdicts: [verdict('v1', 'satisfied', 'valid')] })).toBe(false);
    expect(verificationVoided({ id: 'goal-1', phase: 'active' }, { verdicts: [verdict('v1', 'satisfied', 'invalidated')] })).toBe(false);
  });

  it('says the judgment was voided and offers a follow-up approach instead of the Aim evaluation', () => {
    const onSelect = inspect(projection('invalidated'));
    expect(screen.getByRole('heading', { name: '이 과제의 검증 판단이 무효가 되었습니다' })).toBeTruthy();
    expect(screen.getByText(/유효한 검증 판단이 없습니다/)).toBeTruthy();
    expect(screen.queryByText('이 과제는 검증을 마쳤습니다')).toBeNull();
    expect(screen.queryByText(/목표에서 달성 평가하기/)).toBeNull();
    expect(document.querySelector('.inspector-title .eyebrow')?.textContent).toContain('검증 무효');
    fireEvent.click(screen.getByRole('button', { name: /후속 접근안 적기/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'aim', id: 'aim-1', focus: 'add-proposal' });
  });

  it('keeps the usual guidance while a satisfied Verdict is valid', () => {
    inspect(projection('valid'));
    expect(screen.getByText('이 과제는 검증을 마쳤습니다')).toBeTruthy();
    expect(screen.getByText(/목표에서 달성 평가하기/)).toBeTruthy();
    expect(document.querySelector('.inspector-title .eyebrow')?.textContent).not.toContain('검증 무효');
  });

  it('marks the graph node of the voided Goal', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => projection('invalidated') })));
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    render(<Workspace />);
    await waitFor(() => expect(Array.from(document.querySelectorAll('.react-flow__node .state-badge')).map(item => item.textContent)).toContain('검증됨 · 검증 무효'));
  });
});
