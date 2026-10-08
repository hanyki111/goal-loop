import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AimInspector, GoalInspector, MemorySection, ProposalInspector } from './Workspace';

const goal = { id: 'goal-1', aim_id: 'aim-1', phase: 'verified', target: '과제', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {},
  references: [{ id: 'reference-1', location: '.memory/modules/core/core.md#결정', label: null, actor: 'claude', resolved: 'x', exists: true }] };
const entry = (time: string, text: string, ids: string[]) => ({ date: '2026-10-07', time, text, file: '.memory/timeline/daily/2026-10/07.md', ids });
const data = {
  aims: [{ id: 'aim-1', description: '도구 연결', phase: 'open', paused: false, currently_achieved: false }],
  proposals: [{ id: 'proposal-1', aim_id: 'aim-1', direction: '접근', status: 'accepted', accepted_goal_id: 'goal-1' }],
  goals: [goal],
  attempts: [{ id: 'attempt-3', goal_id: 'goal-1', outcome: 'succeeded', operation: '구현', executor: 'claude', contract_version: 1 }],
  evidence: [], verdicts: [{ id: 'verdict-4', goal_id: 'goal-1', attempt_id: 'attempt-3', result: 'satisfied', validity: 'valid', evidence_ids: [], verifier_actor_id: 'codex' }],
  aim_evaluations: [], nodes: [], edges: [], schema_version: 14,
  memory_mentions: [
    entry('20:49', 'goal-1 verified(verdict-4): 단락을 더했다.', ['goal-1', 'verdict-4']),
    entry('19:00', 'attempt-3이 끝났다.', ['attempt-3']),
    entry('18:00', 'proposal-1 등록, aim-1 아래.', ['proposal-1', 'aim-1']),
    entry('17:00', '관계없는 goal-2 기록', ['goal-2']),
    ...['16:00', '15:00', '14:00', '13:00'].map(time => entry(time, `goal-1 진행 ${time}`, ['goal-1'])),
  ],
  memory_documents: { '.memory/modules/core/core.md#결정': { exists: true, excerpt: '## 결정\n결정 본문 첫 줄.', anchor: '결정', anchor_found: true } },
} as never;
const typed = data as unknown as { aims: never[]; proposals: never[]; goals: never[] };

describe('Memory Tool records beside Goal Loop records', () => {
  afterEach(cleanup);

  it('lists the timeline entries citing the record or its runs and verdicts, newest first, five at a time', () => {
    render(<MemorySection data={data} ids={['goal-1', 'attempt-3', 'verdict-4']} />);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(5);
    expect(items[0].textContent).toContain('2026-10-07 20:49');
    expect(items[0].textContent).toContain('인용: goal-1, verdict-4');
    expect(items[1].textContent).toContain('attempt-3이 끝났다.');
    expect(screen.queryByText(/관계없는 goal-2/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '나머지 1개 더 보기' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(6);
  });

  it('offers a timeline draft for a record the timeline does not cite yet and copies its command', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const drafted = { ...(data as object), memory_mentions: [], memory_drafts: [
      { record_id: 'verdict-4', subject_id: 'goal-1', kind: 'verdict', text: 'goal-1 satisfied(verdict-4, codex): 과제', command: 'python -m memory_tool record "goal-1 satisfied(verdict-4, codex): 과제"' },
      { record_id: 'verdict-9', subject_id: 'goal-9', kind: 'verdict', text: '다른 과제', command: 'x' },
    ] } as never;
    render(<MemorySection data={drafted} ids={['goal-1', 'verdict-4']} />);
    expect(screen.getByText(/초안 1개/)).toBeTruthy();
    expect(screen.getByText('goal-1 satisfied(verdict-4, codex): 과제')).toBeTruthy();
    expect(screen.queryByText('다른 과제')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '명령 복사' }));
    expect(writeText).toHaveBeenCalledWith('python -m memory_tool record "goal-1 satisfied(verdict-4, codex): 과제"');
    expect(await screen.findByRole('button', { name: '복사했습니다' })).toBeTruthy();
  });

  it('shows nothing without memory entries for the record', () => {
    render(<MemorySection data={{ ...(data as object), memory_mentions: [] } as never} ids={['goal-1']} />);
    expect(screen.queryByText('메모리 기록')).toBeNull();
  });

  it('appears in the Goal, Aim and Proposal details and previews a referenced memory document', () => {
    render(<GoalInspector goal={typed.goals[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} admission={{}} activity={[]} />);
    expect(screen.getByText('메모리 기록')).toBeTruthy();
    expect(screen.getByText('attempt-3이 끝났다.')).toBeTruthy();
    fireEvent.click(screen.getByText('미리보기'));
    expect(screen.getByText(/결정 본문 첫 줄/)).toBeTruthy();
    cleanup();
    render(<AimInspector aim={typed.aims[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} />);
    expect(screen.getByText('proposal-1 등록, aim-1 아래.')).toBeTruthy();
    cleanup();
    render(<ProposalInspector proposal={typed.proposals[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} />);
    expect(screen.getByText('proposal-1 등록, aim-1 아래.')).toBeTruthy();
  });

  it('says when a referenced memory section or file cannot be found', () => {
    const missing = { ...(data as object), memory_documents: { '.memory/modules/core/core.md#결정': { exists: true, excerpt: '# 모듈', anchor: '결정', anchor_found: false } } } as never;
    render(<GoalInspector goal={typed.goals[0]} data={missing} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} admission={{}} activity={[]} />);
    fireEvent.click(screen.getByText('미리보기'));
    expect(screen.getByText(/절 '결정'을 찾지 못해 문서 처음부터 보입니다/)).toBeTruthy();
    cleanup();
    const gone = { ...(data as object), memory_documents: { '.memory/modules/core/core.md#결정': { exists: false, excerpt: null, anchor: '결정', anchor_found: false } } } as never;
    render(<GoalInspector goal={typed.goals[0]} data={gone} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} admission={{}} activity={[]} />);
    fireEvent.click(screen.getByText('미리보기'));
    expect(screen.getByText('파일을 읽을 수 없습니다.')).toBeTruthy();
  });
});
