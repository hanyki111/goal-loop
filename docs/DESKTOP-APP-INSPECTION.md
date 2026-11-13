# Inspecting the desktop app as an agent

Moved from `CLAUDE.md` unchanged on 2026-11-13 (goal-160), so the instruction files keep only the rules that
always apply.

Desktop app: `cd ui; npm run tauri dev` (the user starts it). To let an agent inspect the window, start it
with `$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--remote-debugging-port=9222'` first, then use
`node ui/scripts/cdp.mjs shot|eval|click|clicktext|type|key` (screenshots go to the OS temp dir). The app reloads
the journal when its window regains focus.
