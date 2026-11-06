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
  const names = (await client.listTools()).tools.map(tool => tool.name);
  assert.ok(names.includes('launch_game')); assert.ok(names.includes('review'));
  const call = (name, args = {}) => client.callTool({ name, arguments: args });
  const launch = JSON.parse((await call('launch_game')).content[0].text);
  const ready = JSON.parse((await call('wait_for_ready', { timeout_ms: 15000 })).content[0].text);
  assert.equal(ready.phase, 'ready');
  const observed = await call('observe');
  const frame = JSON.parse(observed.content[0].text);
  assert.equal(observed.content[1].type, 'image');
  assert.equal(observed.content[1].mimeType, 'image/jpeg');
  assert.ok(Buffer.from(observed.content[1].data, 'base64').length > 1000);
  const action = await call('act', { session_id: launch.sessionId, request_id: 'mcp-click', frame_id: frame.id,
    actions: [{ type: 'click', x: 100, y: 100 }, { type: 'wait', milliseconds: 400 }] });
  assert.equal(action.isError, false);
  assert.equal(JSON.parse(action.content[0].text).action.completed, 2);
  const review = await call('review', { seconds: 10, count: 3 });
  assert.ok(review.content.some(item => item.type === 'image'));
  assert.equal((await call('get_frame', { frame_id: frame.id })).content[1].type, 'image');
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
  const cancelled = JSON.parse((await call('session_status')).content[0].text).recentActions.at(-1);
  assert.equal(cancelled.state, 'failed'); assert.equal(cancelled.completed, 0);
  assert.equal(JSON.parse((await call('close_game')).content[0].text).phase, 'closed');
});
