import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import packageMetadata from '../package.json' with { type: 'json' };
import { startFixture } from './fixture.js';

test('opt-in telemetry queries unmapped games without leaking state into player tools', { timeout: 30000 }, async t => {
  const fixture = await startFixture({ telemetry: true, startupDelay: 1500 });
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-telemetry-'));
  const client = new Client({ name: 'telemetry-test', version: '1.0' });
  t.after(async () => { await client.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  await client.connect(new StdioClientTransport({ command: process.execPath,
    args: ['src/index.js', '--no-viewer', '--telemetry', '--base-url', fixture.url, '--data-dir', dataDir] }));
  const tools = (await client.listTools()).tools;
  const tool = tools.find(tool => tool.name === 'telemetry');
  assert.equal(tool.annotations.readOnlyHint, true);
  const call = (name, args = {}) => client.callTool({ name, arguments: args });
  const read = async (name, args) => {
    const result = await call(name, args);
    assert.ok(!result.isError, result.content[0].text);
    return JSON.parse(result.content[0].text);
  };
  const { sessionId: session_id } = await read('session', { operation: 'start', game: 'unmapped' });
  assert.equal((await read('telemetry', { session_id })).state, 'pending');
  const status = await read('status', { wait_ms: 10000 });
  assert.equal(status.phase, 'ready');
  assert.equal(status.sourceRevision.mcp_version, packageMetadata.version);
  assert.deepEqual(status.sourceRevision.mcp_tools, tools.map(tool => tool.name).sort());
  assert.doesNotMatch(JSON.stringify(status), /room_id|game_identity|sample_time_ms/);
  const metadata = await read('telemetry', { session_id });
  assert.deepEqual(metadata, { session_id, game: 'unmapped', game_identity: { engine_id: 'fixture', game_id: 'unmapped' },
    state: 'available', room: { id: 17, sample_time_ms: 123 },
    banks: [{ name: 'vars', length: 5 }, { name: 'globals', length: 2, keyed: true }] });
  assert.deepEqual((await read('telemetry', { session_id, bank: 'globals', limit: 1 })).selection,
    { bank: 'globals', available: true, offset: 0, keys: ['score'], values: [1.5], sample_time_ms: 123 });
  const args = { session_id, bank: 'vars', offset: 2, limit: 2 };
  assert.deepEqual((await read('telemetry', args)).selection,
    { bank: 'vars', available: true, offset: 2, values: [-8, null], sample_time_ms: 123 });
  assert.deepEqual((await read('telemetry', { session_id, bank: 'unknown' })).selection, { bank: 'unknown', available: false });
  assert.equal((await call('telemetry', { session_id, offset: 1 })).isError, true);
  const acted = await call('act', { session_id, request_id: 'change-state', actions: [{ type: 'click', x: 50, y: 50 }] });
  assert.equal(acted.isError, false);
  const changed = await read('telemetry', args);
  assert.equal(changed.room.id, 18);
  assert.deepEqual(changed.selection.values, [4, null]);

  const replacement = await read('session', { operation: 'start', game: 'identity-only' });
  assert.equal((await call('telemetry', { session_id })).isError, true);
  assert.equal((await read('status', { wait_ms: 10000 })).phase, 'ready');
  const unavailable = await read('telemetry', { session_id: replacement.sessionId });
  assert.equal(unavailable.state, 'unavailable');
  assert.deepEqual(unavailable.game_identity, metadata.game_identity);
  assert.equal(unavailable.room, null);
  assert.deepEqual(unavailable.banks, []);
});

test('stdio MCP batches native pause, save, resume and load with input and screenshots', { timeout: 30000 }, async t => {
  const fixture = await startFixture({ nativeControls: true });
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-native-actions-'));
  const client = new Client({ name: 'native-actions-test', version: '1.0' });
  t.after(async () => { await client.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  await client.connect(new StdioClientTransport({ command: process.execPath,
    args: ['src/index.js', '--no-viewer', '--base-url', fixture.url, '--data-dir', dataDir] }));
  const call = (name, args = {}) => client.callTool({ name, arguments: args });
  const read = async (name, args) => {
    const result = await call(name, args);
    assert.ok(!result.isError, result.content[0].text);
    return JSON.parse(result.content[0].text);
  };
  const { sessionId: session_id } = await read('session', { operation: 'start' });
  const ready = await read('status', { wait_ms: 10000 });
  assert.equal(ready.phase, 'ready');
  assert.deepEqual(ready.nativeControls, {
    available: true, actions: ['pause', 'resume', 'wait_ticks', 'save', 'load'], tickSource: 'engine',
  });
  const actions = [
    { type: 'pause' }, { type: 'save', id: 3 }, { type: 'resume' },
    { type: 'click', x: 100, y: 100 }, { type: 'pause' }, { type: 'screenshot' },
    { type: 'load', id: 3 }, { type: 'screenshot' }, { type: 'resume' },
  ];
  const args = { session_id, request_id: 'native-checkpoint-and-restore', actions };
  const result = await call('act', args);
  assert.ok(!result.isError, result.content[0].text);
  const { action, screenshots } = JSON.parse(result.content[0].text);
  assert.equal(action.state, 'completed');
  assert.equal(action.completed, actions.length);
  assert.deepEqual(action.steps.map(step => step.type), actions.map(step => step.type));
  assert.deepEqual(action.steps.map(step => step.control), [
    { tick: 0, paused: true }, { tick: 0, paused: true }, { tick: 1, paused: false },
    undefined, { tick: 1, paused: true }, undefined, { tick: 0, paused: true },
    undefined, { tick: 0, paused: false },
  ]);
  assert.deepEqual(screenshots.map(frame => frame.step), [5, 7]);
  assert.equal(result.content.filter(item => item.type === 'image').length, 2);
  assert.equal((await read('act', args)).replayed, true);
  assert.equal((await read('status')).actionSequences, 1);
});

test('stdio MCP steps timing-only engines and rejects unsupported saves before input', { timeout: 30000 }, async t => {
  const fixture = await startFixture({ nativeControls: 'frame', telemetry: true });
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-frame-actions-'));
  const client = new Client({ name: 'frame-actions-test', version: '1.0' });
  t.after(async () => { await client.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  await client.connect(new StdioClientTransport({ command: process.execPath,
    args: ['src/index.js', '--no-viewer', '--telemetry', '--base-url', fixture.url, '--data-dir', dataDir] }));
  const call = (name, args = {}) => client.callTool({ name, arguments: args });
  const read = result => JSON.parse(result.content[0].text);
  const { sessionId: session_id } = read(await call('session', { operation: 'start' }));
  const ready = read(await call('status', { wait_ms: 10000 }));
  assert.equal(ready.phase, 'ready');
  assert.deepEqual(ready.nativeControls, {
    available: true, actions: ['pause', 'resume', 'wait_ticks'], tickSource: 'frame',
  });

  const rejected = await call('act', { session_id, request_id: 'unsupported', actions: [
    { type: 'click', x: 50, y: 50 }, { type: 'save', id: 1 },
  ] });
  assert.equal(rejected.isError, true);
  assert.equal(read(rejected).code, 'NATIVE_CONTROL_UNSUPPORTED');
  assert.equal(read(rejected).control, 'save');
  assert.equal(read(await call('telemetry', { session_id })).room.id, 17);

  const stepped = await call('act', { session_id, request_id: 'step', actions: [
    { type: 'pause' }, { type: 'wait_ticks', ticks: 3 }, { type: 'resume' },
    { type: 'click', x: 50, y: 50 }, { type: 'pause' },
  ] });
  assert.ok(!stepped.isError, stepped.content[0].text);
  assert.deepEqual(read(stepped).action.steps.map(step => step.control), [
    { tick: 0, paused: true }, { tick: 3, paused: true }, { tick: 3, paused: false },
    undefined, { tick: 4, paused: true },
  ]);
});

test('stdio MCP returns actual images, sequence outcomes and history', { timeout: 40000 }, async t => {
  const fixture = await startFixture();
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-mcp-'));
  const client = new Client({ name: 'agentscumm-integration-test', version: '1.0' });
  t.after(async () => { await client.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  await client.connect(new StdioClientTransport({ command: process.execPath,
    args: ['src/index.js', '--no-viewer', '--base-url', fixture.url, '--data-dir', dataDir] }));
  const tools = (await client.listTools()).tools;
  const names = tools.map(tool => tool.name);
  assert.deepEqual(names.sort(), ['act', 'library', 'list_games', 'observe', 'session', 'status']);
  for (const tool of tools) assert.equal(tool.annotations.readOnlyHint, ['list_games', 'observe', 'status'].includes(tool.name));
  const call = (name, args = {}) => client.callTool({ name, arguments: args });
  const idle = JSON.parse((await call('status')).content[0].text);
  assert.equal(idle.phase, 'idle');
  assert.equal(client.getServerVersion().version, packageMetadata.version);
  assert.equal(idle.sourceRevision.mcp_version, packageMetadata.version);
  assert.deepEqual(idle.sourceRevision.mcp_tools, names);
  assert.ok(JSON.parse((await call('list_games')).content[0].text).games.length > 0);
  assert.equal(JSON.parse((await call('library')).content[0].text).state, 'idle');
  assert.equal((await call('library', { operation: 'import' })).isError, true);
  assert.equal((await call('library', { paths: ['/missing.zip'] })).isError, true);
  assert.equal((await call('session', { operation: 'close', restart: true })).isError, true);
  assert.equal(JSON.parse((await call('status')).content[0].text).phase, 'idle');
  const launch = JSON.parse((await call('session', { operation: 'start' })).content[0].text);
  const ready = JSON.parse((await call('status', { wait_ms: 15000 })).content[0].text);
  assert.equal(ready.phase, 'ready');
  assert.deepEqual(ready.nativeControls, { available: false, actions: [], reason: 'runtime_bridge_missing' });
  for (const native of [{ type: 'pause' }, { type: 'resume' }, { type: 'wait_ticks', ticks: 1 },
    { type: 'save', id: 1 }, { type: 'load', id: 1 }]) {
    assert.ok(!ready.capabilities.includes(native.type));
    const rejected = await call('act', { session_id: launch.sessionId, request_id: `unsupported-${native.type}`,
      actions: [{ type: 'click', x: 100, y: 100 }, native] });
    assert.equal(rejected.isError, true);
    const error = JSON.parse(rejected.content[0].text);
    assert.equal(error.code, 'NATIVE_CONTROL_UNSUPPORTED');
    assert.equal(error.control, native.type);
    assert.equal(error.reason, 'runtime_bridge_missing');
    assert.equal(error.status.actionSequences, 0, 'An unsupported step rejects the whole batch before input');
  }
  const observed = await call('observe');
  const frame = JSON.parse(observed.content[0].text);
  assert.equal(observed.content[1].type, 'image');
  assert.equal(observed.content[1].mimeType, 'image/jpeg');
  assert.equal(frame.source, 'fresh');
  assert.ok(Buffer.from(observed.content[1].data, 'base64').length > 1000);
  const action = await call('act', { session_id: launch.sessionId, request_id: 'mcp-click', frame_id: frame.id,
    actions: [{ type: 'click', x: 100, y: 100 }, { type: 'wait', milliseconds: 400 }] });
  assert.equal(action.isError, false);
  assert.equal(JSON.parse(action.content[0].text).action.completed, 2);
  assert.equal(action.content.length, 1, 'Input-only batches return a receipt without an image');
  const review = await call('observe', { source: 'history', seconds: 10, count: 3 });
  assert.ok(review.content.some(item => item.type === 'image'));
  assert.equal((await call('observe', { source: 'frames', frame_ids: [frame.id] })).content[1].type, 'image');
  assert.equal((await call('session', { operation: 'close_viewer' })).isError, true);
  const closedViewer = await call('session', { operation: 'close_viewer', session_id: launch.sessionId });
  assert.ok(!closedViewer.isError, closedViewer.content[0].text);
  const viewerStatus = JSON.parse(closedViewer.content[0].text);
  assert.equal(viewerStatus.viewerOpen, false);
  assert.equal(viewerStatus.sessionId, launch.sessionId);
  assert.equal(viewerStatus.phase, 'ready');
  assert.equal((await call('observe', { source: 'frames', frame_ids: [frame.id] })).content[1].type, 'image');
  const capturesRequest = { session_id: launch.sessionId, request_id: 'hover-survey',
    actions: [{ type: 'move', x: 100, y: 100 }, { type: 'wait', milliseconds: 100 }, { type: 'screenshot' },
      { type: 'move', x: 200, y: 200 }, { type: 'screenshot' }] };
  const captures = await call('act', capturesRequest);
  const captureResult = JSON.parse(captures.content[0].text);
  assert.equal(captures.isError, false);
  assert.deepEqual(captureResult.screenshots.map(frame => frame.step), [2, 4]);
  assert.equal(captures.content.filter(item => item.type === 'image').length, 2);
  const capturesMetadata = [captures.content[1], captures.content[3]].map(item => JSON.parse(item.text));
  assert.ok(capturesMetadata[1].monotonicMs >= capturesMetadata[0].monotonicMs);
  assert.ok(capturesMetadata.every(item => Number.isFinite(Date.parse(item.timestamp))));
  assert.deepEqual(capturesMetadata.map(item => item.id),
    captureResult.action.steps.filter(step => step.type === 'screenshot').map(step => step.frameId));
  assert.deepEqual(captureResult.action.steps.filter(step => step.type === 'screenshot').map(step => step.state), ['captured', 'captured']);
  const replay = await call('act', capturesRequest);
  assert.equal(JSON.parse(replay.content[0].text).replayed, true);
  assert.deepEqual(replay.content.slice(1), captures.content.slice(1), 'Retry returns identical images');
  const frameIds = captureResult.screenshots.map(frame => frame.frame_id).reverse();
  const exact = await call('observe', { source: 'frames', frame_ids: frameIds });
  assert.deepEqual(exact.content.filter(item => item.type === 'text').map(item => JSON.parse(item.text).id), frameIds);
  assert.ok(exact.content.filter(item => item.type === 'text').every(item => JSON.parse(item.text).source === 'frames'));
  assert.equal((await call('observe', { source: 'frames', frame_ids: ['missing-frame'] })).isError, true);
  assert.equal((await call('observe', { source: 'frames' })).isError, true);
  assert.equal((await call('observe', { source: 'fresh', count: 2 })).isError, true);
  assert.equal((await call('observe', { source: 'history', frame_ids: [frame.id] })).isError, true);
  const actionHistory = await call('observe', { source: 'history', action_id: captureResult.action.actionId, count: 8 });
  assert.ok(JSON.parse(actionHistory.content[0].text).selectedFrames >= 2);
  const tooMany = await call('act', { session_id: launch.sessionId, request_id: 'too-many-captures',
    actions: [{ type: 'click', x: 100, y: 100 }, ...Array.from({ length: 9 }, () => ({ type: 'screenshot' }))] });
  assert.equal(tooMany.isError, true);
  const bad = await call('act', { session_id: launch.sessionId, request_id: 'bad-click', actions: [{ type: 'click', x: 99999, y: 0 }] });
  assert.equal(bad.isError, true);
  const controller = new AbortController();
  const pending = client.callTool({ name: 'act', arguments: { session_id: launch.sessionId,
    request_id: 'cancelled-sequence', actions: [{ type: 'wait', milliseconds: 10000 }, { type: 'click', x: 200, y: 200 }] } },
    undefined, { signal: controller.signal });
  await sleep(200);
  controller.abort();
  await assert.rejects(pending);
  // This queues behind the cancelled sequence: it must not wait out the original ten seconds.
  const resumed = await client.callTool({ name: 'observe', arguments: {} }, undefined, { timeout: 2000 });
  assert.equal(resumed.content[1].type, 'image');
  const cancelled = JSON.parse((await call('status')).content[0].text).recentActions.at(-1);
  assert.equal(cancelled.state, 'failed'); assert.equal(cancelled.completed, 0);
  const replacement = JSON.parse((await call('session', { operation: 'start', game: 'abort-on-click-game', mode: 'beginning' })).content[0].text);
  assert.equal(JSON.parse((await call('status', { wait_ms: 10000 })).content[0].text).phase, 'ready');
  const staleClose = await call('session', { operation: 'close_viewer', session_id: launch.sessionId });
  assert.equal(staleClose.isError, true);
  assert.match(JSON.parse(staleClose.content[0].text).error, /Session ID does not match/);
  const failingRequest = { session_id: replacement.sessionId, request_id: 'runtime-abort',
    actions: [{ type: 'screenshot' }, { type: 'click', x: 100, y: 100 }, { type: 'screenshot' }, { type: 'click', x: 200, y: 200 }] };
  const failed = await call('act', failingRequest);
  assert.equal(failed.isError, true);
  assert.equal(JSON.parse(failed.content[0].text).action.completed, 2);
  assert.equal(JSON.parse(failed.content[0].text).screenshots.length, 1, 'Keep the capture made before runtime failure');
  assert.equal(failed.content.filter(item => item.type === 'image').length, 2, 'Partial capture plus error observation');
  const failureStatus = JSON.parse((await call('status')).content[0].text);
  assert.equal(failureStatus.phase, 'error');
  assert.match(failureStatus.runtimeHealth.message, /runtime aborted/);
  assert.equal(JSON.parse((await call('act', failingRequest)).content[0].text).replayed, true);
  assert.equal((await call('observe')).content[1].type, 'image');
  assert.equal(JSON.parse((await call('session', { operation: 'close' })).content[0].text).phase, 'closed');
});
