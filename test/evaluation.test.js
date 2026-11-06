import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { once } from 'node:events';
import { RunEvaluation, parseCondition } from '../src/evaluation.js';
import { GameRuntime } from '../src/runtime.js';
import { startFixture } from './fixture.js';

const condition = { all: [{ variable: 'engine_id', equals: 'queen' },
  { variable: 'room_id', equals: 71 }, { variable: 'room_ready', equals: true }] };
const state = (room_id, native_time_ms, room_ready = true) => ({ schema_version: 1,
  engine_id: 'queen', room_id, room_name: 'Synthetic room', room_ready, native_time_ms });

test('strict typed conditions, ordered transient arrivals, and latched success', () => {
  for (const bad of [{ all: [] }, { all: [{ variable: 'unknown', equals: 1 }] },
    { all: [{ variable: 'room_id', equals: '71' }] }, { ...condition, code: 'true' }]) {
    assert.throws(() => parseCondition(bad));
  }
  const evaluation = new RunEvaluation({ condition });
  evaluation.accept({ current: state(70, 1), events: [] });
  assert.equal(evaluation.result, undefined);
  evaluation.accept({ current: state(71, 2, false), events: [] });
  assert.equal(evaluation.result, undefined, 'A pending arrival is not success');
  evaluation.accept({ current: state(72, 4), events: [state(71, 3)] });
  assert.equal(evaluation.result.success, true, 'A short visit between polls is retained');
  assert.equal(evaluation.result.variables.room_id, 71);
  assert.equal(evaluation.signal.signal.aborted, true);
  evaluation.fail(new Error('later'));
  assert.equal(evaluation.result.success, true);
  const invalid = new RunEvaluation({ condition });
  assert.throws(() => invalid.accept({ current: state(72, 4), events: [state(71, 3), {}] }));
  assert.equal(invalid.result, undefined, 'Malformed telemetry cannot partially satisfy the goal');
});

test('private evaluator interrupts an active wait and prevents remaining inputs', { timeout: 30000 }, async t => {
  const fixture = await startFixture({ evaluation: true });
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-evaluation-'));
  const evaluation = new RunEvaluation({ condition, intervalMs: 50 });
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false, evaluation });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  const launch = await runtime.start();
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
  assert.equal(evaluation.result, undefined);
  assert.doesNotMatch(JSON.stringify(runtime.status()), /room_id|room_ready|Synthetic room|Laundry/);
  const request = { session_id: launch.sessionId, request_id: 'goal', actions: [
    { type: 'click', x: 50, y: 50 }, { type: 'wait', milliseconds: 10000 }, { type: 'click', x: 90, y: 90 }] };
  const started = performance.now();
  const result = await runtime.act(request);
  assert.ok(performance.now() - started < 5000, 'Do not wait out the submitted ten-second delay');
  assert.equal(result.action.state, 'stopped');
  assert.equal(evaluation.result.success, true);
  assert.equal(await runtime.shell.evaluate(() => events.filter(e => e.type === 'mousedown').length), 1);
  assert.equal((await runtime.act(request)).replayed, true);
  await assert.rejects(runtime.act({ ...request, request_id: 'after-goal' }), /Stop condition reached/);
  assert.doesNotMatch(JSON.stringify(result.action), /room_id|room_ready/);
});

test('missing engine telemetry fails explicitly without a visual fallback', { timeout: 20000 }, async t => {
  const fixture = await startFixture();
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-unsupported-'));
  const evaluation = new RunEvaluation({ condition, intervalMs: 50 });
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false, evaluation });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  const terminal = once(evaluation, 'terminal');
  await runtime.start(); await runtime.waitReady(10000);
  const [result] = await terminal;
  assert.equal(result.success, false);
  assert.match(result.error, /does not provide evaluation variables/);
});

test('launcher writes telemetry and a success receipt, exits zero, and keeps stdout MCP-only', { timeout: 30000 }, async t => {
  const fixture = await startFixture({ evaluation: true });
  const directory = await mkdtemp(path.join(tmpdir(), 'agentscumm-launcher-'));
  const resultFile = path.join(directory, 'result.json');
  const child = spawn(process.execPath, ['src/index.js', '--no-viewer', '--base-url', fixture.url,
    '--data-dir', path.join(directory, 'browser'), '--stop-condition', JSON.stringify(condition),
    '--evaluation-log', path.join(directory, 'evaluation.jsonl'), '--result-file', resultFile, '--telemetry-ms', '50'],
  { cwd: new URL('..', import.meta.url), stdio: ['pipe', 'pipe', 'pipe'] });
  const exit = once(child, 'exit');
  t.after(async () => { if (child.exitCode === null) child.kill(); await exit; await fixture.close(); await rm(directory, { recursive: true, force: true }); });
  let nextId = 0;
  const pending = new Map(), messages = [];
  createInterface({ input: child.stdout }).on('line', line => {
    const message = JSON.parse(line); messages.push(message);
    if (pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
  });
  child.stderr.resume();
  const call = (method, params) => new Promise(resolve => {
    const id = ++nextId; pending.set(id, resolve);
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
  await call('initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'test', version: '1' } });
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  const launch = await call('tools/call', { name: 'session', arguments: { operation: 'start' } });
  const session_id = JSON.parse(launch.result.content[0].text).sessionId;
  const ready = await call('tools/call', { name: 'status', arguments: { wait_ms: 10000 } });
  assert.equal(JSON.parse(ready.result.content[0].text).phase, 'ready');
  // Do not depend on the last MCP response surviving transport shutdown; the receipt and exit code are the contract.
  void call('tools/call', { name: 'act', arguments: { session_id, request_id: 'finish', actions: [
    { type: 'click', x: 50, y: 50 }, { type: 'wait', milliseconds: 10000 }, { type: 'click', x: 90, y: 90 }] } });
  assert.deepEqual(await exit, [0, null]);
  const result = JSON.parse(await readFile(resultFile, 'utf8'));
  assert.equal(result.success, true); assert.equal(result.variables.room_id, 71);
  assert.ok((await readFile(result.screenshot)).length > 1000);
  const telemetry = (await readFile(path.join(directory, 'evaluation.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  assert.ok(telemetry.some(event => event.event === 'variables' && event.variables.room_id === 70));
  assert.equal(telemetry.at(-1).event, 'success');
  assert.ok(messages.every(message => message.jsonrpc === '2.0'));
  assert.doesNotMatch(JSON.stringify(messages), /room_id|room_ready|native_time_ms/);
});
