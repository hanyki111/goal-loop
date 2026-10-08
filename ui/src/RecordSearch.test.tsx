import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AimInspector, findRecords, GoalInspector, ProposalInspector, RecordSearch, Workspace } from './Workspace';

const data = {
  aims: [
    { id: 'aim-1', description: '도구를 쓸 만하게 만든다', phase: 'open', paused: false, currently_achieved: false },
    { id: 'aim-2', description: '숨긴 목표', phase: 'open', paused: false, currently_achieved: false },
  ],
  proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '검색 칸을 둔다\n자세한 설명', status: 'accepted', accepted_goal_id: 'goal-1' }],
  goals: [
    { id: 'goal-1', aim_id: 'aim-1', phase: 'verified', target: '앱 상단 검색으로 이동', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} },
    { id: 'goal-2', aim_id: 'aim-2', phase: 'active', target: '숨긴 목표의 과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} },
  ],
  attempts: [{ id: 'attempt-1', goal_id: 'goal-1', outcome: 'succeeded', operation: '구현', executor: 'claude', contract_version: 1 }],
  evidence: [{ id: 'evidence-7', goal_id: 'goal-1', attempt_id: 'attempt-1', observation: '시험 통과\n자세히', actor: 'claude' }],
  verdicts: [{ id: 'verdict-3', goal_id: 'goal-1', attempt_id: 'attempt-1', result: 'satisfied', validity: 'valid', evidence_ids: ['evidence-7'], verifier_actor_id: 'codex' }],
  decisions: [{ id: 'decision-4', goal_id: 'goal-1', kind: 'retry', decided_by_actor_id: 'claude', reason: '다시 한다', target_attempt_id: null, contract_version: 1, consumed_by_attempt_id: null }],
  aim_evaluations: [{ id: 'aim-evaluation-5', aim_id: 'aim-1', validity: 'valid', conclusion: '달성', evaluator_actor_id: 'codex', verdict_ids: ['verdict-3'] }],
  nodes: [
    { id: 'aim-1', kind: 'aim', label: '도구를 쓸 만하게 만든다', phase: 'open', aim_id: 'aim-1' },
    { id: 'goal-1', kind: 'goal', label: '앱 상단 검색으로 이동', phase: 'verified', aim_id: 'aim-1' },
  ],
  edges: [], schema_version: 11,
} as never;

describe('record search', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('resolves exact IDs of every record kind to something the workspace can select', () => {
    expect(findRecords(data, '  GOAL-1 ')).toEqual([expect.objectContaining({ id: 'goal-1', kind: '과제', selection: { kind: 'goal', id: 'goal-1' } })]);
    expect(findRecords(data, 'aim-1')[0].selection).toEqual({ kind: 'aim', id: 'aim-1' });
    expect(findRecords(data, 'proposal-1')[0]).toMatchObject({ title: '검색 칸을 둔다', selection: { kind: 'proposal', id: 'proposal-1' } });
    expect(findRecords(data, 'attempt-1')[0].selection).toEqual({ kind: 'attempt', id: 'attempt-1' });
    expect(findRecords(data, 'evidence-7')[0]).toMatchObject({ title: '시험 통과', selection: { kind: 'attempt', id: 'attempt-1' } });
    expect(findRecords(data, 'verdict-3')[0].selection).toEqual({ kind: 'attempt', id: 'attempt-1' });
    expect(findRecords(data, 'decision-4')[0].selection).toEqual({ kind: 'goal', id: 'goal-1' });
    expect(findRecords(data, 'aim-evaluation-5')[0].selection).toEqual({ kind: 'aim', id: 'aim-1' });
  });

  it('matches part of a title or an ID prefix, at most eight, and marks hidden records', () => {
    expect(findRecords(data, '검색').map(match => match.id)).toEqual(['proposal-1', 'goal-1']);
    expect(findRecords(data, 'goal-').map(match => match.id)).toEqual(['goal-1', 'goal-2']);
    expect(findRecords(data, '없는 낱말')).toEqual([]);
    const hidden = findRecords(data, '숨긴', new Set(['aim-2']));
    expect(hidden.map(match => [match.id, match.hidden])).toEqual([['aim-2', true], ['goal-2', true]]);
    const many = { ...(data as object), goals: Array.from({ length: 12 }, (_, index) => ({ id: `goal-${index + 10}`, aim_id: 'aim-1', phase: 'active', target: `과제 ${index}`, responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} })) } as never;
    expect(findRecords(many, '과제')).toHaveLength(8);
  });

  it('lists candidates with ID and title, picks one, explains no match and hidden records, and focuses on Ctrl+K', () => {
    const onPick = vi.fn();
    render(<RecordSearch data={data} hiddenIds={new Set(['aim-2'])} onPick={onPick} />);
    const field = screen.getByRole('searchbox', { name: 'ID나 제목으로 찾기' });
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(document.activeElement).toBe(field);
    fireEvent.change(field, { target: { value: '검색' } });
    const option = screen.getByRole('option', { name: /goal-1.*앱 상단 검색으로 이동/ });
    fireEvent.click(option);
    expect(onPick).toHaveBeenCalledWith({ kind: 'goal', id: 'goal-1' });
    fireEvent.change(field, { target: { value: '숨긴' } });
    const hidden = screen.getByRole('option', { name: /aim-2/ }) as HTMLButtonElement;
    expect(hidden.disabled).toBe(true);
    expect(hidden.textContent).toContain('숨김');
    fireEvent.change(field, { target: { value: 'goal-99' } });
    expect(screen.getByText(/맞는 항목을 찾을 수 없습니다/)).toBeTruthy();
    fireEvent.change(field, { target: { value: 'evidence-7' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(onPick).toHaveBeenLastCalledWith({ kind: 'attempt', id: 'attempt-1' });
  });

  it('shows the record ID in the Aim, Proposal and Goal detail headers', () => {
    const typed = data as unknown as { aims: never[]; proposals: never[]; goals: never[] };
    render(<AimInspector aim={typed.aims[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} />);
    expect(document.querySelector('.aim-inspector .eyebrow .record-id')?.textContent).toBe('aim-1');
    cleanup();
    render(<ProposalInspector proposal={typed.proposals[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    expect(document.querySelector('.proposal-inspector .eyebrow .record-id')?.textContent).toBe('proposal-1');
    cleanup();
    render(<GoalInspector goal={typed.goals[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} admission={{}} activity={[]} />);
    expect(document.querySelector('.eyebrow .record-id')?.textContent).toBe('goal-1');
  });

  it('opens the searched record from another screen and labels graph nodes with their IDs', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => data })));
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    render(<Workspace />);
    const field = await screen.findByRole('searchbox', { name: 'ID나 제목으로 찾기' });
    await waitFor(() => expect(document.querySelectorAll('.node-id').length).toBeGreaterThan(0));
    expect(Array.from(document.querySelectorAll('.node-id')).map(node => node.textContent)).toEqual(expect.arrayContaining(['aim-1', 'proposal-1', 'goal-1']));
    fireEvent.click(screen.getByRole('button', { name: /사용 안내|도움말/ }));
    fireEvent.change(field, { target: { value: 'goal-1' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() => expect(document.querySelector('.eyebrow .record-id')?.textContent).toBe('goal-1'));
    expect((field as HTMLInputElement).value).toBe('');
  });
});
