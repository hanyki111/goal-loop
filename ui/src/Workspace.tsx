import React, { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, ReactFlow, useNodesState, type Edge, type Node, type ReactFlowInstance } from '@xyflow/react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import './action-scope.css';

type ReferenceRecord = { id: string; location: string; label: string | null; actor: string; resolved: string; exists: boolean };
type AttachmentRecord = { name: string; sha256: string; size: number; path: string | null; status: 'preserved' | 'changed' | 'missing' };
type Aim = { id: string; description: string; phase: string; paused: boolean; currently_achieved: boolean; abandoned_by_actor_id?: string | null; abandon_reason?: string | null; references?: ReferenceRecord[]; parent_aim_id?: string | null };
type GraphNode = { id: string; aim_id: string; label: string; phase: string; kind: 'aim' | 'goal'; dependency_blocked: boolean };
type Evidence = { id: string; goal_id: string; attempt_id: string; observation: string; actor: string; attachments?: AttachmentRecord[] };
type Proposal = { id: string; aim_id: string; direction: string; status: string; accepted_goal_id: string | null; proposed_by_actor_id?: string | null; closure_kind?: string | null; closed_by_actor_id?: string | null; closure_reason?: string | null; references?: ReferenceRecord[] };
type Attempt = { id: string; goal_id: string; outcome: string; operation: string; executor: string; contract_version: number };
// Folded cards and titles show only the first non-empty line of a long record.
const firstLine = (text: string) => text.trim().split(/\r?\n/).find(line => line.trim())?.trim() ?? '';
// A Proposal is one free-text direction; long ones carry criteria and Gap/Hypothesis notes on later lines.
// Lists, graph nodes, and headings show only a short title; the detail view shows the full text.
const PROPOSAL_TITLE_MAX = 120;
export const proposalTitle = (direction: string) => {
  const line = firstLine(direction);
  const sentence = line.split(/(?<=[.!?。])\s+/)[0] ?? line;
  return sentence.length > PROPOSAL_TITLE_MAX ? `${sentence.slice(0, PROPOSAL_TITLE_MAX - 1)}…` : sentence;
};
const proposalHasMore = (direction: string) => direction.trim() !== proposalTitle(direction);
// Validity says whether an Evidence correction put a Verdict under review; it does not say whether the
// Verdict still decides the Goal. The current Verdict is the latest one on the latest Attempt that no other
// Verdict superseded; every other one is history, labelled by why it no longer decides.
export function verdictRelations(verdicts: Verdict[], attemptIds: string[]): { current: Verdict | undefined; history: Array<{ verdict: Verdict; label: string }> } {
  const supersededBy = new Map(verdicts.filter(item => item.supersedes_verdict_id).map(item => [item.supersedes_verdict_id as string, item.id]));
  const lastAttempt = attemptIds.length - 1;
  const label = (item: Verdict) => supersededBy.has(item.id) ? `대체됨 → ${supersededBy.get(item.id)}` : attemptIds.indexOf(item.attempt_id) < lastAttempt ? '이전 실행의 판정' : '';
  const candidates = verdicts.filter(item => !label(item));
  const current = candidates.at(-1);
  return { current, history: verdicts.filter(item => item !== current).map(item => ({ verdict: item, label: label(item) || '이전 판정' })) };
}
type Goal = { id: string; aim_id: string; phase: string; target: string; responsibility: string; current_contract_version: number; verifying_attempt_id: string | null; held: boolean; hold_actor_id?: string | null; hold_reason?: string | null; stopped_by_actor_id?: string | null; stop_reason?: string | null; references?: ReferenceRecord[]; contract: Record<string, string | number | null>; contract_history?: Array<Record<string, string | number | null>> };
type Verdict = { id: string; goal_id: string; attempt_id: string; result: string; validity: string; evidence_ids: string[]; verifier_actor_id: string; reason?: string | null; supersedes_verdict_id?: string | null };
type AimEvaluation = { id: string; aim_id: string; validity: string; conclusion: string; evaluator_actor_id: string; verdict_ids: string[]; child_evaluation_ids?: string[] };
type Decision = { id: string; goal_id: string; kind: string; decided_by_actor_id: string; reason: string; target_attempt_id: string | null; contract_version: number | null; consumed_by_attempt_id: string | null };
type EvidenceCorrection = { id: string; corrected_evidence_id: string; submitted_by_actor_id: string; observation: string };
type ImpactReviewRecord = { id: string; trigger_verdict_id: string; correction_id: string; affected_goal_ids: string[]; affected_aim_evaluation_ids: string[] };
export type MemoryMention = { date: string; time: string; text: string; file: string; ids: string[] };
export type MemoryDraft = { record_id: string; subject_id: string; kind: string; text: string; command: string };
export type MemoryDocument = { exists: boolean; excerpt: string | null; anchor: string | null; anchor_found: boolean };
type Projection = { memory_drafts?: MemoryDraft[]; memory_mentions?: MemoryMention[]; memory_documents?: Record<string, MemoryDocument>; ao_runs?: AoRun[]; project_root?: string | null; evidence_corrections?: EvidenceCorrection[]; impact_reviews?: ImpactReviewRecord[]; decisions?: Decision[]; schema_version: number; aims: Aim[]; nodes: GraphNode[]; edges: Array<{ id: string; source: string; target: string; kind: string }>; evidence: Evidence[]; proposals: Proposal[]; attempts: Attempt[]; goals: Goal[]; verdicts: Verdict[]; aim_evaluations: AimEvaluation[] };
type Action = { kind: string; summary: string; subject: { id: string; kind: string }; command_argv: string[] };
type Next = { actions: Action[] };
type ReviewData = { under_review_verdicts: Verdict[]; under_review_aim_evaluations: AimEvaluation[]; impact_reviews: Array<{ id: string; trigger_verdict_id: string; correction_id: string; affected_aim_evaluation_ids: string[]; resolutions: Array<{ id: string; disposition: string; subject_kind: string; subject_id: string }> }> };
type Admission = { paused_aim_ids?: string[]; goal_hold_ids?: string[]; dependency_blocked_goal_ids?: string[] };
type RuntimeRequest = { request_id: string; attempt_id: string; request_kind: string; status: string; claimed_by?: string | null };
type RuntimeData = { requests: RuntimeRequest[] };
export type AoRun = { goal_id: string; ao_project_id: string; ao_goal_id: string; title?: string | null; status?: string | null; review_reason?: string | null; sessions: Array<{ id: string; status?: string | null; created_at?: string | null; updated_at?: string | null; verifiers_passed: number; verifiers_failed: number; recent_failures: Array<{ type?: string | null; reason?: string | null; at?: string | null }>; advisories?: Array<{ clause?: string | null; reason?: string | null; at?: string | null }> }>; commits: Array<{ sha: string; subject?: string | null; committed_at?: string | null }>; evidence_ids: string[]; attempt_ids: string[] };
export type AoStatusRow = { request_id: string; request_kind: string; status: string; claimed_by?: string | null; attempt_id: string; ao_goal: string; ao_goal_status?: string | null; ao_session?: string | null; ao_session_status?: string | null; waiting_for_human?: { kind: string; question: string; requested_at?: string } | null; next?: string[] };
type ActivityCard = { recorded_at: string; title: string; detail: string; subject: string | null; actor_id: string | null; status: string };
type Selection = { kind: 'aim' | 'goal' | 'attempt' | 'proposal'; id: string; focus?: 'contract' | 'add-proposal' | 'stop' } | null;
type WorkspaceRecord = { id: string; name: string; database: string | null; project?: string | null };
type WorkspaceRegistry = { workspaces: WorkspaceRecord[] };
type WorkspaceDeleteResult = { deleted_workspace_id: string; workspaces: WorkspaceRecord[] };

const input = (form: HTMLFormElement, name: string) => String(new FormData(form).get(name) ?? '').trim();
const actionIds = (action: Action, prefix: string) => action.command_argv.filter(item => new RegExp(`^${prefix}\\d`).test(item));
const stateName = (value: string) => ({ open: '진행 중', achieved: '달성됨', proposed: '제안됨', accepted: '확정됨', committed: '실행 준비됨', superseded: '대체됨', running: '실행 중', succeeded: '성공으로 기록됨', verifying: '검증 중', verified: '검증됨', failed: '실패로 기록됨', active: '실행 진행 중', stopped: '중단됨', cancelled: '취소로 기록됨', unknown: '결과 불명', closed: '닫힘', abandoned: '그만둠' }[value] ?? value);
export const actionTitle = (kind: string) => ({ create_aim: '새 목표 만들기', create_proposal: '접근안 추가', accept_proposal: '이 접근안을 과제로 확정', start_attempt: '실행 시작', start_retry_attempt: '재시도 시작', start_reverification_attempt: '재검증 실행 시작', complete_attempt: '실행 결과 기록', record_evidence: '관찰 근거 기록', record_additional_evidence: '관찰 근거 추가', begin_verification: '검증 시작', submit_verdict: '검증 판단 기록', submit_superseding_verdict: '검증 판단 다시 기록', decide_retry: '재시도 결정', decide_reverification: '재검증 결정', evaluate_aim: '목표 달성 평가', clear_goal_hold: '실행 보류 해제', resume_aim: '목표 다시 진행', resolve_verdict_review: '검증 판단 검토 종결', resolve_aim_evaluation_review: '목표 달성 평가 검토 종결', inspect_goal_dependency: '선행 과제 기다림' }[kind] ?? '다음 행동');
type PaneWidths = { left: number; right: number };
const PANE_WIDTHS_KEY = 'goal-loop.pane-widths.v1';
const UI_SCALE_KEY = 'goal-loop.ui-scale.v1';
const SCALE_OPTIONS = [80, 90, 100, 110, 125, 140] as const;
const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value));
const readPaneWidths = (): PaneWidths => {
  try {
    const saved = JSON.parse(localStorage.getItem(PANE_WIDTHS_KEY) ?? '{}') as Partial<PaneWidths>;
    return { left: clamp(Number(saved.left) || 280, 224, 360), right: clamp(Number(saved.right) || 460, 360, 640) };
  } catch { return { left: 280, right: 460 }; }
};
const readUiScale = () => {
  try { const value = Number(localStorage.getItem(UI_SCALE_KEY) ?? 100); return SCALE_OPTIONS.includes(value as typeof SCALE_OPTIONS[number]) ? value : 100; }
  catch { return 100; }
};

type ActionScope = { aimId?: string; goalId?: string; proposalId?: string };
export function actionScope(data: Projection, action: Action): ActionScope {
  // Aim-level actions (evaluation, resume, evaluation review) cite Verdicts as evidence, but they belong to the Aim, not to the cited Goal.
  if (action.subject.kind === 'aim') return { aimId: action.subject.id };
  if (action.kind === 'resolve_aim_evaluation_review') {
    const evaluationId = actionIds(action, 'aim-evaluation-')[0] ?? data.impact_reviews?.find(review => review.id === action.subject.id)?.affected_aim_evaluation_ids[0];
    const evaluation = data.aim_evaluations.find(item => item.id === evaluationId);
    if (evaluation) return { aimId: evaluation.aim_id };
  }
  if (action.subject.kind === 'impact_review') {
    const review = data.impact_reviews?.find(item => item.id === action.subject.id);
    const reviewed = data.goals.find(item => item.id === data.verdicts.find(verdict => verdict.id === review?.trigger_verdict_id)?.goal_id);
    if (reviewed) return { aimId: reviewed.aim_id, goalId: reviewed.id };
  }
  const goalId = actionIds(action, 'goal-')[0];
  const attemptId = actionIds(action, 'attempt-')[0];
  const verdictId = actionIds(action, 'verdict-')[0];
  const proposal = data.proposals.find(item => item.id === action.subject.id);
  const goal = data.goals.find(item => item.id === goalId)
    ?? (action.subject.kind === 'goal' ? data.goals.find(item => item.id === action.subject.id) : undefined)
    ?? data.goals.find(item => item.id === data.attempts.find(attempt => attempt.id === attemptId)?.goal_id)
    ?? data.goals.find(item => item.id === data.verdicts.find(verdict => verdict.id === verdictId)?.goal_id);
  if (goal) return { aimId: goal.aim_id, goalId: goal.id };
  if (proposal) return { aimId: proposal.aim_id, proposalId: proposal.id };
  if (action.subject.kind === 'aim') return { aimId: action.subject.id };
  return {};
}

export function scopedNextActions(data: Projection, actions: Action[], selection: Selection): Action[] {
  return actions.filter(action => {
    if (!selection) return action.kind === 'create_aim';
    const scope = actionScope(data, action);
    if (selection.kind === 'aim') return scope.aimId === selection.id;
    if (selection.kind === 'proposal') return scope.proposalId === selection.id;
    const selectedGoalId = selection.kind === 'goal' ? selection.id : data.attempts.find(attempt => attempt.id === selection.id)?.goal_id;
    return scope.goalId === selectedGoalId;
  });
}

function Field({ name, label, multiline = false, defaultValue = '', autoFocus = false, remember, hint, exclude, optional = false }: { name: string; label: string; multiline?: boolean; defaultValue?: string; autoFocus?: boolean; remember?: string; hint?: string; exclude?: string; optional?: boolean }) {
  const stop = (event: React.SyntheticEvent) => event.stopPropagation();
  if (remember) {
    const key = `goal-loop.actor.${remember}`;
    let saved = ''; try { saved = localStorage.getItem(key) ?? ''; } catch { /* optional convenience */ }
    if (exclude && saved === exclude) saved = '';
    return <label className="field"><span>{label}</span><input name={name} defaultValue={saved} placeholder="이름을 적어 주세요" required data-remember={remember} onKeyDown={stop} onCompositionStart={stop} onCompositionEnd={stop} />{hint && <small className="field-hint">{hint}</small>}</label>;
  }
  return <label className="field"><span>{label}{optional && <small className="field-optional"> (선택)</small>}</span>{multiline ? <textarea name={name} defaultValue={defaultValue} autoFocus={autoFocus} required={!optional} onKeyDown={stop} onCompositionStart={stop} onCompositionEnd={stop} /> : <input name={name} defaultValue={defaultValue} autoFocus={autoFocus} required={!optional} onKeyDown={stop} onCompositionStart={stop} onCompositionEnd={stop} />}{hint && <small className="field-hint">{hint}</small>}</label>;
}

function PaneSplitter({ side, value, onChange }: { side: 'left' | 'right'; value: number; onChange: (value: number) => void }) {
  const start = useRef<{ x: number; width: number } | null>(null);
  const min = side === 'left' ? 224 : 360; const max = side === 'left' ? 360 : 640;
  const move = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!start.current) return;
    const delta = event.clientX - start.current.x;
    onChange(clamp(start.current.width + (side === 'left' ? delta : -delta), min, max));
  };
  return <div className="pane-splitter" role="separator" aria-orientation="vertical" aria-label={side === 'left' ? '목표 목록 너비 조절' : '상세 보기 너비 조절'} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} tabIndex={0}
    onPointerDown={event => { start.current = { x: event.clientX, width: value }; event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerMove={move} onPointerUp={() => { start.current = null; }} onPointerCancel={() => { start.current = null; }}
    onKeyDown={event => { const direction = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0; if (!direction) return; event.preventDefault(); onChange(clamp(value + (side === 'left' ? direction : -direction) * 16, min, max)); }} />;
}

function AimComposer({ busy, submit, onCancel }: { busy: boolean; submit: (command: string, args: (form: HTMLFormElement) => string[]) => (event: FormEvent<HTMLFormElement>) => Promise<void>; onCancel: () => void }) {
  return <section className="editor-panel"><span className="eyebrow">새 목표</span><h2>어떤 변화를 만들고 싶으신가요?</h2><p className="explanation-card">목표는 이루고 싶은 변화입니다. 지금은 구체적인 작업이나 성공 판정을 정하지 않아도 됩니다.</p><form onSubmit={submit('aim-create', form => [input(form, 'description')])}><Field name="description" label="이루고 싶은 변화" multiline autoFocus /><div className="button-row"><button type="button" className="quiet" onClick={onCancel}>취소</button><button disabled={busy}>목표 기록</button></div></form></section>;
}

function ContextAction({ actions, data, busy, submit, run, showTechnical = true }: { showTechnical?: boolean; actions: Action[]; data: Projection; busy: boolean; submit: (command: string, args: (form: HTMLFormElement) => string[]) => (event: FormEvent<HTMLFormElement>) => Promise<void>; run: (command: string, args: string[], notice?: string) => Promise<void> }) {
  const action = actions[0];
  if (!action) return null;
  return <section className="context-action" key={`${action.kind}:${action.subject.id}`}><span className="eyebrow">지금 할 일</span><h3>{actionTitle(action.kind)}</h3><p>{actionExplanation(action.kind)}</p><ActionForm key={`${action.kind}:${action.subject.id}:${action.command_argv.join(' ')}`} action={action} data={data} busy={busy} submit={submit} run={run} />{showTechnical && <ActionTechnicalDetails action={action} />}</section>;
}

export function Workspace() {
  const [data, setData] = useState<Projection | null>(null);
  const [next, setNext] = useState<Next>({ actions: [] });
  const [reviews, setReviews] = useState<ReviewData>({ under_review_verdicts: [], under_review_aim_evaluations: [], impact_reviews: [] });
  const [admission, setAdmission] = useState<Admission>({});
  const [runtime, setRuntime] = useState<RuntimeData>({ requests: [] });
  const [aoStatus, setAoStatus] = useState<AoStatusRow[]>([]);
  const [activity, setActivity] = useState<ActivityCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [selection, setSelection] = useState<Selection>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [view, setView] = useState<'workspace' | 'inbox' | 'reviews' | 'runtime' | 'help'>('workspace');
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>(() => isTauri() ? [] : [{ id: 'preview', name: '미리보기', database: null }]);
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem('goal-loop.workspace.id') ?? '');
  const [archivedIds, setArchivedIds] = useState<Set<string>>(new Set());
  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState<Node>([]);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance | null>(null);
  const dragOrigin = useRef<{ x: number; y: number } | null>(null);
  const [showProposals, setShowProposals] = useState(() => { try { return localStorage.getItem('goal-loop.graph.show-proposals') !== 'false'; } catch { return true; } });
  useEffect(() => { try { localStorage.setItem('goal-loop.graph.show-proposals', String(showProposals)); } catch { /* optional preference */ } }, [showProposals]);
  const [graphLayout, setGraphLayout] = useState<GraphLayout>(() => { try { return localStorage.getItem('goal-loop.graph.layout') === 'list' ? 'list' : 'columns'; } catch { return 'columns'; } });
  useEffect(() => { try { localStorage.setItem('goal-loop.graph.layout', graphLayout); } catch { /* optional preference */ } }, [graphLayout]);
  const [graphFilter, setGraphFilter] = useState<GraphFilter>(() => { try { return { ...NO_GRAPH_FILTER, ...JSON.parse(localStorage.getItem('goal-loop.graph.filter') ?? '{}') }; } catch { return NO_GRAPH_FILTER; } });
  useEffect(() => { try { localStorage.setItem('goal-loop.graph.filter', JSON.stringify(graphFilter)); } catch { /* optional preference */ } }, [graphFilter]);
  const positionsKey = `goal-loop.graph.positions.v4.${workspaceId}${graphLayout === 'list' ? '.list' : ''}`;
  // A new layout, filter or Proposal toggle moves nodes; show the whole result instead of an empty corner.
  const refit = useRef(false);
  useEffect(() => { refit.current = true; }, [graphLayout, graphFilter, showProposals]);
  useEffect(() => {
    if (!flowInstance || !selection || !data) return;
    const goalOf = (attemptId: string) => data.attempts.find(attempt => attempt.id === attemptId)?.goal_id;
    const nodeId = selection.kind === 'attempt' ? goalOf(selection.id) : selection.kind === 'proposal' && !showProposals ? data.proposals.find(item => item.id === selection.id)?.aim_id : selection.id;
    const node = nodeId ? flowInstance.getNode(nodeId) : undefined;
    const pane = document.querySelector('.canvas-panel .react-flow')?.getBoundingClientRect();
    if (!node || !pane) return;
    const { x, y, zoom } = flowInstance.getViewport();
    const width = node.measured?.width ?? 200; const height = node.measured?.height ?? 110;
    const left = node.position.x * zoom + x; const top = node.position.y * zoom + y;
    const visible = left >= 0 && top >= 0 && left + width * zoom <= pane.width && top + height * zoom <= pane.height;
    if (!visible) void flowInstance.setCenter(node.position.x + width / 2, node.position.y + height / 2, { zoom, duration: 300 });
  }, [flowInstance, selection, data, flowNodes, showProposals]);
  const [flowEdges, setFlowEdges] = useState<Edge[]>([]);
  const [paneWidths, setPaneWidths] = useState<PaneWidths>(readPaneWidths);
  const [uiScale, setUiScale] = useState(readUiScale);
  const [composer, setComposer] = useState<'aim' | null>(null);
  const [notice, setNotice] = useState('');
  const [workspaceDialog, setWorkspaceDialog] = useState<'rename' | 'delete' | null>(null);
  const workspace = workspaces.find(item => item.id === workspaceId) ?? workspaces[0];

  // A quiet reload picks up records another session wrote through the CLI without clearing an error the user is reading.
  const load = async (quiet = false) => {
    if (isTauri() && !workspace) { setData(null); setLoading(false); return; }
    // A focus reload while one is running would only queue more CLI processes; read once more afterwards instead.
    if (quiet && reloading.current) { reloadAgain.current = true; return; }
    if (quiet) { reloading.current = true; setRefreshing(true); }
    if (!quiet) { setLoading(true); setError(''); }
    try {
      if (isTauri()) {
        const database = workspace?.database ?? undefined;
        const [projection, actions, reviewData, admissionData, runtimeData, activityData] = await Promise.all([invoke<Projection>('ui_projection', { database }), invoke<Next>('next_actions', { database }), invoke<ReviewData>('reviews', { database }), invoke<Admission>('admission', { database }), invoke<RuntimeData>('runtime_requests', { database }), invoke<ActivityCard[]>('activity', { database })]);
        setData(projection); setNext(actions); setReviews(reviewData); setAdmission(admissionData); setRuntime(runtimeData); setActivity(activityData);
        // AO 실행 상태는 AO 작업 공간 파일을 읽는다. 읽지 못해도 나머지 화면은 그대로 보인다.
        invoke<{ ao_requests: AoStatusRow[] }>('ao_status', { database }).then(result => setAoStatus(result.ao_requests ?? [])).catch(() => setAoStatus([]));
        return;
      }
      setData(await fetch('/ui-projection.json').then(response => response.json() as Promise<Projection>));
      setNext({ actions: [] }); setReviews({ under_review_verdicts: [], under_review_aim_evaluations: [], impact_reviews: [] }); setAdmission({}); setRuntime({ requests: [] }); setAoStatus([]); setActivity([]);
    } catch (cause) { if (!quiet) setError(cause instanceof Error ? cause.message : 'UI projection을 불러올 수 없습니다.'); }
    finally {
      if (!quiet) setLoading(false);
      if (quiet) {
        reloading.current = false; setRefreshing(false);
        if (reloadAgain.current) { reloadAgain.current = false; void load(true); }
      }
    }
  };
  const quietReload = useRef<() => void>(() => undefined);
  const reloading = useRef(false); const reloadAgain = useRef(false);
  const [refreshing, setRefreshing] = useState(false);
  quietReload.current = () => { if (!busy && !loading) void load(true); };
  useEffect(() => {
    const onFocus = () => quietReload.current();
    const onVisible = () => { if (document.visibilityState === 'visible') quietReload.current(); };
    window.addEventListener('focus', onFocus); document.addEventListener('visibilitychange', onVisible);
    return () => { window.removeEventListener('focus', onFocus); document.removeEventListener('visibilitychange', onVisible); };
  }, []);
  useEffect(() => { if (isTauri()) void invoke<WorkspaceRegistry>('workspaces').then(registry => { setWorkspaces(registry.workspaces); setWorkspaceId(current => registry.workspaces.some(item => item.id === current) ? current : registry.workspaces[0]?.id ?? ''); }).catch(cause => setError(String(cause))); }, []);
  useEffect(() => { void load(); }, [workspace?.database]);
  useEffect(() => { try { if (workspaceId) localStorage.setItem('goal-loop.workspace.id', workspaceId); else localStorage.removeItem('goal-loop.workspace.id'); } catch { /* workspace selection is optional local UI state */ } }, [workspaceId]);
  useEffect(() => { try { setArchivedIds(new Set(JSON.parse(localStorage.getItem(`goal-loop.archive.${workspaceId}`) ?? '[]'))); } catch { setArchivedIds(new Set()); } }, [workspaceId]);
  useEffect(() => { try { localStorage.setItem(PANE_WIDTHS_KEY, JSON.stringify(paneWidths)); } catch { /* pane widths are optional local UI state */ } }, [paneWidths]);
  useEffect(() => { try { localStorage.setItem(UI_SCALE_KEY, String(uiScale)); } catch { /* UI scale is optional local UI state */ } }, [uiScale]);
  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(''), 4500);
    return () => window.clearTimeout(timeout);
  }, [notice]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches('input, textarea, select, [contenteditable="true"]')) return;
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key === '0') { event.preventDefault(); setUiScale(100); }
      if (event.key === '-' || event.key === '_') { event.preventDefault(); setUiScale(current => SCALE_OPTIONS[Math.max(0, SCALE_OPTIONS.indexOf(current as typeof SCALE_OPTIONS[number]) - 1)]); }
      if (event.key === '=' || event.key === '+') { event.preventDefault(); setUiScale(current => SCALE_OPTIONS[Math.min(SCALE_OPTIONS.length - 1, SCALE_OPTIONS.indexOf(current as typeof SCALE_OPTIONS[number]) + 1)]); }
    };
    window.addEventListener('keydown', onKeyDown); return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    if (!data) return;
    let saved: Record<string, { x: number; y: number }> = {};
    try { saved = JSON.parse(localStorage.getItem(positionsKey) ?? '{}'); } catch { /* deterministic layout remains available */ }
    const graph = graphVisibility(data, archivedIds, graphFilter, showProposals, graphLayout);
    const visibleAims = graph.aims;
    const visibleAimIds = new Set(visibleAims.map(aim => aim.id));
    const visibleGoals = graph.goals;
    const visibleGoalIds = new Set(visibleGoals.map(goal => goal.id));
    const visibleProposals = graph.proposals;
    const layoutProposals = graph.layoutProposals;
    const LANE_GAP = 50; const SLOT = 240; const LEVEL_GAP = 90;
    const laneX = new Map<string, number>(); const proposalX = new Map<string, number>();
    const goalProposal = new Map(layoutProposals.filter(proposal => proposal.accepted_goal_id).map(proposal => [proposal.accepted_goal_id as string, proposal]));
    const drawnGoalProposal = new Set(visibleProposals.filter(proposal => proposal.accepted_goal_id).map(proposal => proposal.accepted_goal_id as string));
    const looseRows = new Map<string, number>(); const looseCount = new Map<string, number>();
    for (const goal of visibleGoals) if (!goalProposal.has(goal.id)) { const row = looseCount.get(goal.aim_id) ?? 0; looseRows.set(goal.id, row); looseCount.set(goal.aim_id, row + 1); }
    const parentOf = new Map(data.edges.filter(edge => edge.kind === 'parent').map(edge => [edge.target, edge.source]));
    const depthOf = (id: string) => { let depth = 0; for (let cursor = parentOf.get(id); cursor && depth < 20; cursor = parentOf.get(cursor)) depth += 1; return depth; };
    const PROPOSAL_Y = 190; const GOAL_Y = visibleProposals.length ? 330 : 205;
    // Sub-Aims sit one level below their parent, centred under it; each level is as tall as its tallest lane.
    const subAims = (id: string) => visibleAims.filter(aim => aim.parent_aim_id === id);
    const roots = visibleAims.filter(aim => !aim.parent_aim_id || !visibleAimIds.has(aim.parent_aim_id));
    const ownProposals = (id: string) => layoutProposals.filter(proposal => proposal.aim_id === id);
    const looseGoals = (id: string) => visibleGoals.filter(goal => goal.aim_id === id && !goalProposal.has(goal.id));
    const ownWidth = (id: string) => Math.max(1, ownProposals(id).length + (looseGoals(id).length ? 1 : 0)) * SLOT;
    const subtreeWidth = (id: string, guard = 0): number => { const children = guard > 20 ? [] : subAims(id); const childrenWidth = children.reduce((sum, child) => sum + subtreeWidth(child.id, guard + 1), 0) + Math.max(0, children.length - 1) * LANE_GAP; return Math.max(ownWidth(id), childrenWidth); };
    const laneHeight = (id: string) => { const rows = visibleGoals.filter(goal => goal.aim_id === id).map(goal => goalProposal.has(goal.id) ? depthOf(goal.id) : looseRows.get(goal.id) ?? 0); return rows.length ? GOAL_Y + Math.max(...rows) * 150 + 110 - 40 : visibleProposals.some(proposal => proposal.aim_id === id) ? PROPOSAL_Y + 110 - 40 : 110; };
    const aimLevel = new Map<string, number>(); const levelHeight: number[] = [];
    const place = (id: string, left: number, level: number, guard = 0) => {
      const width = subtreeWidth(id); const own = ownWidth(id); const laneLeft = left + (width - own) / 2;
      aimLevel.set(id, level); levelHeight[level] = Math.max(levelHeight[level] ?? 0, laneHeight(id));
      laneX.set(id, laneLeft + own / 2 - SLOT / 2);
      const proposals = ownProposals(id); proposals.forEach((proposal, index) => proposalX.set(proposal.id, laneLeft + index * SLOT));
      looseGoals(id).forEach(goal => proposalX.set(`loose:${goal.id}`, laneLeft + proposals.length * SLOT));
      if (guard > 20) return;
      const children = subAims(id); const childrenWidth = children.reduce((sum, child) => sum + subtreeWidth(child.id), 0) + Math.max(0, children.length - 1) * LANE_GAP;
      let cursor = left + (width - childrenWidth) / 2;
      for (const child of children) { place(child.id, cursor, level + 1, guard + 1); cursor += subtreeWidth(child.id) + LANE_GAP; }
    };
    let rootCursor = 0;
    for (const root of roots) { place(root.id, rootCursor, 0); rootCursor += subtreeWidth(root.id) + LANE_GAP; }
    const levelTop = (level: number) => { let top = 0; for (let index = 0; index < level; index += 1) top += (levelHeight[index] ?? 110) + LEVEL_GAP; return top; };
    const aimTop = (aimId: string) => levelTop(aimLevel.get(aimId) ?? 0);
    const position = (node: GraphNode) => {
      if (node.kind === 'aim') return { x: laneX.get(node.id) ?? 0, y: 40 + aimTop(node.id) };
      const proposal = goalProposal.get(node.id);
      if (proposal) return { x: proposalX.get(proposal.id) ?? 0, y: aimTop(node.aim_id) + GOAL_Y + depthOf(node.id) * 150 };
      return { x: proposalX.get(`loose:${node.id}`) ?? laneX.get(node.aim_id) ?? 0, y: aimTop(node.aim_id) + GOAL_Y + (looseRows.get(node.id) ?? 0) * 150 };
    };
    const proposalLabel = (proposal: Proposal) => isCriteria(proposal) ? '달성 기준' : ({ open: '검토 중', accepted: '과제로 확정됨', closed: '닫힘' }[proposal.status] ?? stateName(proposal.status));
    const proposalNodes: Node[] = visibleProposals.map(proposal => ({ id: proposal.id, position: saved[proposal.id] ?? { x: proposalX.get(proposal.id) ?? 0, y: aimTop(proposal.aim_id) + PROPOSAL_Y }, data: { label: <><strong>접근안 · Proposal <small className="node-id">{proposal.id}</small></strong><span>{proposalTitle(proposal.direction)}</span><em className={`state-badge proposal-${proposal.status}`}>{proposalLabel(proposal)}</em></> }, className: `graph-node proposal ${proposal.status} ${proposal.status === 'closed' || SETTLED_GOAL_PHASES.has(data.goals.find(goal => goal.id === proposal.accepted_goal_id)?.phase ?? '') ? 'settled' : ''}` }));
    setFlowNodes([...data.nodes.filter(node => node.kind === 'aim' ? visibleAimIds.has(node.id) : visibleGoalIds.has(node.id)).map(node => ({ id: node.id, position: saved[node.id] ?? position(node), data: { label: <><strong>{node.kind === 'aim' ? '목표 · Aim' : '과제 · Goal'} <small className="node-id">{node.id}</small></strong><span>{node.label}</span><em className={`state-badge ${node.phase}`}>{stateName(node.phase)}{node.kind === 'goal' && verificationVoided(node, data) ? ' · 검증 무효' : ''}{node.dependency_blocked ? ' · 의존성 대기' : ''}{node.kind === 'goal' && node.phase === 'active' && data.attempts.filter(item => item.goal_id === node.id).at(-1)?.outcome === 'failed' ? ' · 최근 실행 실패' : ''}{node.kind === 'goal' && data.goals.find(item => item.id === node.id)?.held ? ' · 실행 보류' : ''}</em></> }, className: `graph-node ${node.kind} ${node.dependency_blocked ? 'blocked' : ''} ${node.phase === 'stopped' || node.phase === 'abandoned' ? 'settled' : ''}` })), ...proposalNodes]);
    setFlowEdges(graphEdges(data.edges, visibleProposals, visibleAimIds, visibleGoalIds, drawnGoalProposal));
    if (refit.current && flowInstance) { refit.current = false; window.setTimeout(() => { void flowInstance.fitView({ padding: 0.08, maxZoom: 1.1, minZoom: 0.2, duration: 200 }); }, 60); }
  }, [data, archivedIds, workspaceId, setFlowNodes, showProposals, graphLayout, graphFilter, positionsKey, flowInstance]);

  const runCommand = async (commandName: string, args: string[], notice?: string): Promise<boolean> => {
    if (!isTauri()) { setError('변경은 Tauri 데스크톱 앱에서만 실행할 수 있습니다.'); return false; }
    setBusy(true); setError('');
    try {
      const result = await invoke<{ id?: string }>('goal_loop_command', { commandName, args, database: workspace?.database ?? undefined });
      setComposer(null);
      if (commandName === 'goal-accept' && result.id) {
        setSelection({ kind: 'goal', id: result.id });
        setView('workspace');
        setNotice('과제를 확정했습니다. 이어서 실행을 시작할 수 있습니다.');
      }
      if (commandName === 'aim-create' && result.id) {
        setSelection({ kind: 'aim', id: result.id });
        setView('workspace');
        setNotice(args.includes('--parent') ? '하위 목표를 기록했습니다. 이어서 접근안을 추가하거나 더 작은 하위 목표로 나눌 수 있습니다.' : '목표를 기록했습니다. 이어서 접근안을 추가할 수 있습니다.');
      }
      if ((commandName === 'proposal-create' || commandName === 'proposal-revise') && result.id) {
        setSelection({ kind: 'proposal', id: result.id });
        setView('workspace');
        setNotice(commandName === 'proposal-create' ? '접근안을 기록했습니다. 이어서 과제로 확정할 수 있습니다.' : '새 버전의 접근안을 기록했습니다. 이전 기록은 보존됩니다.');
      }
      const feedback = notice ?? actionFeedback(commandName, args);
      if (feedback) setNotice(feedback);
      await load();
      document.querySelector('.inspector')?.scrollTo({ top: 0, behavior: 'smooth' });
      return true;
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); document.querySelector('.inspector')?.scrollTo({ top: 0, behavior: 'smooth' }); return false; }
    finally { setBusy(false); }
  };
  const run = async (commandName: string, args: string[], notice?: string) => { await runCommand(commandName, args, notice); };
  // The retry decision and the retry run stay two separate durable records; the UI only collects them in one form.
  const retryNow = async (goalId: string, attemptId: string, version: number, executor: string, reason: string, operation: string): Promise<boolean> => {
    if (!isTauri()) { setError('변경은 Tauri 데스크톱 앱에서만 실행할 수 있습니다.'); return false; }
    setBusy(true); setError('');
    try {
      const database = workspace?.database ?? undefined;
      const decision = await invoke<{ id?: string }>('goal_loop_command', { commandName: 'retry-decide', args: [goalId, attemptId, executor, reason], database });
      if (!decision.id) throw new Error('재시도 결정 ID를 받지 못했습니다.');
      await invoke('goal_loop_command', { commandName: 'attempt-start', args: [goalId, String(version), executor, operation, '--retry-decision', decision.id], database });
      try { localStorage.setItem('goal-loop.actor.executor', executor); } catch { /* optional convenience */ }
      setNotice('재시도를 시작했습니다. 이전 실패 기록은 그대로 남습니다.');
      await load();
      document.querySelector('.inspector')?.scrollTo({ top: 0, behavior: 'smooth' });
      return true;
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); await load(); document.querySelector('.inspector')?.scrollTo({ top: 0, behavior: 'smooth' }); return false; }
    finally { setBusy(false); }
  };
  const submit = (command: string, args: (form: HTMLFormElement) => string[]) => async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget;
    // Remember actor names only after the record was accepted, so a rejected name is never offered again.
    if (await runCommand(command, args(form))) form.querySelectorAll<HTMLInputElement>('[data-remember]').forEach(field => { try { localStorage.setItem(`goal-loop.actor.${field.dataset.remember}`, field.value.trim()); } catch { /* optional convenience */ } });
  };
  const savePosition = (_event: unknown, node: Node) => {
    // A plain click also ends a drag gesture; only persist an actual move.
    const origin = dragOrigin.current; dragOrigin.current = null;
    if (origin && Math.abs(origin.x - node.position.x) < 2 && Math.abs(origin.y - node.position.y) < 2) return;
    try { const key = positionsKey; const saved = JSON.parse(localStorage.getItem(key) ?? '{}'); saved[node.id] = node.position; localStorage.setItem(key, JSON.stringify(saved)); } catch { /* local placement is optional */ }
  };
  const selectedAim = selection?.kind === 'aim' ? data?.aims.find(aim => aim.id === selection.id) : undefined;
  const selectedProposal = selection?.kind === 'proposal' ? data?.proposals.find(proposal => proposal.id === selection.id) : undefined;
  const selectedGoal = selection?.kind === 'goal' ? data?.goals.find(goal => goal.id === selection.id) : undefined;
  const selectedAttempt = selection?.kind === 'attempt' ? data?.attempts.find(attempt => attempt.id === selection.id) : undefined;
  const activeGoal = selectedGoal ?? (selectedAttempt ? data?.goals.find(goal => goal.id === selectedAttempt.goal_id) : undefined);
  const createWorkspace = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (!isTauri()) return; const name = input(event.currentTarget, 'workspace-name'); try { const created = await invoke<WorkspaceRecord>('workspace_create', { name }); setWorkspaces(current => [...current, created]); setWorkspaceId(created.id); setSelection(null); event.currentTarget.reset(); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };

  const openProjectFolder = async () => {
    if (!isTauri()) return;
    try {
      const folder = await invoke<string | null>('pick_folder');
      if (!folder) return;
      const name = folder.split(/[\\/]/).filter(Boolean).at(-1) ?? folder;
      const opened = await invoke<WorkspaceRecord>('workspace_open_project', { name, folder });
      setWorkspaces(current => current.some(item => item.id === opened.id) ? current : [...current, opened]);
      setWorkspaceId(opened.id); setSelection(null);
      setNotice(`${opened.name} 폴더를 프로젝트로 열었습니다. 기록은 폴더 안 .goal-loop에 저장됩니다.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };

  if (!workspace && !loading) return <WorkspaceOnboarding error={error} onCreate={createWorkspace} onOpenFolder={openProjectFolder} />;
  if (!data) return <main className="workspace loading">{error ? <section className="load-error" role="alert"><h1>Workspace를 열지 못했습니다.</h1><p>{isTauri() ? '로컬 Goal Loop 데이터에 연결하지 못했습니다.' : '브라우저 preview에는 ui-projection.json fixture가 필요합니다.'}</p><pre>{error}</pre><button onClick={() => void load()} disabled={loading}>다시 시도</button></section> : 'Goal Loop 상태를 불러오는 중…'}</main>;
  const archive = (id: string) => {
    const updated = new Set(archivedIds); updated.add(id); setArchivedIds(updated);
    try { localStorage.setItem(`goal-loop.archive.${workspaceId}`, JSON.stringify([...updated])); } catch { /* local archive is optional */ }
    // Return to the owning Aim so the user keeps their place; hiding an Aim clears the selection.
    const parentAim = data.goals.find(item => item.id === id)?.aim_id ?? data.proposals.find(item => item.id === id)?.aim_id;
    setSelection(parentAim ? { kind: 'aim', id: parentAim } : null);
    setNotice('이 PC 화면에서 숨겼습니다. 왼쪽 목록 아래 "숨긴 항목"에서 다시 볼 수 있습니다.');
  };
  const restore = (id: string) => { const updated = new Set(archivedIds); updated.delete(id); setArchivedIds(updated); try { localStorage.setItem(`goal-loop.archive.${workspaceId}`, JSON.stringify([...updated])); } catch { /* local archive is optional */ } setNotice('다시 표시합니다.'); };
  const visibleAimIds = new Set(data.aims.filter(aim => !archivedIds.has(aim.id)).map(aim => aim.id));
  const visibleGoalIds = new Set(data.goals.filter(goal => visibleAimIds.has(goal.aim_id) && !archivedIds.has(goal.id)).map(goal => goal.id));
  const visibleData: Projection = { ...data, aims: data.aims.filter(aim => visibleAimIds.has(aim.id)), goals: data.goals.filter(goal => visibleGoalIds.has(goal.id)), proposals: data.proposals.filter(proposal => visibleAimIds.has(proposal.aim_id) && !archivedIds.has(proposal.id)), attempts: data.attempts.filter(attempt => visibleGoalIds.has(attempt.goal_id)), evidence: data.evidence.filter(item => visibleGoalIds.has(item.goal_id)), verdicts: data.verdicts.filter(item => visibleGoalIds.has(item.goal_id)), aim_evaluations: data.aim_evaluations.filter(item => visibleAimIds.has(item.aim_id)), nodes: data.nodes.filter(node => node.kind === 'aim' ? visibleAimIds.has(node.id) : visibleGoalIds.has(node.id)), edges: data.edges.filter(edge => (visibleAimIds.has(edge.source) || visibleGoalIds.has(edge.source)) && (visibleAimIds.has(edge.target) || visibleGoalIds.has(edge.target))) };
  const visibleActions = next.actions.filter(action => { const scope = actionScope(data, action); return (!scope.aimId || visibleAimIds.has(scope.aimId)) && (!scope.goalId || visibleGoalIds.has(scope.goalId)) && (!scope.proposalId || !archivedIds.has(scope.proposalId)); });
  const scopedActions = scopedNextActions(visibleData, visibleActions, selection);
  const reviewCount = visibleActions.filter(action => action.kind.startsWith('resolve_')).length;
  const contextAimId = selectedAim?.id ?? activeGoal?.aim_id ?? selectedProposal?.aim_id;
  const selectedNodeId = selection?.kind === 'aim' || selection?.kind === 'goal' ? selection.id : selection?.kind === 'attempt' ? activeGoal?.id : selection?.kind === 'proposal' ? (showProposals ? selectedProposal?.id : selectedProposal?.aim_id) : undefined;
  const displayNodes = selectedNodeId ? flowNodes.map(node => node.id === selectedNodeId ? { ...node, className: `${node.className ?? ''} is-selected` } : node) : flowNodes;
  const resetLayout = () => { try { localStorage.removeItem(positionsKey); } catch { /* no storage */ } void load(); };
  const beginAim = () => { setView('workspace'); setSelection(null); setComposer('aim'); };
  const selectRecord = (value: Selection) => { setComposer(null); setSelection(value); };
  const renameWorkspace = async (name: string) => { if (!isTauri() || !workspace) return; name = name.trim(); if (!name || name === workspace.name) { setWorkspaceDialog(null); return; } try { const renamed = await invoke<WorkspaceRecord>('workspace_rename', { workspaceId: workspace.id, name }); setWorkspaces(current => current.map(item => item.id === renamed.id ? renamed : item)); setWorkspaceDialog(null); setNotice('프로젝트 이름을 바꿨습니다.'); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  const deleteWorkspace = async () => { if (!isTauri() || !workspace) return; try { const result = await invoke<WorkspaceDeleteResult>('workspace_delete', { workspaceId: workspace.id }); try { localStorage.removeItem(`goal-loop.archive.${workspace.id}`); localStorage.removeItem(`goal-loop.graph.positions.v4.${workspace.id}`); } catch { /* local preferences are already secondary */ } setWorkspaces(result.workspaces); setWorkspaceId(result.workspaces[0]?.id ?? ''); setData(null); setSelection(null); setWorkspaceDialog(null); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } };
  return <main className="workspace" style={{ '--ui-scale': String(uiScale / 100) } as React.CSSProperties}>
    <header className="app-header"><div className="app-brand"><h1>Goal Loop</h1></div><label className="workspace-switcher"><span>현재 프로젝트</span><select aria-label="현재 작업 공간" value={workspaceId} onChange={event => { setWorkspaceId(event.target.value); setSelection(null); setComposer(null); }}>{workspaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><details className="header-menu project-menu"><summary>프로젝트 관리</summary><div className="header-menu-content">{workspace?.project ? <p className="project-path" title={workspace.project}>폴더: {workspace.project}</p> : <p className="project-path">폴더 없이 만든 프로젝트입니다. 참고 문서는 전체 경로나 URL로만 연결할 수 있습니다.</p>}<button type="button" className="quiet" onClick={() => setWorkspaceDialog('rename')}>이름 변경</button><button type="button" onClick={() => void openProjectFolder()}>프로젝트 폴더 열기</button><details className="folderless-create"><summary>폴더 없이 만들기</summary><form onSubmit={createWorkspace}><input name="workspace-name" aria-label="새 작업 공간 이름" placeholder="새 프로젝트 이름" required /><button type="submit">만들기</button></form></details><button type="button" className="workspace-delete" onClick={() => setWorkspaceDialog('delete')}>{workspace?.project ? '목록에서 제거' : '프로젝트 삭제'}</button></div></details><nav aria-label="주요 화면"><button className={view === 'workspace' ? 'selected' : ''} onClick={() => setView('workspace')}>대시보드</button><button className={view === 'inbox' ? 'selected' : ''} onClick={() => setView('inbox')}>전체 작업함 {visibleActions.length ? `(${visibleActions.length})` : ''}</button><button className={view === 'reviews' ? 'selected' : ''} onClick={() => setView('reviews')}>검토함{reviewCount ? ` (${reviewCount})` : ''}</button><button className={view === 'runtime' ? 'selected' : ''} onClick={() => setView('runtime')}>실행 대기함{aoStatus.some(row => row.waiting_for_human) ? ` (사람 대기 ${aoStatus.filter(row => row.waiting_for_human).length})` : ''}</button><button className={view === 'help' ? 'selected' : ''} onClick={() => setView('help')}>도움말</button></nav><div className="header-actions">{data && <RecordSearch data={data} hiddenIds={archivedIds} onPick={value => { setView('workspace'); selectRecord(value); }} />}<details className="header-menu view-menu"><summary>보기 · {uiScale}%</summary><div className="header-menu-content"><span>화면 배율</span><div className="scale-options">{SCALE_OPTIONS.map(scale => <button key={scale} className={scale === uiScale ? 'selected' : 'quiet'} type="button" onClick={() => setUiScale(scale)}>{scale}%</button>)}</div><button type="button" className="quiet" onClick={() => setUiScale(100)}>기본 배율로 되돌리기</button></div></details>{refreshing && <span className="refresh-indicator" role="status" aria-live="polite"><i className="spinner" aria-hidden="true" />불러오는 중…</span>}<button className="quiet" onClick={() => void load()} disabled={busy}>새로고침</button></div></header>
    {error && view !== 'workspace' && <ErrorNotice raw={error} onClose={() => setError('')} />}
    {notice && <div className="toast-notice" role="status">{notice}</div>}
    {workspaceDialog && workspace && <WorkspaceDialog mode={workspaceDialog} workspace={workspace} busy={busy} onCancel={() => setWorkspaceDialog(null)} onRename={renameWorkspace} onDelete={deleteWorkspace} />}
    {view === 'workspace' && <div className="workspace-grid" style={{ '--left-pane': `${paneWidths.left}px`, '--right-pane': `${paneWidths.right}px` } as React.CSSProperties}>
      <AimNavigator aims={visibleData.aims} goals={visibleData.goals} selected={contextAimId ? { kind: 'aim', id: contextAimId } : null} onSelect={selectRecord} onCreate={beginAim} hidden={[...data.aims.filter(item => archivedIds.has(item.id)).map(item => ({ id: item.id, label: `목표 · ${item.description}` })), ...data.proposals.filter(item => archivedIds.has(item.id)).map(item => ({ id: item.id, label: `접근안 · ${proposalTitle(item.direction)}` })), ...data.goals.filter(item => archivedIds.has(item.id)).map(item => ({ id: item.id, label: `과제 · ${item.target}` }))]} onRestore={restore} />
      <PaneSplitter side="left" value={paneWidths.left} onChange={left => setPaneWidths(current => ({ ...current, left }))} />
      <section className="canvas-panel" aria-label="목표와 과제 구조"><div className="panel-heading"><div><h2>목표 구조</h2><p>목표 → 접근안 → 과제의 흐름을 봅니다. 항목을 누르면 오른쪽에 상세가 열립니다.</p></div><div className="canvas-actions"><button className="quiet" onClick={resetLayout}>배치 초기화</button></div></div><div className="graph-legend"><label className="legend-toggle layout-choice">배치 <select aria-label="그래프 배치" value={graphLayout} onChange={event => setGraphLayout(event.target.value as GraphLayout)}><option value="columns">접근안별 열</option><option value="list">목록</option></select></label><label className="legend-toggle" title={graphLayout === 'list' ? '목록 배치에서는 접근안을 그리지 않습니다' : undefined}><input type="checkbox" checked={showProposals && graphLayout === 'columns'} disabled={graphLayout === 'list'} onChange={event => setShowProposals(event.target.checked)} />접근안 표시</label><GraphFilterMenu filter={graphFilter} onChange={setGraphFilter} hidden={graphVisibility(data ?? { aims: [], goals: [], proposals: [] } as unknown as Projection, archivedIds, graphFilter, showProposals, graphLayout).hidden} /><span>{legendLine('aim')}목표 연결</span>{showProposals && graphLayout === 'columns' && <span>{legendLine('accepted')}과제로 확정</span>}<span>{legendLine('aim-parent')}하위 목표</span><span>{legendLine('parent')}상위 과제</span><span>{legendLine('dependency')}검증 의존성</span></div>{visibleData.aims.length ? <ReactFlow key={uiScale} nodes={displayNodes} edges={flowEdges} nodesConnectable={false} onInit={setFlowInstance} fitView fitViewOptions={{ padding: 0.08, maxZoom: 1.1, minZoom: 0.7 }} onNodesChange={onNodesChange} onNodeDragStart={(_, node) => { dragOrigin.current = { ...node.position }; }} onNodeDragStop={savePosition} onNodeClick={(_, node) => { if (node.id.startsWith('proposal-')) { selectRecord({ kind: 'proposal', id: node.id }); return; } const source = visibleData.nodes.find(item => item.id === node.id); selectRecord(source ? { kind: source.kind, id: source.id } : null); }}><Background gap={20} /><Controls /></ReactFlow> : <EmptyWorkspace onStart={beginAim} />}</section>
      <PaneSplitter side="right" value={paneWidths.right} onChange={right => setPaneWidths(current => ({ ...current, right }))} />
      <aside className="inspector">{error && <ErrorNotice raw={error} onClose={() => setError('')} />}<Inspector ao={aoStatus} onCreateAim={beginAim} onRetry={retryNow} hiddenIds={archivedIds} onRestore={restore} aim={selectedAim} proposal={selectedProposal} goal={activeGoal} attempt={selectedAttempt} data={data} activity={activity} admission={admission} busy={busy} onSelect={selectRecord} submit={submit} run={run} onArchive={archive} focus={selection?.focus} actions={scopedActions} allActions={visibleActions} onOpenInbox={() => setView('inbox')} composer={composer} onCancelComposer={() => setComposer(null)} /></aside>
    </div>}
    {view === 'inbox' && <WorkInbox actions={visibleActions} data={visibleData} onOpen={scope => { setView('workspace'); selectRecord(scope.goalId ? { kind: 'goal', id: scope.goalId } : scope.proposalId ? { kind: 'proposal', id: scope.proposalId } : scope.aimId ? { kind: 'aim', id: scope.aimId } : null); }} />}
    {view === 'reviews' && <ReviewInbox data={data} actions={visibleActions} busy={busy} submit={submit} run={run} onOpen={value => { setView('workspace'); selectRecord(value); }} />}
    {view === 'runtime' && <RuntimeScreen runtime={runtime} ao={aoStatus} goalOfAttempt={attemptId => data?.attempts.find(item => item.id === attemptId)?.goal_id} onOpenGoal={goalId => { setView('workspace'); selectRecord({ kind: 'goal', id: goalId }); }} />}
    {view === 'help' && <HelpCenter />}
  </main>;
}

function EmptyWorkspace({ onStart }: { onStart: () => void }) { return <div className="empty-workspace"><span className="eyebrow">처음 시작하기</span><h2>목표부터 시작하세요</h2><p>이루고 싶은 변화를 한 문장으로 기록한 뒤, 접근안과 과제를 차례로 구체화합니다.</p><button onClick={onStart}>＋ 첫 목표 만들기</button></div>; }

function WorkspaceOnboarding({ error, onCreate, onOpenFolder }: { error: string; onCreate: (event: FormEvent<HTMLFormElement>) => Promise<void>; onOpenFolder: () => Promise<void> }) {
  return <main className="workspace workspace-onboarding"><section className="onboarding-card"><span className="eyebrow">처음 시작하기</span><h1>프로젝트 폴더를 여세요</h1><p>기록은 고른 폴더 안의 <code>.goal-loop</code>에 저장됩니다. 그 폴더에서 일하는 AI 에이전트도 같은 기록을 자동으로 사용하고, 참고 문서는 폴더 기준 경로로 연결됩니다.</p>{error && <p className="error-banner" role="alert">{error}</p>}<button type="button" onClick={() => void onOpenFolder()}>프로젝트 폴더 열기</button><p className="tab-hint">OneDrive 같은 동기화 폴더는 기록 파일이 손상될 수 있으니 피하세요.</p><details className="folderless-create"><summary>폴더 없이 만들기</summary><form onSubmit={onCreate}><Field name="workspace-name" label="프로젝트 이름" /><button type="submit">만들기</button></form></details></section></main>;
}

function WorkspaceDialog({ mode, workspace, busy, onCancel, onRename, onDelete }: { mode: 'rename' | 'delete'; workspace: WorkspaceRecord; busy: boolean; onCancel: () => void; onRename: (name: string) => Promise<void>; onDelete: () => Promise<void> }) {
  const isDelete = mode === 'delete';
  return <div className="modal-backdrop" role="presentation"><section className="workspace-dialog" role="dialog" aria-modal="true" aria-labelledby="workspace-dialog-title"><span className="eyebrow">프로젝트 관리</span><h2 id="workspace-dialog-title">{isDelete ? (workspace.project ? '이 프로젝트를 목록에서 제거할까요?' : '이 프로젝트를 삭제할까요?') : '프로젝트 이름 바꾸기'}</h2>{isDelete && workspace.project ? <><p><strong>{workspace.name}</strong>을(를) 이 앱의 목록에서만 제거합니다. 폴더 안의 <code>.goal-loop</code> 기록은 그대로 남고, 같은 폴더를 다시 열면 이어서 볼 수 있습니다.</p><div className="button-row"><button type="button" className="quiet" autoFocus onClick={onCancel}>취소</button><button type="button" className="workspace-delete" disabled={busy} onClick={() => void onDelete()}>목록에서 제거</button></div></> : isDelete ? <><p><strong>{workspace.name}</strong>의 SQLite 기록과 이 PC의 보관·배치 설정을 삭제합니다. 이 작업은 되돌릴 수 없습니다.</p><p>마지막 프로젝트를 삭제하면 새 프로젝트를 만드는 시작 화면으로 돌아갑니다.</p><div className="button-row"><button type="button" className="quiet" autoFocus onClick={onCancel}>취소</button><button type="button" className="workspace-delete" disabled={busy} onClick={() => void onDelete()}>프로젝트 삭제</button></div></> : <form onSubmit={event => { event.preventDefault(); void onRename(input(event.currentTarget, 'name')); }}><Field name="name" label="새 프로젝트 이름" defaultValue={workspace.name} /><div className="button-row"><button type="button" className="quiet" onClick={onCancel}>취소</button><button disabled={busy}>이름 저장</button></div></form>}</section></div>;
}

export type RecordMatch = { id: string; kind: string; title: string; selection: NonNullable<Selection>; hidden: boolean };

// Resolves what an agent's report names (goal-89, evidence-402, ...) or part of a title to records the
// workspace can select. Records that only live inside a Goal or Aim open that Goal's run or that Aim.
export function findRecords(data: Projection, query: string, hiddenIds: Set<string> = new Set()): RecordMatch[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const goalById = new Map(data.goals.map(goal => [goal.id, goal]));
  const attemptById = new Map(data.attempts.map(attempt => [attempt.id, attempt]));
  const aimOfGoal = (goalId: string) => goalById.get(goalId)?.aim_id;
  const isHidden = (...ids: Array<string | undefined>) => ids.some(id => id !== undefined && hiddenIds.has(id));
  const runOf = (goalId: string, attemptId: string | null | undefined) => attemptId && attemptById.has(attemptId) ? { kind: 'attempt' as const, id: attemptId } : { kind: 'goal' as const, id: goalId };
  const records: RecordMatch[] = [
    ...data.aims.map(aim => ({ id: aim.id, kind: '목표', title: aim.description, selection: { kind: 'aim' as const, id: aim.id }, hidden: isHidden(aim.id) })),
    ...data.proposals.map(proposal => ({ id: proposal.id, kind: '접근안', title: proposalTitle(proposal.direction), selection: { kind: 'proposal' as const, id: proposal.id }, hidden: isHidden(proposal.id, proposal.aim_id) })),
    ...data.goals.map(goal => ({ id: goal.id, kind: '과제', title: goal.target, selection: { kind: 'goal' as const, id: goal.id }, hidden: isHidden(goal.id, goal.aim_id) })),
    ...data.attempts.map(attempt => ({ id: attempt.id, kind: '실행', title: `${goalById.get(attempt.goal_id)?.target ?? attempt.goal_id} · ${attempt.operation}`, selection: { kind: 'attempt' as const, id: attempt.id }, hidden: isHidden(attempt.goal_id, aimOfGoal(attempt.goal_id)) })),
    ...data.evidence.map(item => ({ id: item.id, kind: '관찰 근거', title: firstLine(item.observation ?? ''), selection: runOf(item.goal_id, item.attempt_id), hidden: isHidden(item.goal_id, aimOfGoal(item.goal_id)) })),
    ...data.verdicts.map(item => ({ id: item.id, kind: '검증 판단', title: `${goalById.get(item.goal_id)?.target ?? item.goal_id} · ${stateName(item.result)}`, selection: runOf(item.goal_id, item.attempt_id), hidden: isHidden(item.goal_id, aimOfGoal(item.goal_id)) })),
    ...(data.decisions ?? []).map(item => ({ id: item.id, kind: '결정', title: firstLine(item.reason ?? ''), selection: { kind: 'goal' as const, id: item.goal_id }, hidden: isHidden(item.goal_id, aimOfGoal(item.goal_id)) })),
    ...data.aim_evaluations.map(item => ({ id: item.id, kind: '목표 달성 평가', title: firstLine(item.conclusion ?? ''), selection: { kind: 'aim' as const, id: item.aim_id }, hidden: isHidden(item.aim_id) })),
  ];
  const exact = records.find(record => record.id.toLowerCase() === q);
  if (exact) return [exact];
  const titled = new Set(['목표', '접근안', '과제']);
  return records.filter(record => record.id.toLowerCase().startsWith(q) || (titled.has(record.kind) && record.title.toLowerCase().includes(q))).slice(0, 8);
}

export function RecordSearch({ data, hiddenIds, onPick }: { data: Projection; hiddenIds: Set<string>; onPick: (selection: NonNullable<Selection>) => void }) {
  const [query, setQuery] = useState('');
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const focus = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); field.current?.focus(); field.current?.select(); } };
    window.addEventListener('keydown', focus);
    return () => window.removeEventListener('keydown', focus);
  }, []);
  const matches = findRecords(data, query, hiddenIds);
  const pick = (match: RecordMatch) => { if (match.hidden) return; onPick(match.selection); setQuery(''); field.current?.blur(); };
  return <div className="record-search" role="search">
    <input ref={field} type="search" aria-label="ID나 제목으로 찾기" placeholder="ID·제목 찾기 (Ctrl+K)" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && matches[0]) { event.preventDefault(); pick(matches[0]); } else if (event.key === 'Escape') setQuery(''); }} />
    {query.trim() && <div className="record-search-results" role="listbox" aria-label="찾은 항목">{matches.length ? matches.map(match => <button type="button" role="option" aria-selected={false} key={match.id} className={match.hidden ? 'hidden-match' : ''} disabled={match.hidden} onClick={() => pick(match)}><code>{match.id}</code><span className="match-kind">{match.kind}</span><span className="match-title">{match.title}</span>{match.hidden && <small>숨김 · 목표 목록 아래 "숨긴 항목"에서 다시 보이게 하면 이동할 수 있습니다</small>}</button>) : <p className="empty-note">"{query.trim()}"에 맞는 항목을 찾을 수 없습니다.</p>}</div>}
  </div>;
}

// One colour family per connection kind. Edges and the legend both read this table, so they cannot drift apart.
export const EDGE_COLORS = { aim: '#a78bfa', proposal: '#a78bfa', accepted: '#10b981', 'aim-parent': '#475569', parent: '#3b82f6', dependency: '#f59e0b' } as const;
const edgeColor = (kind: string) => (EDGE_COLORS as Record<string, string>)[kind] ?? '#94a3b8';
const legendLine = (kind: keyof typeof EDGE_COLORS) => <i className={`legend-line ${kind}`} style={{ borderTopColor: EDGE_COLORS[kind] }} />;

// The edges the graph draws: an Aim's direct line to a Goal gives way to Aim → Proposal → Goal when that
// Proposal is drawn. Each edge carries its colour from EDGE_COLORS.
export function graphEdges(edges: Projection['edges'], proposals: Proposal[], aimIds: Set<string>, goalIds: Set<string>, drawnGoalProposal: Set<string>) {
  const shown = (id: string) => aimIds.has(id) || goalIds.has(id);
  const recorded = edges.filter(edge => !(edge.kind === 'aim' && drawnGoalProposal.has(edge.target)) && shown(edge.source) && shown(edge.target))
    .map(edge => ({ ...edge, animated: edge.kind === 'dependency', className: `edge-${edge.kind}`, style: { stroke: edgeColor(edge.kind) } }));
  const viaProposals = proposals.flatMap(proposal => [
    { id: `proposal:${proposal.aim_id}:${proposal.id}`, source: proposal.aim_id, target: proposal.id, className: 'edge-proposal', style: { stroke: EDGE_COLORS.proposal } },
    ...(proposal.accepted_goal_id && goalIds.has(proposal.accepted_goal_id)
      ? [{ id: `accepted:${proposal.id}:${proposal.accepted_goal_id}`, source: proposal.id, target: proposal.accepted_goal_id, className: 'edge-accepted', style: { stroke: EDGE_COLORS.accepted } }] : []),
  ]);
  return [...recorded, ...viaProposals];
}

export type GraphLayout = 'columns' | 'list';
export type GraphFilter = { hideDoneAims: boolean; hideDoneGoals: boolean; hideClosedProposals: boolean };
export const NO_GRAPH_FILTER: GraphFilter = { hideDoneAims: false, hideDoneGoals: false, hideClosedProposals: false };
const DONE_AIM = new Set(['achieved', 'abandoned']);
const DONE_GOAL = new Set(['verified', 'stopped']);

// What the graph draws and where Goals sit. A display filter never changes records; hidden items stay
// reachable from the navigator and the search. In the column layout each accepted Goal keeps the column of
// the Proposal it came from even when Proposal nodes are not drawn.
export function graphVisibility(data: Projection, archivedIds: Set<string>, filter: GraphFilter, showProposals: boolean, layout: GraphLayout) {
  const aims = data.aims.filter(aim => !archivedIds.has(aim.id) && !(filter.hideDoneAims && DONE_AIM.has(aim.phase)));
  const aimIds = new Set(aims.map(aim => aim.id));
  const goals = data.goals.filter(goal => aimIds.has(goal.aim_id) && !archivedIds.has(goal.id) && !(filter.hideDoneGoals && DONE_GOAL.has(goal.phase)));
  const goalIds = new Set(goals.map(goal => goal.id));
  const candidates = data.proposals.filter(proposal => aimIds.has(proposal.aim_id) && !archivedIds.has(proposal.id) && proposal.status !== 'superseded');
  const layoutProposals = layout === 'columns'
    ? candidates.filter(proposal => proposal.status !== 'closed' || !filter.hideClosedProposals).filter(proposal => !proposal.accepted_goal_id || goalIds.has(proposal.accepted_goal_id))
    : [];
  const proposals = showProposals && layout === 'columns' ? layoutProposals : [];
  return {
    // Undrawn Proposals without a Goal would only leave empty columns.
    aims, goals, proposals, layoutProposals: proposals.length ? layoutProposals : layoutProposals.filter(proposal => proposal.accepted_goal_id),
    hidden: {
      aims: data.aims.filter(aim => !archivedIds.has(aim.id)).length - aims.length,
      goals: data.goals.filter(goal => !archivedIds.has(goal.id)).length - goals.length,
    },
  };
}

export function GraphFilterMenu({ filter, onChange, hidden }: { filter: GraphFilter; onChange: (filter: GraphFilter) => void; hidden: { aims: number; goals: number } }) {
  const active = filter.hideDoneAims || filter.hideDoneGoals || filter.hideClosedProposals;
  const option = (key: keyof GraphFilter, label: string) => <label className="check"><input type="checkbox" checked={filter[key]} onChange={event => onChange({ ...filter, [key]: event.target.checked })} />{label}</label>;
  return <details className={`header-menu graph-filter${active ? ' active' : ''}`}><summary>표시 필터{active ? ` · 숨김 목표 ${hidden.aims} · 과제 ${hidden.goals}` : ''}</summary><div className="header-menu-content">
    {option('hideDoneAims', '달성·그만둔 목표 숨기기')}
    {option('hideDoneGoals', '검증·중단된 과제 숨기기')}
    {option('hideClosedProposals', '닫힌 접근안 숨기기')}
    <p className="tab-hint">이 PC 화면에서만 숨깁니다. 기록은 그대로이고, 목표 목록과 검색에서 계속 찾을 수 있습니다.</p>
    {active && <button type="button" className="quiet" onClick={() => onChange(NO_GRAPH_FILTER)}>모두 보이기</button>}
  </div></details>;
}

export function AimNavigator({ aims, goals, selected, onSelect, onCreate = () => undefined, hidden = [], onRestore = () => undefined }: { aims: Aim[]; goals: Goal[]; selected: Selection; onSelect: (value: Selection) => void; onCreate?: () => void; hidden?: Array<{ id: string; label: string }>; onRestore?: (id: string) => void }) {
  const groups = [['open', '진행 중 목표'], ['achieved', '달성된 목표'], ['abandoned', '그만둔 목표']] as const;
  // Depth-first order keeps each sub-Aim right below its parent; indentation counts only ancestors shown in the same group.
  const known = new Set(aims.map(aim => aim.id));
  const ordered: Aim[] = []; const visit = (aim: Aim, guard = 0) => { ordered.push(aim); if (guard < 20) aims.filter(child => child.parent_aim_id === aim.id).forEach(child => visit(child, guard + 1)); };
  aims.filter(aim => !aim.parent_aim_id || !known.has(aim.parent_aim_id)).forEach(aim => visit(aim));
  const byId = new Map(aims.map(aim => [aim.id, aim]));
  const indent = (aim: Aim) => { let depth = 0; for (let cursor = aim.parent_aim_id ? byId.get(aim.parent_aim_id) : undefined; cursor && depth < 20; cursor = cursor.parent_aim_id ? byId.get(cursor.parent_aim_id) : undefined) if (cursor.phase === aim.phase) depth += 1; return depth; };
  const parentOutside = (aim: Aim) => { const parent = aim.parent_aim_id ? byId.get(aim.parent_aim_id) : undefined; return parent && parent.phase !== aim.phase ? parent : undefined; };
  return <aside className="aim-navigator"><div className="panel-heading"><div><h2>목표 목록</h2><p>이루고 싶은 변화와 과제를 찾습니다.</p></div><span>{aims.length}</span></div><button className="navigator-create" onClick={onCreate}>＋ 새 목표</button>{groups.map(([phase, title]) => <section key={phase}><h3>{title.replace('Aim', '목표')}</h3>{ordered.filter(aim => aim.phase === phase).map(aim => { const depth = indent(aim); const outside = parentOutside(aim); const subCount = aims.filter(child => child.parent_aim_id === aim.id).length; return <button className={`aim-row ${depth ? 'sub-aim' : ''} ${selected?.kind === 'aim' && selected.id === aim.id ? 'active' : ''}`} style={depth ? { '--aim-depth': depth } as React.CSSProperties : undefined} key={aim.id} onClick={() => onSelect({ kind: 'aim', id: aim.id })}><span>{depth ? '↳ ' : ''}{aim.description}</span><small>{subCount ? `하위 목표 ${subCount}개 · ` : ''}과제 {goals.filter(goal => goal.aim_id === aim.id).length}개{aim.paused ? ' · 잠시 멈춤' : ''}{outside ? ` · 상위: ${outside.description}` : ''}</small></button>; })}{aims.every(aim => aim.phase !== phase) && <p className="empty-note">없음</p>}</section>)}{hidden.length > 0 && <details className="hidden-items"><summary>숨긴 항목 {hidden.length}개</summary><p>이 PC 화면에서만 숨긴 것이고, 기록은 그대로입니다.</p><ul>{hidden.map(item => <li key={item.id}><span>{item.label}</span><button type="button" className="quiet" onClick={() => onRestore(item.id)}>다시 보기</button></li>)}</ul></details>}</aside>;
}

function Inspector({ ao = [], aim, proposal, goal, attempt, data, activity, admission, busy, onSelect, submit, run, onArchive, focus, actions, allActions = [], onOpenInbox, onCreateAim, onRetry, hiddenIds, onRestore, composer, onCancelComposer }: { ao?: AoStatusRow[]; hiddenIds?: Set<string>; onRestore?: (id: string) => void; onRetry?: RetryHandler; onCreateAim?: () => void; focus?: 'contract' | 'add-proposal' | 'stop'; aim?: Aim; proposal?: Proposal; goal?: Goal; attempt?: Attempt; data: Projection; activity: ActivityCard[]; admission: Admission; busy: boolean; onSelect: (value: Selection) => void; submit: (command: string, args: (form: HTMLFormElement) => string[]) => (event: FormEvent<HTMLFormElement>) => Promise<void>; run: (command: string, args: string[], notice?: string) => Promise<void>; onArchive: (id: string) => void; actions: Action[]; allActions?: Action[]; onOpenInbox?: () => void; composer: 'aim' | null; onCancelComposer: () => void }) {
  if (composer === 'aim') return <AimComposer busy={busy} submit={submit} onCancel={onCancelComposer} />;
  if (proposal) return <ProposalInspector proposal={proposal} data={data} busy={busy} onSelect={onSelect} submit={submit} run={run} onArchive={onArchive} actions={actions} />;
  if (aim) return <AimInspector aim={aim} hiddenIds={hiddenIds} onRestore={onRestore} onCreateAim={onCreateAim} focus={focus} data={data} busy={busy} onSelect={onSelect} submit={submit} onArchive={onArchive} actions={actions} run={run} />;
  if (!goal) return <section className="inspector-empty"><span className="eyebrow">선택한 항목</span><h2>목표를 선택해 주세요</h2><p>왼쪽 목록 또는 중앙 구조에서 목표나 과제를 선택하면, 내용과 지금 할 일을 이곳에서 확인할 수 있습니다.</p><NextActionSummary actions={allActions} data={data} onSelect={onSelect} onOpenInbox={onOpenInbox} /></section>;
  return <GoalInspector ao={ao} goal={goal} attempt={attempt} onRetry={onRetry} focus={focus} data={data} activity={activity} admission={admission} busy={busy} onSelect={onSelect} submit={submit} run={run} onArchive={onArchive} actions={actions} />;
}

const SETTLED_GOAL_PHASES = new Set(['verifying', 'verified', 'stopped']);
const CLOSURE_KINDS: Record<string, string> = { declined: '채택 안 함', withdrawn: '철회', criteria: '목표의 달성 기준으로 채택' };
// A Proposal closed as `criteria` holds its Aim's achievement criteria: settled, but not rejected.
const isCriteria = (proposal: Proposal) => proposal.status === 'closed' && proposal.closure_kind === 'criteria';
const verdictResult = (value: string) => ({ satisfied: '충족됨', not_satisfied: '충족되지 않음', inconclusive: '판정 불충분' }[value] ?? value);
const verdictValidity = (value: string) => ({ valid: '유효', under_review: '재검토 중', invalidated: '무효' }[value] ?? value);
const ACTIVITY_TITLES: Record<string, string> = { 'Aim 생성': '목표 기록', 'Goal Proposal 생성': '접근안 기록', 'Goal Proposal 교체': '접근안 교체', 'Proposal 수락 및 Goal 확정': '접근안을 과제로 확정', '실행 시도 시작': '실행 시작', '실행 시도 완료': '실행 결과 기록', 'Evidence 기록': '관찰 근거 기록', '검증 시작': '검증 시작', 'Verification Verdict 제출': '검증 판단 기록', 'Evidence 정정': '관찰 근거 정정', 'Verdict 영향 검토 시작': '검증 판단 재검토 시작', 'Verdict 영향 검토 종결': '검증 판단 재검토 종결', 'Verdict 무효화': '검증 판단 무효화', 'Aim 달성 평가': '목표 달성 평가', 'Goal Hold 추가': '실행 보류 추가', 'Goal Hold 해제': '실행 보류 해제', 'Contract 버전 추가': '완료 기준 새 버전', '재시도 Decision 기록': '재시도 결정', '재검증 Decision 기록': '재검증 결정', 'Verdict 의존성 추가': '선행 과제 연결' };
const goalLabel = (goal: Goal, data: Projection, currentAimId: string) => { const aim = data.aims.find(item => item.id === goal.aim_id); return goal.aim_id === currentAimId ? goal.target : `${aim?.description ?? '다른 목표'} › ${goal.target}`; };

export function GoalInspector({ ao = [], goal, attempt, focus, data, activity, admission, busy, onSelect, submit, run, onArchive, actions, onRetry }: { ao?: AoStatusRow[]; onRetry?: RetryHandler; goal: Goal; attempt?: Attempt; focus?: 'contract' | 'add-proposal' | 'stop'; data: Projection; activity: ActivityCard[]; admission: Admission; busy: boolean; onSelect: (value: Selection) => void; submit: (command: string, args: (form: HTMLFormElement) => string[]) => (event: FormEvent<HTMLFormElement>) => Promise<void>; run: (command: string, args: string[], notice?: string) => Promise<void>; onArchive: (id: string) => void; actions: Action[] }) {
  const [tab, setTab] = useState<1 | 2 | 3 | null>(null);
  const [contractOpen, setContractOpen] = useState(false);
  const [revising, setRevising] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const stopRef = useRef<HTMLDetailsElement | null>(null);
  const contractRef = useRef<HTMLDetailsElement | null>(null);
  useEffect(() => { setTab(attempt ? 1 : null); setContractOpen(focus === 'contract'); setRevising(focus === 'contract'); setStopOpen(focus === 'stop'); if (focus === 'contract') window.setTimeout(() => contractRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 50); if (focus === 'stop') window.setTimeout(() => stopRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 50); }, [goal.id, attempt?.id, focus]);
  // A recorded revision bumps the version; close the form so the new criteria are shown read-only.
  useEffect(() => { setRevising(false); }, [goal.current_contract_version]);
  const retryAction = actions.find(action => action.kind === 'decide_retry');
  const waitAction = actions.find(action => action.kind === 'inspect_goal_dependency');
  const holdAction = actions.find(action => action.kind === 'clear_goal_hold');
  const attempts = data.attempts.filter(item => item.goal_id === goal.id);
  const evidence = data.evidence.filter(item => item.goal_id === goal.id);
  const verdicts = data.verdicts.filter(item => item.goal_id === goal.id);
  const aim = data.aims.find(item => item.id === goal.aim_id);
  const origin = data.proposals.find(item => item.accepted_goal_id === goal.id);
  const attemptNumber = (id: string) => attempts.findIndex(item => item.id === id) + 1;
  const verified = goal.phase === 'verified';
  const closed = verified || goal.phase === 'stopped';
  const current = attempts.filter(item => item.contract_version === goal.current_contract_version);
  const succeeded = current.filter(item => item.outcome === 'succeeded');
  const observed = evidence.some(item => succeeded.some(done => done.id === item.attempt_id));
  const stage = verified || goal.phase === 'verifying' || observed ? 3 : succeeded.length ? 2 : 1;
  const activeTab = tab ?? stage;
  const lastFailed = goal.phase === 'active' && current.at(-1)?.outcome === 'failed';
  const latestVerdict = verdicts[verdicts.length - 1];
  const steps = [['실행', current.length !== attempts.length ? `v${goal.current_contract_version} 기준 ${current.length}회 · 전체 ${attempts.length}회` : `${attempts.length}회`], ['관찰 근거', `${evidence.length}개`], ['검증', latestVerdict ? verdictResult(latestVerdict.result) : '아직 없음']] as const;
  const field = (key: string) => String(goal.contract[key] ?? '');
  const history = goal.contract_history ?? [goal.contract];
  const canRevise = !SETTLED_GOAL_PHASES.has(goal.phase);
  const parentEdge = data.edges.find(edge => edge.kind === 'parent' && edge.target === goal.id);
  const children = data.edges.filter(edge => edge.kind === 'parent' && edge.source === goal.id);
  const requires = data.edges.filter(edge => edge.kind === 'dependency' && edge.target === goal.id);
  const requiredBy = data.edges.filter(edge => edge.kind === 'dependency' && edge.source === goal.id);
  const goalById = (id: string) => data.goals.find(item => item.id === id);
  // Ancestors of this task: depending on one would wait forever (the parent waits for its children).
  const ancestors = new Set<string>(); for (let cursor = parentEdge?.source; cursor && !ancestors.has(cursor); cursor = data.edges.find(edge => edge.kind === 'parent' && edge.target === cursor)?.source) ancestors.add(cursor);
  const parentCandidates = data.goals.filter(item => item.id !== goal.id && item.aim_id === goal.aim_id && !requires.some(edge => edge.source === item.id) && !children.some(edge => edge.target === item.id));
  const dependencyCandidates = data.goals.filter(item => item.id !== goal.id && !requires.some(edge => edge.source === item.id) && !ancestors.has(item.id));
  const otherAimIds = [...new Set(dependencyCandidates.filter(item => item.aim_id !== goal.aim_id).map(item => item.aim_id))];
  const relationCount = (parentEdge ? 1 : 0) + children.length + requires.length + requiredBy.length;
  const dependencyBlocked = Boolean(data.nodes.find(node => node.id === goal.id)?.dependency_blocked);
  const activityIds = new Set([goal.id, ...attempts.map(item => item.id), ...evidence.map(item => item.id), ...verdicts.map(item => item.id)]);
  // Rejected/older journal cards can omit a display detail.  Activity is read-only context and
  // must never make selecting an otherwise valid Goal crash the workspace.
  const relatedActivity = activity.filter(card => [...activityIds].some(id => (card.subject ?? '').includes(id) || (card.detail ?? '').includes(id)));
  const relationRow = (id: string, label: string) => { const other = goalById(id); return other && <li key={`${label}:${id}`}><span className="relation-kind">{label}</span><button type="button" className="relation-link" onClick={() => onSelect({ kind: 'goal', id })}>{goalLabel(other, data, goal.aim_id)}</button></li>; };
  return <section className="goal-inspector">
    <div className="inspector-title"><span className="eyebrow">과제 · {stateName(goal.phase)}{verificationVoided(goal, data) ? ' · 검증 무효' : ''}{lastFailed ? ' · 최근 실행 실패' : ''} <code className="record-id">{goal.id}</code></span><div className="titled"><h2>{goal.target}</h2><ConceptHint term="과제">접근안에서 확정한 실제 일입니다. <b>완료 기준</b>(무엇을, 어디까지, 어떻게 확인할지)을 가지며, 그 기준으로 <b>실행</b>하고 <b>관찰 근거</b>를 남긴 뒤 <b>검증</b>합니다. 실행을 성공으로 기록해도 검증은 따로이고, 과제가 검증되어도 목표 달성은 따로 평가합니다.</ConceptHint></div>
      <nav className="origin-trail" aria-label="이 과제의 출처">{aim && <button type="button" className="relation-link" onClick={() => onSelect({ kind: 'aim', id: aim.id })}>목표: {aim.description}</button>}{origin && <><span aria-hidden="true">›</span><button type="button" className="relation-link" onClick={() => onSelect({ kind: 'proposal', id: origin.id })}>{origin.direction.trim() === goal.target.trim() ? '접근안 보기' : `접근안: ${proposalTitle(origin.direction)}`}</button></>}</nav>
      {verdicts.some(item => item.validity === 'under_review') && <p className="inline-warning">관찰 근거가 정정되어 이 과제의 검증 판단을 다시 확인해야 합니다. 지금 할 일에서 판단을 유지할지 무효로 할지 결정하세요.</p>}{goal.held && !holdAction && <p className="inline-warning">실행 보류 상태라서 새 실행을 시작할 수 없습니다.</p>}{!goal.held && dependencyBlocked && !waitAction && <p className="inline-warning">선행 과제가 아직 검증되지 않아 새 실행을 시작할 수 없습니다.</p>}</div>
    {actions.length === 0 && goal.phase === 'stopped' ? <section className="context-action goal-done"><span className="eyebrow">중단됨</span><h3>이 과제는 그만두었습니다</h3><p>이유: {goal.stop_reason ?? '기록 없음'}{goal.stopped_by_actor_id ? ` — ${goal.stopped_by_actor_id}` : ''}</p><button type="button" className="route-button" onClick={() => onSelect({ kind: 'aim', id: goal.aim_id, focus: 'add-proposal' })}><strong>다른 접근안으로 다시 시작 →</strong><small>같은 목표에 새 접근안을 적습니다.</small></button></section> : actions.length === 0 && verified ? <GoalVerifiedNext goal={goal} data={data} onSelect={onSelect} /> : retryAction && onRetry ? <FailureChoices goal={goal} action={retryAction} busy={busy} onSelect={onSelect} onRetry={onRetry} /> : waitAction ? <section className="context-action dependency-card"><span className="eyebrow">지금 할 일</span><h3>선행 과제를 기다리는 중입니다</h3><p>아래 과제가 검증을 통과해야 이 과제를 실행할 수 있습니다.</p>{requires.map(edge => { const other = goalById(edge.source); return other && <button type="button" className="route-button" key={edge.source} onClick={() => onSelect({ kind: 'goal', id: other.id })}><strong>{goalLabel(other, data, goal.aim_id)} →</strong><small>현재 상태: {stateName(other.phase)}{other.phase === 'stopped' ? ' — 선행 과제가 중단되어 이 과제는 실행할 수 없습니다. 이 과제도 그만두거나 다른 접근안으로 새로 시작하세요.' : ''}</small></button>; })}</section> : <ContextAction actions={actions} data={data} busy={busy} submit={submit} run={run} showTechnical={false} />}
    <div className="stage-tabs" role="tablist" aria-label="과제 진행 단계">{steps.map(([label, value], index) => { const step = (index + 1) as 1 | 2 | 3; const done = verified || step < stage; return <button type="button" role="tab" key={label} aria-selected={activeTab === step} className={`${activeTab === step ? 'selected' : ''} ${!verified && step === stage ? 'current' : done ? 'done' : ''}`} onClick={() => setTab(step)}><b>{step}. {label}</b><small>{value}{!verified && step === stage ? ' · 지금 단계' : ''}</small></button>; })}</div>
    {activeTab === 1 && <section className="aim-section" role="tabpanel"><p className="tab-hint">완료 기준에 따라 실제로 해 본 작업입니다. 결과(성공·실패)는 검증과 별개입니다.</p><AttemptTimeline attempts={attempts} evidence={evidence} verdicts={verdicts} selected={attempt?.id} busy={busy} onSelect={onSelect} run={run} currentVersion={goal.current_contract_version} decisions={(data.decisions ?? []).filter(item => item.goal_id === goal.id)} /></section>}
    {activeTab === 2 && <section className="aim-section" role="tabpanel"><p className="tab-hint">실행 뒤에 직접 확인한 사실입니다. 검증 판단은 이 근거를 인용합니다.</p>{evidence.length ? <div className="record-cards">{evidence.map(item => { const correction = (data.evidence_corrections ?? []).find(record => record.corrected_evidence_id === item.id); const cited = verdicts.some(verdict => verdict.evidence_ids?.includes(item.id) && verdict.validity === 'valid'); return <details key={item.id} className="record-card-fold evidence-card"><summary><span className="fold-head"><b>{attemptNumber(item.attempt_id)}번째 실행의 관찰 근거</b><small>{item.id} · 관찰자 {item.actor}</small></span><span className="fold-badges"><em className={`fold-chip${item.attachments?.length ? '' : ' empty'}`}>첨부 {item.attachments?.length ?? 0}</em>{cited && <em className="fold-chip cited">인용됨</em>}{correction && <em className="fold-chip corrected">정정됨</em>}</span><span className="fold-preview">{firstLine(item.observation)}</span></summary><div className="fold-body"><p className={`observation-text${correction ? ' corrected-original' : ''}`}>{item.observation}</p>{item.attachments?.length ? <AttachmentList items={item.attachments} /> : null}{correction && <p className="correction-note observation-text">정정: {correction.observation} <small>— {correction.submitted_by_actor_id}</small></p>}{!correction && cited && <details className="correct-evidence"><summary>정정하기</summary><p className="tab-hint">관찰이 잘못되었다면 정정을 남깁니다. 원래 기록은 지워지지 않고, 이 근거를 인용한 검증 판단은 다시 확인해야 합니다.</p><form onSubmit={submit('evidence-correct', form => [item.id, input(form, 'actor'), input(form, 'observation')])}><Field name="observation" label="바로잡은 관찰" multiline /><Field name="actor" label="정정자" remember="observer" /><button disabled={busy}>정정 기록</button></form></details>}</div></details>; })}</div> : <p className="empty-note">아직 관찰 근거가 없습니다. 실행을 성공으로 기록하면 관찰 근거를 남길 수 있습니다.</p>}</section>}
    {activeTab === 3 && <section className="aim-section" role="tabpanel"><p className="tab-hint">실행한 사람과 다른 검증자가 관찰 근거를 인용해 완료 기준 충족 여부를 판정한 기록입니다.</p>{verdicts.length ? (() => { const { current, history } = verdictRelations(verdicts, attempts.map(item => item.id)); const verdictCard = (item: Verdict, chip: React.ReactNode, isCurrent: boolean) => <details key={item.id} className={`record-card-fold verdict-card${isCurrent ? ' current' : ' history'}`}><summary><span className="fold-head"><b>{verdictResult(item.result)}</b><small>{attemptNumber(item.attempt_id)}번째 실행 · 검증자 {item.verifier_actor_id} · 인용 근거 {(item.evidence_ids ?? []).length}개</small></span><span className="fold-badges">{chip}{item.validity !== 'valid' && <em className={`state-badge verdict-${item.validity}`}>{verdictValidity(item.validity)}</em>}</span>{item.reason && <span className="fold-preview">{firstLine(item.reason)}</span>}</summary><div className="fold-body">{item.reason && <p className="observation-text verdict-reason">판단 이유: {item.reason}</p>}{(item.evidence_ids ?? []).length > 0 && <ul className="cited-evidence" aria-label="인용한 관찰 근거">{item.evidence_ids.map(id => <li key={id}><b>{id}</b> · {firstLine(evidence.find(record => record.id === id)?.observation ?? '')}</li>)}</ul>}</div></details>; const historyCards = history.map(({ verdict, label }) => verdictCard(verdict, <em className="fold-chip relation">{label}</em>, false)); return <div className="record-cards">{current && verdictCard(current, <em className="fold-chip current">현재 판정</em>, true)}{history.length > 0 && (current ? <details className="verdict-history"><summary>이전 판정 {history.length}건</summary><div className="record-cards">{historyCards}</div></details> : historyCards)}</div>; })() : <p className="empty-note">{goal.phase === 'verifying' ? '검증이 진행 중입니다. 위의 지금 할 일에서 판정을 기록하세요.' : '아직 검증 판단이 없습니다. 관찰 근거를 남긴 뒤 검증을 시작하면 판정할 수 있습니다.'}</p>}</section>}
    <div className="goal-sections">
      <details ref={contractRef} className="goal-section" open={contractOpen} onToggle={event => setContractOpen((event.target as HTMLDetailsElement).open)}><summary>완료 기준 <small>v{goal.current_contract_version}</small></summary>
        {!revising ? <><dl className="contract-view"><dt>실행 종류</dt><dd>{({ produce: '만들기', research: '조사하기', decide: '결정하기' }[goal.responsibility] ?? goal.responsibility)}</dd><dt>실행 범위</dt><dd>{field('operational_scope')}</dd><dt>성공 기준</dt><dd>{field('success_criterion')}</dd><dt>검증 방법</dt><dd>{field('verification_specification')}</dd></dl>{history.length > 1 && <details className="contract-history"><summary>이전 버전 보기 ({history.length - 1}개)</summary>{history.slice(0, -1).reverse().map(version => <div className="contract-version" key={String(version.version)}><strong>v{String(version.version)}</strong><dl className="contract-view">{([['operational_scope', '실행 범위'], ['success_criterion', '성공 기준'], ['verification_specification', '검증 방법']] as const).map(([key, label]) => <React.Fragment key={key}><dt>{label}</dt><dd className={String(version[key] ?? '') !== field(key) ? 'changed' : ''}>{String(version[key] ?? '')}{String(version[key] ?? '') !== field(key) && <small> · 지금과 다름</small>}</dd></React.Fragment>)}</dl></div>)}</details>}{canRevise ? <button type="button" className="secondary-action" onClick={() => setRevising(true)}>기준 바꾸기</button> : <p className="tab-hint">검증을 시작했거나 끝난 과제의 기준은 바꿀 수 없습니다.</p>}</>
        : <form onSubmit={submit('contract-revise', form => [goal.id, '--scope', input(form, 'scope'), '--criterion', input(form, 'criterion'), '--verification', input(form, 'verification'), '--actor', input(form, 'actor'), '--reason', input(form, 'reason')])}><p className="tab-hint">바꾸면 v{goal.current_contract_version + 1}로 기록됩니다. 이미 한 실행의 기록은 바뀌지 않습니다.</p><Field name="scope" label="실행 범위" multiline defaultValue={field('operational_scope')} /><Field name="criterion" label="성공 기준" multiline defaultValue={field('success_criterion')} /><Field name="verification" label="검증 방법" multiline defaultValue={field('verification_specification')} /><Field name="actor" label="결정자" remember="decider" /><Field name="reason" label="바꾸는 이유" multiline /><div className="button-row"><button type="button" className="quiet" onClick={() => setRevising(false)}>취소</button><button disabled={busy || !canRevise}>새 기준 기록</button></div></form>}
      </details>
      <AoRunSection goal={goal} data={data} ao={ao} onSelect={onSelect} />
      <MemorySection data={data} ids={[goal.id, ...data.attempts.filter(item => item.goal_id === goal.id).map(item => item.id), ...data.evidence.filter(item => item.goal_id === goal.id).map(item => item.id), ...data.verdicts.filter(item => item.goal_id === goal.id).map(item => item.id), ...(data.decisions ?? []).filter(item => item.goal_id === goal.id).map(item => item.id)]} />
      <ReferenceSection subjectId={goal.id} references={goal.references ?? []} projectRoot={data.project_root ?? null} busy={busy} submit={submit} documents={data.memory_documents} />
      <details className="goal-section"><summary>다른 과제와의 연결 <small>{relationCount ? `${relationCount}개` : '없음'}</small></summary>
        {relationCount > 0 && <ul className="relation-list">{parentEdge && relationRow(parentEdge.source, '상위 과제')}{children.map(edge => relationRow(edge.target, '하위 과제'))}{requires.map(edge => relationRow(edge.source, '선행 과제'))}{requiredBy.map(edge => relationRow(edge.target, '이 과제를 기다림'))}</ul>}
        {closed ? <p className="tab-hint">{relationCount ? '끝난 과제라서 연결을 더 추가하지 않습니다.' : '끝난 과제라서 새 연결을 추가하지 않습니다.'}</p> : <p className="tab-hint">선행 과제를 연결하면, 그 과제가 검증을 통과해야 이 과제를 실행할 수 있습니다. 상위 과제는 같은 목표 안에서 하나만 정할 수 있습니다.</p>}
        {!closed && !parentEdge && parentCandidates.length > 0 && <form key={`parent-${relationCount}`} onSubmit={submit('goal-parent-add', form => [goal.id, input(form, 'other')])}><label className="field"><span>상위 과제 정하기</span><select name="other" required defaultValue=""><option value="" disabled>같은 목표의 과제 선택…</option>{parentCandidates.map(item => <option value={item.id} key={item.id}>{item.target}</option>)}</select></label><button disabled={busy}>상위 과제 연결</button></form>}
        {!closed && dependencyCandidates.length > 0 && <form key={`dependency-${relationCount}`} onSubmit={submit('goal-dependency-add', form => [goal.id, input(form, 'other')])}><label className="field"><span>선행 과제 추가</span><select name="other" required defaultValue=""><option value="" disabled>과제 선택…</option>{dependencyCandidates.some(item => item.aim_id === goal.aim_id) && <optgroup label="이 목표의 과제">{dependencyCandidates.filter(item => item.aim_id === goal.aim_id).map(item => <option value={item.id} key={item.id}>{item.target}</option>)}</optgroup>}{otherAimIds.map(aimId => <optgroup key={aimId} label={`다른 목표 · ${data.aims.find(item => item.id === aimId)?.description ?? ''}`}>{dependencyCandidates.filter(item => item.aim_id === aimId).map(item => <option value={item.id} key={item.id}>{item.target}</option>)}</optgroup>)}</select></label><button disabled={busy}>선행 과제 연결</button></form>}
      </details>
      {!closed && <details className="goal-section" open={goal.held}><summary>실행 보류 <small>{goal.held ? '보류 중' : '없음'}</small></summary>{goal.held ? <><p className="hold-reason">사유: {goal.hold_reason ?? '기록 없음'}{goal.hold_actor_id && goal.hold_actor_id !== 'system' ? ` — ${goal.hold_actor_id}` : ''}</p><p className="tab-hint">보류를 해제해도 실행이 자동으로 시작되지는 않습니다.</p>{holdAction ? <p className="tab-hint">위의 지금 할 일에서 해제할 수 있습니다.</p> : <button type="button" className="secondary-action" disabled={busy} onClick={() => void run('goal-hold-clear', [goal.id])}>실행 보류 해제</button>}</> : <form onSubmit={submit('goal-hold-add', form => [goal.id, '--actor', input(form, 'actor'), '--reason', input(form, 'reason')])}><p className="tab-hint">외부 사정으로 당분간 새 실행을 막아 두고 싶을 때 사용합니다. 이미 진행 중인 실행은 멈추지 않습니다.</p><Field name="actor" label="결정자" remember="decider" /><Field name="reason" label="보류 사유" multiline /><button className="secondary" disabled={busy}>실행 보류 추가</button></form>}</details>}
      {!SETTLED_GOAL_PHASES.has(goal.phase) || goal.phase === 'verifying' ? <details ref={stopRef} className="goal-section" open={stopOpen} onToggle={event => setStopOpen((event.target as HTMLDetailsElement).open)}><summary>과제 그만두기</summary><form onSubmit={submit('goal-stop', form => [goal.id, '--actor', input(form, 'actor'), '--reason', input(form, 'reason')])}><p className="tab-hint">되돌릴 수 없습니다. 이 과제에서 새 실행과 검증을 할 수 없게 되고, 진행 중인 실행의 결과는 계속 기록할 수 있습니다. 다시 하려면 새 접근안으로 시작합니다.</p><Field name="actor" label="결정자" remember="decider" /><Field name="reason" label="그만두는 이유" multiline /><button className="danger-action" disabled={busy}>과제 그만두기</button></form></details> : null}
      <details className="goal-section"><summary>활동 기록 <small>{relatedActivity.length}건</small></summary>{relatedActivity.length ? <ol className="activity-list">{relatedActivity.map((card, index) => <li key={`${card.recorded_at}:${index}`}><strong>{ACTIVITY_TITLES[card.title] ?? card.title}</strong><small>{new Date(card.recorded_at).toLocaleString()} · {card.actor_id && card.actor_id !== 'system' ? card.actor_id : '앱'}</small><details className="technical-details"><summary>원문</summary><code>{card.detail}</code></details></li>)}</ol> : <p className="empty-note">아직 활동 기록이 없습니다.</p>}</details>
    </div>
    <div className="inspector-footer"><TechnicalInfo rows={[['내부 ID', goal.id], ['완료 기준 버전', `v${goal.current_contract_version}`], ...(attempts.length ? [['실행 ID', attempts.map(item => item.id).join(', ')] as [string, string]] : []), ...(actions[0] ? [['다음 행동 명령', actions[0].command_argv.join(' ')] as [string, string]] : [])]} /><details className="local-management"><summary>화면 관리</summary><p>목록에서 보고 싶지 않을 때 이 PC 화면에서만 숨깁니다. 기록을 삭제하거나 상태를 바꾸지 않습니다.</p><button className="quiet archive-button" onClick={() => onArchive(goal.id)}>이 과제 숨기기</button></details></div>
  </section>;
}


export function ProposalInspector({ proposal, data, busy, onSelect, submit, onArchive, actions, run }: { proposal: Proposal; data: Projection; busy: boolean; onSelect: (value: Selection) => void; submit: (command: string, args: (form: HTMLFormElement) => string[]) => (event: FormEvent<HTMLFormElement>) => Promise<void>; onArchive: (id: string) => void; actions: Action[]; run: (command: string, args: string[], notice?: string) => Promise<void> }) {
  const aim = data.aims.find(item => item.id === proposal.aim_id);
  const goal = proposal.accepted_goal_id ? data.goals.find(item => item.id === proposal.accepted_goal_id) : undefined;
  const status = isCriteria(proposal) ? '목표의 달성 기준' : ({ open: '과제로 만들 수 있음', proposed: '과제로 만들 수 있음', accepted: '과제로 확정됨', superseded: '새 버전으로 대체됨', closed: '닫힘' }[proposal.status] ?? stateName(proposal.status));
  return <section className="proposal-inspector"><span className="eyebrow">접근안 · {status} <code className="record-id">{proposal.id}</code></span><div className="titled"><h2>{proposalTitle(proposal.direction)}</h2><ConceptHint term="접근안">목표에 다가가기 위한 후보 접근법입니다. 같은 목표에 여러 접근안을 적어 비교할 수 있고, 그중 하나를 <b>과제로 확정</b>하면 실제로 진행됩니다. 적어 두는 것만으로는 실행이 시작되지 않습니다.</ConceptHint></div>{proposalHasMore(proposal.direction) && <section className="proposal-body" aria-label="접근안 전체 내용">{proposal.direction.trim()}</section>}<dl className="proposal-meta"><dt>연결된 목표</dt><dd><button className="link-inline" onClick={() => onSelect({ kind: 'aim', id: proposal.aim_id })}>{aim?.description ?? '목표 보기'}</button></dd><dt>현재 상태</dt><dd>{status}</dd>{proposal.proposed_by_actor_id && <><dt>제안</dt><dd>{proposal.proposed_by_actor_id}</dd></>}</dl><ContextAction actions={actions} data={data} busy={busy} submit={submit} run={run} />{proposal.status === 'closed' && <div className="settled-note"><p>이 접근안은 닫혔습니다 · {CLOSURE_KINDS[proposal.closure_kind ?? ''] ?? proposal.closure_kind}</p><p>이유: {proposal.closure_reason}{proposal.closed_by_actor_id ? ` — ${proposal.closed_by_actor_id}` : ''}</p><button type="button" className="relation-link" onClick={() => onSelect({ kind: 'aim', id: proposal.aim_id, focus: 'add-proposal' })}>다른 접근안 추가 →</button></div>}{goal && (SETTLED_GOAL_PHASES.has(goal.phase) ? <div className="settled-note"><p>이 접근안으로 확정한 과제는 {goal.phase === 'verifying' ? '검증을 진행하고 있습니다' : goal.phase === 'stopped' ? '중단되었습니다' : '검증을 마쳤습니다'}.</p><button type="button" className="relation-link" onClick={() => onSelect({ kind: 'goal', id: goal.id })}>→ 과제: {goal.target} · {stateName(goal.phase)}</button></div> : <button className="link-row" onClick={() => onSelect({ kind: 'goal', id: goal.id })}><span>{goal.target}</span><small>확정된 과제 열기 →</small></button>)}{proposal.status === 'superseded' && <p className="tab-hint">이 접근안은 새 버전으로 대체되었습니다. 이전 기록은 보존됩니다.</p>}{proposal.status === 'accepted' && goal && (SETTLED_GOAL_PHASES.has(goal.phase)
      ? <section className="change-routes"><h3>다음에 할 수 있는 일</h3><div className="route-buttons"><button type="button" className="route-button" onClick={() => onSelect({ kind: 'aim', id: proposal.aim_id, focus: 'add-proposal' })}><strong>다른 접근안 추가 →</strong><small>결과가 충분하지 않거나 다른 방법을 시도하려면, 같은 목표에 새 접근안을 적습니다.</small></button></div></section>
      : <section className="change-routes"><h3>확정된 내용을 바꾸고 싶으신가요?</h3><p className="tab-hint">확정된 접근안은 과제의 근거라서 직접 고치지 않습니다. 대신 다음 중 하나를 선택하세요.</p><div className="route-buttons"><button type="button" className="route-button" onClick={() => onSelect({ kind: 'goal', id: goal.id, focus: 'contract' })}><strong>과제의 완료 기준 바꾸기 →</strong><small>범위, 성공 기준, 검증 방법을 새 버전으로 기록합니다.</small></button><button type="button" className="route-button" onClick={() => onSelect({ kind: 'aim', id: proposal.aim_id, focus: 'add-proposal' })}><strong>다른 접근안 추가 →</strong><small>접근 자체를 바꾸려면 같은 목표에 새 접근안을 적습니다.</small></button></div></section>)}{(proposal.status === 'open' || proposal.status === 'proposed') && <details className="goal-section"><summary>과제로 만들지 않고 닫기</summary><form onSubmit={submit('proposal-close', form => [proposal.id, '--kind', input(form, 'kind'), '--actor', input(form, 'actor'), '--reason', input(form, 'reason')])}><p className="tab-hint">이 접근안을 과제로 만들지 않기로 했다면 닫습니다. 기록은 남고, 다시 열지 않습니다. 숨기기와 달리 모든 화면과 AI 에이전트에게 닫힌 상태로 보입니다.</p><label className="field"><span>닫는 종류</span><select name="kind" defaultValue="declined"><option value="declined">채택 안 함 — 비교한 뒤 고르지 않았습니다</option><option value="withdrawn">철회 — 제안을 거둬들입니다</option><option value="criteria">달성 기준 — 이 접근안을 목표의 달성 기준으로 남깁니다</option></select></label><Field name="actor" label="결정자" remember="decider" /><Field name="reason" label="닫는 이유" multiline /><button className="secondary" disabled={busy}>접근안 닫기</button></form></details>}<MemorySection data={data} ids={[proposal.id]} /><ReferenceSection subjectId={proposal.id} references={proposal.references ?? []} projectRoot={data.project_root ?? null} busy={busy} submit={submit} documents={data.memory_documents} /><section className="local-management"><h3>화면 관리</h3><p>더 이상 목록에서 보고 싶지 않을 때만 이 PC 화면에서 숨깁니다. 기록을 삭제하지 않습니다.</p><button type="button" className="quiet archive-button" onClick={() => onArchive(proposal.id)}>이 접근안 숨기기</button></section></section>;
}

const AIM_ACTION_KINDS = new Set(['create_proposal', 'evaluate_aim', 'resume_aim', 'resolve_aim_evaluation_review']);

export function ConceptHint({ term, children }: { term: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return <><button type="button" className={`concept-toggle ${open ? 'open' : ''}`} aria-expanded={open} aria-label={`${term} 설명 ${open ? '닫기' : '보기'}`} title={`${term}란?`} onClick={() => setOpen(value => !value)}>ⓘ</button>{open && <div className="concept-note" role="note">{children}</div>}</>;
}

export function AimInspector({ aim, focus, data, busy, onSelect, submit, onArchive = () => undefined, actions = [], run = async () => undefined, onCreateAim, hiddenIds = new Set<string>(), onRestore }: { hiddenIds?: Set<string>; onRestore?: (id: string) => void; onCreateAim?: () => void; focus?: 'contract' | 'add-proposal' | 'stop'; aim: Aim; data: Projection; busy: boolean; onSelect: (value: Selection) => void; submit: (command: string, args: (form: HTMLFormElement) => string[]) => (event: FormEvent<HTMLFormElement>) => Promise<void>; onArchive?: (id: string) => void; actions?: Action[]; run?: (command: string, args: string[], notice?: string) => Promise<void> }) {
  const [addingProposal, setAddingProposal] = useState(false);
  const [tab, setTab] = useState<1 | 2 | 3 | null>(null);
  useEffect(() => { setAddingProposal(focus === 'add-proposal'); setTab(focus === 'add-proposal' ? 1 : null); }, [aim.id, focus]);
  const goals = data.goals.filter(goal => goal.aim_id === aim.id && !hiddenIds.has(goal.id)); const proposals = data.proposals.filter(proposal => proposal.aim_id === aim.id && !hiddenIds.has(proposal.id));
  const hiddenChildren = [...data.proposals.filter(item => item.aim_id === aim.id && hiddenIds.has(item.id)).map(item => ({ id: item.id, label: `접근안 · ${proposalTitle(item.direction)}` })), ...data.goals.filter(item => item.aim_id === aim.id && hiddenIds.has(item.id)).map(item => ({ id: item.id, label: `과제 · ${item.target}` }))];
  const evaluations = data.aim_evaluations.filter(item => item.aim_id === aim.id);
  const parentAim = aim.parent_aim_id ? data.aims.find(item => item.id === aim.parent_aim_id) : undefined;
  const subAims = data.aims.filter(item => item.parent_aim_id === aim.id && !hiddenIds.has(item.id));
  const proposalStatus = (proposal: Proposal) => isCriteria(proposal) ? '달성 기준' : ({ open: '검토 중', proposed: '검토 중', accepted: '과제로 확정됨', superseded: '새 버전으로 대체됨', closed: '닫힘' }[proposal.status] ?? stateName(proposal.status));
  const criteria = proposals.filter(isCriteria);
  const aimAction = actions.find(action => AIM_ACTION_KINDS.has(action.kind));
  const childActions = actions.filter(action => !AIM_ACTION_KINDS.has(action.kind));
  const isOpen = aim.phase === 'open';
  const proposalForm = <form onSubmit={submit('proposal-create', form => [aim.id, input(form, 'direction')])}><Field name="direction" label="어떤 접근으로 이 변화에 다가갈까요?" multiline /><div className="button-row">{aimAction?.kind !== 'create_proposal' && <button type="button" className="quiet" onClick={() => setAddingProposal(false)}>취소</button>}<button disabled={busy}>접근안 기록</button></div></form>;
  // The approach stage is current again when every approach is closed and every task stopped.
  const liveGoals = goals.filter(goal => goal.phase !== 'stopped');
  const openApproaches = proposals.filter(proposal => proposal.status === 'open' || proposal.status === 'proposed');
  // An Aim split into sub-Aims advances through them; its own approach stage is optional, not "current".
  const splitOnly = subAims.length > 0 && proposals.length === 0 && goals.length === 0;
  const subAimAchieved = subAims.some(item => data.aim_evaluations.some(evaluation => evaluation.aim_id === item.id && evaluation.validity === 'valid'));
  const stage = evaluations.length || goals.some(goal => goal.phase === 'verified') || subAimAchieved ? 3 : openApproaches.length || liveGoals.length ? 2 : 1;
  const finished = aim.phase !== 'open';
  const openCount = proposals.filter(proposal => proposal.status === 'open' || proposal.status === 'proposed').length;
  const abandoned = aim.phase === 'abandoned';
  const steps = [['접근안', openCount && stage > 1 && !finished ? `${proposals.length}개 · 검토 중 ${openCount}개` : `${proposals.length}개`], ['과제', `${goals.length}개`], ['달성 평가', evaluations.length ? '기록됨' : '없음']] as const;
  const activeTab = tab ?? stage;
  return <section className="aim-inspector">
    <span className="eyebrow">{parentAim ? '하위 목표' : '목표'} · {stateName(aim.phase)} <code className="record-id">{aim.id}</code></span>
    {parentAim && <button type="button" className="relation-link parent-aim-link" onClick={() => onSelect({ kind: 'aim', id: parentAim.id })}>↑ 상위 목표: {parentAim.description}</button>}
    <div className="titled"><h2>{aim.description}</h2><ConceptHint term="목표">이루고 싶은 변화입니다. 아직 구체적인 작업이 아니며, <b>접근안</b>(접근법)을 적고 그중 하나를 <b>과제</b>로 확정해 실제로 진행합니다. 과제가 검증되어도 목표는 자동으로 달성되지 않고, 따로 <b>달성 평가</b>를 기록해야 합니다.</ConceptHint></div>
    {aim.paused && <p className="inline-warning">잠시 멈춘 목표라서 새 실행을 시작할 수 없습니다.</p>}
    {abandoned && <section className="context-action aim-abandoned"><span className="eyebrow">그만둠</span><h3>이 목표는 그만두었습니다</h3><p>이유: {aim.abandon_reason ?? '기록 없음'}{aim.abandoned_by_actor_id ? ` — ${aim.abandoned_by_actor_id}` : ''}</p><p>기록은 그대로 남습니다. 이 목표에서는 새 접근안, 과제 확정, 실행을 할 수 없습니다.</p>{onCreateAim && <button type="button" className="secondary-action" onClick={onCreateAim}>＋ 새 목표 만들기</button>}</section>}{!isOpen && !abandoned && <section className="context-action aim-done"><span className="eyebrow">달성됨</span><h3>{evaluations.some(item => item.validity === 'under_review') ? '달성 평가가 재검토 중입니다' : '이 목표는 달성되었습니다'}</h3><p>{evaluations.some(item => item.validity === 'under_review') ? '달성 판단의 근거가 정정되었습니다. 아래 지금 할 일에서 평가를 유지할지 무효로 할지 결정하세요. 과제의 검증 판단이 먼저 재검토 중이라면, 그 과제에서 먼저 결정합니다.' : '기록은 그대로 남고, 이 목표에는 새 접근안이나 과제를 추가하지 않습니다. 새로 이루고 싶은 변화가 생기면 새 목표를 만드세요.'}</p>{onCreateAim && !evaluations.some(item => item.validity === 'under_review') && <button type="button" className="secondary-action" onClick={onCreateAim}>＋ 새 목표 만들기</button>}</section>}
    {aimAction ? (aimAction.kind === 'create_proposal'
      ? <section className="context-action"><span className="eyebrow">지금 할 일</span><div className="titled"><h3>접근안 추가</h3><ConceptHint term="접근안">목표에 다가가기 위한 후보 접근법입니다. 여러 개를 적어 비교할 수 있고, 적는 것만으로는 실행이 시작되지 않습니다.</ConceptHint></div><p>이 변화에 다가갈 접근법을 하나 적어 주세요.</p>{proposalForm}</section>
      : <ContextAction actions={[aimAction]} data={data} busy={busy} submit={submit} run={run} showTechnical={false} />)
      : splitOnly && isOpen && childActions.length === 0 ? <section className="context-action child-actions"><span className="eyebrow">지금 할 일</span><p>이 목표는 하위 목표로 나누어 진행합니다. 하위 목표를 골라 이어서 진행하세요.</p>{subAims.filter(item => item.phase === 'open').map(item => <button type="button" className="next-summary-item" key={item.id} onClick={() => onSelect({ kind: 'aim', id: item.id })}><strong>{item.description}</strong><small>하위 목표{item.paused ? ' · 잠시 멈춤' : ''}</small></button>)}</section>
      : childActions.length > 0 && <section className="context-action child-actions"><span className="eyebrow">지금 할 일</span><p>다음 행동은 아래 항목에서 이어집니다.</p>{childActions.map(action => { const scope = actionScope(data, action); const goal = data.goals.find(item => item.id === scope.goalId); const proposal = data.proposals.find(item => item.id === scope.proposalId); return <button type="button" className="next-summary-item" key={`${action.kind}:${action.subject.id}`} onClick={() => onSelect(goal ? { kind: 'goal', id: goal.id } : { kind: 'proposal', id: proposal?.id ?? action.subject.id })}><strong>{actionTitle(action.kind)}</strong><small>{goal ? `과제 — ${goal.target}` : `접근안 — ${proposal?.direction ?? ''}`}</small></button>; })}</section>}
    {criteria.length > 0 && <section className="aim-criteria" aria-label="달성 기준"><h3>달성 기준</h3><p className="tab-hint">이 목표를 이루었다고 판단할 기준입니다. 접근안으로 적은 뒤 달성 기준으로 채택했습니다.</p>{criteria.map(item => <button type="button" className="relation-link" key={item.id} onClick={() => onSelect({ kind: 'proposal', id: item.id })}>{proposalTitle(item.direction)} →</button>)}</section>}
    <div className="stage-tabs" role="tablist" aria-label="목표 진행 단계">{steps.map(([label, value], index) => { const step = (index + 1) as 1 | 2 | 3; return <button type="button" role="tab" key={label} aria-selected={activeTab === step} className={`${activeTab === step ? 'selected' : ''} ${!finished && step === stage && !(splitOnly && stage === 1) ? 'current' : step === 1 && openCount && !finished ? 'pending' : finished || step < stage ? 'done' : ''}`} onClick={() => setTab(step)}><b>{step}. {label}</b><small>{value}{!finished && step === stage && !(splitOnly && stage === 1) ? ' · 지금 단계' : ''}</small></button>; })}</div>
    {activeTab === 1 && <section className="aim-section" role="tabpanel"><div className="aim-section-heading"><p className="tab-hint">목표에 다가갈 후보 접근법입니다. 하나를 골라 과제로 확정합니다.</p>{isOpen && aimAction?.kind !== 'create_proposal' && !addingProposal && <button type="button" className="secondary-action" onClick={() => setAddingProposal(true)}>＋ 접근안 추가</button>}</div>
      {addingProposal && aimAction?.kind !== 'create_proposal' && <div className="inline-composer">{proposalForm}</div>}
      {proposals.length ? <ul className="aim-proposal-tree">{proposals.map(proposal => { const goal = goals.find(item => item.id === proposal.accepted_goal_id); return <li key={proposal.id} className={proposal.status === 'closed' || (goal && SETTLED_GOAL_PHASES.has(goal.phase)) ? 'settled' : ''}><button className="proposal-row" onClick={() => onSelect({ kind: 'proposal', id: proposal.id })}><span>{proposalTitle(proposal.direction)}</span><small>{proposalStatus(proposal)}</small></button>{goal && <button type="button" className="relation-link" onClick={() => onSelect({ kind: 'goal', id: goal.id })}>→ 과제: {goal.target}</button>}</li>; })}</ul> : splitOnly ? <p className="empty-note">하위 목표로 나누어 진행하는 목표입니다. 통합 확인처럼 이 목표 수준에서만 할 수 있는 일이 있을 때 접근안을 추가하세요.</p> : <p className="empty-note">아직 접근안이 없습니다. 위의 지금 할 일에서 첫 접근안을 적어 주세요.</p>}
    </section>}
    {activeTab === 2 && <section className="aim-section" role="tabpanel"><p className="tab-hint">접근안에서 확정한 실제 일입니다. 각 과제에서 실행하고 검증합니다.</p>
      {goals.length ? <ul className="aim-proposal-tree">{goals.map(goal => { const origin = proposals.find(proposal => proposal.accepted_goal_id === goal.id); return <li key={goal.id}><button className="proposal-row" onClick={() => onSelect({ kind: 'goal', id: goal.id })}><span>{goal.target}</span><em className={`state-badge ${goal.phase}`}>{stateName(goal.phase)}</em></button>{origin && <button type="button" className="relation-link" onClick={() => onSelect({ kind: 'proposal', id: origin.id })}>← 접근안: {origin.direction}</button>}</li>; })}</ul> : <p className="empty-note">아직 과제가 없습니다. 접근안 탭에서 접근안을 골라 과제로 확정하면 여기에 나타납니다.</p>}
    </section>}
    {activeTab === 3 && <section className="aim-section" role="tabpanel"><p className="tab-hint">검증된 과제들이 목표 자체를 이루었는지 따로 판단한 기록입니다.</p>
      {evaluations.length ? <ul className="record-list">{evaluations.map(item => <li className="record-item" key={item.id}><div className="record-head"><strong>목표를 이루었다고 평가</strong><em className={`state-badge verdict-${item.validity}`}>{verdictValidity(item.validity)}</em></div><p>{item.conclusion}</p><small>평가자 {item.evaluator_actor_id} · 근거로 삼은 검증 판단 {item.verdict_ids?.length ?? 0}개{item.child_evaluation_ids?.length ? ` · 하위 목표 평가 ${item.child_evaluation_ids.length}개` : ''}</small></li>)}</ul> : <p className="empty-note">{goals.some(goal => goal.phase === 'verified') ? '검증된 과제가 있습니다. 지금 할 일에서 목표 달성 평가를 기록할 수 있습니다.' : '아직 평가가 없습니다. 검증된 과제가 생기면 평가할 수 있습니다.'}</p>}
    </section>}
    <details className="goal-section sub-aims" open={subAims.length > 0}><summary>하위 목표 <small>{subAims.length ? `${subAims.length}개` : '없음'}</small></summary>
      {subAims.length ? <ul className="aim-proposal-tree">{subAims.map(item => <li key={item.id} className={item.phase === 'abandoned' ? 'settled' : ''}><button className="proposal-row" onClick={() => onSelect({ kind: 'aim', id: item.id })}><span>{item.description}</span><em className={`state-badge ${item.phase}`}>{stateName(item.phase)}{item.paused ? ' · 잠시 멈춤' : ''}</em></button></li>)}</ul> : <p className="tab-hint">아직 완료 기준을 쓸 수 없을 만큼 큰 목표라면 하위 목표로 나눕니다. 기준을 쓸 수 있는 작업이라면 과제로 확정하고, 과제를 상위·하위 과제로 나눕니다.</p>}
      {subAims.length > 0 && <p className="tab-hint">하위 목표를 모두 이뤄도 이 목표는 자동으로 달성되지 않습니다. 하위 목표의 달성 평가를 근거로 이 목표의 달성 평가를 따로 기록합니다.</p>}
      {isOpen && <details className="sub-choice"><summary>＋ 하위 목표 추가</summary><form onSubmit={submit('aim-create', form => [input(form, 'description'), '--parent', aim.id])}><Field name="description" label="이 목표를 이루기 위한 더 작은 변화" multiline /><button disabled={busy}>하위 목표 만들기</button></form></details>}
    </details>
    {isOpen && <details className="goal-section aim-management"><summary>목표 관리 <small>{aim.paused ? '잠시 멈춤' : ''}</small></summary>
      {aim.paused ? (aimAction?.kind === 'resume_aim' ? <p className="tab-hint">위의 지금 할 일에서 다시 진행할 수 있습니다.</p> : <form onSubmit={submit('aim-resume', form => [aim.id, input(form, 'actor'), input(form, 'reason')])}><p className="tab-hint">잠시 멈춘 목표입니다. 다시 진행해도 실행이 자동으로 시작되지는 않습니다.</p><Field name="actor" label="결정자" remember="decider" /><Field name="reason" label="다시 진행하는 이유" multiline /><button disabled={busy}>다시 진행</button></form>)
        : <details className="sub-choice"><summary>잠시 멈추기</summary><form onSubmit={submit('aim-pause', form => [aim.id, input(form, 'actor'), input(form, 'reason')])}><p className="tab-hint">되돌릴 수 있습니다. 멈춘 동안 이 목표의 과제에서 새 실행을 시작할 수 없고, 진행 중인 실행은 멈추지 않습니다.</p><Field name="actor" label="결정자" remember="decider" /><Field name="reason" label="멈추는 이유" multiline /><button disabled={busy}>잠시 멈추기</button></form></details>}
      {!parentAim && (() => { const below = new Set<string>([aim.id]); let grew = true; while (grew) { grew = false; for (const item of data.aims) if (item.parent_aim_id && below.has(item.parent_aim_id) && !below.has(item.id)) { below.add(item.id); grew = true; } } const candidates = data.aims.filter(item => item.phase === 'open' && !below.has(item.id)); return candidates.length > 0 && <details className="sub-choice"><summary>다른 목표의 하위 목표로 연결</summary><form onSubmit={submit('aim-parent-add', form => [aim.id, input(form, 'parent')])}><p className="tab-hint">한 번 연결하면 바꿀 수 없습니다. 상위 목표를 멈추거나 그만두면 이 목표의 새 실행도 막힙니다.</p><label className="field"><span>상위 목표</span><select name="parent" required defaultValue="">{[<option key="" value="" disabled>선택하세요</option>, ...candidates.map(item => <option key={item.id} value={item.id}>{item.description}</option>)]}</select></label><button disabled={busy}>하위 목표로 연결</button></form></details>; })()}
      <details className="sub-choice danger"><summary>이 목표 그만두기</summary><form onSubmit={submit('aim-abandon', form => [aim.id, '--actor', input(form, 'actor'), '--reason', input(form, 'reason')])}><p className="tab-hint">되돌릴 수 없습니다. 기록은 남지만 이 목표에서는 새 접근안, 과제 확정, 실행을 할 수 없습니다. 아래 접근안과 과제는 자동으로 닫히지 않습니다.</p><Field name="actor" label="결정자" remember="decider" /><Field name="reason" label="그만두는 이유" multiline /><button className="danger-action" disabled={busy}>목표 그만두기</button></form></details>
    </details>}
    <MemorySection data={data} ids={[aim.id, ...data.aim_evaluations.filter(item => item.aim_id === aim.id).map(item => item.id)]} />
    <ReferenceSection subjectId={aim.id} references={aim.references ?? []} projectRoot={data.project_root ?? null} busy={busy} submit={submit} documents={data.memory_documents} />
    {hiddenChildren.length > 0 && <details className="hidden-children"><summary>이 목표에서 숨긴 항목 {hiddenChildren.length}개</summary><ul>{hiddenChildren.map(item => <li key={item.id}><span>{item.label}</span>{onRestore && <button type="button" className="quiet" onClick={() => onRestore(item.id)}>다시 보기</button>}</li>)}</ul></details>}
    <div className="inspector-footer"><TechnicalInfo rows={[['내부 ID', aim.id], ...(aimAction ? [['다음 행동 명령', aimAction.command_argv.join(' ')] as [string, string]] : [])]} /><details className="local-management"><summary>화면 관리</summary><p>목록에서 보고 싶지 않을 때 이 PC 화면에서만 숨깁니다. 기록을 삭제하거나 상태를 바꾸지 않습니다.</p><button className="quiet archive-button" onClick={() => onArchive(aim.id)}>이 목표 숨기기</button></details></div>
  </section>;
}

export function AttemptTimeline({ attempts, evidence, verdicts, selected, busy, onSelect, run, currentVersion, decisions = [] }: { decisions?: Decision[]; currentVersion?: number; attempts: Attempt[]; evidence: Evidence[]; verdicts: Verdict[]; selected?: string; busy: boolean; onSelect: (value: Selection) => void; run: (command: string, args: string[], notice?: string) => Promise<void> }) {
  if (!attempts.length) return <div className="inspector-content"><p className="tab-hint">아직 실행이 없습니다. 위의 지금 할 일에서 실행을 시작하면 이곳에 기록됩니다.</p></div>;
  return <div className="attempt-timeline">{attempts.map((item, index) => { const itemEvidence = evidence.filter(record => record.attempt_id === item.id); const itemVerdicts = verdicts.filter(record => record.attempt_id === item.id); const decision = decisions.find(record => record.consumed_by_attempt_id === item.id); return <React.Fragment key={item.id}>{decision && <p className="decision-note">↻ {decision.kind === 'retry_same_contract' ? '재시도 결정' : '재검증 결정'} · {decision.reason} <small>— {decision.decided_by_actor_id}</small></p>}<article className={`attempt-card ${selected === item.id ? 'selected' : ''}`}><button className="attempt-heading" onClick={() => onSelect({ kind: 'attempt', id: item.id })}><span><b>{index + 1}번째 실행</b><small>기준 버전 {item.contract_version} · 실행자 {item.executor}</small></span><em className={`state-badge ${item.outcome}`}>{stateName(item.outcome)}</em></button><p>{item.operation}</p>{currentVersion !== undefined && item.contract_version < currentVersion && (item.outcome === 'running' ? <div className="stale-attempt"><p>완료 기준이 v{currentVersion}로 바뀌기 전, v{item.contract_version} 기준으로 시작한 실행입니다. 결과를 기록해 정리할 수 있지만, v{currentVersion} 검증에는 쓰이지 않습니다.</p><div className="button-row"><button type="button" className="secondary" disabled={busy} onClick={() => void run('attempt-complete', [item.id, 'succeeded'], `이전 기준(v${item.contract_version})의 실행을 성공으로 정리했습니다. 현재 기준의 할 일은 그대로입니다.`)}>성공으로 정리</button><button type="button" className="secondary" disabled={busy} onClick={() => void run('attempt-complete', [item.id, 'failed'], `이전 기준(v${item.contract_version})의 실행을 실패로 정리했습니다. 현재 기준의 할 일은 그대로입니다.`)}>실패로 정리</button></div></div> : <small className="stale-note">이전 기준(v{item.contract_version})의 실행입니다. 현재 기준의 검증에는 쓰이지 않습니다.</small>)}<div className="record-summary"><span>관찰 근거 {itemEvidence.length}</span><span>검증 판단 {itemVerdicts.length}</span>{itemVerdicts.map(verdict => <span key={verdict.id}>{verdictResult(verdict.result)} · {verdictValidity(verdict.validity)}</span>)}</div></article></React.Fragment>; })}</div>;
}


function LifecycleGuide() { return <ol className="lifecycle-guide" aria-label="Goal Loop 기본 흐름"><li>목표</li><li>접근안</li><li>과제</li><li>실행</li><li>관찰 근거</li><li>검증 판단</li></ol>; }

export const actionExplanation = (kind: string) => ({
  create_aim: '이루고 싶은 변화를 목표로 기록합니다. 구체적인 작업이나 성공 기준은 아직 정하지 않아도 됩니다.',
  create_proposal: '이 목표에 다가갈 접근법을 접근안으로 적어 둡니다. 적어 두기만 하며, 과제는 아직 만들어지지 않습니다.',
  accept_proposal: '이 접근안을 실제로 진행할 과제로 확정합니다. 무엇을, 어디까지 하고, 어떻게 확인할지 완료 기준으로 정합니다. 확정해도 실행이 시작되지는 않습니다.',
  start_attempt: '이 과제를 위해 실제로 할 작업과 담당자를 적고 실행을 시작합니다.',
  start_retry_attempt: '앞서 내린 재시도 결정에 따라 같은 완료 기준으로 다시 실행을 시작합니다. 이전 실패 기록은 그대로 남습니다.',
  start_reverification_attempt: '재검증 결정에 따라 검증에 필요한 실행을 새로 시작합니다.',
  complete_attempt: '진행 중인 실행이 끝났다면 결과를 성공 또는 실패로 기록합니다. 이것은 실행 결과일 뿐, 과제의 검증이나 목표의 달성을 뜻하지 않습니다.',
  record_evidence: '실행 뒤에 직접 확인한 사실을 관찰 근거로 남깁니다. 이후 검증 판단이 이 근거를 인용합니다.',
  record_additional_evidence: '검증에 더 필요한 사실을 관찰 근거로 추가합니다.',
  begin_verification: '관찰 근거가 모였으면 검증을 시작합니다. 검증은 완료 기준을 충족했는지 판단하는 단계입니다.',
  submit_verdict: '실행한 사람과 다른 검증자가 관찰 근거를 인용해, 완료 기준을 충족했는지 판정합니다.',
  submit_superseding_verdict: '새 관찰 근거를 반영해 검증 판단을 다시 기록합니다. 이전 판단도 기록으로 남습니다.',
  decide_retry: '실패한 실행을 같은 기준으로 다시 해 볼지 결정합니다. 이 결정을 기록해야 재시도를 시작할 수 있습니다.',
  decide_reverification: '판단을 내리지 못한 검증을 위해 새 실행이 필요한지 결정합니다.',
  evaluate_aim: '검증된 과제들이 목표 자체를 이루었는지 평가합니다. 이 평가를 기록해야 목표가 달성됨으로 바뀝니다.',
  clear_goal_hold: '이 과제의 실행 보류를 해제합니다. 해제해도 실행이 자동으로 시작되지는 않습니다.',
  resume_aim: '잠시 멈춘 목표를 다시 진행합니다. 실행이 자동으로 시작되지는 않습니다.',
  resolve_verdict_review: '관찰 근거가 정정되어 다시 검토해야 하는 검증 판단을 유지할지 무효로 할지 결정합니다.',
  resolve_aim_evaluation_review: '근거가 바뀐 목표 평가를 유지할지 무효로 할지 결정합니다.',
  inspect_goal_dependency: '선행 과제가 검증을 통과해야 이 과제를 실행할 수 있습니다. 선행 과제를 먼저 진행하세요.',
}[kind] ?? '이 행동은 새 기록을 남깁니다. 제출하기 전에 대상과 내용을 확인하세요.');

export function TechnicalInfo({ rows, note = 'CLI나 AI 에이전트가 이 항목을 가리킬 때 쓰는 내부 정보입니다. 화면을 사용할 때는 몰라도 됩니다.' }: { rows: Array<[string, string]>; note?: string }) {
  return <details className="technical-details"><summary>기술 정보</summary><p className="technical-note">{note}</p><dl className="technical-rows">{rows.map(([label, value]) => <React.Fragment key={label}><dt>{label}</dt><dd><code>{value}</code></dd></React.Fragment>)}</dl></details>;
}

function ActionTechnicalDetails({ action }: { action: Action }) {
  return <TechnicalInfo note="이 행동을 CLI에서 직접 실행할 때 쓰는 정보입니다." rows={[['CLI 요약', action.summary], ['명령', action.command_argv.join(' ')]]} />;
}

function ActionGuidance({ action }: { action: Action }) {
  return <p className="explanation-card">{actionExplanation(action.kind)}</p>;
}

function HelpCenter() { return <section className="help-center"><div className="panel-heading"><div><h2>Goal Loop 사용 안내</h2><p>기록의 종류와 정상 흐름을 짧게 설명합니다. 자세한 terminal 안내는 <code>goal-loop guide</code>에서도 볼 수 있습니다.</p></div></div><LifecycleGuide /><section className="test-guide"><h3>처음이라면 이렇게 확인하세요</h3><ol><li>왼쪽에서 열린 <strong>목표</strong>를 선택하고, 그 아래 <strong>과제</strong>를 선택합니다.</li><li>오른쪽 상세 보기의 <strong>실행</strong> 탭에서 이전 실행을 확인합니다.</li><li>다른 목표의 작업은 위 <strong>전체 작업함</strong>에만 있는지 확인합니다.</li><li>창을 좁혔다 넓히고, 한글 입력 칸에서 “테스트”를 입력해 봅니다.</li><li>문제가 생기면 보인 문구와 누른 순서를 알려 주세요. 기록은 자동으로 바뀌지 않습니다.</li></ol></section><div className="help-grid"><article><h3>목표 <small>(Aim)</small></h3><p>이루고 싶은 변화입니다. 아직 구체 작업이나 성공 판정이 아닙니다.</p></article><article><h3>과제 <small>(Goal)</small></h3><p>접근안을 바탕으로 무엇을, 어디까지, 어떻게 확인할지 완료 기준을 정한 실제 실행 단위입니다.</p></article><article><h3>실행 <small>(Attempt)</small></h3><p>과제를 위한 한 번의 실행입니다. 성공은 실행 결과일 뿐입니다.</p></article><article><h3>관찰 근거와 검증 판단</h3><p>관찰 근거(Evidence)는 사실이고, 검증 판단(Verdict)은 독립 검증자가 근거를 인용해 내리는 판정입니다.</p></article><article><h3>목표 달성 평가</h3><p>검증된 과제가 목표를 실제로 충족하는지는 별도로 평가합니다.</p></article><article><h3>검토와 Runtime</h3><p>관찰 근거 정정은 검토(review)를 열고, Runtime은 기술적 전달 상태만 나타냅니다.</p></article></div></section>; }

type RetryHandler = (goalId: string, attemptId: string, version: number, executor: string, reason: string, operation: string) => Promise<boolean>;

function FailureChoices({ goal, action, busy, onSelect, onRetry }: { goal: Goal; action: Action; busy: boolean; onSelect: (value: Selection) => void; onRetry: RetryHandler }) {
  const attemptId = actionIds(action, 'attempt-')[0] ?? '';
  const retry = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = event.currentTarget; await onRetry(goal.id, attemptId, goal.current_contract_version, input(form, 'executor'), input(form, 'reason'), input(form, 'operation')); };
  return <section className="context-action failure-choices"><span className="eyebrow">지금 할 일</span><h3>실행이 실패했습니다. 어떻게 할까요?</h3><p>실패 기록은 그대로 남습니다. 다음 중 하나를 고르세요.</p>
    <details className="choice"><summary><strong>같은 기준으로 다시 하기 ▾</strong><small>무엇을 바꿔서 다시 할지 적으면, 재시도 결정과 새 실행을 함께 기록합니다.</small></summary>
      <form onSubmit={retry}><Field name="executor" label="실행자" remember="executor" /><Field name="reason" label="왜 다시 하나요?" multiline /><Field name="operation" label="이번에 할 작업" multiline /><button disabled={busy}>재시도 시작</button></form></details>
    <button type="button" className="route-button" onClick={() => onSelect({ kind: 'goal', id: goal.id, focus: 'contract' })}><strong>기준을 바꾸고 다시 하기 →</strong><small>완료 기준이 맞지 않았다면 새 버전으로 고친 뒤 실행합니다.</small></button>
    <button type="button" className="route-button" onClick={() => onSelect({ kind: 'aim', id: goal.aim_id, focus: 'add-proposal' })}><strong>다른 접근안 시도 →</strong><small>접근 자체가 맞지 않았다면 같은 목표에 새 접근안을 적습니다.</small></button><button type="button" className="route-button quiet-route" onClick={() => onSelect({ kind: 'goal', id: goal.id, focus: 'stop' })}><strong>이 과제 그만두기 →</strong><small>더 이상 추진하지 않기로 했다면 이유를 남기고 그만둡니다.</small></button>
  </section>;
}

// A verified Goal keeps its history, but once every satisfied Verdict is invalidated nothing backs it any more.
export const verificationVoided = (goal: { id: string; phase: string }, data: Pick<Projection, 'verdicts'>) => {
  if (goal.phase !== 'verified') return false;
  const satisfied = data.verdicts.filter(item => item.goal_id === goal.id && item.result === 'satisfied');
  return satisfied.some(item => item.validity === 'invalidated') && !satisfied.some(item => item.validity === 'valid');
};

function GoalVerifiedNext({ goal, data, onSelect }: { goal: Goal; data: Projection; onSelect: (value: Selection) => void }) {
  const aim = data.aims.find(item => item.id === goal.aim_id);
  const achieved = aim?.phase === 'achieved';
  const reviewing = data.aim_evaluations.some(item => item.aim_id === goal.aim_id && item.validity === 'under_review');
  if (verificationVoided(goal, data)) return <section className="context-action goal-voided"><span className="eyebrow">다음 단계</span><h3>이 과제의 검증 판단이 무효가 되었습니다</h3><p>'검증됨' 기록은 남지만, 지금은 이 과제의 완료를 뒷받침하는 유효한 검증 판단이 없습니다. 무효가 된 판단은 되돌릴 수 없고 이 과제에서는 다시 실행할 수 없습니다. 빠진 부분은 같은 목표에 후속 접근안을 적어 이어 갑니다.</p>{aim && <button type="button" className="route-button" onClick={() => onSelect({ kind: 'aim', id: aim.id, focus: 'add-proposal' })}><strong>후속 접근안 적기 →</strong><small>목표 — {aim.description}</small></button>}</section>;
  return <section className="context-action goal-done"><span className="eyebrow">다음 단계</span><h3>이 과제는 검증을 마쳤습니다</h3><p>{reviewing ? '상위 목표의 달성 평가가 재검토 중입니다. 목표 화면에서 평가를 유지할지 무효로 할지 결정하세요.' : achieved ? '상위 목표는 이미 달성으로 평가되었습니다.' : '목표를 이루었는지는 과제 하나가 아니라 목표 화면에서 따로 평가합니다.'}</p>{aim && <button type="button" className="route-button" onClick={() => onSelect({ kind: 'aim', id: aim.id })}><strong>{reviewing ? '목표에서 평가 다시 확인하기 →' : achieved ? '목표 보기 →' : '목표에서 달성 평가하기 →'}</strong><small>목표 — {aim.description}</small></button>}</section>;
}

// Short confirmation for durable records; creation commands set their own message before this runs.
const actionFeedback = (commandName: string, args: string[]) => ({
  'attempt-start': '실행을 시작했습니다. 끝나면 결과를 기록하세요.',
  'attempt-complete': args[1] === 'failed' ? '실패로 기록했습니다. 아래에서 다음 방법을 고르세요.' : '성공으로 기록했습니다. 다음은 관찰 근거입니다.',
  'evidence-record': args.includes('--attach') ? `관찰 근거와 첨부 ${args.filter(item => item === '--attach').length}개를 기록했습니다. 첨부는 복사본으로 보존됩니다.` : '관찰 근거를 기록했습니다.',
  'aim-parent-add': '하위 목표로 연결했습니다.',
  'reference-add': '참고 문서를 연결했습니다.',
  'reference-remove': '참고 문서 연결을 해제했습니다. 해제 기록은 활동 기록에 남습니다.',
  'evidence-correct': '관찰 근거 정정을 기록했습니다. 이 근거를 인용한 판단을 다시 확인해야 합니다.',
  'verification-begin': '검증을 시작했습니다. 판정을 기록하세요.',
  'verdict-submit': '검증 판단을 기록했습니다.',
  'aim-evaluate': '목표 달성 평가를 기록했습니다.',
  'contract-revise': '완료 기준의 새 버전을 기록했습니다.',
  'retry-decide': '재시도 결정을 기록했습니다. 이제 재시도를 시작할 수 있습니다.',
  'reverification-decide': '재검증 결정을 기록했습니다.',
  'goal-hold-add': '실행 보류를 추가했습니다.',
  'proposal-close': '접근안을 닫았습니다. 기록은 그대로 남습니다.',
  'goal-stop': '과제를 그만두었습니다. 다시 하려면 새 접근안으로 시작합니다.',
  'aim-abandon': '목표를 그만두었습니다. 기록은 그대로 남습니다.',
  'aim-pause': '목표를 잠시 멈췄습니다. 새 실행은 다시 진행할 때까지 막힙니다.',
  'goal-hold-clear': '실행 보류를 해제했습니다.',
  'goal-parent-add': '상위 과제를 연결했습니다.',
  'goal-dependency-add': '선행 과제를 연결했습니다.',
  'aim-resume': '목표를 다시 진행합니다. 실행은 직접 시작하세요.',
  'verdict-review-resolve': '검증 판단 재검토를 마쳤습니다.',
  'aim-evaluation-review-resolve': '목표 달성 평가 재검토를 마쳤습니다.',
} as Record<string, string>)[commandName];

// Core rejections arrive as a CLI traceback; show the reason in Korean and keep the raw text as technical detail.
const CORE_ERROR_MESSAGES: Record<string, string> = {
  'The executing Actor cannot issue the independent final Verdict.': '실행한 사람은 같은 과제의 검증 판단을 내릴 수 없습니다. 다른 검증자 이름을 적어 주세요.',
  'A Verdict must cite evidence.': '검증 판단에는 인용할 관찰 근거가 하나 이상 필요합니다.',
  'A Verdict requires a Goal in verifying phase.': '검증을 시작한 과제에서만 검증 판단을 기록할 수 있습니다.',
  'A terminal Verdict already exists for this Attempt.': '이 실행에는 이미 확정된 검증 판단이 있습니다.',
  'A superseding Verdict requires new Evidence.': '판정을 다시 기록하려면 먼저 새 관찰 근거를 남겨야 합니다.',
  'Only a new Verdict superseding the latest inconclusive Verdict is allowed.': '판정 불충분으로 기록한 최근 판단만 새 판정으로 바꿀 수 있습니다.',
  'Verdict evidence must belong to the same Goal Contract.': '같은 완료 기준 버전의 관찰 근거만 인용할 수 있습니다.',
  'Verdict must target the Goal\'s current Contract and attached Attempt.': '현재 완료 기준과 검증 중인 실행에 대해서만 판정할 수 있습니다.',
  'Corrected Evidence cannot support a new Verdict.': '정정된 관찰 근거는 새 검증 판단의 근거로 쓸 수 없습니다.',
  'A Contract can be revised only for a committed or active Goal.': '검증을 시작했거나 끝난 과제의 완료 기준은 바꿀 수 없습니다.',
  'A Contract revision requires a changed Contract draft.': '완료 기준이 바뀌지 않았습니다. 범위, 성공 기준, 검증 방법 중 하나 이상을 바꿔 주세요.',
  'A Contract revision requires a reason.': '완료 기준을 바꾸는 이유를 적어 주세요.',
  'A Goal already has an immutable parent link.': '이 과제에는 이미 상위 과제가 있습니다. 상위 과제는 한 번만 정할 수 있습니다.',
  'A Goal cannot be its own parent.': '과제를 자기 자신의 상위 과제로 정할 수 없습니다.',
  'A Goal cannot depend on itself.': '과제를 자기 자신의 선행 과제로 정할 수 없습니다.',
  'A Goal dependency cannot create a cycle.': '이 연결을 추가하면 과제들이 서로를 기다리는 순환이 생깁니다.',
  'A Goal parent link cannot create a cycle.': '이 연결을 추가하면 상위·하위 관계가 순환합니다.',
  'A Goal parent must belong to the same primary Aim.': '상위 과제는 같은 목표에 속한 과제만 정할 수 있습니다.',
  'This Goal dependency already exists.': '이미 연결된 선행 과제입니다.',
  'A matching unused retry Decision is required.': '재시도하려면 먼저 재시도 결정을 기록해야 합니다.',
  'A matching unused re-verification Decision is required.': '다시 검증하려면 먼저 재검증 결정을 기록해야 합니다.',
  'A same-contract retry requires a failed current-Contract Attempt and reason.': '재시도는 현재 완료 기준으로 실패한 실행이 있고, 결정 사유를 적었을 때만 기록할 수 있습니다.',
  'Re-verification requires a verifying Goal and a reason.': '재검증 결정은 검증 중인 과제에서, 사유와 함께 기록할 수 있습니다.',
  'A required Verdict has the wrong result.': '선행 과제의 검증 판단이 아직 충족되지 않았습니다.',
  'A required Verdict is under review or invalidated.': '선행 과제의 검증 판단이 재검토 중이거나 무효가 되었습니다.',
  'Actor lacks execute Authority for this Goal.': '이 실행자에게는 이 과제를 실행할 권한이 없습니다.',
  'An Attempt may be completed once.': '이 실행의 결과는 이미 기록되었습니다.',
  'Attempt uses a stale Goal Contract version.': '완료 기준이 새 버전으로 바뀌었습니다. 새 기준으로 실행을 시작해 주세요.',
  'Attempts can start only for committed or active Goals.': '검증을 시작했거나 끝난 과제에서는 새 실행을 시작할 수 없습니다.',
  'Evidence requires a completed Attempt in this vertical slice.': '관찰 근거는 결과를 기록한 실행에 대해서만 남길 수 있습니다.',
  'Only a successful Attempt can enter verification.': '성공으로 기록한 실행만 검증할 수 있습니다.',
  'Only an active Goal can enter verification.': '실행이 진행 중인 과제만 검증을 시작할 수 있습니다.',
  'Verification must use the Goal\'s current Contract and an attached Attempt.': '현재 완료 기준으로 한 실행만 검증할 수 있습니다.',
  'The Goal has an unresolved execution Hold.': '실행 보류 중인 과제입니다. 보류를 해제한 뒤 실행하세요.',
  'The Goal\'s Aim is achieved.': '목표가 이미 달성되어 이 과제에서 새 실행을 시작할 수 없습니다.',
  'The Goal\'s primary Aim is paused.': '목표가 잠시 멈춘 목표라서 새 실행을 시작할 수 없습니다.',
  'An achieved Aim cannot accept a new Proposal.': '달성된 목표에서는 접근안을 과제로 확정할 수 없습니다.',
  'Only an open proposal can be accepted.': '검토 중인 접근안만 과제로 확정할 수 있습니다.',
  'Only an open Proposal can be revised.': '검토 중인 접근안만 고칠 수 있습니다.',
  'Only an open Aim can be evaluated as achieved.': '진행 중인 목표만 달성 평가를 기록할 수 있습니다.',
  'An Aim evaluation must cite at least one Verdict.': '목표 달성 평가에는 근거가 되는 검증 판단이 하나 이상 필요합니다.',
  'An achieved Aim requires satisfied Verdicts.': '충족됨으로 판정된 과제가 있어야 목표 달성을 평가할 수 있습니다.',
  'An achieved Aim requires currently valid Verdicts.': '현재 유효한 검증 판단이 있어야 목표 달성을 평가할 수 있습니다.',
  'Aim resume requires a reason.': '목표를 다시 진행하는 이유를 적어 주세요.',
  'Aim pause requires a reason.': '목표를 잠시 멈추는 이유를 적어 주세요.',
  'Only an open Proposal can be closed.': '검토 중인 접근안만 닫을 수 있습니다.',
  'Closing a Proposal requires a reason.': '접근안을 닫는 이유를 적어 주세요.',
  'Only a committed, active or verifying Goal can be stopped.': '검증을 마쳤거나 이미 중단된 과제는 그만둘 수 없습니다.',
  'Stopping a Goal requires a reason.': '과제를 그만두는 이유를 적어 주세요.',
  'Only an open Aim can be abandoned.': '진행 중인 목표만 그만둘 수 있습니다.',
  'Abandoning an Aim requires a reason.': '목표를 그만두는 이유를 적어 주세요.',
  'An abandoned Aim cannot receive a new Proposal.': '그만둔 목표에는 접근안을 추가할 수 없습니다.',
  'An abandoned Aim cannot accept a new Proposal.': '그만둔 목표에서는 접근안을 과제로 확정할 수 없습니다.',
  "The Goal's Aim is abandoned.": '목표를 그만두어 이 과제에서 새 실행을 시작할 수 없습니다.',
  'A Goal cannot depend on its own ancestor.': '자기 상위 과제를 선행 과제로 연결할 수 없습니다. 서로를 기다리게 됩니다.',
  'A Goal cannot have a prerequisite as its ancestor.': '이미 선행 과제인 과제를 상위 과제로 정할 수 없습니다. 서로를 기다리게 됩니다.',
};

export function friendlyError(raw: string) {
  const lines = raw.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const coreLine = [...lines].reverse().find(line => /(GoalLoopError|Error):\s/.test(line));
  const reason = coreLine ? coreLine.replace(/^.*?(GoalLoopError|Error):\s*/, '') : lines.at(-1) ?? raw;
  const translated = CORE_ERROR_MESSAGES[reason];
  return { message: translated ?? (lines.length > 1 ? `요청을 처리하지 못했습니다. (${reason})` : reason), detail: lines.length > 1 || !translated ? raw : '' };
}

function ErrorNotice({ raw, onClose }: { raw: string; onClose: () => void }) {
  const { message, detail } = friendlyError(raw);
  return <div className="error-banner" role="alert"><div className="error-body"><strong>기록하지 못했습니다</strong><p>{message}</p>{detail && <details className="technical-details"><summary>기술 정보</summary><pre>{detail}</pre></details>}</div><button type="button" className="quiet" onClick={onClose}>닫기</button></div>;
}

const actionTargetLabel = (data: Projection, scope: ActionScope) => { const goal = data.goals.find(item => item.id === scope.goalId); const proposal = data.proposals.find(item => item.id === scope.proposalId); const aim = data.aims.find(item => item.id === scope.aimId); return goal ? `과제 — ${goal.target}` : proposal ? `접근안 — ${proposalTitle(proposal.direction)}` : aim ? `목표 — ${aim.description}` : '프로젝트 전체'; };

const SUMMARY_LIMIT = 5;
export function NextActionSummary({ actions, data, onSelect, onOpenInbox }: { actions: Action[]; data: Projection; onSelect: (value: Selection) => void; onOpenInbox?: () => void }) {
  const items = actions.map(action => ({ action, scope: actionScope(data, action) })).filter(item => item.scope.goalId || item.scope.proposalId || item.scope.aimId);
  const open = (scope: ActionScope) => onSelect(scope.goalId ? { kind: 'goal', id: scope.goalId } : scope.proposalId ? { kind: 'proposal', id: scope.proposalId } : { kind: 'aim', id: scope.aimId! });
  return <section className="next-summary" aria-label="지금 할 일 요약"><div className="next-summary-heading"><h3>지금 할 일</h3>{items.length > 0 && <span>{items.length}</span>}</div>{items.length === 0 ? <p className="empty-note">지금 기다리는 작업이 없습니다. 새로 시작하려면 왼쪽의 <strong>＋ 새 목표</strong>를 누르세요.</p> : <><p className="next-summary-hint">항목을 누르면 해당 목표가 선택되고, 오른쪽에서 바로 이어서 진행할 수 있습니다.</p><ul>{items.slice(0, SUMMARY_LIMIT).map(({ action, scope }) => { const goal = data.goals.find(item => item.id === scope.goalId); const proposal = data.proposals.find(item => item.id === scope.proposalId); const aim = data.aims.find(item => item.id === scope.aimId); const target = actionTargetLabel(data, scope); return <li key={`${action.kind}:${action.subject.id}`}><button type="button" className="next-summary-item" onClick={() => open(scope)}><strong>{actionTitle(action.kind)}</strong><small>{target}</small></button></li>; })}</ul>{(items.length > SUMMARY_LIMIT || onOpenInbox) && <button type="button" className="quiet next-summary-more" onClick={onOpenInbox}>전체 작업함에서 모두 보기{items.length > SUMMARY_LIMIT ? ` (${items.length}개)` : ''}</button>}</>}</section>;
}

function WorkInbox({ actions, data, onOpen }: { actions: Action[]; data: Projection; onOpen: (scope: ActionScope) => void }) {
  const groups = new Map<string, { label: string; actions: Action[] }>();
  for (const action of actions) {
    const scope = actionScope(data, action);
    const aim = data.aims.find(item => item.id === scope.aimId);
    const key = aim?.id ?? 'workspace';
    const group = groups.get(key) ?? { label: aim?.description ?? '프로젝트 전체', actions: [] };
    group.actions.push(action); groups.set(key, group);
  }
  return <section className="work-inbox"><div className="panel-heading"><div><h2>전체 작업함</h2><p>모든 목표의 다음 행동입니다. 항목을 누르면 해당 화면으로 이동해 그곳에서 진행합니다.</p></div><span>{actions.length}</span></div>{[...groups.entries()].map(([key, group]) => <section className="inbox-group" key={key}><h3>{group.label}</h3><ul className="inbox-list">{group.actions.map(action => { const scope = actionScope(data, action); return <li key={`${action.kind}:${action.subject.id}`}><button type="button" className="next-summary-item" onClick={() => onOpen(scope)}><strong>{actionTitle(action.kind)}</strong><small>{actionTargetLabel(data, scope)}</small></button></li>; })}</ul></section>)}{actions.length === 0 && <p className="empty-note">지금 기다리는 작업이 없습니다.</p>}</section>;
}

function ResponsibilityChoice() { return <fieldset className="responsibility-choice"><legend>어떤 방식의 실행인가요?</legend><label className="field"><span>실행 종류</span><select name="responsibility" defaultValue="produce"><option value="produce">만들기 — 결과물이나 변화를 준비합니다</option><option value="research">조사하기 — 판단에 필요한 근거를 모읍니다</option><option value="decide">결정하기 — 선택지를 비교해 결론을 남깁니다</option></select></label></fieldset>; }

function ActionForm({ action, data, busy, submit, run }: { action: Action; data: Projection; busy: boolean; submit: (command: string, args: (form: HTMLFormElement) => string[]) => (event: FormEvent<HTMLFormElement>) => Promise<void>; run: (command: string, args: string[], notice?: string) => Promise<void> }) {
  const goal = actionIds(action, 'goal-')[0] ?? action.subject.id; const attempt = actionIds(action, 'attempt-')[0] ?? action.subject.id; const decision = actionIds(action, 'decision-')[0]; const impact = actionIds(action, 'impact-review-')[0] ?? action.subject.id; const evaluation = actionIds(action, 'aim-evaluation-')[0] ?? action.subject.id;
  if (action.kind === 'create_aim') return <form onSubmit={submit('aim-create', form => [input(form, 'description')])}><Field name="description" label="목표 — 이루고 싶은 변화" multiline /><button disabled={busy}>목표 만들기</button></form>;
  if (action.kind === 'accept_proposal') return <form className="goal-contract-form" onSubmit={submit('goal-accept', form => [action.subject.id, '--target', input(form, 'target'), '--responsibility', input(form, 'responsibility'), '--scope', input(form, 'scope'), '--criterion', input(form, 'criterion'), '--verification', input(form, 'verification'), '--key', `ui-${action.subject.id}-${Date.now()}`])}><section className="form-step"><strong>1. 무엇을 해낼 과제인가요?</strong><Field name="target" label="과제 이름 — 끝났을 때 확인할 결과" defaultValue={proposalTitle(data.proposals.find(item => item.id === action.subject.id)?.direction ?? '')} /><ResponsibilityChoice /></section><section className="form-step"><strong>2. 어디까지 하면 되나요?</strong><Field name="scope" label="실행 범위" multiline /><Field name="criterion" label="성공 기준" multiline /></section><section className="form-step"><strong>3. 어떻게 확인하나요?</strong><Field name="verification" label="검증 방법" multiline /></section><p className="explanation-card">기록하면 과제와 첫 완료 기준이 함께 확정됩니다. 실행 자체는 다음 단계에서 별도로 시작합니다.</p><button disabled={busy}>과제 확정</button></form>;
  if (['start_attempt', 'start_retry_attempt', 'start_reverification_attempt'].includes(action.kind)) { const version = action.command_argv.find(item => /^\d+$/.test(item)) ?? '1'; const flag = action.kind === 'start_retry_attempt' ? '--retry-decision' : action.kind === 'start_reverification_attempt' ? '--reverification-decision' : ''; return <form onSubmit={submit('attempt-start', form => [goal, version, input(form, 'executor'), input(form, 'operation'), ...(flag && decision ? [flag, decision] : [])])}><Field name="executor" label="실행자" remember="executor" /><Field name="operation" label="이번 실행 작업" multiline /><button disabled={busy}>실행 시작</button></form>; }
  if (action.kind === 'complete_attempt') { const executor = data.attempts.find(item => item.id === attempt)?.executor ?? ''; const fail = async (event: React.MouseEvent<HTMLButtonElement>) => { const reason = String((event.currentTarget.closest('.complete-attempt')?.querySelector('textarea') as HTMLTextAreaElement | null)?.value ?? '').trim(); await run('attempt-complete', [attempt, 'failed']); if (reason) await run('evidence-record', [attempt, executor, `실패 원인: ${reason}`], '실패로 기록하고 이유를 관찰 근거로 남겼습니다. 아래에서 다음 방법을 고르세요.'); }; return <div className="complete-attempt"><label className="field"><span>실패했다면, 무엇이 문제였나요? (선택)</span><textarea name="failure-reason" onKeyDown={event => event.stopPropagation()} /></label><div className="button-row"><button disabled={busy} onClick={() => void run('attempt-complete', [attempt, 'succeeded'])}>성공으로 기록</button><button className="secondary" disabled={busy} onClick={event => void fail(event)}>실패로 기록</button></div></div>; }
  if (['record_evidence', 'record_additional_evidence'].includes(action.kind)) return <EvidenceForm attempt={attempt} projectRoot={data.project_root ?? null} busy={busy} submit={submit} />;
  if (action.kind === 'begin_verification') return <button disabled={busy} onClick={() => void run('verification-begin', [goal, attempt])}>검증 시작</button>;
  if (['submit_verdict', 'submit_superseding_verdict'].includes(action.kind)) { const usable = data.evidence.filter(item => item.goal_id === goal && item.attempt_id === attempt); return <form onSubmit={submit('verdict-submit', form => { const cited = usable.filter(item => (form.elements.namedItem(item.id) as HTMLInputElement)?.checked).map(item => item.id); const reason = input(form, 'reason'); return [goal, attempt, input(form, 'verifier'), input(form, 'result'), ...cited, ...(action.kind === 'submit_superseding_verdict' ? ['--supersedes-verdict', actionIds(action, 'verdict-').at(-1) ?? ''] : []), ...(reason ? ['--reason', reason] : [])]; })}><Field name="verifier" label="독립 검증자" remember="verifier" exclude={data.attempts.find(item => item.id === attempt)?.executor} hint="실행한 사람과 다른 이름이어야 합니다." /><label className="field"><span>판정</span><select name="result" defaultValue="satisfied"><option value="satisfied">충족됨</option><option value="not_satisfied">충족되지 않음</option><option value="inconclusive">판정 불충분</option></select></label><Field name="reason" label="판단 이유" multiline optional hint="완료 기준 항목마다 충족 여부와 근거를 적어 주세요. 일부만 충족했다면 판정은 '충족되지 않음'으로 하고, 보완 실행·기준 축소·기준 문구 명확화·후속 접근안 중 무엇이 맞는지 적습니다. 기준 밖의 개선점은 판정을 막지 않습니다." /><fieldset><legend>인용할 관찰 근거</legend>{usable.map((item, index) => <label className="check" key={item.id}><input type="checkbox" name={item.id} defaultChecked={index === 0} /><span><span className="observation-text">{item.observation}</span>{item.attachments?.length ? <AttachmentList items={item.attachments} /> : null}</span></label>)}</fieldset><p className="tab-hint">기록한 판정은 고치거나 지울 수 없습니다.</p><button disabled={busy}>검증 판단 기록</button></form>; }
  if (action.kind === 'decide_retry' || action.kind === 'decide_reverification') return <form onSubmit={submit(action.kind === 'decide_retry' ? 'retry-decide' : 'reverification-decide', form => action.kind === 'decide_retry' ? [goal, attempt, input(form, 'actor'), input(form, 'reason')] : [goal, input(form, 'actor'), input(form, 'reason')])}><Field name="actor" label="결정자" remember="decider" /><Field name="reason" label="결정 사유" multiline /><button disabled={busy}>{action.kind === 'decide_retry' ? '재시도 결정 기록' : '재검증 결정 기록'}</button></form>;
  if (action.kind === 'evaluate_aim') { const verdicts = actionIds(action, 'verdict-'); const childEvaluations = actionIds(action, 'aim-evaluation-'); const childAims = childEvaluations.map(id => data.aims.find(aim => aim.id === data.aim_evaluations.find(item => item.id === id)?.aim_id)?.description).filter(Boolean); return <form onSubmit={submit('aim-evaluate', form => [action.subject.id, input(form, 'actor'), input(form, 'reason'), ...verdicts, ...childEvaluations])}><p className="tab-hint">근거: {[verdicts.length ? `검증 판단 ${verdicts.length}개` : '', childAims.length ? `달성된 하위 목표 — ${childAims.join(', ')}` : ''].filter(Boolean).join(' · ')}</p><Field name="actor" label="평가자" remember="evaluator" /><Field name="reason" label="목표 충족 근거" multiline /><button disabled={busy}>목표 달성 평가 기록</button></form>; }
  if (action.kind === 'resolve_verdict_review' || action.kind === 'resolve_aim_evaluation_review') return <form onSubmit={submit(action.kind === 'resolve_verdict_review' ? 'verdict-review-resolve' : 'aim-evaluation-review-resolve', form => action.kind === 'resolve_verdict_review' ? [impact, input(form, 'disposition'), input(form, 'actor'), input(form, 'reason')] : [evaluation, impact, input(form, 'disposition'), input(form, 'actor'), input(form, 'reason')])}><label className="field"><span>결론</span><select name="disposition" defaultValue="reaffirm"><option value="reaffirm">유지 — 정정 후에도 이 판단은 유효합니다</option><option value="invalidate">무효 — 이 판단을 더 이상 근거로 쓰지 않습니다</option></select></label><Field name="actor" label="검토자" remember="reviewer" /><Field name="reason" label="판단 사유" multiline /><button disabled={busy}>검토 결론 기록</button></form>;
  if (action.kind === 'resume_aim') return <form onSubmit={submit('aim-resume', form => [action.subject.id, input(form, 'actor'), input(form, 'reason')])}><Field name="actor" label="결정자" remember="decider" /><Field name="reason" label="재개 사유" multiline /><button disabled={busy}>다시 진행</button></form>;
  if (action.kind === 'clear_goal_hold') return <button disabled={busy} onClick={() => void run('goal-hold-clear', [action.subject.id])}>실행 보류 해제</button>;
  if (action.kind === 'inspect_goal_dependency') { const waiting = data.edges.filter(edge => edge.kind === 'dependency' && edge.target === goal).map(edge => data.goals.find(item => item.id === edge.source)).filter(Boolean) as Goal[]; return <ul className="dependency-wait">{waiting.map(item => <li key={item.id}>기다리는 선행 과제: <strong>{item.target}</strong> · {stateName(item.phase)}</li>)}</ul>; }
  return <><p className="empty-note">이 행동은 아직 화면에서 지원하지 않습니다. 필요한 명령은 기술 정보에 있습니다.</p><ActionTechnicalDetails action={action} /></>;
}

function ReviewInbox({ data, actions, busy, submit, run, onOpen }: { data: Projection; actions: Action[]; busy: boolean; submit: (command: string, args: (form: HTMLFormElement) => string[]) => (event: FormEvent<HTMLFormElement>) => Promise<void>; run: (command: string, args: string[], notice?: string) => Promise<void>; onOpen: (selection: Selection) => void }) {
  const reviewActions = actions.filter(action => action.kind.startsWith('resolve_'));
  return <section className="review-screen"><div className="panel-heading"><div><h2>검토함</h2><p>관찰 근거가 정정되면, 그 근거를 인용한 검증 판단과 목표 달성 평가를 다시 확인해야 합니다. 여기서 유지할지 무효로 할지 결정합니다.</p></div><span>{reviewActions.length}</span></div>
    {reviewActions.length === 0 && <p className="empty-note">지금 다시 확인할 판단이 없습니다.</p>}
    {reviewActions.map(action => { const scope = actionScope(data, action); const review = data.impact_reviews?.find(item => item.id === action.subject.id || item.affected_aim_evaluation_ids.some(id => action.command_argv.includes(id))); const verdict = data.verdicts.find(item => item.id === review?.trigger_verdict_id); const correction = data.evidence_corrections?.find(item => item.id === review?.correction_id); const original = data.evidence.find(item => item.id === correction?.corrected_evidence_id); const goal = data.goals.find(item => item.id === (scope.goalId ?? verdict?.goal_id)); const aim = data.aims.find(item => item.id === scope.aimId);
      return <article className="action-card review-card" key={`${action.kind}:${action.subject.id}`}><span className="eyebrow">{actionTitle(action.kind)}</span>
        <button type="button" className="relation-link" onClick={() => onOpen(action.kind === 'resolve_aim_evaluation_review' && aim ? { kind: 'aim', id: aim.id } : goal ? { kind: 'goal', id: goal.id } : null)}>{action.kind === 'resolve_aim_evaluation_review' ? `목표 — ${aim?.description ?? ''}` : `과제 — ${goal?.target ?? ''}`} →</button>
        {correction && <dl className="correction-diff"><dt>원래 관찰</dt><dd className="before observation-text">{original?.observation ?? ''}</dd><dt>정정</dt><dd className="observation-text">{correction.observation}</dd><dt>정정자</dt><dd>{correction.submitted_by_actor_id}</dd></dl>}
        {action.kind === 'resolve_verdict_review' && verdict && <p className="review-subject">다시 확인할 판정: <strong>{verdictResult(verdict.result)}</strong> · 검증자 {verdict.verifier_actor_id}</p>}
        <p className="tab-hint">{actionExplanation(action.kind)}</p>
        <ActionForm action={action} data={data} busy={busy} submit={submit} run={run} /><ActionTechnicalDetails action={action} /></article>; })}
  </section>;
}

export function RuntimeScreen({ runtime, ao = [], goalOfAttempt, onOpenGoal }: { runtime: RuntimeData; ao?: AoStatusRow[]; goalOfAttempt?: (attemptId: string) => string | undefined; onOpenGoal?: (goalId: string) => void }) { return <section className="review-screen"><div className="panel-heading"><div><h2>실행 대기함</h2><p>AI 에이전트에게 맡긴 실행 요청의 전달 상태입니다. 전달이 끝나도 실행 결과, 과제 검증, 목표 달성을 뜻하지는 않습니다.</p></div><span>{runtime.requests.length}</span></div>{runtime.requests.length ? runtime.requests.map((request, index) => <article className="record-card" key={request.request_id}><strong>실행 요청 {index + 1}</strong><p>요청 종류: {({ codex_exec: 'Codex 실행', claude_print: 'Claude 실행' } as Record<string, string>)[request.request_kind.replace(/\.v\d+$/, '')] ?? request.request_kind}</p><small>전달 상태: {request.status}{request.claimed_by ? ` · 처리자 ${request.claimed_by}` : ''}</small><TechnicalInfo rows={[['요청 ID', request.request_id], ['실행 ID', request.attempt_id]]} /></article>) : <><p className="empty-note">AI 에이전트에게 맡긴 실행 요청이 없습니다.</p><TechnicalInfo note="요청을 만들고 처리하는 작업은 CLI에서 합니다." rows={[['요청 목록', 'goal-loop runtime-requests'], ['요청 맡기', 'goal-loop runtime-claim'], ['요청 실행', 'goal-loop runtime-dispatch']]} /></>}<AoRuns rows={ao} goalOfAttempt={goalOfAttempt} onOpenGoal={onOpenGoal} /></section>; }

/** AO에 맡긴 실행 — 진행 중이거나 사람을 기다리는 것을 위에 보인다. 답하기는 AO CLI(또는 AO 앱)에서 한다(Decision 8). */
const AO_GOAL_STATUS: Record<string, string> = { completed: '완료', failed: '실패', awaiting_review: '검토 대기', in_progress: '진행 중', ready: '준비', planned: '계획', cancelled: '취소' };
const AO_CLAUSE: Record<string, string> = { 'objective-achievement': '목표 달성 재확인', 'has-changes': '변경 없음 판정', 'new-test-pre-change': '변경 전 실행 가드' };
const aoStatusName = (value?: string | null) => value ? AO_GOAL_STATUS[value] ?? value : '알 수 없음';
const shortTime = (value?: string | null) => value ? value.replace('T', ' ').slice(0, 16) : '';

// What an AO run did for this Goal, read from the AO snapshots in the journal plus the live AO request state.
export function AoRunSection({ goal, data, ao = [], onSelect }: { goal: Goal; data: Projection; ao?: AoStatusRow[]; onSelect: (value: Selection) => void }) {
  const runs = (data.ao_runs ?? []).filter(run => run.goal_id === goal.id);
  const attemptIds = new Set(data.attempts.filter(item => item.goal_id === goal.id).map(item => item.id));
  const live = ao.filter(row => attemptIds.has(row.attempt_id));
  const liveOnly = live.filter(row => !runs.some(run => `${run.ao_project_id}/${run.ao_goal_id}` === row.ao_goal));
  if (!runs.length && !liveOnly.length) return null;
  const waiting = (key: string) => live.filter(row => row.ao_goal === key && (row.waiting_for_human || row.next?.length));
  const liveCard = (row: AoStatusRow) => <div className="ao-live" key={row.request_id}>{row.waiting_for_human && <p className="ao-question">사람 {row.waiting_for_human.kind === 'approval' ? '승인' : '답'}을 기다립니다: {row.waiting_for_human.question}</p>}{row.next?.length ? <div className="ao-next"><span>이어 갈 명령</span>{row.next.map(command => <code key={command}>goal-loop {command}</code>)}</div> : null}</div>;
  return <details className="goal-section ao-run-section" open><summary>AO 실행 <small>{runs.length + liveOnly.length}개</small></summary>
    <p className="tab-hint">이 과제를 AI_ORCHESTRATION(AO)에 맡겨 실행한 기록입니다. 더 자세한 실행 과정은 AO 앱(ao.cmd app)에서 해당 프로젝트의 AO Goal을 열거나 AO CLI(ao status)로 봅니다.</p>
    {runs.map(run => { const key = `${run.ao_project_id}/${run.ao_goal_id}`; return <article className="record-card ao-run" key={key}>
      <div className="record-head"><strong>AO Goal <code>{run.ao_goal_id}</code></strong><em className={`state-badge ao-${run.status ?? 'unknown'}`}>{aoStatusName(run.status)}</em></div>
      {run.title && <p className="ao-run-title">{run.title}</p>}
      {waiting(key).map(liveCard)}
      {run.review_reason && <p className="ao-review-reason">{run.status === 'awaiting_review' ? '검토 사유' : '검토로 넘어갔던 사유(해소됨)'}: {run.review_reason}</p>}
      {run.sessions.length > 0 && <div className="ao-run-block"><span>세션</span><ul>{run.sessions.map(session => <li key={session.id}><code>{session.id}</code> · {aoStatusName(session.status)} · {shortTime(session.created_at)}~{shortTime(session.updated_at)} · 검증기 통과 {session.verifiers_passed} / 실패 {session.verifiers_failed}{session.recent_failures.length > 0 && <ul className="ao-failures">{session.recent_failures.map((item, index) => <li key={index}>{item.type}: {item.reason}</li>)}</ul>}{(session.advisories?.length ?? 0) > 0 && <div className="ao-advisories"><span>참고 판정(완료를 막지 않음)</span><ul>{session.advisories!.map((item, index) => <li key={index}>{AO_CLAUSE[item.clause ?? ''] ?? item.clause}: {item.reason}</li>)}</ul></div>}</li>)}</ul></div>}
      {run.commits.length > 0 && <div className="ao-run-block"><span>commit</span><ul>{run.commits.map(commit => <li key={commit.sha}><code>{commit.sha.slice(0, 8)}</code> {commit.subject}</li>)}</ul></div>}
      {run.evidence_ids.length > 0 && <div className="ao-run-links">{run.evidence_ids.map(id => { const evidence = data.evidence.find(item => item.id === id); return evidence ? <button type="button" className="relation-link" key={id} onClick={() => onSelect({ kind: 'attempt', id: evidence.attempt_id })}>근거 {id} · 실행 {evidence.attempt_id} 열기 →</button> : null; })}</div>}
    </article>; })}
    {liveOnly.map(row => <article className="record-card ao-run" key={row.request_id}><div className="record-head"><strong>AO Goal <code>{row.ao_goal}</code></strong><em className="state-badge">{aoStatusName(row.ao_goal_status)}</em></div><small>AO 세션 {row.ao_session ?? '없음'} · {aoStatusName(row.ao_session_status)} · 실행이 끝나면 결과가 여기에 정리됩니다.</small>{liveCard(row)}</article>)}
  </details>;
}

export function AoRuns({ rows, goalOfAttempt, onOpenGoal }: { rows: AoStatusRow[]; goalOfAttempt?: (attemptId: string) => string | undefined; onOpenGoal?: (goalId: string) => void }) {
  if (!rows.length) return null;
  const active = rows.filter(row => row.status !== 'completed' || row.waiting_for_human);
  const done = rows.length - active.length;
  return <section className="ao-runs"><div className="panel-heading"><div><h3>AO 실행</h3><p>AI_ORCHESTRATION(AO)에 맡긴 과제입니다. 사람 입력(승인·답)은 AO CLI에서 하고, 이어서 실행하면 결과가 이 과제의 실행 결과로 전달됩니다.</p></div><span>{active.length}</span></div>{active.length ? active.map(row => <article className={row.waiting_for_human ? 'record-card ao-waiting' : 'record-card'} key={row.request_id}><strong>AO Goal {row.ao_goal}</strong>{row.waiting_for_human ? <p className="ao-question">사람 {row.waiting_for_human.kind === 'approval' ? '승인' : '답'}을 기다립니다: {row.waiting_for_human.question}</p> : null}<small>요청 {row.status}{row.claimed_by ? ` · 처리자 ${row.claimed_by}` : ''} · AO Goal {row.ao_goal_status ?? '알 수 없음'} · AO 세션 {row.ao_session_status ?? '없음'}</small>{row.next?.length ? <div className="ao-next"><span>다음 명령</span>{row.next.map(command => <code key={command}>goal-loop {command}</code>)}</div> : null}<TechnicalInfo rows={[['요청 ID', row.request_id], ['요청 종류', row.request_kind], ['실행 ID', row.attempt_id], ['AO 세션', row.ao_session ?? '없음']]} /></article>) : <p className="empty-note">진행 중이거나 사람을 기다리는 AO 실행이 없습니다.</p>}{done ? <details className="ao-done"><summary>끝난 AO 실행 {done}건</summary><ul>{rows.filter(row => !active.includes(row)).slice().reverse().map(row => { const goalId = goalOfAttempt?.(row.attempt_id); return <li key={row.request_id}><code>{row.ao_goal}</code> · {aoStatusName(row.ao_goal_status)}{goalId && onOpenGoal ? <button type="button" className="relation-link" onClick={() => onOpenGoal(goalId)}>과제 {goalId} 열기 →</button> : null}</li>; })}</ul></details> : null}</section>;
}

const ATTACHMENT_STATUS: Record<AttachmentRecord['status'], string> = { preserved: '보존됨', changed: '변경됨', missing: '없음' };

async function openLocation(location: string): Promise<string> {
  if (!isTauri()) return '미리보기에서는 파일을 열 수 없습니다.';
  try { await invoke('open_location', { location }); return ''; } catch (cause) { return cause instanceof Error ? cause.message : String(cause); }
}

/** Copies of observed material. The status says whether the copy still matches the digest recorded with the Evidence. */
export function AttachmentList({ items }: { items: AttachmentRecord[] }) {
  const [problem, setProblem] = useState('');
  return <span className="attachment-list">{items.map(item => <span key={`${item.sha256}/${item.name}`} className={`attachment ${item.status}`}><button type="button" className="link-button" disabled={!item.path || item.status === 'missing'} title={item.status === 'changed' ? '복사본 내용이 기록할 때와 다릅니다.' : '기록할 때 복사한 파일을 엽니다.'} onClick={event => { event.preventDefault(); if (item.path) void openLocation(item.path).then(setProblem); }}>📎 {item.name}</button><small>{formatSize(item.size)} · {ATTACHMENT_STATUS[item.status]}</small></span>)}{problem && <small className="inline-problem">{problem}</small>}</span>;
}

function formatSize(size: number) { return size < 1024 ? `${size}B` : size < 1024 * 1024 ? `${(size / 1024).toFixed(1)}KB` : `${(size / 1024 / 1024).toFixed(1)}MB`; }

/** Living documents that give context. They can change, so they never stand in for a completion criterion. */
// Memory Tool timeline entries that cite these records, read-only. Nothing shows for a project without .memory.
export function MemorySection({ ids, data }: { ids: string[]; data: Projection }) {
  const wanted = new Set(ids);
  const entries = (data.memory_mentions ?? []).filter(entry => entry.ids.some(id => wanted.has(id)));
  const [all, setAll] = useState(false);
  const [copied, setCopied] = useState('');
  const pending = (data.memory_drafts ?? []).filter(draft => wanted.has(draft.record_id));
  if (!entries.length && !pending.length) return null;
  const copy = (draft: MemoryDraft) => { void navigator.clipboard?.writeText(draft.command).then(() => setCopied(draft.record_id), () => setCopied('')); };
  const shown = all ? entries : entries.slice(0, 5);
  return <details className="goal-section memory-section" open><summary>메모리 기록 <small>{entries.length}개{pending.length ? ` · 초안 ${pending.length}개` : ''}</small></summary>
    {pending.length > 0 && <div className="memory-drafts"><p className="tab-hint">타임라인에 아직 없는 기록입니다. 남길지는 직접 정하고, 남기려면 명령을 복사해 실행합니다. Goal Loop는 메모리 파일에 쓰지 않습니다.</p><ul>{pending.map(draft => <li key={draft.record_id}><p>{draft.text}</p><code>{draft.command}</code><button type="button" className="quiet" onClick={() => copy(draft)}>{copied === draft.record_id ? '복사했습니다' : '명령 복사'}</button></li>)}</ul></div>}
    <p className="tab-hint">Memory Tool 타임라인에서 이 기록을 인용한 항목입니다(최신순). 기록과 편집은 Memory Tool에서 합니다.</p>
    <ul className="memory-entries">{shown.map(entry => <li key={`${entry.file}:${entry.time}:${entry.text.slice(0, 20)}`}><span className="memory-when">{entry.date} {entry.time}</span><p>{entry.text}</p><small>{entry.file} · 인용: {entry.ids.filter(id => wanted.has(id)).join(', ')}</small></li>)}</ul>
    {entries.length > shown.length && <button type="button" className="quiet" onClick={() => setAll(true)}>나머지 {entries.length - shown.length}개 더 보기</button>}
  </details>;
}

export function MemoryDocumentPreview({ document }: { document: MemoryDocument }) {
  return <details className="memory-preview"><summary>미리보기</summary>{document.exists
    ? <>{document.anchor && !document.anchor_found && <p className="tab-hint">절 '{document.anchor}'을 찾지 못해 문서 처음부터 보입니다.</p>}<pre>{document.excerpt}</pre></>
    : <p className="inline-problem">파일을 읽을 수 없습니다.</p>}</details>;
}

export function ReferenceSection({ subjectId, references, projectRoot, busy, submit, documents = {} }: { documents?: Record<string, MemoryDocument>; subjectId: string; references: ReferenceRecord[]; projectRoot: string | null; busy: boolean; submit: (command: string, args: (form: HTMLFormElement) => string[]) => (event: FormEvent<HTMLFormElement>) => Promise<void> }) {
  const [problem, setProblem] = useState('');
  const location = useRef<HTMLInputElement>(null);
  const addArgs = (form: HTMLFormElement) => { const label = String((form.elements.namedItem('label') as HTMLInputElement | null)?.value ?? '').trim(); return [subjectId, input(form, 'location'), ...(label ? ['--label', label] : []), '--actor', input(form, 'actor')]; };
  const pick = async () => { if (!isTauri()) return; const file = await invoke<string | null>('pick_file', { directory: projectRoot }); if (file && location.current) location.current.value = file; };
  return <details className="goal-section reference-section"><summary>참고 문서 <small>{references.length ? `${references.length}개` : '없음'}</small></summary>
    {references.length ? <ul className="reference-list">{references.map(item => <li key={item.id}>{item.location.startsWith('ao:') ? <span className="ao-reference">{item.label ?? item.location} <small>AO 앱(또는 AO CLI)에서 이 AO Goal을 엽니다</small></span> : <button type="button" className="link-button" onClick={() => void openLocation(item.resolved).then(setProblem)}>{item.label ?? item.location}</button>}{!item.exists && <em className="state-badge missing">찾을 수 없음</em>}<small>{item.label ? `${item.location} · ` : ''}연결: {item.actor}</small>{documents[item.location] && <MemoryDocumentPreview document={documents[item.location]} />}<details className="sub-choice"><summary>연결 해제</summary><form onSubmit={submit('reference-remove', form => { const reason = String((form.elements.namedItem('reason') as HTMLInputElement | null)?.value ?? '').trim(); return [item.id, '--actor', input(form, 'actor'), ...(reason ? ['--reason', reason] : [])]; })}><Field name="actor" label="해제하는 사람" remember="reference" /><label className="field"><span>이유 (선택)</span><input name="reason" onKeyDown={event => event.stopPropagation()} /></label><button className="secondary" disabled={busy}>연결 해제</button></form></details></li>)}</ul> : <p className="tab-hint">설계 문서, memory 모듈, 이슈 URL처럼 이 항목을 이해하는 데 필요한 문서를 연결합니다.</p>}
    {problem && <p className="inline-problem">{problem}</p>}
    <details className="sub-choice reference-add"><summary>＋ 참고 문서 연결</summary><form className="reference-form" onSubmit={submit('reference-add', addArgs)}>
      <label className="field"><span>위치 — 파일 경로 또는 URL</span><span className="input-with-button"><input ref={location} name="location" required placeholder={projectRoot ? 'docs/설계.md 또는 https://…' : '전체 경로 또는 https://…'} />{isTauri() && <button type="button" className="secondary" onClick={() => void pick()}>파일 선택</button>}</span></label>
      <label className="field"><span>이름 (선택)</span><input name="label" onKeyDown={event => event.stopPropagation()} /></label>
      <Field name="actor" label="연결한 사람" remember="reference" />
      <p className="tab-hint">문서 내용은 나중에 바뀔 수 있습니다. 판정에 필요한 조건은 완료 기준 본문에 적으세요.</p>
      <button className="secondary" disabled={busy}>참고 문서 연결</button>
    </form></details>
  </details>;
}

/** Evidence with optional attachments. Files are copied when the Evidence is recorded and cannot be added afterwards. */
function EvidenceForm({ attempt, projectRoot, busy, submit }: { attempt: string; projectRoot: string | null; busy: boolean; submit: (command: string, args: (form: HTMLFormElement) => string[]) => (event: FormEvent<HTMLFormElement>) => Promise<void> }) {
  const [files, setFiles] = useState<string[]>([]);
  const pick = async () => { if (!isTauri()) return; const file = await invoke<string | null>('pick_file', { directory: projectRoot }); if (file) setFiles(current => current.includes(file) ? current : [...current, file]); };
  return <form onSubmit={submit('evidence-record', form => [attempt, input(form, 'actor'), input(form, 'observation'), ...files.flatMap(file => ['--attach', file])])}><Field name="actor" label="관찰자" remember="observer" /><Field name="observation" label="관찰 사실" multiline />
    <fieldset className="attachment-picker"><legend>첨부 (선택)</legend>{files.length ? <ul>{files.map(file => <li key={file}><span>{file.split(/[\\/]/).at(-1)}</span><button type="button" className="quiet" onClick={() => setFiles(current => current.filter(item => item !== file))}>빼기</button></li>)}</ul> : <p className="tab-hint">관찰에 사용한 로그나 출력 파일을 함께 보존합니다. 기록할 때 복사되며, 나중에는 붙일 수 없습니다. 파일 하나당 10MB까지입니다.</p>}{isTauri() && <button type="button" className="secondary" onClick={() => void pick()}>파일 추가</button>}</fieldset>
    <button disabled={busy}>관찰 근거 기록</button></form>;
}
