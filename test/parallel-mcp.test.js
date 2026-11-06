import test from 'node:test';
import assert from 'node:assert/strict';
import { rm } from 'node:fs/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { startFixture } from './fixture.js';

test('parallel stdio servers isolate profiles, input, history, viewers and shutdown', { timeout: 60000 }, async t => {
  const fixture = await startFixture();
  const clients = [];
  const directories = new Set();
  t.after(async () => {
    await Promise.all(clients.map(client => client.close()));
    await fixture.close();
    await Promise.all([...directories].map(directory => rm(directory, { recursive: true, force: true })));
  });
  async function connect(extraArgs = []) {
    const client = new Client({ name: 'parallel-test', version: '1.0' });
    clients.push(client);
    await client.connect(new StdioClientTransport({ command: process.execPath,
      args: ['src/index.js', '--no-viewer', '--base-url', fixture.url, ...extraArgs] }));
    return async (name, args = {}) => client.callTool({ name, arguments: args });
  }
  const json = result => JSON.parse(result.content[0].text);
  const [a, b] = await Promise.all([connect(), connect()]);
  const initial = await Promise.all([a('status'), b('status')]);
  const [first, second] = initial.map(json);
  for (const state of [first, second]) {
    assert.match(state.instanceId, /^auto-/);
    assert.ok(state.dataDir.endsWith(`/instances/${state.instanceId}`));
    directories.add(state.dataDir);
  }
  assert.notEqual(first.instanceId, second.instanceId);
  assert.notEqual(first.serverPid, second.serverPid);
  assert.notEqual(first.dataDir, second.dataDir);
  await Promise.all([a('session', { operation: 'start' }), b('session', { operation: 'start', game: 'parallel-game' })]);
  const [readyA, readyB] = (await Promise.all([a('status', { wait_ms: 15000 }), b('status', { wait_ms: 15000 })])).map(json);
  assert.equal(readyA.phase, 'ready', readyA.message);
  assert.equal(readyB.phase, 'ready', readyB.message);
  assert.notEqual(readyA.sessionId, readyB.sessionId);
  assert.notEqual(new URL(readyA.viewerUrl).port, new URL(readyB.viewerUrl).port);
  const sequence = state => ({ session_id: state.sessionId, request_id: 'same-request-id', after_ms: 0,
    actions: [{ type: 'key', key: 'a' }, { type: 'screenshot' }] });
  assert.equal((await b('act', sequence(readyA))).isError, true, 'Cross-instance session IDs cannot deliver input');
  const [actionA, actionB] = await Promise.all([a('act', sequence(readyA)), b('act', sequence(readyB))]);
  for (const action of [actionA, actionB]) assert.equal(action.isError, false);
  assert.equal(json(actionA).replayed, false);
  assert.equal(json(actionB).replayed, false, 'The other agent has its own request cache');
  assert.equal(json(await a('act', sequence(readyA))).replayed, true);
  for (const [call, state] of [[a, readyA], [b, readyB]]) {
    const frames = (await call('observe', { source: 'history', count: 3 })).content.filter(item => item.type === 'text').slice(1).map(item => JSON.parse(item.text));
    assert.ok(frames.length);
    assert.ok(frames.every(frame => frame.sessionId === state.sessionId));
    const viewer = await (await fetch(new URL('status', state.viewerUrl))).json();
    assert.equal(viewer.instanceId, state.instanceId);
    assert.equal(viewer.actionSequences, 1);
  }
  // Reusing a live profile fails without disturbing its current owner.
  const conflicting = await connect(['--instance', first.instanceId]);
  await conflicting('session', { operation: 'start' });
  const conflict = json(await conflicting('status', { wait_ms: 10000 }));
  assert.equal(conflict.phase, 'error');
  assert.match(conflict.message, /profile is already in use/);
  assert.equal(json(await a('status')).phase, 'ready');
  await clients[0].close();
  await conflicting('session', { operation: 'start', restart: true });
  const reclaimed = json(await conflicting('status', { wait_ms: 15000 }));
  assert.equal(reclaimed.phase, 'ready', reclaimed.message);
  assert.equal(reclaimed.dataDir, first.dataDir, 'A named instance reuses its profile after the previous server exits');
  assert.equal(json(await b('status')).phase, 'ready');
  assert.equal((await b('observe')).content[1].type, 'image');
  assert.equal((await b('act', { ...sequence(readyB), request_id: 'after-other-server-exit' })).isError, false);
  await b('session', { operation: 'start', restart: true });
  const restarted = json(await b('status', { wait_ms: 15000 }));
  assert.equal(restarted.phase, 'ready', restarted.message);
  assert.equal(restarted.instanceId, second.instanceId);
  assert.equal(restarted.dataDir, second.dataDir);
  assert.notEqual(restarted.sessionId, readyB.sessionId);
});
