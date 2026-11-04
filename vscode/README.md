# Goal Loop for VS Code

Open a project's Goal Loop journal inside VS Code: the same graph, detail, and record screens as the desktop
app, including changes. Click `.goal-loop/journal.sqlite3` in the Explorer, run **Goal Loop: 화면 열기**, or use
**Goal Loop** in the status bar.

The extension runs where the journal is. In a Remote SSH window the screen is drawn on your client while the
extension and the `goal-loop` CLI run on the server, so install the extension and the CLI on the server. Linux
clients need a Korean font (for example `fonts-noto-cjk`) to draw the screen's Korean text.

The CLI is looked for in the `goalLoop.executable` setting, then the project's `.venv` (`Scripts/goal-loop.exe`
or `bin/goal-loop`), `goal-loop` on PATH, and `python3` or `python` with `-m goal_loop_tool persistent`.

Each screen uses only the journal it opened and project folders opened from it, and runs only the commands the
desktop app allows. It needs a trusted workspace. Records made here are the same as records made in the app:
what you click is recorded under your name, so keep agents out of this screen. Managing the app's workspace list
stays in the desktop app.

## Build

```bash
npm --prefix ../ui ci      # first time
npm test                   # extension tests
npm run package            # builds the screen into media/ and writes goal-loop-<version>.vsix
```

Install the `.vsix` with **Extensions: Install from VSIX...** in a window connected to the server, or run
`code --install-extension goal-loop-<version>.vsix` in that window's terminal.
