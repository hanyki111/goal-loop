import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AimInspector, AimLinkSection, ReferenceSection, actionScope, actionTitle, linkSummary, type AimLink } from './Workspace';

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

  it('shows one card per project with each linked Aim on its own line, and opens the project once (goal-148)', () => {
    const onOpenProject = vi.fn();
    const links = [
      link({}),
      link({ reference_id: 'reference-2', project: 'E:/code_projects/music', external_aim_id: 'aim-3', external_aim_description: '피아노', state: 'evaluation_invalidated', live_goal_ids: [],
        imported_evaluations: [{ id: 'external-evaluation-1', external_evaluation_id: 'aim-evaluation-2', still_valid: false }], stale_imports: 1 }),
      link({ reference_id: 'reference-3', project: 'C:/gone', state: 'broken', reason: '하위 프로젝트 폴더가 없습니다: C:/gone' }),
      link({ reference_id: 'reference-4', external_aim_id: 'aim-5', external_aim_description: '지도를 만든다', live_goal_ids: ['goal-20'] }),
      link({ reference_id: 'reference-5', external_aim_id: 'aim-9', state: 'broken', reason: '하위 저널에 Aim aim-9이 없습니다.' }),
    ];
    const { data } = projection(links);
    render(<AimInspector aim={data.aims[0] as never} data={data as never} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} onOpenProject={onOpenProject} />);
    const section = screen.getByRole('region', { name: '연결된 목표' });
    const cards = Array.from(section.querySelectorAll('li.aim-link-project'));
    expect(cards).toHaveLength(3);  // guildmaster (three links), music, gone
    const [game, music, gone] = cards;
    expect(game.querySelector('.aim-link-heading')!.textContent).toContain('guildmaster');
    expect(game.querySelector('.aim-link-summary')!.textContent).toBe('연결된 목표 3개 · 진행 중인 과제 3개');
    // goal-149: name and button first, then the summary, then one line per Aim with its ID in front.
    expect(Array.from(game.children).map(child => child.className)).toEqual(['aim-link-heading', 'aim-link-summary', 'aim-link-aims']);
    expect(game.querySelector('.aim-link-heading button')!.textContent).toContain('이 프로젝트로 이동');
    expect(game.querySelector('li.aim-link .aim-link-row')!.firstElementChild!.textContent).toBe('aim-1');
    expect(game.querySelector('li.aim-link .state-badge')!.className).toContain('link-open');
    expect(game.querySelector('li.aim-link .aim-link-meta small')!.className).toBe('live');
    const rows = Array.from(game.querySelectorAll('li.aim-link')).map(row => row.textContent);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toContain('게임을 만든다');
    expect(rows[0]).toContain('진행 중인 과제 2개');
    expect(rows[1]).toContain('지도를 만든다');
    expect(rows[2]).toContain('연결 끊김');
    expect(rows[2]).toContain('하위 저널에 Aim aim-9이 없습니다');
    expect(game.querySelectorAll('button')).toHaveLength(1);
    expect(game.textContent!.match(/guildmaster/g)).toHaveLength(1);
    expect(music.textContent).toContain('평가 무효');
    expect(music.textContent).toContain('가져온 달성 평가 1개');
    expect(music.textContent).toContain('aim-evaluation-2이 그 프로젝트에서 무효가 되었습니다');
    expect(gone.textContent).toContain('연결 끊김');
    expect(gone.textContent!.match(/폴더가 없습니다/g)).toHaveLength(1);
    expect(gone.querySelector('button')!.disabled).toBe(true);
    fireEvent.click(game.querySelector('button')!);
    expect(onOpenProject).toHaveBeenCalledWith('E:\\code_projects\\guildmaster');
  });

  it('a linked Aim with nothing of its own shows no stage tabs, only a small link to add local work (goal-151)', () => {
    const { data } = projection([link({})]);
    const calls: Array<[string, string[]]> = [];
    const submit = (command: string, build: (form: HTMLFormElement) => string[]) => (event: { preventDefault: () => void; currentTarget: HTMLFormElement }) => { event.preventDefault(); calls.push([command, build(event.currentTarget)]); };
    const view = render(<AimInspector aim={data.aims[0] as never} data={data as never} busy={false} onSelect={vi.fn()} submit={submit as never} />);
    expect(view.container.querySelector('.stage-tabs')).toBeNull();
    expect(view.container.textContent).not.toContain('접근안 추가');
    expect(view.container.textContent).not.toContain('첫 접근안');
    fireEvent.click(screen.getByText('＋ 이 프로젝트에서 따로 할 일 추가'));
    const form = view.container.querySelector('.linked-extra form')!;
    fireEvent.change(form.querySelector('[name="direction"]')!, { target: { value: '프로젝트 회고 노트를 쓴다' } });
    fireEvent.submit(form);
    expect(calls).toEqual([['proposal-create', ['aim-1', '프로젝트 회고 노트를 쓴다']]]);
  });

  it('a linked Aim that has its own work keeps the stage tabs, and an Aim without links is unchanged', () => {
    const { data } = projection([link({})]);
    const withGoal = { ...data, goals: [{ id: 'goal-1', aim_id: 'aim-1', phase: 'verified', target: '안내 절', responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} }] };
    const view = render(<AimInspector aim={withGoal.aims[0] as never} data={withGoal as never} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} />);
    expect(view.container.querySelector('.stage-tabs')).not.toBeNull();
    expect(view.container.querySelector('.linked-extra')).toBeNull();
    cleanup();
    const plain = render(<AimInspector aim={data.aims[1] as never} data={data as never} busy={false} onSelect={vi.fn()} submit={() => vi.fn()} />);
    expect(plain.container.querySelector('.stage-tabs')).not.toBeNull();
    expect(plain.container.querySelector('.linked-extra')).toBeNull();
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

  it('opens the linked project from a goal-loop-aim reference instead of a file (goal-145)', () => {
    const onOpenProject = vi.fn();
    const references = [
      { id: 'reference-9', location: 'goal-loop-aim:E:/code_projects/agent_make_money_test#aim-1', label: 'Goal Loop Aim agent_make_money_test/aim-1', actor: 'claude', resolved: 'E:/code_projects/agent_make_money_test', exists: true },
      { id: 'reference-10', location: 'goal-loop-aim:C:/gone#aim-1', label: 'Goal Loop Aim gone/aim-1', actor: 'claude', resolved: 'C:/gone', exists: false },
    ];
    const view = render(<ReferenceSection subjectId="aim-10" references={references} projectRoot="E:/code_projects/memory_notion" busy={false} submit={() => vi.fn() as never} onOpenProject={onOpenProject} />);
    const buttons = Array.from(view.container.querySelectorAll('.reference-list button.link-button')) as HTMLButtonElement[];
    expect(buttons[0].textContent).toContain('연결된 프로젝트로 이동합니다');
    fireEvent.click(buttons[0]);
    expect(onOpenProject).toHaveBeenCalledWith('E:/code_projects/agent_make_money_test');
    expect(buttons[1].disabled).toBe(true);
  });
});
