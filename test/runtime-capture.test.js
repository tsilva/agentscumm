import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { GameRuntime } from '../src/runtime.js';
import { startFixture } from './fixture.js';

test('history begins at ready; startup checks and loading/error observations stay unbuffered', { timeout: 30000 }, async t => {
  const fixture = await startFixture();
  fixture.setGameMode('elvira-ii-the-jaws-of-cerberus', 'error');
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-capture-'));
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false, fps: 5 });
  let release, reached;
  const gate = new Promise(resolve => { release = resolve; });
  const checking = new Promise(resolve => { reached = resolve; });
  const hasRenderedContent = runtime.hasRenderedContent.bind(runtime);
  runtime.hasRenderedContent = async (frame, bounds) => {
    reached(frame);
    await gate;
    return hasRenderedContent(frame, bounds);
  };
  t.after(async () => {
    release();
    await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true });
  });

  await runtime.start();
  const startup = await checking;
  assert.equal(runtime.phase, 'loading');
  assert.ok(startup.data.length > 1000, 'Readiness still inspects an actual screenshot');
  const loading = await runtime.observe();
  assert.equal(loading.phase, 'loading');
  assert.ok(loading.data.length > 1000, 'Explicit observations remain available while loading');
  assert.equal(runtime.history.status().frames, 0);
  assert.equal(runtime.history.status().bytes, 0);
  assert.deepEqual(runtime.history.review(), []);
  assert.equal(runtime.history.get(startup.id), undefined);
  assert.equal(runtime.history.get(loading.id), undefined);
  assert.equal((await fetch(new URL(`frame/${loading.id}`, runtime.viewer.url))).status, 404);

  release();
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
  await runtime.bootTask;
  assert.ok(runtime.history.frames.length > 0);
  assert.equal(runtime.history.frames[0].reason, 'launch-ready');
  const initialCount = runtime.history.frames.length;
  for (let i = 0; i < 30 && runtime.history.frames.length <= initialCount; i++) await sleep(100);
  assert.ok(runtime.history.frames.length > initialCount, 'Periodic buffering begins after readiness');
  assert.ok(runtime.history.frames.every(frame => frame.phase === 'ready'));

  await runtime.closeGame();
  await runtime.start({ game: 'elvira-ii-the-jaws-of-cerberus', mode: 'beginning' });
  assert.equal((await runtime.waitReady(10000)).phase, 'error');
  const error = await runtime.observe();
  assert.equal(error.phase, 'error');
  assert.equal(runtime.history.status().frames, 0);
  assert.equal(runtime.history.get(error.id), undefined);
});
