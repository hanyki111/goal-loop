import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Workspace } from './Workspace';

const data = {
  aims: [{ id: 'aim-1', description: '목표', phase: 'open', paused: false, currently_achieved: false }],
  proposals: [], goals: [], attempts: [], evidence: [], verdicts: [], aim_evaluations: [], schema_version: 15,
  nodes: [{ id: 'aim-1', kind: 'aim', label: '목표', phase: 'open', aim_id: 'aim-1', dependency_blocked: false }], edges: [],
};

describe('reading the journal again when the window regains focus', () => {
  let pending: Array<(value: unknown) => void>;
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    pending = [];
    let first = true;
    fetchMock = vi.fn(() => {
      if (first) { first = false; return Promise.resolve({ json: async () => data }); }
      return new Promise(resolve => { pending.push(resolve); });  // held open until the test releases it
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('shows that it is loading, does not start overlapping reads, and reads once more afterwards', async () => {
    render(<Workspace />);
    await screen.findByRole('combobox', { name: '그래프 배치' });
    expect(screen.queryByText('불러오는 중…')).toBeNull();
    act(() => { window.dispatchEvent(new Event('focus')); });
    expect(await screen.findByText('불러오는 중…')).toBeTruthy();
    expect(document.querySelector('.refresh-indicator .spinner')).toBeTruthy();
    act(() => { window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('focus')); });
    expect(fetchMock).toHaveBeenCalledTimes(2);  // the first load and one reload; the extra focus events wait
    await act(async () => { pending.shift()!({ json: async () => data }); });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));  // read once more for the focus events that waited
    await act(async () => { pending.shift()!({ json: async () => data }); });
    await waitFor(() => expect(screen.queryByText('불러오는 중…')).toBeNull());
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
