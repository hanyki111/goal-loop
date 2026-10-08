import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AoRunSection, GoalInspector, RuntimeScreen, type AoStatusRow } from './Workspace';

const goal = { id: 'goal-1', aim_id: 'aim-1', phase: 'verifying', target: '증명 시험', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: 'attempt-1', held: false, contract: {} };
const data = {
  aims: [{ id: 'aim-1', description: 'AO 동작 다듬기', phase: 'open', paused: false, currently_achieved: false }],
  proposals: [], goals: [goal, { ...goal, id: 'goal-2', target: 'AO 없이 한 과제', verifying_attempt_id: null }],
  attempts: [{ id: 'attempt-1', goal_id: 'goal-1', outcome: 'succeeded', operation: 'AO 실행', executor: 'claude', contract_version: 1 }],
  evidence: [{ id: 'evidence-9', goal_id: 'goal-1', attempt_id: 'attempt-1', observation: 'runtime-dispatch: …', actor: 'claude' }],
  verdicts: [], aim_evaluations: [], nodes: [], edges: [], schema_version: 13,
  ao_runs: [{
    goal_id: 'goal-1', ao_project_id: 'ai-orchestration', ao_goal_id: 'ao-fix-proof', title: '재현 시험으로 증명', status: 'completed',
    review_reason: '새 시험이 기준 커밋에서도 통과했습니다',
    sessions: [{ id: 'run-7', status: 'completed', created_at: '2026-10-07T08:57:22Z', updated_at: '2026-10-07T10:00:41Z', verifiers_passed: 40, verifiers_failed: 5,
      recent_failures: [{ type: 'command', reason: 'Command failed (exit 1): npx vitest run --coverage', at: '2026-10-07T09:35:54Z' }] }],
    commits: [{ sha: '9c789992b8d8a79c6aef7393a3897a0803f7a5d3', subject: '[awaiting-review] goal-1을 AO에서 실행한다.', committed_at: '2026-10-07T18:58:48+09:00' }],
    evidence_ids: ['evidence-9'], attempt_ids: ['attempt-1'],
  }],
} as never;
const waiting: AoStatusRow = { request_id: 'runtime-request-1', request_kind: 'ao_cli.v1', status: 'claimed', claimed_by: 'claude', attempt_id: 'attempt-1', ao_goal: 'ai-orchestration/ao-fix-proof', ao_goal_status: 'awaiting_review', ao_session: 'run-7', ao_session_status: 'running', waiting_for_human: { kind: 'approval', question: '범위 밖 경로를 허용할까요?' }, next: ['ao-respond runtime-request-1 --approve | --reject'] };

describe('AO runs shown with the Goal they ran for', () => {
  afterEach(cleanup);

  it('shows the AO Goal, its sessions with verifier results, commits, and opens the evidence run', () => {
    const onSelect = vi.fn();
    render(<AoRunSection goal={goal as never} data={data} ao={[waiting]} onSelect={onSelect} />);
    const section = screen.getByText('AO 실행').closest('details')!;
    const view = within(section as HTMLElement);
    expect(view.getByText('ao-fix-proof')).toBeTruthy();
    expect(view.getByText('완료')).toBeTruthy();
    expect(view.getByText('재현 시험으로 증명')).toBeTruthy();
    expect(view.getByText(/검토로 넘어갔던 사유\(해소됨\): 새 시험이 기준 커밋에서도 통과했습니다/)).toBeTruthy();
    expect(section.textContent).toContain('검증기 통과 40 / 실패 5');
    expect(view.getByText(/npx vitest run --coverage/)).toBeTruthy();
    expect(view.getByText('9c789992')).toBeTruthy();
    expect(view.getByText(/사람 승인을 기다립니다: 범위 밖 경로를 허용할까요/)).toBeTruthy();
    expect(view.getByText('goal-loop ao-respond runtime-request-1 --approve | --reject')).toBeTruthy();
    expect(section.textContent).toContain('AO 앱');
    expect(section.textContent).not.toContain('localhost');
    fireEvent.click(view.getByRole('button', { name: /근거 evidence-9 · 실행 attempt-1 열기/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'attempt', id: 'attempt-1' });
  });

  it('calls the review reason current only while the AO Goal awaits review', () => {
    const awaiting = { ...(data as object), ao_runs: [{ ...(data as unknown as { ao_runs: object[] }).ao_runs[0], status: 'awaiting_review' }] } as never;
    render(<AoRunSection goal={goal as never} data={awaiting} onSelect={vi.fn()} />);
    expect(screen.getByText(/^검토 사유: 새 시험이 기준 커밋에서도 통과했습니다/)).toBeTruthy();
    expect(screen.getByText('검토 대기')).toBeTruthy();
  });

  it('shows judgments an --external-verdict run recorded instead of blocking completion', () => {
    const base = (data as unknown as { ao_runs: Array<{ sessions: object[] }> }).ao_runs[0];
    const withAdvisory = { ...(data as object), ao_runs: [{ ...base, sessions: [{ ...base.sessions[0], advisories: [
      { clause: 'objective-achievement', reason: '판정자가 미달로 봄', at: '2026-10-08T01:00:00Z' },
      { clause: 'new-test-pre-change', reason: '새 시험이 기준에서도 통과', at: '2026-10-08T01:00:01Z' },
    ] }] }] } as never;
    render(<AoRunSection goal={goal as never} data={withAdvisory} onSelect={vi.fn()} />);
    expect(screen.getByText('참고 판정(완료를 막지 않음)')).toBeTruthy();
    expect(screen.getByText('목표 달성 재확인: 판정자가 미달로 봄')).toBeTruthy();
    expect(screen.getByText('변경 전 실행 가드: 새 시험이 기준에서도 통과')).toBeTruthy();
    cleanup();
    render(<AoRunSection goal={goal as never} data={data} onSelect={vi.fn()} />);
    expect(screen.queryByText('참고 판정(완료를 막지 않음)')).toBeNull();
  });

  it('appears in the Goal detail only for a Goal that ran in AO', () => {
    const typed = data as unknown as { goals: never[] };
    render(<GoalInspector goal={typed.goals[0]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} admission={{}} activity={[]} />);
    expect(screen.getByText('ao-fix-proof')).toBeTruthy();
    cleanup();
    render(<GoalInspector goal={typed.goals[1]} data={data} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} run={async () => undefined} onArchive={vi.fn()} actions={[]} admission={{}} activity={[]} />);
    expect(screen.queryByText('AO 실행')).toBeNull();
  });

  it('lists finished AO runs in the runtime screen and opens their Goal', () => {
    const onOpenGoal = vi.fn();
    const done: AoStatusRow = { ...waiting, request_id: 'runtime-request-2', status: 'completed', ao_goal_status: 'completed', waiting_for_human: null, next: [] };
    render(<RuntimeScreen runtime={{ requests: [] }} ao={[done]} goalOfAttempt={id => (id === 'attempt-1' ? 'goal-1' : undefined)} onOpenGoal={onOpenGoal} />);
    fireEvent.click(screen.getByText('끝난 AO 실행 1건'));
    fireEvent.click(screen.getByRole('button', { name: /과제 goal-1 열기/ }));
    expect(onOpenGoal).toHaveBeenCalledWith('goal-1');
  });
});

describe('AO Goal references without a web address', () => {
  afterEach(cleanup);

  it('point to the AO app instead of offering to open a retired web page', async () => {
    const { ReferenceSection } = await import('./Workspace');
    render(<ReferenceSection subjectId="goal-1" projectRoot={null} busy={false} submit={() => vi.fn()} references={[
      { id: 'reference-1', location: 'ao:/projects/demo/goals/fix', label: 'AO Goal demo/fix', actor: 'claude', resolved: 'ao:/projects/demo/goals/fix', exists: true },
      { id: 'reference-2', location: 'docs/x.md', label: '설계', actor: 'claude', resolved: 'C:/p/docs/x.md', exists: true },
    ]} />);
    fireEvent.click(screen.getByText('참고 문서'));
    expect(screen.getByText('AO 앱(또는 AO CLI)에서 이 AO Goal을 엽니다')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'AO Goal demo/fix' })).toBeNull();
    expect(screen.getByRole('button', { name: '설계' })).toBeTruthy();
  });
});
