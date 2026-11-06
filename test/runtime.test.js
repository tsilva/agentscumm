import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { GameRuntime } from '../src/runtime.js';
import { startFixture } from './fixture.js';

test('owned Chromium, reliable inputs, viewer isolation and rolling history', { timeout: 60000 }, async t => {
  const fixture = await startFixture();
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-test-'));
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false, width: 640, height: 480, fps: 5 });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });

  const started = await runtime.start();
  assert.equal(started.phase, 'loading');
  await assert.rejects(runtime.act({ session_id: started.sessionId, request_id: 'early', actions: [{ type: 'click', x: 100, y: 100 }] }), /not ready/);
  assert.equal((await runtime.waitReady(15000)).phase, 'ready');
  const first = await runtime.observe();
  assert.equal(first.width, 640); assert.equal(first.height, 480);
  assert.ok(first.data.length > 1000);
  const request = { session_id: started.sessionId, request_id: 'first', frame_id: first.id,
    actions: [{ type: 'click', x: 200, y: 120 }, { type: 'key', key: 'a' }, { type: 'wait', milliseconds: 100 }], after_ms: 0 };
  const acted = await runtime.act(request);
  assert.equal(acted.action.state, 'completed'); assert.equal(acted.action.completed, 3);
  assert.ok(acted.frame.monotonicMs > first.monotonicMs);
  const countInputs = () => runtime.shell.evaluate(() => events.filter(e => e.type === 'mousedown').length);
  assert.equal(await countInputs(), 1);
  const clicked = await runtime.shell.evaluate(() => events.find(e => e.type === 'mousedown'));
  assert.equal(clicked.x, 200); assert.equal(clicked.y, 120);
  assert.equal((await runtime.act(request)).replayed, true);
  assert.equal(await countInputs(), 1);
  await assert.rejects(runtime.act({ ...request, actions: [{ type: 'click', x: 20, y: 20 }] }), /different inputs/);
  runtime.requests.delete('first');
  await assert.rejects(runtime.act(request), /expired/);
  assert.equal(await countInputs(), 1);

  await assert.rejects(runtime.act({ session_id: started.sessionId, request_id: 'invalid',
    actions: [{ type: 'click', x: 100, y: 100 }, { type: 'click', x: 10000, y: 0 }] }), /outside/);
  assert.equal(await countInputs(), 1);
  // Changing the internal game's canvas geometry must not change screenshot coordinates.
  await runtime.canvas.evaluate(canvas => {
    canvas.style.width = '400px'; canvas.style.height = '300px';
    canvas.style.marginLeft = '90px'; canvas.style.marginTop = '50px';
  });
  const resized = await runtime.observe();
  assert.equal(resized.width, 640); assert.equal(resized.height, 480);
  assert.equal(resized.canvasBounds.x, 90); assert.equal(resized.canvasBounds.y, 50);
  await runtime.act({ session_id: started.sessionId, request_id: 'canvas-offset', frame_id: resized.id,
    actions: [{ type: 'click', x: 200, y: 120 }] });
  const offsetClick = await runtime.shell.evaluate(() => events.filter(e => e.type === 'mousedown').at(-1));
  assert.equal(offsetClick.x, 110); assert.equal(offsetClick.y, 70);
  await sleep(800);
  assert.ok(runtime.history.review({ count: 3 }).length >= 3);

  const viewerUrl = await runtime.viewer.open({ headless: true });
  await runtime.viewer.page.locator('#screen').waitFor({ state: 'visible' });
  await runtime.viewer.page.waitForFunction(() => document.title === 'AgentSCUMM - Flight of the Amazon Queen');
  await runtime.viewer.page.waitForFunction(() => {
    const screen = document.getElementById('screen');
    return screen.width === 400 && screen.height === 300;
  });
  assert.equal(await runtime.page.locator('.game-route-controls').isVisible(), false);
  assert.equal(await runtime.page.locator('.game-route-bottom-actions').isVisible(), false);
  assert.equal(await runtime.viewer.page.locator('header,footer,button').count(), 0);
  const corner = await runtime.viewer.page.locator('#screen').evaluate(canvas =>
    [...canvas.getContext('2d').getImageData(0, 0, 1, 1).data]);
  assert.ok(corner[1] > corner[0] && corner[1] > corner[2], 'Cropped game starts with green game pixels, not black padding or magenta controls');
  const before = await countInputs();
  await runtime.viewer.page.mouse.click(200, 120);
  await runtime.viewer.page.keyboard.press('a');
  await runtime.viewer.page.setViewportSize({ width: 1200, height: 900 });
  assert.equal(await countInputs(), before);
  assert.deepEqual(runtime.page.viewportSize(), { width: 640, height: 480 });
  assert.equal((await fetch(new URL('status', viewerUrl))).status, 200);
  assert.equal((await fetch(new URL('status', viewerUrl), { method: 'POST' })).status, 405);
  assert.equal((await fetch(new URL('/status', viewerUrl))).status, 404);
  assert.equal((await runtime.observe()).width, 640);

  // Failure halfway through: retain completed steps, never issue the rest, never replay on retry.
  const execute = runtime.execute.bind(runtime);
  runtime.execute = async (action, bounds) => { if (action.type === 'key') throw new Error('Synthetic delivery loss'); return execute(action, bounds); };
  const partial = { session_id: started.sessionId, request_id: 'partial',
    actions: [{ type: 'click', x: 20, y: 20 }, { type: 'key', key: 'b' }, { type: 'click', x: 30, y: 30 }] };
  const failed = await runtime.act(partial);
  assert.equal(failed.action.state, 'failed'); assert.equal(failed.action.completed, 1);
  assert.equal(failed.action.steps[1].state, 'delivery-uncertain');
  const afterFailure = await countInputs();
  assert.equal((await runtime.act(partial)).replayed, true);
  assert.equal(await countInputs(), afterFailure);
  runtime.execute = execute;

  // A replaced document cannot receive actions based on an old observation.
  await runtime.shell.goto(`${fixture.url}/scummvm.html`);
  await assert.rejects(runtime.act({ ...request, request_id: 'stale-document' }), /document changed/);
  assert.equal(runtime.status().phase, 'disconnected');
  assert.equal((await runtime.act(partial)).replayed, true);
  await runtime.closeGame();
  assert.equal(runtime.status().history.frames, 0);
  const next = await runtime.start({ restart: true });
  assert.notEqual(next.sessionId, started.sessionId);
  assert.equal((await runtime.waitReady(15000)).phase, 'ready');
  await assert.rejects(runtime.act({ ...request, request_id: 'stale-session' }), /Session ID/);
  await runtime.closeGame();

  await runtime.start({ game: 'broken-game', mode: 'beginning' });
  assert.equal((await runtime.waitReady(10000)).phase, 'error');
  assert.match(runtime.status().message, /Fixture startup error/);
  await runtime.closeGame();
  await runtime.start({ game: 'fallback-game', mode: 'beginning' });
  assert.equal((await runtime.waitReady(10000)).phase, 'error');
  assert.match(runtime.status().message, /not confirmed/);
  await runtime.closeGame();
  await runtime.start();
  assert.equal((await runtime.waitReady(15000)).phase, 'ready');
  const pending = runtime.act({ session_id: runtime.sessionId, request_id: 'shutdown-wait',
    actions: [{ type: 'wait', milliseconds: 10000 }] });
  await sleep(100);
  const shutdownStarted = Date.now();
  await runtime.close();
  assert.equal((await pending).action.state, 'failed');
  assert.ok(Date.now() - shutdownStarted < 3000, 'Shutdown must interrupt pending waits');
  assert.equal(runtime.context, null);
});
