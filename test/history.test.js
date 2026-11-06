import test from 'node:test';
import assert from 'node:assert/strict';
import { FrameHistory } from '../src/history.js';
import { validateSequence, SerialQueue } from '../src/actions.js';

test('history enforces byte/time bounds and retrieves chronological samples', () => {
  const history = new FrameHistory({ seconds: 60, maxBytes: 8, maxFrames: 10 });
  const old = history.add(Buffer.from('1234'), { actionId: 'old' });
  history.add(Buffer.from('5678'), { actionId: 'new' });
  history.add(Buffer.from('abcd'), { actionId: 'new' });
  assert.equal(history.get(old.id), undefined);
  assert.equal(history.bytes, 8);
  assert.equal(history.review({ count: 1 })[0].id, history.latest().id);
  assert.equal(history.review({ actionId: 'new' }).length, 2);
  history.prune(performance.now() + 61000);
  assert.equal(history.frames.length, 0);
  assert.equal(history.bytes, 0);
});

test('input validates the entire sequence before execution', () => {
  assert.throws(() => validateSequence([{ type: 'click', x: 960, y: 0 }], 960, 720), /outside/);
  assert.throws(() => validateSequence([{ type: 'key', key: 'Meta+L' }], 960, 720), /Unsupported/);
  assert.throws(() => validateSequence(Array.from({ length: 3 }, () => ({ type: 'wait', milliseconds: 10000 })), 960, 720), /budget/);
  assert.equal(validateSequence([{ type: 'click', x: 100, y: 100 }], 960, 720)[0].hold_ms, 80);
});

test('queue preserves ordering and recovers after errors', async () => {
  const queue = new SerialQueue(), events = [];
  const a = queue.run(async () => { events.push(1); throw new Error('expected'); });
  const b = queue.run(async () => events.push(2));
  await assert.rejects(a, /expected/); await b;
  assert.deepEqual(events, [1, 2]);
});
