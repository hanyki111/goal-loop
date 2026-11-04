'use strict';
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vscode = require('vscode');
const { createChangeDetector, createCli, createHost, journalRecord, journalStamp, projectRootOf } = require('./host');

const VIEW_TYPE = 'goalLoop.journal';
const STORAGE_KEY = 'goalLoop.storage';
const SELECTED_WORKSPACE_KEY = 'goal-loop.workspace.id';
const CHECK_INTERVAL_MS = 2000;

const cli = createCli({ setting: () => vscode.workspace.getConfiguration('goalLoop').get('executable', '').trim() });

const hostUi = {
  pickFile: async directory => {
    const picked = await vscode.window.showOpenDialog({ canSelectMany: false, defaultUri: directory ? vscode.Uri.file(directory) : undefined });
    return picked?.[0]?.fsPath ?? null;
  },
  pickFolder: async () => {
    const picked = await vscode.window.showOpenDialog({ canSelectFiles: false, canSelectFolders: true, canSelectMany: false });
    return picked?.[0]?.fsPath ?? null;
  },
  openExternal: url => vscode.env.openExternal(vscode.Uri.parse(url)),
  openFile: file => vscode.commands.executeCommand('vscode.open', vscode.Uri.file(file)),
  revealFolder: folder => vscode.commands.executeCommand('revealInExplorer', vscode.Uri.file(folder)),
};

function screenHtml(webview, context, storage) {
  const media = vscode.Uri.joinPath(context.extensionUri, 'media');
  const nonce = crypto.randomBytes(16).toString('hex');
  const source = name => webview.asWebviewUri(vscode.Uri.joinPath(media, name));
  const data = JSON.stringify(storage).replace(/</g, '\\u003c');
  // In the style below, a narrow editor tab scrolls as one page because the app's narrow layout grows with its content.
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} data:; font-src ${webview.cspSource}; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<link rel="stylesheet" href="${source('index.css')}">
<style>html,body,#root{height:100%}body{margin:0;padding:0}@media(max-width:1100px){html{overflow:auto}body{overflow:visible}.workspace{overflow:visible}}</style>
<script id="goal-loop-storage" type="application/json">${data}</script></head>
<body><div id="root"></div><script type="module" nonce="${nonce}" src="${source('index.js')}"></script></body></html>`;
}

function openScreen(panel, database, context) {
  const webview = panel.webview;
  webview.options = { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, 'media')] };
  const host = createHost({ database, cli, ui: hostUi });
  const storage = context.globalState.get(STORAGE_KEY, {});
  // This screen's journal is decided by what was opened, never by what another screen last selected.
  webview.html = screenHtml(webview, context, { ...storage, [SELECTED_WORKSPACE_KEY]: journalRecord(database).id });

  const subscription = webview.onDidReceiveMessage(async message => {
    if (message?.type === 'storage') {
      if (message.key === SELECTED_WORKSPACE_KEY) return;
      const items = { ...context.globalState.get(STORAGE_KEY, {}) };
      if (message.value === null) delete items[message.key]; else items[message.key] = message.value;
      await context.globalState.update(STORAGE_KEY, items);
    }
    if (message?.type === 'request') {
      try {
        void webview.postMessage({ type: 'response', id: message.id, result: await host.handle(message.command, message.args) ?? null });
      } catch (cause) {
        void webview.postMessage({ type: 'response', id: message.id, error: cause instanceof Error ? cause.message : String(cause) });
      }
    }
  });

  const detector = createChangeDetector({
    stamp: async () => journalStamp(database),
    checkpoint: async () => JSON.parse(await cli.run(['--database', database, 'status'], projectRootOf(database))).checkpoint,
    notify: () => void webview.postMessage({ type: 'journal-changed' }),
  });
  // Polling a file's time also works where file events do not arrive, such as a network folder on the server.
  void detector.check();
  const timer = setInterval(() => { if (panel.visible) void detector.check(); }, CHECK_INTERVAL_MS);
  panel.onDidDispose(() => { clearInterval(timer); subscription.dispose(); });
}

async function journalsInWorkspace() {
  const found = await vscode.workspace.findFiles('**/.goal-loop/journal.sqlite3', '**/node_modules/**', 20);
  return found.sort((left, right) => left.fsPath.localeCompare(right.fsPath));
}

async function openCommand() {
  const journals = await journalsInWorkspace();
  if (!journals.length) {
    const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const start = '이 폴더에서 시작';
    if (!folder || await vscode.window.showInformationMessage('이 폴더에는 Goal Loop 저널이 없습니다.', start) !== start) return;
    const initialized = JSON.parse(await cli.run(['project-init', folder], folder));
    updateStatusBar();
    return vscode.commands.executeCommand('vscode.openWith', vscode.Uri.file(initialized.database), VIEW_TYPE);
  }
  let target = journals[0];
  if (journals.length > 1) {
    const picked = await vscode.window.showQuickPick(journals.map(uri => ({ label: path.basename(projectRootOf(uri.fsPath)), description: uri.fsPath, uri })), { placeHolder: '열 프로젝트를 고르세요' });
    if (!picked) return;
    target = picked.uri;
  }
  return vscode.commands.executeCommand('vscode.openWith', target, VIEW_TYPE);
}

let statusBar;
function updateStatusBar() {
  void journalsInWorkspace().then(journals => { if (journals.length) statusBar.show(); else statusBar.hide(); });
}

function activate(context) {
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 10);
  statusBar.text = '$(type-hierarchy) Goal Loop';
  statusBar.tooltip = 'Goal Loop 화면 열기';
  statusBar.command = 'goalLoop.open';
  updateStatusBar();
  context.subscriptions.push(
    statusBar,
    vscode.commands.registerCommand('goalLoop.open', () => openCommand().catch(cause => vscode.window.showErrorMessage(cause instanceof Error ? cause.message : String(cause)))),
    vscode.workspace.onDidChangeWorkspaceFolders(updateStatusBar),
    vscode.workspace.onDidChangeConfiguration(event => { if (event.affectsConfiguration('goalLoop.executable')) cli.forget(); }),
    vscode.window.registerCustomEditorProvider(VIEW_TYPE, {
      openCustomDocument: uri => ({ uri, dispose() {} }),
      resolveCustomEditor: (document, panel) => {
        if (!fs.existsSync(path.join(context.extensionPath, 'media', 'index.js'))) {
          panel.webview.html = '<p>화면 파일이 없습니다. vscode 폴더에서 npm run build를 실행하세요.</p>';
          return;
        }
        panel.title = `Goal Loop: ${path.basename(projectRootOf(document.uri.fsPath))}`;
        openScreen(panel, document.uri.fsPath, context);
      },
    }, { webviewOptions: { retainContextWhenHidden: true }, supportsMultipleEditorsPerDocument: false }),
  );
}

module.exports = { activate, deactivate() {} };
