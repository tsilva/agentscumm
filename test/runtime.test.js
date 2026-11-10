import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { GameRuntime } from '../src/runtime.js';
import { ACTION_TIMEOUT_MS } from '../src/actions.js';
import { startFixture } from './fixture.js';

test('fixed game crops keep capture and click coordinates aligned without a frame ID', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'agentscumm-fixed-crop-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const games = ['flight-of-the-amazon-queen', 'elvira-ii-the-jaws-of-cerberus'];
  for (const [width, height, expected] of [
    [960, 720, { x: 154, y: 115, width: 651, height: 490 }],
    [640, 480, { x: 0, y: 0, width: 640, height: 480 }],
  ]) {
    const runtime = new GameRuntime({ dataDir: path.join(root, String(width)), width, height, viewer: false });
    const inputs = [];
    const clips = [];
    let canvasBounds;
    runtime.boot = async () => {
      runtime.page = {
        isClosed: () => false, viewportSize: () => ({ width, height }), close: async () => {},
        screenshot: async ({ clip }) => { clips.push(clip); return Buffer.from('frame'); },
        mouse: { move: async () => {}, click: async (x, y) => { inputs.push({ x, y }); } },
      };
      runtime.canvas = { boundingBox: async () => canvasBounds, focus: async () => {} };
      runtime.phase = 'ready';
    };
    runtime.checkHealth = async () => {};
    runtime.pollEvaluation = async () => {};
    t.after(() => runtime.close());
    for (const game of games) {
      canvasBounds = { x: 0, y: 0, width, height };
      const session = await runtime.start({ game, mode: 'beginning' });
      await runtime.bootTask;
      assert.deepEqual((await runtime.observe()).captureBounds, expected);
      await assert.rejects(runtime.start({ game: 'beneath-a-steel-sky', restart: true }), /fixed bounding box/);
      assert.equal(runtime.sessionId, session.sessionId, 'An unsupported launch leaves the active game in place');
      // A change in the renderer's bounds must not change the coordinate origin.
      canvasBounds = { x: 2, y: 8, width: width - 2, height: height - 8 };
      const receipt = await runtime.act({ session_id: session.sessionId, request_id: 'fixed-crop',
        actions: [{ type: 'click', x: 0, y: 0 }, { type: 'screenshot' }] });
      assert.equal(receipt.action.state, 'completed');
      assert.deepEqual(receipt.screenshots[0].captureBounds, expected);
      assert.deepEqual(inputs.at(-1), { x: expected.x, y: expected.y });
    }
    assert.deepEqual(clips, Array(4).fill(expected));
  }
});

test('action deadline returns captured frames and skips the remaining inputs', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const runtime = new GameRuntime({ viewer: false });
  const bounds = { x: 0, y: 0, width: 100, height: 100 };
  runtime.sessionId = 'test-session';
  runtime.assertReady = async () => {};
  runtime.fixedCrop = bounds;
  runtime.canvas = { boundingBox: async () => bounds, focus: async () => {} };
  runtime.log = async () => {};
  runtime.capture = async reason => runtime.history.add(Buffer.from('frame'), { reason, step: runtime.currentStep });
  const waitStarted = Promise.withResolvers();
  const executed = [];
  runtime.execute = async (action, _bounds, signal) => {
    executed.push(action.type);
    await new Promise((_, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      waitStarted.resolve();
    });
  };
  const request = { session_id: runtime.sessionId, request_id: 'deadline',
    actions: [{ type: 'screenshot' }, { type: 'wait', ms: 500 }, { type: 'click', x: 25, y: 25 }] };
  const pending = runtime.act(request);
  await waitStarted.promise;
  t.mock.timers.tick(ACTION_TIMEOUT_MS);
  const result = await pending;
  assert.equal(result.action.state, 'failed');
  assert.equal(result.action.code, 'ACTION_TIMEOUT');
  assert.equal(result.action.completed, 1);
  assert.deepEqual(result.action.steps.map(step => step.state), ['captured', 'interrupted', 'skipped']);
  assert.deepEqual(executed, ['wait']);
  assert.equal(result.screenshots.length, 1);
  assert.equal(result.action.steps[0].frameId, result.screenshots[0].id);
  assert.equal(runtime.requestResult(request.session_id, request.request_id), result);
  assert.deepEqual(runtime.requestResult(request.session_id, 'never-submitted'), { state: 'unknown' });
  assert.equal((await runtime.act(request)).replayed, true);
  assert.deepEqual(executed, ['wait']);
});

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
  const request = { session_id: started.sessionId, request_id: 'first',
    actions: [{ type: 'click', x: 200, y: 120 }, { type: 'key', key: 'a' }, { type: 'wait', ms: 100 }] };
  const acted = await runtime.act(request);
  assert.equal(acted.action.state, 'completed'); assert.equal(acted.action.completed, 3);
  assert.deepEqual(acted.screenshots, []);
  const countInputs = () => runtime.shell.evaluate(() => events.filter(e => e.type === 'mousedown').length);
  assert.equal(await countInputs(), 1);
  assert.equal(runtime.status().actionSequences, 1, 'One sequence counts once regardless of its steps');
  const clicked = await runtime.shell.evaluate(() => events.find(e => e.type === 'mousedown'));
  assert.equal(clicked.x, 200); assert.equal(clicked.y, 120);
  assert.equal((await runtime.act(request)).replayed, true);
  assert.equal(runtime.status().actionSequences, 1, 'Cached retries do not count as new action sequences');
  assert.equal(await countInputs(), 1);
  await assert.rejects(runtime.act({ ...request, actions: [{ type: 'click', x: 20, y: 20 }] }), /different inputs/);
  runtime.requests.delete('first');
  await assert.rejects(runtime.act(request), /expired/);
  assert.equal(await countInputs(), 1);

  await assert.rejects(runtime.act({ session_id: started.sessionId, request_id: 'invalid',
    actions: [{ type: 'click', x: 100, y: 100 }, { type: 'click', x: 10000, y: 0 }] }), /outside/);
  assert.equal(await countInputs(), 1);
  // The fixed custom viewport keeps the same origin even when the canvas moves.
  await runtime.canvas.evaluate(canvas => {
    canvas.style.width = '400px'; canvas.style.height = '300px';
    canvas.style.marginLeft = '90px'; canvas.style.marginTop = '50px';
  });
  const resized = await runtime.observe();
  assert.deepEqual(resized.captureBounds, { x: 0, y: 0, width: 640, height: 480 });
  await runtime.act({ session_id: started.sessionId, request_id: 'fixed-origin',
    actions: [{ type: 'click', x: 200, y: 120 }] });
  const offsetClick = await runtime.shell.evaluate(() => events.filter(e => e.type === 'mousedown').at(-1));
  assert.equal(offsetClick.x, 110); assert.equal(offsetClick.y, 70);
  await runtime.canvas.evaluate(canvas => { canvas.style.margin = '0'; });
  await sleep(800);
  assert.ok(runtime.history.review({ count: 3 }).length >= 3);

  const viewerUrl = await runtime.viewer.open({ headless: true });
  await runtime.viewer.page.locator('#screen').waitFor({ state: 'visible' });
  await runtime.viewer.page.waitForFunction(label => document.title === `AgentSCUMM - Flight of the Amazon Queen [${label}]`, runtime.instanceLabel);
  await runtime.viewer.page.waitForFunction(() => {
    const screen = document.getElementById('screen');
    return screen.width === 640 && screen.height === 480;
  });
  assert.equal(await runtime.page.locator('.game-route-controls').isVisible(), false);
  assert.equal(await runtime.page.locator('.game-route-bottom-actions').isVisible(), false);
  assert.equal(await runtime.viewer.page.locator('header').count(), 0);
  assert.equal(await runtime.viewer.page.locator('button').count(), 1);
  await runtime.viewer.page.waitForFunction(count => document.getElementById('actions').textContent === String(count), runtime.status().actionSequences);
  assert.match((await runtime.viewer.page.locator('footer').textContent()).trim().replace(/\s+/g, ' '), /^Actions: \d+ Sound off$/);
  assert.equal(await runtime.viewer.page.locator('#story-progress').isHidden(), true);
  const sound = runtime.viewer.page.getByRole('button', { name: 'Game sound', exact: true });
  assert.equal(await sound.getAttribute('aria-pressed'), 'false');
  const soundBounds = await sound.boundingBox();
  assert.ok(soundBounds.x > 500 && soundBounds.y >= 456, 'Sound toggle sits in the bottom-right footer');
  const audioSample = () => runtime.shell.evaluate(async () => {
    const context = new OfflineAudioContext(1, 128, 44100);
    const source = context.createConstantSource(); source.offset.value = 0.25;
    if (source.connect(context.destination) !== context.destination) throw Error('Audio connect chaining changed');
    source.disconnect(context.destination);
    source.connect(context.destination); source.start();
    return (await context.startRendering()).getChannelData(0)[64];
  });
  assert.equal(await audioSample(), 0, 'Game audio starts muted');
  const sequencesBeforeSound = runtime.status().actionSequences;
  await sound.click();
  await runtime.viewer.page.waitForFunction(() => document.getElementById('sound').textContent === 'Sound on');
  assert.equal(runtime.status().soundEnabled, true);
  assert.equal(await audioSample(), 0.25, 'Enabling sound passes game audio to the output');
  // Existing graphs must also follow the setting, without suspending their clocks.
  await runtime.shell.evaluate(() => {
    globalThis.pendingAudio = new OfflineAudioContext(1, 128, 44100);
    const source = pendingAudio.createConstantSource(); source.offset.value = 0.25;
    source.connect(pendingAudio.destination); source.start();
  });
  await sound.click();
  await runtime.viewer.page.waitForFunction(() => document.getElementById('sound').textContent === 'Sound off');
  assert.equal(await audioSample(), 0, 'Disabling sound mutes newly connected graphs');
  assert.equal(await runtime.shell.evaluate(async () =>
    (await pendingAudio.startRendering()).getChannelData(0)[64]), 0, 'Disabling sound also mutes existing graphs');
  assert.equal(runtime.status().actionSequences, sequencesBeforeSound, 'Sound is not a gameplay action');
  assert.equal((await fetch(new URL('sound/on', viewerUrl), { method: 'POST' })).status, 403);
  assert.equal((await fetch(new URL('sound/on', viewerUrl), { method: 'POST', headers: {
    Origin: 'https://untrusted.example', 'X-AgentSCUMM-Spectator': '1',
  } })).status, 403);
  assert.equal(runtime.status().soundEnabled, false, 'Untrusted requests cannot enable audio');
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
  await runtime.viewer.page.waitForFunction(count => document.getElementById('actions').textContent === String(count), runtime.status().actionSequences);
  assert.equal(failed.action.steps[1].state, 'delivery-uncertain');
  assert.equal(failed.action.steps[2].state, 'skipped');
  const afterFailure = await countInputs();
  assert.equal((await runtime.act(partial)).replayed, true);
  assert.equal(await countInputs(), afterFailure);
  runtime.execute = execute;

  // Explicit captures occur at their exact sequence positions, without extra input.
  const capture = runtime.capture.bind(runtime);
  const captureClicks = [];
  runtime.capture = async reason => {
    if (reason === 'sequence-screenshot') captureClicks.push(await countInputs());
    return capture(reason);
  };
  const survey = { session_id: started.sessionId, request_id: 'capture-order',
    actions: [{ type: 'screenshot' }, { type: 'click', x: 200, y: 120 }, { type: 'screenshot' }] };
  const beforeSurvey = await countInputs();
  const surveyed = await runtime.act(survey);
  assert.deepEqual(captureClicks, [beforeSurvey, beforeSurvey + 1]);
  assert.deepEqual(surveyed.screenshots.map(frame => frame.step), [0, 2]);
  // Cached captures survive rolling-buffer eviction, including byte-identical replay.
  runtime.history.clear();
  const surveyedAgain = await runtime.act(survey);
  assert.equal(surveyedAgain.replayed, true);
  assert.deepEqual(surveyedAgain.screenshots, surveyed.screenshots);
  assert.equal(await countInputs(), beforeSurvey + 1);
  runtime.capture = async reason => {
    if (reason === 'sequence-screenshot') throw new Error('Synthetic capture failure');
    return capture(reason);
  };
  const captureFailure = await runtime.act({ session_id: started.sessionId, request_id: 'capture-failure',
    actions: [{ type: 'screenshot' }, { type: 'click', x: 200, y: 120 }] });
  assert.equal(captureFailure.action.state, 'failed');
  assert.equal(captureFailure.action.steps[0].state, 'capture-failed');
  assert.equal(captureFailure.action.completed, 0);
  assert.equal(await countInputs(), beforeSurvey + 1, 'No following click after failed capture');
  runtime.capture = capture;

  // A replaced document cannot receive actions based on an old observation.
  await runtime.setSoundEnabled(true);
  await runtime.shell.goto(`${fixture.url}/scummvm.html`);
  assert.equal(await audioSample(), 0.25, 'Replacement game documents inherit the current sound setting');
  await assert.rejects(runtime.act({ ...request, request_id: 'stale-document' }), /document changed/);
  assert.equal(runtime.status().phase, 'disconnected');
  assert.equal((await runtime.act(partial)).replayed, true);
  await runtime.closeGame();
  assert.equal(runtime.status().history.frames, 0);
  const next = await runtime.start({ restart: true });
  assert.notEqual(next.sessionId, started.sessionId);
  assert.equal(runtime.status().actionSequences, 0, 'A new game session resets the counter');
  assert.equal(runtime.status().soundEnabled, false, 'A new game session defaults to sound off');
  assert.equal((await runtime.waitReady(15000)).phase, 'ready');
  await assert.rejects(runtime.act({ ...request, request_id: 'stale-session' }), /Session ID/);
  await runtime.closeGame();

  fixture.setGameMode('elvira-ii-the-jaws-of-cerberus', 'error');
  await runtime.start({ game: 'elvira-ii-the-jaws-of-cerberus', mode: 'beginning' });
  assert.equal((await runtime.waitReady(10000)).phase, 'error');
  assert.match(runtime.status().message, /Fixture startup error/);
  await runtime.closeGame();
  fixture.setGameMode('elvira-ii-the-jaws-of-cerberus', 'fallback');
  await runtime.start({ game: 'elvira-ii-the-jaws-of-cerberus', mode: 'beginning' });
  assert.equal((await runtime.waitReady(10000)).phase, 'error');
  assert.match(runtime.status().message, /not confirmed/);
  await runtime.closeGame();
  await runtime.start();
  assert.equal((await runtime.waitReady(15000)).phase, 'ready');
  const pending = runtime.act({ session_id: runtime.sessionId, request_id: 'shutdown-wait',
    actions: [{ type: 'wait', ms: 10000 }] });
  for (let attempt = 0; attempt < 200 && !runtime.actions.some(action =>
    action.requestId === 'shutdown-wait' && action.steps.some(step => step.state === 'issuing')); attempt++) await sleep(10);
  assert.ok(runtime.actions.some(action => action.requestId === 'shutdown-wait' &&
    action.steps.some(step => step.state === 'issuing')), 'Wait for the input wait to start before testing its interruption');
  const shutdownStarted = Date.now();
  await runtime.close();
  assert.equal((await pending).action.state, 'failed');
  assert.ok(Date.now() - shutdownStarted < 3000, 'Shutdown must interrupt pending waits');
  assert.equal(runtime.context, null);
});
