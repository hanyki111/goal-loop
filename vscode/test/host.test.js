'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { UI_COMMANDS, cliCandidates, createChangeDetector, createCli, createHost, journalRecord } = require('../src/host');

const database = path.join(os.tmpdir(), 'project', '.goal-loop', 'journal.sqlite3');
const fakeCli = (reply = () => '{}') => {
  const calls = [];
  return { calls, run: async (args, root) => { calls.push({ args, root }); return reply(args); } };
};
const noUi = {};

test('the allowed commands match the desktop app', () => {
  const source = fs.readFileSync(path.join(__dirname, '../../ui/src-tauri/src/main.rs'), 'utf8');
  const block = source.match(/const UI_COMMANDS: &\[&str\] = &\[([\s\S]*?)\];/)[1];
  assert.deepEqual([...UI_COMMANDS].sort(), [...block.matchAll(/"([^"]+)"/g)].map(match => match[1]).sort());
});

test('the CLI is looked for in the setting alone, or the Linux venv, PATH, then Python', () => {
  assert.deepEqual(cliCandidates({ setting: '/opt/goal-loop', projectRoot: '/p', platform: 'linux' }), [{ command: '/opt/goal-loop', args: [] }]);
  const module = ['-m', 'goal_loop_tool', 'persistent'];
  assert.deepEqual(cliCandidates({ setting: '', projectRoot: '/home/me/p', platform: 'linux' }), [
    { command: '/home/me/p/.venv/bin/goal-loop', args: [] },
    { command: 'goal-loop', args: [] },
    { command: 'python3', args: module },
    { command: 'python', args: module },
  ]);
  assert.equal(cliCandidates({ setting: '', projectRoot: 'E:\\p', platform: 'win32' })[0].command, 'E:\\p\\.venv\\Scripts\\goal-loop.exe');
});

test('the first CLI that starts is kept, and a missing one explains where it must be installed', async () => {
  const started = [];
  const cli = createCli({ setting: () => '', platform: 'linux', run: async (command, args) => {
    started.push([command, ...args].join(' '));
    if (command !== 'python3') throw new Error('ENOENT');
    return 'ok';
  } });
  assert.equal(await cli.run(['status'], '/p'), 'ok');
  await cli.run(['next'], '/p');
  assert.deepEqual(started.slice(-2), ['python3 -m goal_loop_tool persistent status', 'python3 -m goal_loop_tool persistent next']);
  assert.equal(started.filter(line => line.endsWith('--help')).length, 3);
  const missing = createCli({ setting: () => '', platform: 'linux', run: async () => { throw new Error('ENOENT'); } });
  await assert.rejects(missing.run(['status'], '/p'), /원격 서버/);
});

test('changes run allowed commands against this screen\'s journal only', async () => {
  const cli = fakeCli(() => '{"id":"aim-1"}');
  const host = createHost({ database, cli, ui: noUi });
  assert.deepEqual(await host.handle('goal_loop_command', { commandName: 'aim-create', args: ['목표'], database }), { id: 'aim-1' });
  assert.deepEqual(cli.calls[0].args, ['--database', database, 'aim-create', '목표']);
  await assert.rejects(host.handle('goal_loop_command', { commandName: 'runtime-dispatch', args: [], database }), /허용되지 않은/);
  await assert.rejects(host.handle('goal_loop_command', { commandName: 'aim-create', args: ['x'], database: '/other/.goal-loop/journal.sqlite3' }), /연 저널이 아닙니다/);
  await assert.rejects(host.handle('ui_projection', { database: '/other/.goal-loop/journal.sqlite3' }), /연 저널이 아닙니다/);
  await assert.rejects(host.handle('goal_loop_command', { commandName: 'aim-create', args: [1], database }), /인자/);
  assert.equal(cli.calls.length, 1);
});

test('reads map to the CLI, activity lines become a list, and workspace management is refused', async () => {
  const cli = fakeCli(args => args.at(-1) === 'activity' ? '{"a":1}\r\n\n{"a":2}\n' : '{"ok":true}');
  const host = createHost({ database, cli, ui: noUi });
  assert.deepEqual(await host.handle('next_actions', {}), { ok: true });
  assert.deepEqual(cli.calls[0].args, ['--database', database, 'next']);
  assert.deepEqual(await host.handle('activity', { database }), [{ a: 1 }, { a: 2 }]);
  assert.deepEqual(await host.handle('workspaces'), { workspaces: [journalRecord(database)] });
  await assert.rejects(host.handle('workspace_delete', { workspaceId: 'x' }), /데스크톱 앱/);
  await assert.rejects(host.handle('fs_write', {}), /지원하지 않는/);
});

test('a slow ao-status is shared and reused instead of rerun on every reload', async () => {
  const cli = fakeCli(() => '{"ao_requests":[]}');
  const host = createHost({ database, cli, ui: noUi });
  await Promise.all([host.handle('ao_status', { database }), host.handle('ao_status', { database })]);
  await host.handle('ao_status', { database });
  assert.equal(cli.calls.length, 1);
});

test('only a folder chosen in the dialog can be opened as a project, and it can then be used', async () => {
  const other = path.join(os.tmpdir(), 'other', '.goal-loop', 'journal.sqlite3');
  const folder = path.dirname(path.dirname(other));
  const cli = fakeCli(args => args[0] === 'project-init' ? JSON.stringify({ database: other }) : '{}');
  const host = createHost({ database, cli, ui: { pickFolder: async () => folder } });
  await assert.rejects(host.handle('workspace_open_project', { name: 'x', folder }), /선택 창/);
  assert.equal(await host.handle('pick_folder'), folder);
  assert.equal((await host.handle('workspace_open_project', { name: 'x', folder })).database, other);
  await host.handle('ui_projection', { database: other });
  assert.deepEqual(cli.calls.at(-1).args, ['--database', other, 'ui-projection']);
});

test('locations open in the editor or browser; other schemes and missing files do not', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'goal-loop-host-'));
  const file = path.join(directory, 'note.md'); fs.writeFileSync(file, 'x');
  const opened = [];
  const host = createHost({ database, cli: fakeCli(), ui: {
    openExternal: async url => opened.push(['external', url]), openFile: async item => opened.push(['file', item]), revealFolder: async item => opened.push(['folder', item]),
  } });
  await host.handle('open_location', { location: 'https://example.com/a' });
  await host.handle('open_location', { location: file });
  await host.handle('open_location', { location: directory });
  assert.deepEqual(opened, [['external', 'https://example.com/a'], ['file', file], ['folder', directory]]);
  await assert.rejects(host.handle('open_location', { location: 'javascript:alert(1)' }), /찾지 못했습니다/);
  await assert.rejects(host.handle('open_location', { location: path.join(directory, 'missing') }), /찾지 못했습니다/);
  assert.equal(opened.length, 3);
  fs.rmSync(directory, { recursive: true });
});

test('reading a journal does not cause repeated updates; another writer causes exactly one', async () => {
  let file = 0; let events = 1; let checks = 0; let notified = 0;
  const detector = createChangeDetector({
    stamp: async () => file,
    checkpoint: async () => { checks += 1; file += 1; return events; }, // reading touches the WAL file
    notify: () => { notified += 1; },
  });
  await detector.check();
  assert.equal(notified, 0);
  for (let index = 0; index < 5; index += 1) await detector.check();
  assert.equal(checks, 1, 'an untouched journal is not read again');
  file += 1; // the screen's own read touched the file: looked at, not reported
  await detector.check();
  assert.equal(notified, 0);
  file += 1; events += 1;
  await detector.check();
  await detector.check();
  assert.equal(notified, 1);
});
