'use strict';
// What the screen asks for, without the VS Code API, so it can be tested with plain Node.
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

// The same list as UI_COMMANDS in ui/src-tauri/src/main.rs (test/host.test.js compares them).
const UI_COMMANDS = [
  'aim-create', 'proposal-create', 'proposal-revise', 'goal-accept', 'attempt-start', 'attempt-complete',
  'evidence-record', 'evidence-correct', 'verification-begin', 'verdict-submit',
  'retry-decide', 'reverification-decide', 'aim-evaluate', 'contract-revise',
  'verdict-review-resolve', 'aim-evaluation-review-resolve', 'aim-closure-invalidate',
  'aim-pause', 'aim-resume', 'goal-hold-add', 'goal-hold-clear', 'goal-parent-add', 'goal-follow-add',
  'goal-dependency-add', 'proposal-close', 'goal-stop', 'aim-abandon',
  'reference-add', 'reference-remove', 'aim-parent-add', 'aim-link-import',
];
const READS = { ui_projection: 'ui-projection', next_actions: 'next', reviews: 'reviews', admission: 'admission', runtime_requests: 'runtime-requests' };
const CLI_TIMEOUT_MS = 120_000;
// ao-status reads AO workspace files and takes seconds; the screen asks for it on every reload.
const AO_STATUS_TTL_MS = 60_000;

const projectRootOf = database => path.dirname(path.dirname(database));

function journalRecord(database) {
  return {
    id: `journal-${crypto.createHash('sha1').update(database).digest('hex').slice(0, 12)}`,
    name: path.basename(projectRootOf(database)),
    project: projectRootOf(database),
    database,
  };
}

/** Every way to start the CLI, in the order to try them. An explicit setting is the only candidate. */
function cliCandidates({ setting, projectRoot, platform = process.platform }) {
  if (setting) return [{ command: setting, args: [] }];
  const venv = platform === 'win32'
    ? path.win32.join(projectRoot, '.venv', 'Scripts', 'goal-loop.exe')
    : path.posix.join(projectRoot, '.venv', 'bin', 'goal-loop');
  const module = ['-m', 'goal_loop_tool', 'persistent'];
  return [
    { command: venv, args: [] },
    { command: 'goal-loop', args: [] },
    { command: 'python3', args: module },
    { command: 'python', args: module },
  ];
}

function runProcess(command, args, cwd, timeout = CLI_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } });
    const stdout = []; const stderr = [];
    const timer = setTimeout(() => { child.kill(); reject(new Error(`Goal Loop CLI가 ${timeout / 1000}초 안에 끝나지 않았습니다: ${args.join(' ')}`)); }, timeout);
    child.stdout.on('data', chunk => stdout.push(chunk));
    child.stderr.on('data', chunk => stderr.push(chunk));
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code === 0) resolve(Buffer.concat(stdout).toString('utf8'));
      else reject(new Error(Buffer.concat(stderr).toString('utf8').trim() || `Goal Loop CLI가 코드 ${code}로 끝났습니다: ${args.join(' ')}`));
    });
  });
}

/** Returns run(cliArgs, projectRoot) -> stdout. Each project keeps the first candidate that starts. */
function createCli({ setting, run = runProcess, platform = process.platform }) {
  const found = new Map();
  const resolve = async projectRoot => {
    if (found.has(projectRoot)) return found.get(projectRoot);
    const candidates = cliCandidates({ setting: setting(), projectRoot, platform });
    for (const candidate of candidates) {
      try {
        await run(candidate.command, [...candidate.args, '--help'], projectRoot);
        found.set(projectRoot, candidate);
        return candidate;
      } catch { /* try the next way to start it */ }
    }
    throw new Error(`이 환경에서 Goal Loop CLI를 찾지 못했습니다(확인한 대상: ${candidates.map(item => [item.command, ...item.args].join(' ')).join(', ')}). 확장은 저널이 있는 쪽(원격이면 원격 서버)에서 CLI를 실행합니다. 그곳에 goal-loop-tool을 설치하거나 goalLoop.executable 설정에 경로를 지정하세요.`);
  };
  return {
    forget: () => found.clear(),
    run: async (args, projectRoot) => {
      const candidate = await resolve(projectRoot);
      return run(candidate.command, [...candidate.args, ...args], projectRoot);
    },
  };
}

const NOT_HERE = 'VS Code에서는 저널 파일이나 폴더를 직접 엽니다. 작업 공간 목록을 관리하는 기능은 데스크톱 앱에서만 할 수 있습니다.';

/** One screen's requests. It may only touch the journal it opened and project folders opened from it. */
function createHost({ database, cli, ui }) {
  const records = new Map([[database, journalRecord(database)]]);
  const aoStatus = new Map();
  let pickedFolder = null;

  const journal = requested => {
    const selected = requested ?? database;
    if (!records.has(selected)) throw new Error(`이 화면에서 연 저널이 아닙니다: ${selected}`);
    return selected;
  };
  const read = async (selected, args) => JSON.parse(await cli.run(['--database', selected, ...args], projectRootOf(selected)));
  const aoStatusOf = selected => {
    const cached = aoStatus.get(selected);
    if (cached && (cached.running || Date.now() - cached.at < AO_STATUS_TTL_MS)) return cached.result;
    const entry = { running: true, at: 0, result: null };
    entry.result = read(selected, ['ao-status']).then(
      value => { entry.running = false; entry.at = Date.now(); return value; },
      error => { aoStatus.delete(selected); throw error; },
    );
    aoStatus.set(selected, entry);
    return entry.result;
  };

  const handle = async (command, args = {}) => {
    if (READS[command]) return read(journal(args.database), [READS[command]]);
    switch (command) {
      case 'workspaces': return { workspaces: [...records.values()] };
      case 'ao_status': return aoStatusOf(journal(args.database));
      case 'activity': {
        const selected = journal(args.database);
        const output = await cli.run(['--database', selected, 'activity'], projectRootOf(selected));
        return output.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
      }
      case 'goal_loop_command': {
        if (!UI_COMMANDS.includes(args.commandName)) throw new Error(`허용되지 않은 명령입니다: ${args.commandName}`);
        if (!Array.isArray(args.args) || args.args.some(item => typeof item !== 'string')) throw new Error('명령 인자가 올바르지 않습니다.');
        return read(journal(args.database), [args.commandName, ...args.args]);
      }
      case 'pick_file': return ui.pickFile(typeof args.directory === 'string' ? args.directory : undefined);
      case 'pick_folder': pickedFolder = await ui.pickFolder(); return pickedFolder;
      case 'workspace_open_project': {
        if (!pickedFolder || args.folder !== pickedFolder) throw new Error('폴더 선택 창에서 고른 폴더만 열 수 있습니다.');
        const initialized = JSON.parse(await cli.run(['project-init', pickedFolder], pickedFolder));
        const record = journalRecord(initialized.database);
        records.set(record.database, record);
        return record;
      }
      case 'open_location': return openLocation(String(args.location ?? ''));
      case 'workspace_create': case 'workspace_rename': case 'workspace_delete': throw new Error(NOT_HERE);
      default: throw new Error(`지원하지 않는 요청입니다: ${command}`);
    }
  };

  const openLocation = async location => {
    if (/^https?:\/\//i.test(location)) return void await ui.openExternal(location);
    const resolved = path.resolve(projectRootOf(database), location);
    let stat;
    try { stat = fs.statSync(resolved); } catch { throw new Error(`파일을 찾지 못했습니다: ${location}`); }
    return void await (stat.isDirectory() ? ui.revealFolder(resolved) : ui.openFile(resolved));
  };

  return { handle };
}

/**
 * Tells the screen when another process wrote to the journal. Merely reading a journal changes its WAL file's
 * time, so a changed file only prompts a look at the checkpoint, and the file state is compared after that read.
 */
function createChangeDetector({ stamp, checkpoint, notify }) {
  let lastStamp = null; let lastCheckpoint = null; let running = false;
  const check = async () => {
    if (running) return;
    running = true;
    try {
      if (lastStamp !== null && (await stamp()) === lastStamp) return;
      const current = await checkpoint();
      lastStamp = await stamp();
      if (lastCheckpoint !== null && current !== lastCheckpoint) notify();
      lastCheckpoint = current;
    } catch { /* the screen shows CLI problems itself; look again on the next check */ }
    finally { running = false; }
  };
  return { check };
}

function journalStamp(database) {
  return [database, `${database}-wal`].map(file => {
    try { const stat = fs.statSync(file); return `${stat.size}:${stat.mtimeMs}`; } catch { return '-'; }
  }).join('|');
}

module.exports = { UI_COMMANDS, cliCandidates, createChangeDetector, createCli, createHost, journalRecord, journalStamp, projectRootOf };
