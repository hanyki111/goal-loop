import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AimInspector, AimLinkSection, actionScope, actionTitle, linkSummary, type AimLink } from './Workspace';

const link = (over: Partial<AimLink>): AimLink => ({
  aim_id: 'aim-1', reference_id: 'reference-1', project: 'E:\\code_projects\\guildmaster', database: 'E:\\code_projects\\guildmaster\\.goal-loop\\journal.sqlite3',
  external_aim_id: 'aim-1', state: 'open', reason: null, external_aim_description: '게임을 만든다', live_goal_ids: ['goal-8', 'goal-9'],
  valid_evaluation_ids: [], paused_by: null, imported_evaluations: [], stale_imports: 0, ...over,
});

function projection(links: AimLink[], actions: unknown[] = []) {
  return {
    data: {
      aims: [{ id: 'aim-1', description: '사이드 프로젝트', phase: 'open', paused: false, currently_achieved: false },
        { id: 'aim-2', description: '연결 없는 목표', phase: 'open', paused: false, currently_achieved: false }],
      proposals: [], goals: [], attempts: [], verdicts: [], evidence: [], aim_evaluations: [], nodes: [], edges: [], schema_version: 18, aim_links: links,
    },
    actions,
  };
}

describe('Aim links in the app (goal-143)', () => {
  afterEach(cleanup);

  it('shows each link with its state, open tasks, imports and a stale warning, and opens the linked project', () => {
    const onOpenProject = vi.fn();
    const links = [
      link({}),
      link({ reference_id: 'reference-2', project: 'E:\\code_projects\\music', external_aim_id: 'aim-3', external_aim_description: '피아노', state: 'evaluation_invalidated', live_goal_ids: [],
        imported_evaluations: [{ id: 'external-evaluation-1', external_evaluation_id: 'aim-evaluation-2', still_valid: false }], stale_imports: 1 }),
      link({ reference_id: 'reference-3', project: 'C:\\gone', state: 'broken', reason: '하위 프로젝트 폴더가 없습니다: C:\\gone' }),
    ];
    const { data } = projection(links);
    render(<AimInspector aim={data.aims[0] as never} data={data as never} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} onOpenProject={onOpenProject} />);
    const section = screen.getByRole('region', { name: '연결된 목표' });
    const items = Array.from(section.querySelectorAll('li'));
    expect(items).toHaveLength(3);
    expect(items[0].textContent).toContain('guildmaster');
    expect(items[0].textContent).toContain('진행 중');
    expect(items[0].textContent).toContain('게임을 만든다');
    expect(items[0].textContent).toContain('진행 중인 과제 2개');
    expect(items[1].textContent).toContain('평가 무효');
    expect(items[1].textContent).toContain('가져온 달성 평가 1개');
    expect(items[1].textContent).toContain('aim-evaluation-2이 그 프로젝트에서 무효가 되었습니다');
    expect(items[2].textContent).toContain('연결 끊김');
    expect(items[2].textContent).toContain('폴더가 없습니다');
    const buttons = items.map(item => item.querySelector('button')!);
    expect(buttons[2].disabled).toBe(true);
    fireEvent.click(buttons[0]);
    expect(onOpenProject).toHaveBeenCalledWith('E:\\code_projects\\guildmaster');
  });

  it('treats a linked Aim like a split one: its approach stage is optional', () => {
    const { data } = projection([link({})]);
    const view = render(<AimInspector aim={data.aims[0] as never} data={data as never} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} />);
    expect(view.container.textContent).toContain('위의 연결된 목표로 진행하는 목표입니다');
    expect(view.container.textContent).not.toContain('지금 단계');
    expect(view.container.textContent).not.toContain('하위 목표를 골라');
  });

  it('shows no link section for an Aim without links', () => {
    const { data } = projection([link({})]);
    render(<AimInspector aim={data.aims[1] as never} data={data as never} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} />);
    expect(screen.queryByRole('region', { name: '연결된 목표' })).toBeNull();
    render(<AimLinkSection links={[]} />);
    expect(screen.queryByRole('region', { name: '연결된 목표' })).toBeNull();
  });

  it('summarizes links for a graph card', () => {
    expect(linkSummary([])).toBe('');
    expect(linkSummary([link({}), link({ state: 'achieved' }), link({ state: 'open' })])).toBe('연결 3개 · 진행 중 2 · 달성 1');
  });

  it('offers the import suggestion on its Aim and submits aim-link-import', () => {
    const action = { kind: 'import_linked_evaluation', subject: { kind: 'reference', id: 'reference-1' }, aim_id: 'aim-1', summary: 's',
      command_argv: ['goal-loop', '--database', 'x', 'aim-link-import', 'reference-1', '--actor', '<actor>'] };
    const { data } = projection([link({ state: 'achieved', valid_evaluation_ids: ['aim-evaluation-1'] })], [action]);
    expect(actionScope(data as never, action as never)).toEqual({ aimId: 'aim-1' });
    expect(actionTitle('import_linked_evaluation')).toBe('연결된 목표의 달성 평가 가져오기');
    const calls: Array<[string, string[]]> = [];
    const submit = (command: string, build: (form: HTMLFormElement) => string[]) => (event: { preventDefault: () => void; currentTarget: HTMLFormElement }) => { event.preventDefault(); calls.push([command, build(event.currentTarget)]); };
    const view = render(<AimInspector aim={data.aims[0] as never} data={data as never} busy={false} onSelect={vi.fn()} submit={submit as never} actions={[action as never]} />);
    expect(screen.getByText('연결된 목표의 달성 평가 가져오기')).toBeTruthy();
    const form = view.container.querySelector('form')!;
    fireEvent.change(form.querySelector('[name="actor"]')!, { target: { value: 'claude' } });
    fireEvent.submit(form);
    expect(calls).toEqual([['aim-link-import', ['reference-1', '--actor', 'claude']]]);
  });

  it('cites imported evaluations when the Aim is evaluated', () => {
    const action = { kind: 'evaluate_aim', subject: { kind: 'aim', id: 'aim-1' }, summary: 's',
      command_argv: ['goal-loop', '--database', 'x', 'aim-evaluate', 'aim-1', '<evaluator>', '<conclusion>', 'external-evaluation-1', 'external-evaluation-2'] };
    const { data } = projection([link({ state: 'achieved' })], [action]);
    const calls: Array<[string, string[]]> = [];
    const submit = (command: string, build: (form: HTMLFormElement) => string[]) => (event: { preventDefault: () => void; currentTarget: HTMLFormElement }) => { event.preventDefault(); calls.push([command, build(event.currentTarget)]); };
    const view = render(<AimInspector aim={data.aims[0] as never} data={data as never} busy={false} onSelect={vi.fn()} submit={submit as never} actions={[action as never]} />);
    expect(view.container.textContent).toContain('연결된 목표에서 가져온 평가 2개');
    const form = view.container.querySelector('form')!;
    fireEvent.change(form.querySelector('[name="actor"]')!, { target: { value: 'codex' } });
    fireEvent.change(form.querySelector('[name="reason"]')!, { target: { value: '마쳤다' } });
    fireEvent.submit(form);
    expect(calls).toEqual([['aim-evaluate', ['aim-1', 'codex', '마쳤다', 'external-evaluation-1', 'external-evaluation-2']]]);
  });
});
