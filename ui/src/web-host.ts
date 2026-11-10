// Stands in for @tauri-apps/api/core in the browser build (vite.web.config.ts) that `goal-loop ui` serves, so the
// app's screen runs unchanged. The server answers reads only; it refuses every change with a message the screen shows.
const CHECK_INTERVAL_MS = 2000;

export const isTauri = () => true;

export async function invoke<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  // A web address opens in a new browser tab; files on the server cannot be opened from the browser.
  if (command === 'open_location' && typeof args.location === 'string' && /^https?:\/\//i.test(args.location)) {
    window.open(args.location, '_blank', 'noopener');
    return undefined as T;
  }
  const response = await fetch('./api', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command, args }),
  });
  const body = await response.json().catch(() => ({ error: `서버 응답을 읽지 못했습니다(HTTP ${response.status}).` }));
  if (body.error !== undefined) throw new Error(String(body.error));
  return body.result as T;
}

// The screen already rereads quietly when its window regains focus, so a changed checkpoint raises that event.
let lastCheckpoint: unknown = null;
async function checkJournal() {
  try {
    const response = await fetch('./checkpoint', { cache: 'no-store' });
    const { checkpoint } = await response.json();
    if (lastCheckpoint !== null && checkpoint !== lastCheckpoint) window.dispatchEvent(new Event('focus'));
    lastCheckpoint = checkpoint;
  } catch { /* the server stopped or is busy; look again on the next check */ }
}
void checkJournal();
setInterval(() => { if (document.visibilityState === 'visible') void checkJournal(); }, CHECK_INTERVAL_MS);
