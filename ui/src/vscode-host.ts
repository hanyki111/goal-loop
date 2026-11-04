// Stands in for @tauri-apps/api/core in the VS Code build (vite.vscode.config.ts), so the app's screen runs unchanged.
declare function acquireVsCodeApi(): { postMessage(message: unknown): void };

const vscode = acquireVsCodeApi();
const pending = new Map<number, { resolve: (value: unknown) => void; reject: (reason: Error) => void }>();
let nextRequest = 0;

window.addEventListener('message', event => {
  const message = event.data;
  if (message?.type === 'response') {
    const request = pending.get(message.id);
    pending.delete(message.id);
    if (message.error !== undefined) request?.reject(new Error(message.error));
    else request?.resolve(message.result);
  }
  // The screen already rereads quietly when its window regains focus.
  if (message?.type === 'journal-changed') window.dispatchEvent(new Event('focus'));
});

export const isTauri = () => true;

export function invoke<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const id = ++nextRequest;
    pending.set(id, { resolve: value => resolve(value as T), reject });
    vscode.postMessage({ type: 'request', id, command, args });
  });
}

// A webview's own localStorage may not survive reopening, so the extension keeps these preferences.
function hostStorage(initial: Record<string, string>): Storage {
  const items = new Map(Object.entries(initial));
  return {
    get length() { return items.size; },
    key: index => [...items.keys()][index] ?? null,
    getItem: key => items.get(key) ?? null,
    setItem: (key, value) => { items.set(key, String(value)); vscode.postMessage({ type: 'storage', key, value: String(value) }); },
    removeItem: key => { items.delete(key); vscode.postMessage({ type: 'storage', key, value: null }); },
    clear: () => { for (const key of [...items.keys()]) { items.delete(key); vscode.postMessage({ type: 'storage', key, value: null }); } },
  };
}

const initial = JSON.parse(document.getElementById('goal-loop-storage')?.textContent ?? '{}') as Record<string, string>;
Object.defineProperty(window, 'localStorage', { configurable: true, value: hostStorage(initial) });
