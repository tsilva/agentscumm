import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { GameRuntime } from '../src/runtime.js';
import { startFixture } from './fixture.js';

test('screenshots, spectator and inputs share the fixed game crop', { timeout: 40000 }, async t => {
  const fixture = await startFixture();
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-display-'));
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false, fps: 2 });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  const { sessionId } = await runtime.start();
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
  await runtime.bootTask;
  await runtime.viewer.open({ headless: true });
  const crop = { x: 154, y: 115, width: 651, height: 490 };
  for (const [left, top, width, height] of [[0, 0, 960, 720], [24, 50, 900, 630]]) {
    await runtime.canvas.evaluate((canvas, [left, top, width, height]) => {
      Object.assign(canvas.style, { marginLeft: left + 'px', marginTop: top + 'px',
        width: width + 'px', height: height + 'px' });
    }, [left, top, width, height]);
    const frame = await runtime.observe();
    assert.deepEqual(frame.captureBounds, crop);
    const input = await runtime.act({ session_id: sessionId, request_id: 'click-' + left,
      actions: [{ type: 'click', x: 23, y: 31 }, { type: 'screenshot' }] });
    assert.equal(input.action.state, 'completed');
    assert.deepEqual(input.screenshots[0].captureBounds, crop);
    const event = await runtime.shell.evaluate(() => events.filter(e => e.type === 'mousedown').at(-1));
    assert.equal(event.x, crop.x + 23 - left);
    assert.equal(event.y, crop.y + 31 - top);
    const pixels = await runtime.page.evaluate(async encoded => {
      const image = new Image(); image.src = 'data:image/jpeg;base64,' + encoded; await image.decode();
      return { width: image.naturalWidth, height: image.naturalHeight };
    }, frame.data.toString('base64'));
    assert.deepEqual(pixels, { width: crop.width, height: crop.height });
    await runtime.viewer.page.waitForFunction(() => {
      const screen = document.getElementById('screen');
      return !screen.hidden && screen.width === 651 && screen.height === 490;
    });
  }
  assert.equal((await runtime.closeViewer(sessionId)).viewerOpen, false);
  assert.equal((await runtime.observe()).sessionId, sessionId);
});
