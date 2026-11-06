import test from 'node:test';
import assert from 'node:assert/strict';
import { GameRuntime } from '../src/runtime.js';
import { validateSequence } from '../src/actions.js';

const drag = { type: 'drag', from_x: 10, from_y: 20, x: 90, y: 80 };

test('both drag holds count toward the combined input budget', () => {
  assert.equal(validateSequence([drag], 100, 100)[0].hold_ms, 150);
  assert.equal(validateSequence(Array.from({ length: 10 }, () => ({ ...drag, hold_ms: 1000 })), 100, 100).length, 10);
  assert.throws(() => validateSequence(Array.from({ length: 11 }, () => ({ ...drag, hold_ms: 1000 })), 100, 100), /budget/);
  assert.throws(() => validateSequence([{ ...drag, hold_ms: -1 }], 100, 100));
});

function delivery() {
  const events = [];
  const record = (type, ...args) => events.push({ type, args, at: performance.now() });
  const runtime = Object.create(GameRuntime.prototype);
  runtime.page = { mouse: {
    move: async (...args) => record('move', ...args),
    down: async (...args) => record('down', ...args),
    up: async (...args) => record('up', ...args),
  } };
  return { runtime, events };
}

test('drag waits while held at both endpoints and translates coordinates', async () => {
  const { runtime, events } = delivery();
  const action = validateSequence([{ ...drag, hold_ms: 120 }], 100, 100)[0];
  await runtime.execute(action, { x: 15, y: 25 }, new AbortController().signal);
  assert.deepEqual(events.map(event => event.type), ['move', 'down', 'move', 'up']);
  assert.deepEqual(events[0].args.slice(0, 2), [25, 45]);
  assert.deepEqual(events[2].args.slice(0, 2), [105, 105]);
  assert.ok(events[2].at - events[1].at >= 110, 'Source hold completes before moving');
  assert.ok(events[3].at - events[2].at >= 110, 'Target hold completes before release');
});

test('cancelling a source hold releases the mouse without moving the object', async () => {
  const { runtime, events } = delivery();
  const controller = new AbortController();
  const action = validateSequence([{ ...drag, hold_ms: 1000 }], 100, 100)[0];
  const pending = runtime.execute(action, { x: 0, y: 0 }, controller.signal);
  setTimeout(() => controller.abort(), 20);
  await assert.rejects(pending, { name: 'AbortError' });
  assert.deepEqual(events.map(event => event.type), ['move', 'down', 'up']);
});
