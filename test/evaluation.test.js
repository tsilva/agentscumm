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
import { fixtureMapRevision, startFixture } from './fixture.js';

const condition = { all: [{ variable: 'engine_id', equals: 'queen' },
  { variable: 'room_id', equals: 71 }, { variable: 'room_ready', equals: true }] };
const variableCondition = { all: [{ variable: 'engine_id', equals: 'queen' },
  { variable: 'game_state.93', from: 0, equals: 1 }] };
const state = (room_id, native_time_ms, room_ready = true) => ({ schema_version: 1,
  engine_id: 'queen', room_id, room_name: 'Synthetic room', room_ready, native_time_ms });

test('strict typed conditions, ordered transient arrivals, and latched success', async () => {
  for (const bad of [{ all: [] }, { all: [{ variable: 'unknown', equals: 1 }] },
    { all: [{ variable: 'room_id', equals: '71' }] }, { ...condition, code: 'true' }]) {
    assert.throws(() => parseCondition(bad));
  }
  const evaluation = new RunEvaluation({ condition });
  await evaluation.accept({ current: state(70, 1), events: [] });
  assert.equal(evaluation.result, undefined);
  await evaluation.accept({ current: state(71, 2, false), events: [] });
  assert.equal(evaluation.result, undefined, 'A pending arrival is not success');
  await evaluation.accept({ current: state(72, 4), events: [state(71, 3)] });
  assert.equal(evaluation.result.success, true, 'A short visit between polls is retained');
  assert.equal(evaluation.result.variables.room_id, 71);
  assert.equal(evaluation.signal.signal.aborted, true);
  evaluation.fail(new Error('later'));
  assert.equal(evaluation.result.success, true);
  const invalid = new RunEvaluation({ condition });
  await assert.rejects(invalid.accept({ current: state(72, 4), events: [state(71, 3), {}] }));
  assert.equal(invalid.result, undefined, 'Malformed telemetry cannot partially satisfy the goal');
});

const baseline = (value, native_time_ms) => ({ event: 'game_variables_baseline', native_time_ms,
  banks: { game_state: [...Array(93).fill(0), value] } });
const change = (previous, value, native_time_ms) => ({ event: 'game_variables_change', native_time_ms,
  bank: 'game_state', index: 93, previous, value });

const queenSlug = 'flight-of-the-amazon-queen';
function progressBaseline(native_time_ms, achieved = []) {
  const bank = Array(211).fill(0);
  for (const [index, value] of achieved) bank[index] = value;
  return { event: 'game_variables_baseline', native_time_ms, banks: { game_state: bank } };
}

test('the ScummWEB tracker receives only the telemetry at or before the matched stop', async () => {
  const evaluation = new RunEvaluation({ condition });
  evaluation.beginSession({ telemetry: 'events', revision: fixtureMapRevision });
  const room = state(71, 4);
  const baseline = progressBaseline(2, [[3, 1]]);
  await evaluation.accept({ current: state(110, 5, false), events: [room], gameVariableEvents: [baseline] }, input => {
    assert.deepEqual(input.rooms, [room]);
    assert.deepEqual(input.events, [baseline]);
    return { report: { state: 'tracked', percent: 50 }, nextMilestone: 'Left the room' };
  });
  assert.equal(evaluation.result.variables.room_id, 71);
  assert.equal(evaluation.result.progress.map_revision, fixtureMapRevision);
});

test('game variable transitions ignore restored values and retain the matching snapshot', async () => {
  const evaluation = new RunEvaluation({ condition: variableCondition });
  const records = [];
  evaluation.on('telemetry', event => records.push(event));
  const accept = (...gameVariableEvents) => evaluation.accept({ current: state(70, 10), events: [], gameVariableEvents });
  accept(baseline(0, 1), baseline(1, 2));
  assert.equal(evaluation.result, undefined);
  accept(change(1, 0, 3), change(0, 1, 4), change(1, 0, 5));
  assert.equal(evaluation.result.game_variables.game_state[93], 1);
  assert.deepEqual(evaluation.result.game_variable_match, change(0, 1, 4));
  assert.deepEqual(records.filter(event => event.event === 'game_variables_baseline')
    .map(event => event.banks.game_state[93]), [0, 1]);
  assert.deepEqual(records.filter(event => event.event === 'game_variables_change')
    .map(event => event.value), [0, 1, 0]);

  const equality = new RunEvaluation({ condition: { all: [{ variable: 'game_state.93', equals: 1 }] } });
  equality.accept({ current: state(70, 10), events: [], gameVariableEvents: [baseline(1, 2)] });
  assert.equal(equality.result.game_variable_match.event, 'game_variables_baseline');
});

test('game variable evaluation rejects unavailable slots and inconsistent batches before matching', async () => {
  const payload = { current: state(70, 10), events: [] };
  const evaluation = new RunEvaluation({ condition: variableCondition });
  await assert.rejects(evaluation.accept(payload), /does not provide game variable telemetry/);
  await assert.rejects(evaluation.accept({ ...payload, gameVariableEvents: [baseline(true, 1)] }),
    /Unavailable or incompatible game variable/);
  await assert.rejects(evaluation.accept({ ...payload, gameVariableEvents: [baseline(0, 1), change(0, 1, 2), change(0, 2, 3)] }),
    /Inconsistent game variable change/);
  assert.equal(evaluation.result, undefined);
  assert.throws(() => parseCondition({ all: [{ variable: 'game_state.4096', equals: 1 }] }), /Invalid game variable index/);
  assert.throws(() => parseCondition({ all: [{ variable: 'game_state.93', from: false, equals: 1 }] }),
    /Transition values must have the same type/);
  const roomBank = new RunEvaluation({ condition: { all: [{ variable: 'room_vars.0', equals: 1 }] } });
  assert.equal(roomBank.requiresRoomTelemetry, false);
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

test('a slow telemetry read does not stop progress tracking during normal play', async t => {
  const runtime = new GameRuntime({ viewer: false });
  const evaluation = new RunEvaluation();
  evaluation.beginSession({ telemetry: 'events', revision: fixtureMapRevision });
  runtime.phase = 'ready';
  runtime.sessionEvaluation = evaluation;
  runtime.log = async () => {};
  const readTelemetry = () => ({ current: state(70, 2), events: [], gameVariableEvents: [progressBaseline(1)] });
  const report = percent => ({ report: { state: 'tracked', percent } });
  runtime.shell = { evaluate: async (_fn, input) => input?.provider ? report(0) : readTelemetry() };
  await runtime.pollEvaluation();
  assert.equal(runtime.spectatorProgress.percent, 0);

  const read = Promise.withResolvers();
  runtime.shell.evaluate = (_fn, input) => input?.provider ? report(50) : read.promise;
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const pending = runtime.pollEvaluation();
  t.mock.timers.tick(2000);
  await new Promise(resolve => setImmediate(resolve));
  read.resolve({ current: state(70, 4), events: [], gameVariableEvents: [
    { event: 'game_variables_change', native_time_ms: 3, bank: 'game_state', index: 91, previous: 0, value: 1 },
  ] });
  await pending;
  assert.equal(evaluation.result, undefined);
  assert.equal(runtime.spectatorProgress.percent, 50);
});

test('normal play logs progress and updates the spectator footer', { timeout: 20000 }, async t => {
  const fixture = await startFixture({ evaluation: 'progress' });
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-progress-'));
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  const { sessionId } = await runtime.start();
  await runtime.waitReady(10000);
  await runtime.viewer.open({ headless: true });
  const viewer = runtime.viewer.page;
  const expectFooter = text => viewer.waitForFunction(expected => {
    const progress = document.getElementById('story-progress');
    return !progress.hidden && progress.textContent === expected;
  }, text);
  await expectFooter('0% · Next: Opened the door');
  await runtime.act({ session_id: sessionId, request_id: 'first-milestone',
    actions: [{ type: 'click', x: 50, y: 50 }] });
  await expectFooter('50% · Next: Left the room');
  const rows = (await readFile(path.join(dataDir, 'runs', `${sessionId}.jsonl`), 'utf8')).trim().split('\n').map(JSON.parse);
  const samples = rows.filter(row => row.type === 'progress');
  assert.ok(samples.every(row => row.sessionId === sessionId));
  assert.deepEqual(samples.at(-1).progress, { game: queenSlug, total: 2, state: 'tracked',
    completed: 1, percent: 50, latest_milestone: 'Opened the door',
    reached: ['opened-door'], map_revision: fixtureMapRevision });
  assert.equal(runtime.status().phase, 'ready');
  assert.doesNotMatch(JSON.stringify(runtime.status()), /latest_milestone|nextMilestone|game_state|room_id/);

  const publish = (bank, room) => runtime.shell.evaluate(({ bank, room }) => {
    globalThis.__scummwebEvaluationGameVariableEvents.push({ ...bank, native_time_ms: performance.now() });
    globalThis.__scummwebEvaluationState = { ...room, native_time_ms: performance.now() };
  }, { bank, room });
  await publish(progressBaseline(0, [[3, 1], [101, 1], [123, 2], [41, 2], [67, 2], [105, 1]]), state(110, 0, false));
  await expectFooter('100%');
  await publish(progressBaseline(0), state(70, 0));
  await expectFooter('0% · Next: Opened the door');
  await runtime.shell.evaluate(() => { globalThis.__scummwebEvaluationGameVariableOverflow = true; });
  await runtime.pollEvaluation();
  assert.equal(runtime.status().phase, 'ready');
  assert.equal(runtime.spectatorProgress, undefined);
  const finalLog = await readFile(path.join(dataDir, 'runs', `${sessionId}.jsonl`), 'utf8');
  assert.match(finalLog, /"type":"progress-error"[^\n]+"message":"[^"]*Game variable telemetry queue overflow/);
  await runtime.closeGame();
  await viewer.locator('#story-progress').waitFor({ state: 'hidden' });
});

test('launcher accepts a variable-only provider and keeps receipts and telemetry off stdout', { timeout: 30000 }, async t => {
  const fixture = await startFixture({ evaluation: 'game_variables_only' });
  const directory = await mkdtemp(path.join(tmpdir(), 'agentscumm-launcher-'));
  const resultFile = path.join(directory, 'result.json');
  const child = spawn(process.execPath, ['src/index.js', '--no-viewer', '--base-url', fixture.url,
    '--data-dir', path.join(directory, 'browser'), '--stop-condition', JSON.stringify(variableCondition),
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
  assert.equal(result.success, true);
  assert.deepEqual(result.variables, { engine_id: 'queen' });
  assert.equal(result.progress, undefined);
  assert.equal(result.game_variables.game_state[93], 1);
  assert.equal(result.game_variable_match.previous, 0);
  assert.ok((await readFile(result.screenshot)).length > 1000);
  const telemetry = (await readFile(path.join(directory, 'evaluation.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  assert.ok(telemetry.some(event => event.event === 'variables' && event.game_variables.game_state[93] === 0));
  assert.ok(telemetry.some(event => event.event === 'game_variables_baseline' && event.banks.game_state[93] === 0));
  assert.deepEqual(telemetry.filter(event => event.event === 'game_variables_change').map(event => event.value), [1, 0]);
  assert.equal(telemetry.at(-1).event, 'success');
  assert.ok(messages.every(message => message.jsonrpc === '2.0'));
  assert.doesNotMatch(JSON.stringify(messages), /room_id|room_ready|native_time_ms|game_state|game_variables|"progress"/);
});
