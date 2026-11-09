import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { GameRuntime } from '../src/runtime.js';
import { startFixture } from './fixture.js';

async function setup(t, fixtureOptions, runtimeOptions) {
  const fixture = await startFixture(fixtureOptions);
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-health-'));
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false, fps: 2, ...runtimeOptions });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  return runtime;
}

test('detect runtime failures with a live document; block input and preserve recovery', { timeout: 40000 }, async t => {
  const runtime = await setup(t);
  for (const [kind, fail, expected] of [
    ['context-loss', () => {
      document.getElementById('canvas').id = 'previous-canvas';
      const canvas = document.createElement('canvas'); canvas.id = 'canvas'; document.body.append(canvas);
      const gl = canvas.getContext('webgl');
      if (!gl) throw new Error('Real WebGL context required for context-loss regression');
      gl.getExtension('WEBGL_lose_context').loseContext();
    }, /WebGL context lost/],
    ['abort', () => { globalThis.ABORT = true; }, /runtime aborted/],
    ['runtime-trap', () => dispatchEvent(new ErrorEvent('error', { error: new WebAssembly.RuntimeError('unreachable'), message: 'unreachable' })), /runtime trapped/],
    ['status-error', () => { const s = document.getElementById('status'); s.className = 'error'; s.textContent = 'Runtime failed'; }, /Runtime failed/],
  ]) {
    await runtime.start({ restart: true });
    assert.equal((await runtime.waitReady(10000)).phase, 'ready');
    const request = { session_id: runtime.sessionId, request_id: kind, actions: [{ type: 'click', x: 100, y: 100 }] };
    await runtime.shell.evaluate(fail);
    if (kind === 'context-loss') await runtime.shell.waitForFunction(() => globalThis.__agentscummHealth.failure);
    const status = await runtime.inspectStatus();
    assert.equal(status.connected, true, 'The page is still connected, as in the reported freeze');
    assert.equal(status.phase, 'error');
    assert.equal(status.readyReason, null);
    assert.match(status.runtimeHealth.message, expected);
    await assert.rejects(runtime.act(request), /not ready/);
    assert.equal(await runtime.shell.evaluate(() => events.filter(e => e.type === 'mousedown').length), 0);
    assert.equal((await runtime.observe()).phase, 'error', 'Failed sessions remain inspectable');
  }
  await runtime.start({ restart: true });
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
  assert.equal(runtime.status().runtimeHealth.state, 'responsive');
  assert.deepEqual(runtime.status().runtimeHealth.errors, []);
});

test('failure during a sequence retains delivered steps, stops the rest and never replays', { timeout: 20000 }, async t => {
  const runtime = await setup(t);
  await runtime.start();
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
  await runtime.canvas.evaluate(canvas => canvas.addEventListener('mousedown', () => {
    canvas.dispatchEvent(new Event('webglcontextlost'));
  }, { once: true }));
  const request = { session_id: runtime.sessionId, request_id: 'loss-during-input', after_ms: 0,
    actions: [{ type: 'click', x: 100, y: 100 }, { type: 'click', x: 200, y: 200 }] };
  const result = await runtime.act(request);
  assert.equal(result.action.state, 'failed');
  assert.equal(result.action.completed, 1);
  assert.equal(result.action.steps[0].state, 'delivered');
  assert.equal(result.action.steps.length, 1);
  assert.match(result.action.error, /WebGL context lost/);
  assert.equal((await runtime.act(request)).replayed, true);
  assert.equal(await runtime.shell.evaluate(() => events.filter(e => e.type === 'mousedown').length), 1);
});

test('background monitoring detects failure without an action or status poll', { timeout: 15000 }, async t => {
  const runtime = await setup(t);
  await runtime.start();
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
  await runtime.shell.evaluate(() => { globalThis.ABORT = true; });
  for (let i = 0; i < 30 && runtime.phase === 'ready'; i++) await sleep(100);
  assert.equal(runtime.status().phase, 'error');
  assert.match(runtime.status().message, /runtime aborted/);
});

test('static scenes and ordinary script diagnostics are not evidence of a frozen engine', { timeout: 15000 }, async t => {
  const runtime = await setup(t);
  await runtime.start();
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
  await runtime.shell.evaluate(() => {
    dispatchEvent(new ErrorEvent('error', { message: 'Optional resource failed', error: new Error('Optional resource failed') }));
  });
  const state = await runtime.inspectStatus();
  assert.equal(state.phase, 'ready');
  assert.deepEqual(state.runtimeHealth.errors, ['Optional resource failed']);
});

test('startup survives wall-clock jumps after its first rendered sample', { timeout: 15000 }, async t => {
  const runtime = await setup(t);
  const now = Date.now;
  t.after(() => { Date.now = now; });
  const rendered = runtime.hasRenderedContent.bind(runtime);
  runtime.hasRenderedContent = async (...args) => {
    Date.now = () => now() + 3600000;
    return rendered(...args);
  };
  await runtime.start();
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
});

test('navigation and runtime startup share one deadline', { timeout: 15000 }, async t => {
  const runtime = await setup(t, { navigationDelay: 900, startupDelay: 900 }, { launchTimeoutMs: 1600 });
  await runtime.start();
  const result = await runtime.waitReady(10000);
  assert.equal(result.phase, 'error');
  assert.match(result.message, /timed out|Timeout/);
});

test('archive bytes renew the startup deadline while the download progresses', { timeout: 15000 }, async t => {
  const runtime = await setup(t, { archiveUpdates: 18, archiveComplete: true, startupDelay: 4100 },
    { launchTimeoutMs: 2600, archiveLaunchTimeoutMs: 10000 });
  await runtime.start();
  const result = await runtime.waitReady(10000);
  assert.equal(result.phase, 'ready', result.message);
});

test('stalled archive downloads and the overall launch cap still time out', { timeout: 20000 }, async t => {
  const stalled = await setup(t, { archiveUpdates: 2, startupDelay: 10000 },
    { launchTimeoutMs: 2600, archiveLaunchTimeoutMs: 10000 });
  const capped = await setup(t, { archiveUpdates: 50, startupDelay: 10000 },
    { launchTimeoutMs: 2600, archiveLaunchTimeoutMs: 3900 });
  for (const runtime of [stalled, capped]) {
    await runtime.start();
    const result = await runtime.waitReady(7000);
    assert.equal(result.phase, 'error');
    assert.match(result.message, /Game startup timed out/);
  }
});

test('a failed download is reported even if the runtime iframe renders', { timeout: 15000 }, async t => {
  const runtime = await setup(t);
  await runtime.start({ game: 'failed-download' });
  const result = await runtime.waitReady(10000);
  assert.equal(result.phase, 'error');
  assert.equal(result.message, 'Could not download the game ZIP');
});

test('a game iframe URL may appear before its document head is available', { timeout: 15000 }, async t => {
  const runtime = await setup(t, { documentDelay: 900 });
  await runtime.start();
  const result = await runtime.waitReady(10000);
  assert.equal(result.phase, 'ready', result.message);
  assert.equal(await runtime.canvas.evaluate(canvas => getComputedStyle(canvas).outlineStyle), 'none');
});

test('an unresponsive renderer produces a bounded health failure and can be restarted', { timeout: 20000 }, async t => {
  const runtime = await setup(t);
  await runtime.start();
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
  await runtime.bootTask;
  clearTimeout(runtime.captureTimer);
  await runtime.captureTask;
  await runtime.healthTask;
  let startedStall;
  const stalled = new Promise(resolve => { startedStall = resolve; });
  await runtime.page.exposeBinding('__testStallStarted', () => startedStall());
  const stall = runtime.shell.evaluate(() => {
    void globalThis.__testStallStarted();
    const until = performance.now() + 5000;
    while (performance.now() < until) { /* Simulated hung runtime. */ }
  });
  await stalled;
  const started = performance.now();
  const result = await runtime.inspectStatus();
  assert.equal(result.phase, 'error');
  assert.match(result.runtimeHealth.message, /renderer is unresponsive/);
  assert.ok(performance.now() - started < 4500, 'Health check must not await the renderer indefinitely');
  await stall;
  await runtime.start({ restart: true });
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
});
