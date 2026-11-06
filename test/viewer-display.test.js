import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { GameRuntime } from '../src/runtime.js';
import { startFixture } from './fixture.js';

test('screenshots and spectator share game-only geometry across resolutions, black scenes and menus', { timeout: 40000 }, async t => {
  const fixture = await startFixture();
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-display-'));
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false, fps: 2 });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  await runtime.start();
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
  await runtime.bootTask;
  clearTimeout(runtime.captureTimer);
  await runtime.captureTask;
  await runtime.viewer.open({ headless: true });
  await runtime.shell.evaluate(() => {
    document.getElementById('canvas').remove();
    const canvas = document.createElement('canvas'); canvas.id = 'canvas'; document.body.append(canvas);
    globalThis.cropEvents = [];
    for (const type of ['mousedown', 'mouseup', 'mousemove']) canvas.addEventListener(type, event =>
      cropEvents.push({ type, x: event.offsetX, y: event.offsetY }));
    const gl = canvas.getContext('webgl', { preserveDrawingBuffer: true });
    const shader = (type, source) => { const s = gl.createShader(type); gl.shaderSource(s, source); gl.compileShader(s); return s; };
    const program = gl.createProgram();
    gl.attachShader(program, shader(gl.VERTEX_SHADER, 'attribute vec2 position; void main(){gl_Position=vec4(position,0.,1.);}'));
    gl.attachShader(program, shader(gl.FRAGMENT_SHADER, 'precision mediump float; uniform vec4 color; void main(){gl_FragColor=color;}'));
    gl.linkProgram(program); gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,1,1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, 'position');
    gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    globalThis.paintGame = ({ width, height, x, y, gameWidth, gameHeight, black = false, overlay = false, offscreen = false }) => {
      canvas.width = width; canvas.height = height;
      canvas.style.cssText = `width:${width}px;height:${height}px;margin:20px 0 0 10px`;
      gl.viewport(0, 0, width, height); gl.disable(gl.SCISSOR_TEST);
      gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
      gl.enable(gl.SCISSOR_TEST); gl.scissor(x, height - y - gameHeight, gameWidth, gameHeight);
      gl.uniform4f(gl.getUniformLocation(program, 'color'), 0, black ? 0 : 0.8, 0, 1);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      gl.disable(gl.SCISSOR_TEST);
      if (overlay) gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      if (offscreen) {
        const texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 32, 32, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        const framebuffer = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
        if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error('Offscreen test framebuffer incomplete');
        gl.viewport(0, 0, 32, 32); gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, width, height);
        gl.deleteFramebuffer(framebuffer); gl.deleteTexture(texture);
      }
    };
  });
  for (const scenario of [
    { width: 940, height: 480, x: 150, y: 0, gameWidth: 640, gameHeight: 480 },
    { width: 940, height: 480, x: 150, y: 0, gameWidth: 640, gameHeight: 480, black: true },
    { width: 940, height: 480, x: 150, y: 0, gameWidth: 640, gameHeight: 480, overlay: true },
    { width: 940, height: 480, x: 150, y: 0, gameWidth: 640, gameHeight: 480, offscreen: true },
    { width: 900, height: 600, x: 50, y: 75, gameWidth: 800, gameHeight: 450 },
    { width: 600, height: 600, x: 140, y: 60, gameWidth: 320, gameHeight: 480 },
    { width: 800, height: 600, x: 0, y: 0, gameWidth: 800, gameHeight: 600 },
  ]) {
    await runtime.shell.evaluate(scenario => paintGame(scenario), scenario);
    const frame = await runtime.observe();
    const expected = scenario.overlay ? { x: 10, y: 20, width: scenario.width, height: scenario.height }
      : { x: scenario.x + 10, y: scenario.y + 20, width: scenario.gameWidth, height: scenario.gameHeight };
    assert.deepEqual(frame.captureBounds, expected);
    assert.deepEqual(frame.displayBounds, { x: 0, y: 0, width: expected.width, height: expected.height });
    assert.equal(frame.canvasBounds.width, scenario.width, 'Original canvas geometry stays available');
    assert.equal(frame.width, expected.width); assert.equal(frame.height, expected.height);
    assert.deepEqual(runtime.status().dimensions, { width: frame.width, height: frame.height });
    const pixels = await runtime.page.evaluate(async encoded => {
      const image = new Image(); image.src = 'data:image/jpeg;base64,' + encoded; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
      return { width: image.width, height: image.height, corner: [...ctx.getImageData(2, 2, 1, 1).data] };
    }, frame.data.toString('base64'));
    assert.equal(pixels.width, expected.width); assert.equal(pixels.height, expected.height);
    if (!scenario.black) assert.ok(pixels.corner[1] > 150, 'MCP image starts with game pixels, without gutters or the grab hint');
    else assert.ok(pixels.corner.slice(0, 3).every(value => value < 10), 'Black game scenes retain their full geometry');
    const input = await runtime.act({ session_id: runtime.sessionId, request_id: 'crop-' + frame.id, frame_id: frame.id,
      actions: [{ type: 'move', x: 21, y: 29 }, { type: 'click', x: 23, y: 31 },
        { type: 'drag', from_x: 25, from_y: 33, x: 27, y: 35 }], after_ms: 0 });
    assert.equal(input.action.state, 'completed');
    const events = await runtime.shell.evaluate(() => cropEvents.splice(0));
    const offsetX = expected.x - 10, offsetY = expected.y - 20;
    assert.ok(events.some(event => event.type === 'mousemove' && event.x === offsetX + 21 && event.y === offsetY + 29));
    assert.deepEqual(events.filter(event => event.type === 'mousedown').map(({ x, y }) => ({ x, y })),
      [{ x: offsetX + 23, y: offsetY + 31 }, { x: offsetX + 25, y: offsetY + 33 }]);
    assert.deepEqual(events.filter(event => event.type === 'mouseup').at(-1),
      { type: 'mouseup', x: offsetX + 27, y: offsetY + 35 });
    await runtime.viewer.page.waitForFunction(id => lastId === id, input.frame.id);
    const actual = await runtime.viewer.page.locator('#screen').evaluate(canvas => ({ width: canvas.width, height: canvas.height,
      corner: [...canvas.getContext('2d').getImageData(2, 2, 1, 1).data] }));
    assert.equal(actual.width, expected.width); assert.equal(actual.height, expected.height);
    if (!scenario.black) assert.ok(actual.corner[1] > 150, 'The rendered crop starts with game pixels, not a black gutter');
  }
});
