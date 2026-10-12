import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EDGE_COLORS, followFlow, graphEdges, highlightFlow, graphVisibility, NO_GRAPH_FILTER, Workspace } from './Workspace';

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
    localStorage.setItem('goal-loop.graph.positions.v5.preview.list', JSON.stringify({ 'goal-1': { x: 777, y: 555 } }));
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

  it('draws follow links side to side with an arrow and highlights the selected task flow with its reasons', () => {
    const recorded = [
      { id: 'aim:aim-1:goal-1', source: 'aim-1', target: 'goal-1', kind: 'aim' },
      { id: 'follows:goal-1:goal-2', source: 'goal-1', target: 'goal-2', kind: 'follows', reason: '조사 결론에서 시작' },
      { id: 'follows:goal-2:goal-3', source: 'goal-2', target: 'goal-3', kind: 'follows', reason: '중단된 과제를 이어받음' },
      { id: 'dependency:goal-4:goal-5', source: 'goal-4', target: 'goal-5', kind: 'dependency' },
    ];
    const drawn = graphEdges(recorded, [], new Set(['aim-1']), new Set(['goal-1', 'goal-2', 'goal-3', 'goal-4', 'goal-5']), new Set());
    const follow = drawn.find(edge => edge.id === 'follows:goal-1:goal-2')!;
    expect(follow).toMatchObject({ type: 'follows', sourceHandle: 'follow-out', targetHandle: 'follow-in', markerEnd: { type: 'arrowclosed', color: EDGE_COLORS.follows }, data: { reason: '조사 결론에서 시작' } });
    expect((follow.style as { strokeWidth?: number }).strokeWidth).toBeGreaterThan(2);
    // Structure lines keep the top/bottom handles, so a follow link never shares a handle with an accepted line.
    expect(drawn.filter(edge => edge.id !== follow.id && !edge.id.startsWith('follows:')).every(edge => edge.sourceHandle === 'out' && edge.targetHandle === 'in')).toBe(true);

    expect(followFlow(recorded, 'goal-3')).toEqual(['goal-1', 'goal-2', 'goal-3']);
    expect(followFlow([...recorded, { id: 'f', source: 'goal-1', target: 'goal-9', kind: 'follows' }], 'goal-2')).toEqual(['goal-1', 'goal-2', 'goal-3', 'goal-9']);
    expect(followFlow(recorded, 'goal-4')).toEqual(['goal-4']);

    const nodes = ['aim-1', 'proposal-1', 'goal-1', 'goal-2', 'goal-3', 'goal-4'].map(id => ({ id, position: { x: 0, y: 0 }, data: {}, className: 'graph-node' }));
    const selected = highlightFlow(nodes, drawn, recorded, 'goal-2');
    const classOf = (id: string) => selected.nodes.find(node => node.id === id)!.className;
    expect(classOf('goal-2')).toContain('is-selected');
    expect([classOf('goal-1'), classOf('goal-3')].every(name => name?.includes('in-flow'))).toBe(true);
    expect(['aim-1', 'proposal-1', 'goal-4'].every(id => classOf(id)?.includes('dimmed'))).toBe(true);
    const highlighted = selected.edges.filter(edge => edge.className?.includes('in-flow'));
    expect(highlighted.map(edge => edge.id).sort()).toEqual(['follows:goal-1:goal-2', 'follows:goal-2:goal-3']);
    expect(highlighted.every(edge => (edge.data as { showReason?: boolean }).showReason)).toBe(true);
    expect(selected.edges.filter(edge => !edge.className?.includes('in-flow')).every(edge => edge.className?.includes('dimmed'))).toBe(true);
    // Without a flow (or after clearing the selection) nothing is dimmed and no reason is shown.
    for (const result of [highlightFlow(nodes, drawn, recorded, 'goal-4'), highlightFlow(nodes, drawn, recorded, undefined)]) {
      expect(result.nodes.some(node => node.className?.includes('dimmed'))).toBe(false);
      expect(result.edges).toBe(drawn);
    }
  });
});

// A wide journal: one parent Aim with many sub-Aims, and one Aim with many Proposals.
const wideData = (() => {
  const aims: Array<Record<string, unknown>> = [{ id: 'aim-1', description: '부모 목표', phase: 'open', paused: false, currently_achieved: false }];
  const proposals: Array<Record<string, unknown>> = []; const goals: Array<ReturnType<typeof goal>> = [];
  const nodes: Array<Record<string, unknown>> = [{ id: 'aim-1', kind: 'aim', label: '부모 목표', phase: 'open', aim_id: 'aim-1', dependency_blocked: false }];
  for (let index = 2; index <= 13; index += 1) {
    aims.push({ id: `aim-${index}`, parent_aim_id: 'aim-1', description: `하위 목표 ${index}`, phase: 'open', paused: false, currently_achieved: false });
    nodes.push({ id: `aim-${index}`, kind: 'aim', label: `하위 목표 ${index}`, phase: 'open', aim_id: `aim-${index}`, dependency_blocked: false });
  }
  for (let index = 1; index <= 8; index += 1) {
    proposals.push({ id: `proposal-${index}`, aim_id: 'aim-2', direction: `접근안 ${index}`, status: 'open', accepted_goal_id: null });
  }
  return { aims, proposals, goals, attempts: [], evidence: [], verdicts: [], aim_evaluations: [], schema_version: 16, nodes, edges: [] } as never;
})();

describe('wide graph layout', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => wideData })));
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

  const boxes = () => Array.from(document.querySelectorAll<HTMLElement>('.react-flow__node')).map(item => {
    const [, x, y] = (item.getAttribute('style')?.match(/translate\(([-\d.]+)px,\s*([-\d.]+)px/) ?? []).map(Number);
    return { id: item.getAttribute('data-id') ?? '', x, y };
  });

  it('wraps sibling sub-Aims into rows, lays many Proposals out as a grid, and keeps nodes apart', async () => {
    render(<Workspace />);
    await waitFor(() => expect(boxes().length).toBe(21));
    const all = boxes(); const children = all.filter(box => /^aim-(?:[3-9]|1\d)$/.test(box.id));
    expect(new Set(children.map(box => box.y)).size).toBeGreaterThan(1);  // twelve sub-Aims no longer sit on one row
    const proposals = all.filter(box => box.id.startsWith('proposal-'));
    expect(new Set(proposals.map(box => box.x)).size).toBeLessThanOrEqual(4);  // at most four columns
    expect(new Set(proposals.map(box => box.y)).size).toBeGreaterThan(1);
    for (const [index, first] of all.entries()) for (const second of all.slice(index + 1)) {
      expect(Math.abs(first.x - second.x) >= 220 || Math.abs(first.y - second.y) >= 120, `${first.id} overlaps ${second.id}`).toBe(true);
    }
  });
});

describe('collapsing finished Aims', () => {
  const aim = (id: string, phase: string, parent?: string) => ({ id, description: id, phase, parent_aim_id: parent, paused: false, currently_achieved: phase === 'achieved' });
  const tree = {
    aims: [aim('aim-1', 'achieved'), aim('aim-2', 'achieved', 'aim-1'), aim('aim-3', 'achieved'), aim('aim-4', 'open', 'aim-3'), aim('aim-5', 'achieved')],
    proposals: [{ id: 'proposal-1', aim_id: 'aim-2', direction: '접근', status: 'accepted', accepted_goal_id: 'goal-1' }],
    goals: [goal('goal-1', 'aim-2', 'verified'), goal('goal-2', 'aim-4', 'active')],
    edges: [],
  } as never;
  const collapse = { ...NO_GRAPH_FILTER, collapseDoneAims: true };

  it('folds a finished subtree into its Aim, counts what it hides, and leaves running work alone', () => {
    const view = graphVisibility(tree, none, collapse, true, 'columns');
    expect(view.aims.map(item => item.id)).toEqual(['aim-1', 'aim-3', 'aim-4', 'aim-5']);  // aim-2 is folded into aim-1
    expect(view.collapsed.get('aim-1')).toEqual({ aims: 1, goals: 1 });
    expect(view.goals.map(item => item.id)).toEqual(['goal-2']);
    expect(view.proposals).toEqual([]);
    expect(view.collapsed.has('aim-3')).toBe(false);  // aim-4 is still open
    expect(view.collapsible.has('aim-5')).toBe(false);  // nothing to fold
    expect(view.hidden).toEqual({ aims: 1, goals: 1 });
  });

  it('opens a folded Aim again and shows everything when the filter is off', () => {
    expect(graphVisibility(tree, none, collapse, true, 'columns', new Set(['aim-1'])).aims.map(item => item.id)).toContain('aim-2');
    expect(graphVisibility(tree, none, NO_GRAPH_FILTER, true, 'columns').aims.length).toBe(5);
  });
});
