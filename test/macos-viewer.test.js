import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { once } from 'node:events';
import { GameRuntime } from '../src/runtime.js';
import { startFixture } from './fixture.js';

test('macOS spectator has app identity, receives frames, and reopens independently', {
  skip: process.platform !== 'darwin', timeout: 180000,
}, async t => {
  const fixture = await startFixture();
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-native-'));
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, fps: 5 });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  await runtime.start();
  let status;
  for (let attempt = 0; attempt < 15; attempt++) {
    status = await runtime.waitReady(10000);
    if (status.phase !== 'loading') break;
  }
  assert.equal(status.phase, 'ready', status.message);
  assert.equal(status.viewerOpen, true, status.viewerError);
  const sessionId = runtime.sessionId;
  let framesRequested = 0;
  runtime.viewer.server.on('request', request => {
    if (request.url.includes('/frame/')) framesRequested++;
  });

  await runtime.viewer.open();
  const native = runtime.viewer.native;
  assert.deepEqual(native.launchInfo, { event: 'ready', bundleIdentifier: 'eu.tsilva.agentscumm.spectator',
    activationPolicy: 0, iconLoaded: true });
  assert.equal(runtime.status().viewerOpen, true);
  assert.equal(runtime.viewer.page, undefined, 'Native viewer must not start a second Chromium window');
  for (let i = 0; i < 100 && framesRequested < 2; i++) await sleep(100);
  assert.ok(framesRequested >= 2, `WebKit must fetch successive live frames (received ${framesRequested})`);
  assert.deepEqual(runtime.page.viewportSize(), { width: 960, height: 720 });
  // Native content follows geometry changes rather than a fixed per-game ratio.
  for (const [width, height] of [[640, 360], [320, 480], [800, 600]]) {
    await runtime.canvas.evaluate((canvas, size) => {
      canvas.width = size.width; canvas.height = size.height;
      canvas.style.width = `${size.width}px`; canvas.style.height = `${size.height}px`;
    }, { width, height });
    await runtime.observe();
    for (let i = 0; i < 50 && (native.windowGeometry?.gameWidth !== width || native.windowGeometry?.gameHeight !== height); i++) await sleep(100);
    assert.equal(native.windowGeometry?.gameWidth, width, JSON.stringify({ native: native.windowGeometry,
      frame: runtime.history.latest()?.displayBounds, framesRequested, isOpen: native.isOpen }));
    assert.equal(native.windowGeometry?.gameHeight, height);
    assert.ok(Math.abs(native.windowGeometry.contentWidth / native.windowGeometry.contentHeight - width / height) < 0.005,
      'Window content fits the current game aspect ratio');
  }
  const pid = native.child.pid;
  await runtime.viewer.open();
  assert.equal(runtime.viewer.native.child.pid, pid, 'Reuse the open window');

  // EOF simulates a lost MCP owner; the app must clean itself up.
  const exited = once(native.child, 'exit', { signal: AbortSignal.timeout(5000) });
  native.child.stdin.end();
  await exited;
  assert.equal(runtime.status().viewerOpen, false);
  assert.equal(runtime.status().phase, 'ready');
  await runtime.viewer.open();
  assert.equal(runtime.status().viewerOpen, true);
  assert.notEqual(runtime.viewer.native.child.pid, pid);
  assert.equal(runtime.sessionId, sessionId);
  assert.equal((await runtime.observe()).width, 960);
  await runtime.close();
  assert.equal(runtime.status().viewerOpen, false);
});
