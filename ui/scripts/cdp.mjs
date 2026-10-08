// Minimal CDP client for the Goal Loop WebView2 window (dev only).
// Start the app with $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--remote-debugging-port=9222'; npm run tauri dev
// Usage:
//   node cdp.mjs shot [name]            -> screenshot to $CDP_SHOTS (default: OS temp dir)/goal-loop-shots/<name>.png
//   node cdp.mjs eval "<js expression>" -> prints JSON result (awaits promises)
//   node cdp.mjs click "<css selector>" | clicktext "<visible text>" [nth]
//   node cdp.mjs type "<text>"          -> insertText into focused element
//   node cdp.mjs key <Enter|Tab|Escape|...>
//   node cdp.mjs console                -> recent console messages / exceptions (collected for 1.5s after reload not needed)
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const port = process.env.CDP_PORT || 9222;
const [cmd, ...args] = process.argv.slice(2);

const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const page = targets.find((t) => t.type === "page" && !t.url.startsWith("devtools"));
if (!page) throw new Error("no page target: " + JSON.stringify(targets));

const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let id = 0;
const pending = new Map();
const events = [];
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) {
    const { res, rej } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
  } else if (msg.method) events.push(msg);
};
const send = (method, params = {}) =>
  new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });

async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}

async function clickAt(x, y) {
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"])
    await send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
}

const findByText = (text, nth = 0) => `(() => {
  const want = ${JSON.stringify(text)};
  const els = [...document.querySelectorAll('button, a, [role=button], [role=tab], [role=menuitem], label, summary, option, li, h1, h2, h3, h4, span, div, p')]
    .filter(e => e.offsetParent !== null && (e.innerText || '').trim() === want);
  // prefer innermost
  const inner = els.filter(e => !els.some(o => o !== e && e.contains(o)));
  const el = inner[${nth}];
  if (!el) return null;
  el.scrollIntoView({block: 'center'});
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2, tag: el.tagName, count: inner.length };
})()`;

const bySelector = (sel, nth = 0) => `(() => {
  const el = document.querySelectorAll(${JSON.stringify(sel)})[${nth}];
  if (!el) return null;
  el.scrollIntoView({block: 'center'});
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2, tag: el.tagName };
})()`;

try {
  if (cmd === "shot") {
    const name = args[0] || `shot-${Date.now()}`;
    const r = await send("Page.captureScreenshot", { format: "png" });
    const out = join(process.env.CDP_SHOTS || join(tmpdir(), "goal-loop-shots"), `${name}.png`);
    mkdirSync(dirname(out), { recursive: true });
    writeFileSync(out, Buffer.from(r.data, "base64"));
    console.log(out);
  } else if (cmd === "eval") {
    console.log(JSON.stringify(await evaluate(args[0]), null, 2));
  } else if (cmd === "click" || cmd === "clicktext") {
    const pos = await evaluate(cmd === "click" ? bySelector(args[0], +(args[1] || 0)) : findByText(args[0], +(args[1] || 0)));
    if (!pos) { console.log("NOT FOUND:", args[0]); process.exitCode = 2; }
    else { await clickAt(pos.x, pos.y); console.log("clicked", JSON.stringify(pos)); }
  } else if (cmd === "type") {
    await send("Input.insertText", { text: args[0] });
    console.log("typed");
  } else if (cmd === "key") {
    const key = args[0];
    const codes = { Enter: 13, Tab: 9, Escape: 27, Backspace: 8, ArrowDown: 40, ArrowUp: 38 };
    const base = { key, code: key, windowsVirtualKeyCode: codes[key] || 0 };
    await send("Input.dispatchKeyEvent", { type: "rawKeyDown", ...base, ...(key === "Enter" ? { text: "\r" } : {}) });
    if (key === "Enter") await send("Input.dispatchKeyEvent", { type: "char", ...base, text: "\r" });
    await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    console.log("key", key);
  } else if (cmd === "console") {
    await send("Runtime.enable");
    await send("Log.enable");
    await new Promise((r) => setTimeout(r, 800));
    for (const e of events) {
      if (e.method === "Runtime.consoleAPICalled")
        console.log(`[${e.params.type}]`, e.params.args.map((a) => a.value ?? a.description).join(" "));
      else if (e.method === "Runtime.exceptionThrown")
        console.log("[exception]", e.params.exceptionDetails.exception?.description);
      else if (e.method === "Log.entryAdded") console.log(`[log:${e.params.entry.level}]`, e.params.entry.text);
    }
  } else {
    console.log("unknown command");
  }
} finally {
  ws.close();
}
