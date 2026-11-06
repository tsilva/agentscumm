import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { readFile, rm } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { startFixture } from './fixture.js';

const root = path.resolve(import.meta.dirname, '..');
const script = path.join(root, '.agents/skills/agentscumm_play-game/scripts/mcp-client.mjs');

function startClient(args) {
  const child = spawn(process.execPath, [script, ...args], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  const iterator = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  const exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal, stderr })));
  async function next() {
    let timer;
    try {
      const line = await Promise.race([iterator.next(), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`Client response timed out: ${stderr}`)), 20000);
      })]);
      assert.equal(line.done, false, stderr);
      return JSON.parse(line.value);
    } finally { clearTimeout(timer); }
  }
  return { child, exited, next, call: async (name, args = {}) => {
    child.stdin.write(JSON.stringify({ name, arguments: args }) + '\n');
    return next();
  } };
}

test('play-game client records MCP images and calls inside a resumable run', { timeout: 40000 }, async t => {
  const fixture = await startFixture();
  const runDir = path.join(root, '.internal/synthetic-game', `client-test-${randomUUID()}`);
  const args = ['--run-dir', runDir, '--no-viewer', '--base-url', fixture.url];
  const client = startClient(args);
  client.child.stdin.write(JSON.stringify({ name: 'session', arguments: { operation: 'start', game: 'synthetic-game', mode: 'beginning' } }) + '\n');
  t.after(async () => {
    if (client.child.exitCode === null) client.child.kill('SIGTERM');
    await client.exited; await fixture.close(); await rm(runDir, { recursive: true, force: true });
  });
  const ready = await client.next();
  assert.equal(ready.ready, true); assert.equal(ready.runDir, runDir);
  assert.ok(ready.tools.includes('session'));
  assert.equal((await client.next()).isError, false, 'Input queued during client startup must be retained');
  const launched = await client.call('status', { wait_ms: 15000 });
  assert.equal(launched.content[0].phase, 'ready');
  const observed = await client.call('observe');
  const image = observed.content.find(item => item.image);
  assert.equal(path.dirname(image.image), path.join(runDir, 'screenshots'));
  const data = await readFile(image.image);
  assert.equal(data.subarray(0, 2).toString('hex'), 'ffd8');
  const acted = await client.call('act', { session_id: launched.content[0].sessionId,
    request_id: 'client-sequence', actions: [{ type: 'click', x: 100, y: 100 }, { type: 'wait', milliseconds: 100 }] });
  assert.equal(acted.isError, false);
  assert.equal(acted.content[0].action.completed, 2);
  const events = (await readFile(path.join(runDir, 'events.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
  assert.equal(events.length, 4);
  assert.equal(events[2].request.name, 'observe');
  assert.ok(events[2].result.content.some(item => item.image === image.image));
  assert.ok(!('data' in events[2].result.content.at(-1)), 'Image bytes belong in files, not JSON traces');
  client.child.stdin.write('quit\n');
  assert.equal((await client.exited).code, 0);

  const duplicate = startClient(args);
  const rejected = await duplicate.exited;
  assert.equal(rejected.code, 1);
  assert.match(rejected.stderr, /use --resume/);
  const resumed = startClient([...args, '--resume']);
  assert.equal((await resumed.next()).ready, true);
  assert.equal((await resumed.call('status')).content[0].phase, 'idle');
  resumed.child.stdin.write('quit\n');
  assert.equal((await resumed.exited).code, 0);
});

test('play-game client forwards an evaluator goal and exits when it succeeds', { timeout: 30000 }, async t => {
  const fixture = await startFixture({ evaluation: true });
  const runDir = path.join(root, '.internal/synthetic-game', `goal-test-${randomUUID()}`);
  const condition = { all: [{ variable: 'engine_id', equals: 'queen' },
    { variable: 'room_id', equals: 71 }, { variable: 'room_ready', equals: true }] };
  const client = startClient(['--run-dir', runDir, '--no-viewer', '--base-url', fixture.url,
    '--stop-condition', JSON.stringify(condition), '--telemetry-ms', '50']);
  t.after(async () => {
    if (client.child.exitCode === null) client.child.kill('SIGTERM');
    await client.exited; await fixture.close(); await rm(runDir, { recursive: true, force: true });
  });
  assert.equal((await client.next()).ready, true);
  await client.call('session', { operation: 'start', game: 'synthetic-game', mode: 'beginning' });
  const launched = await client.call('status', { wait_ms: 10000 });
  assert.equal(launched.content[0].phase, 'ready');
  client.child.stdin.write(JSON.stringify({ name: 'act', arguments: {
    session_id: launched.content[0].sessionId, request_id: 'goal', actions: [
      { type: 'click', x: 20, y: 20 }, { type: 'wait', milliseconds: 10000 }, { type: 'click', x: 30, y: 30 }],
  } }) + '\n');
  let ended;
  do { ended = await client.next(); } while (ended.event !== 'evaluation-ended');
  assert.equal(ended.success, true);
  assert.equal((await client.exited).code, 0);
  const receipt = JSON.parse(await readFile(ended.resultFile, 'utf8'));
  assert.equal(receipt.success, true); assert.equal(receipt.variables.room_id, 71);
});
