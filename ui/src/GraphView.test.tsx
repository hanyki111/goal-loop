import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EDGE_COLORS, graphEdges, graphVisibility, NO_GRAPH_FILTER, Workspace } from './Workspace';

const goal = (id: string, aim: string, phase: string) => ({ id, aim_id: aim, phase, target: `과제 ${id}`, responsibility: 'produce', current_contract_version: 1, verifying_attempt_id: null, held: false, contract: {} });
const data = {
  aims: [
    { id: 'aim-1', description: '진행 중 목표', phase: 'open', paused: false, currently_achieved: false },
    { id: 'aim-2', description: '달성한 목표', phase: 'achieved', paused: false, currently_achieved: true },
  ],
  proposals: [
    { id: 'proposal-1', aim_id: 'aim-1', direction: '확정한 접근', status: 'accepted', accepted_goal_id: 'goal-1' },
    { id: 'proposal-2', aim_id: 'aim-1', direction: '검증된 과제의 접근', status: 'accepted', accepted_goal_id: 'goal-2' },
    { id: 'proposal-3', aim_id: 'aim-1', direction: '검토 중 접근', status: 'open', accepted_goal_id: null },
    { id: 'proposal-4', aim_id: 'aim-1', direction: '닫은 접근', status: 'closed', closure_kind: 'declined', accepted_goal_id: null },
    { id: 'proposal-5', aim_id: 'aim-2', direction: '달성 목표의 접근', status: 'accepted', accepted_goal_id: 'goal-3' },
  ],
  goals: [goal('goal-1', 'aim-1', 'active'), goal('goal-2', 'aim-1', 'verified'), goal('goal-3', 'aim-2', 'verified')],
  attempts: [], evidence: [], verdicts: [], aim_evaluations: [], schema_version: 15,
  nodes: [
    { id: 'aim-1', kind: 'aim', label: '진행 중 목표', phase: 'open', aim_id: 'aim-1', dependency_blocked: false },
    { id: 'aim-2', kind: 'aim', label: '달성한 목표', phase: 'achieved', aim_id: 'aim-2', dependency_blocked: false },
    ...['goal-1', 'goal-2', 'goal-3'].map((id, index) => ({ id, kind: 'goal', label: `과제 ${id}`, phase: index === 0 ? 'active' : 'verified', aim_id: index === 2 ? 'aim-2' : 'aim-1', dependency_blocked: false })),
  ],
  edges: [{ id: 'aim:aim-1:goal-1', source: 'aim-1', target: 'goal-1', kind: 'aim' }],
} as never;
const none = new Set<string>();

describe('graph visibility', () => {
  it('keeps the column layout when Proposal nodes are hidden, without empty columns', () => {
    const shown = graphVisibility(data, none, NO_GRAPH_FILTER, true, 'columns');
    expect(shown.proposals.map(item => item.id)).toEqual(['proposal-1', 'proposal-2', 'proposal-3', 'proposal-4', 'proposal-5']);
    const hidden = graphVisibility(data, none, NO_GRAPH_FILTER, false, 'columns');
    expect(hidden.proposals).toEqual([]);
    expect(hidden.layoutProposals.map(item => item.id)).toEqual(['proposal-1', 'proposal-2', 'proposal-5']);  // Goals keep their columns
    const list = graphVisibility(data, none, NO_GRAPH_FILTER, true, 'list');
    expect([list.proposals, list.layoutProposals]).toEqual([[], []]);  // the list layout stacks Goals under the Aim
  });

  it('filters finished Aims with their Goals, finished Goals, and closed Proposals, and counts what it hid', () => {
    const filtered = graphVisibility(data, none, { hideDoneAims: true, hideDoneGoals: true, hideClosedProposals: true }, true, 'columns');
    expect(filtered.aims.map(item => item.id)).toEqual(['aim-1']);
    expect(filtered.goals.map(item => item.id)).toEqual(['goal-1']);
    expect(filtered.proposals.map(item => item.id)).toEqual(['proposal-1', 'proposal-3']);  // proposal-2's Goal is hidden, proposal-4 is closed
    expect(filtered.hidden).toEqual({ aims: 1, goals: 2 });
  });
});

describe('graph controls in the workspace', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => data })));
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

  const nodeIds = () => Array.from(document.querySelectorAll('.react-flow__node')).map(item => item.getAttribute('data-id'));

  it('separates the layout choice from showing Proposals and remembers it on this PC', async () => {
    render(<Workspace />);
    const layout = await screen.findByRole('combobox', { name: '그래프 배치' }) as HTMLSelectElement;
    await waitFor(() => expect(nodeIds()).toContain('proposal-1'));
    const toggle = screen.getByRole('checkbox', { name: '접근안 표시' }) as HTMLInputElement;
    fireEvent.click(toggle);
    await waitFor(() => expect(nodeIds()).not.toContain('proposal-1'));
    expect(layout.value).toBe('columns');
    fireEvent.change(layout, { target: { value: 'list' } });
    await waitFor(() => expect((screen.getByRole('checkbox', { name: '접근안 표시' }) as HTMLInputElement).disabled).toBe(true));
    expect(localStorage.getItem('goal-loop.graph.layout')).toBe('list');
  });

  it('keeps dragged positions per layout', async () => {
    localStorage.setItem('goal-loop.workspace.id', 'preview');
    localStorage.setItem('goal-loop.graph.layout', 'list');
    localStorage.setItem('goal-loop.graph.positions.v4.preview.list', JSON.stringify({ 'goal-1': { x: 777, y: 555 } }));
    render(<Workspace />);
    await waitFor(() => expect(document.querySelector('.react-flow__node[data-id="goal-1"]')?.getAttribute('style') ?? '').toContain('777'));
    fireEvent.change(screen.getByRole('combobox', { name: '그래프 배치' }), { target: { value: 'columns' } });
    await waitFor(() => expect(document.querySelector('.react-flow__node[data-id="goal-1"]')?.getAttribute('style') ?? '').not.toContain('777'));
  });

  it('hides finished items with a display filter and can show them all again', async () => {
    render(<Workspace />);
    await waitFor(() => expect(nodeIds()).toContain('goal-2'));
    fireEvent.click(screen.getByText('표시 필터'));
    fireEvent.click(screen.getByRole('checkbox', { name: '달성·그만둔 목표 숨기기' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '검증·중단된 과제 숨기기' }));
    await waitFor(() => expect(nodeIds()).not.toContain('goal-2'));
    expect(nodeIds()).not.toContain('aim-2');
    expect(nodeIds()).not.toContain('goal-3');
    expect(screen.getByText(/숨김 목표 1 · 과제 2/)).toBeTruthy();
    expect(JSON.parse(localStorage.getItem('goal-loop.graph.filter') ?? '{}')).toMatchObject({ hideDoneAims: true, hideDoneGoals: true });
    fireEvent.click(screen.getByRole('button', { name: '모두 보이기' }));
    await waitFor(() => expect(nodeIds()).toContain('goal-3'));
  });
});

describe('connection colours', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => data })));
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

  it('gives each connection kind its own colour and draws the legend and the edges in that colour', async () => {
    const kinds = ['aim', 'accepted', 'aim-parent', 'parent', 'dependency', 'follows'] as const;
    expect(new Set(kinds.map(kind => EDGE_COLORS[kind])).size).toBe(kinds.length);
    render(<Workspace />);
    await screen.findByRole('combobox', { name: '그래프 배치' });
    const toRgb = (hex: string) => { const n = parseInt(hex.slice(1), 16); return `rgb(${n >> 16}, ${(n >> 8) & 255}, ${n & 255})`; };
    for (const kind of kinds) {
      const line = document.querySelector(`.legend-line.${kind}`) as HTMLElement;
      expect(line.style.borderTopColor).toBe(toRgb(EDGE_COLORS[kind]));
    }
    // React Flow does not draw edges in jsdom (nodes are never measured), so check the edges the graph hands it.
    const typed = data as unknown as { edges: never[]; proposals: never[] };
    const drawn = graphEdges([...typed.edges, { id: 'p', source: 'goal-1', target: 'goal-2', kind: 'parent' }, { id: 'd', source: 'goal-2', target: 'goal-1', kind: 'dependency' }, { id: 's', source: 'aim-1', target: 'aim-2', kind: 'aim-parent' }, { id: 'f', source: 'goal-1', target: 'goal-2', kind: 'follows', reason: '이어감' }],
      typed.proposals.slice(0, 1), new Set(['aim-1', 'aim-2']), new Set(['goal-1', 'goal-2']), new Set(['goal-1']));
    const colour = (className: string) => drawn.find(edge => edge.className === className)?.style.stroke;
    expect(drawn.some(edge => edge.className === 'edge-aim')).toBe(false);  // aim-1 → goal-1 goes through proposal-1 instead
    expect([colour('edge-proposal'), colour('edge-accepted'), colour('edge-parent'), colour('edge-dependency'), colour('edge-aim-parent'), colour('edge-follows')])
      .toEqual([EDGE_COLORS.proposal, EDGE_COLORS.accepted, EDGE_COLORS.parent, EDGE_COLORS.dependency, EDGE_COLORS['aim-parent'], EDGE_COLORS.follows]);
    expect(drawn.find(edge => edge.className === 'edge-follows')).toMatchObject({ animated: false });
    expect(graphEdges(typed.edges, [], new Set(['aim-1']), new Set(['goal-1']), new Set())[0].style.stroke).toBe(EDGE_COLORS.aim);
  });
});
