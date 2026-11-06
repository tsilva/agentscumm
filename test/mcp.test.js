import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { startFixture } from './fixture.js';

test('stdio MCP returns actual images, sequence outcomes and history', { timeout: 40000 }, async t => {
  const fixture = await startFixture();
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-mcp-'));
  const client = new Client({ name: 'agentscumm-integration-test', version: '1.0' });
  t.after(async () => { await client.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  await client.connect(new StdioClientTransport({ command: process.execPath,
    args: ['src/index.js', '--no-viewer', '--base-url', fixture.url, '--data-dir', dataDir] }));
  const tools = (await client.listTools()).tools;
  const names = tools.map(tool => tool.name);
  assert.deepEqual(names.sort(), ['act', 'list_games', 'observe', 'session', 'status']);
  for (const tool of tools) assert.equal(tool.annotations.readOnlyHint, ['list_games', 'observe', 'status'].includes(tool.name));
  const call = (name, args = {}) => client.callTool({ name, arguments: args });
  assert.equal(JSON.parse((await call('status')).content[0].text).phase, 'idle');
  assert.ok(JSON.parse((await call('list_games')).content[0].text).games.length > 0);
  assert.equal((await call('session', { operation: 'close', restart: true })).isError, true);
  assert.equal(JSON.parse((await call('status')).content[0].text).phase, 'idle');
  const launch = JSON.parse((await call('session', { operation: 'start' })).content[0].text);
  const ready = JSON.parse((await call('status', { wait_ms: 15000 })).content[0].text);
  assert.equal(ready.phase, 'ready');
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
  const review = await call('observe', { source: 'history', seconds: 10, count: 3 });
  assert.ok(review.content.some(item => item.type === 'image'));
  assert.equal((await call('observe', { source: 'frames', frame_ids: [frame.id] })).content[1].type, 'image');
  const capturesRequest = { session_id: launch.sessionId, request_id: 'hover-survey', after_ms: 0,
    actions: [{ type: 'move', x: 100, y: 100 }, { type: 'wait', milliseconds: 100 }, { type: 'screenshot' },
      { type: 'move', x: 200, y: 200 }, { type: 'screenshot' }] };
  const captures = await call('act', capturesRequest);
  const captureResult = JSON.parse(captures.content[0].text);
  assert.equal(captures.isError, false);
  assert.deepEqual(captureResult.screenshots.map(frame => frame.step), [2, 4]);
  assert.equal(captures.content.filter(item => item.type === 'image').length, 3, 'Two explicit captures plus the final frame');
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
