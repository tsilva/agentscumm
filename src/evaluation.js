import { EventEmitter } from 'node:events';
import { mkdir, open, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { z } from 'zod';

const fields = { engine_id: z.string().min(1).max(80), room_id: z.number().int().min(0),
  room_name: z.string().max(200), room_ready: z.boolean() };
const predicate = z.object({ variable: z.enum(Object.keys(fields)),
  equals: z.union([z.string(), z.number().finite(), z.boolean()]) }).strict().superRefine((value, ctx) => {
  if (!fields[value.variable].safeParse(value.equals).success) ctx.addIssue({ code: 'custom', message: 'Condition value has the wrong type' });
});
export const conditionSchema = z.object({ all: z.array(predicate).min(1).max(16) }).strict();
const variablesSchema = z.object({ schema_version: z.literal(1), ...fields,
  native_time_ms: z.number().finite().nonnegative() }).strict();

export function parseCondition(json) {
  return conditionSchema.parse(typeof json === 'string' ? JSON.parse(json) : json);
}

// Installed by the server only for an evaluator-enabled instance. No player tool reads this state.
export function enableEvaluation() {
  globalThis.__scummwebEvaluationEnabled = true;
}

export function readEvaluation() {
  if (globalThis.__scummwebEvaluationOverflow) throw new Error('Evaluation transition queue overflow');
  return { current: globalThis.__scummwebEvaluationState ?? null,
    events: (globalThis.__scummwebEvaluationEvents ?? []).splice(0) };
}

export class RunEvaluation extends EventEmitter {
  constructor({ condition, intervalMs = 250 } = {}) {
    super();
    this.condition = condition === undefined ? undefined : parseCondition(condition);
    if (!Number.isInteger(intervalMs) || intervalMs < 50 || intervalMs > 5000) throw new Error('Telemetry interval must be 50–5000 ms');
    this.intervalMs = intervalMs;
    this.signal = new AbortController();
    this.startedAt = performance.now();
    this.lastPublished = -Infinity;
    this.lastNativeTime = -Infinity;
  }

  accept(payload) {
    if (this.result) return;
    // Validate the whole read before accepting any of it. Partial malformed telemetry cannot win.
    const current = payload.current === null ? null : variablesSchema.parse(payload.current);
    const events = z.array(variablesSchema).max(256).parse(payload.events);
    for (const variables of [...events, ...(current ? [current] : [])]) {
      if (variables.native_time_ms < this.lastNativeTime) throw new Error('Evaluation native clock moved backwards');
      this.lastNativeTime = variables.native_time_ms;
      this.current = variables;
      if (this.condition?.all.every(({ variable, equals }) => variables[variable] === equals)) {
        this.result = { event: 'success', success: true, reason: 'stop_condition',
          detected_at: new Date().toISOString(), elapsed_ms: performance.now() - this.startedAt,
          condition: this.condition, variables };
        this.signal.abort(new Error('Stop condition reached'));
        this.emit('terminal', this.result);
        return;
      }
    }
    if (current && performance.now() - this.lastPublished >= this.intervalMs) {
      this.lastPublished = performance.now();
      this.emit('telemetry', { event: 'variables', timestamp: new Date().toISOString(), variables: current });
    }
  }

  fail(error) {
    if (this.result) return;
    this.result = { event: 'failure', success: false, reason: 'evaluation_error', error: error.message };
    this.signal.abort(error);
    this.emit('terminal', this.result);
  }
}

// Exclusive creation prevents accidentally appending a new trial to an old result.
export async function evaluationReporter(logPath, resultPath) {
  if (path.resolve(logPath) === path.resolve(resultPath)) throw new Error('Evaluation log and result paths must differ');
  await mkdir(path.dirname(logPath), { recursive: true, mode: 0o700 });
  await mkdir(path.dirname(resultPath), { recursive: true, mode: 0o700 });
  const result = await open(resultPath, 'wx', 0o600);
  let log;
  try { log = await open(logPath, 'wx', 0o600); }
  catch (error) { await result.close(); throw error; }
  let tail = Promise.resolve();
  const append = event => (tail = tail.then(() => log.writeFile(JSON.stringify(event) + '\n')));
  return { append,
    async finish(event, frame) {
      await append(event);
      const receipt = { ...event };
      if (frame) {
        const imagePath = `${resultPath}.jpg`;
        await writeFile(imagePath, frame.data, { flag: 'wx', mode: 0o600 });
        receipt.screenshot = imagePath;
      }
      await result.writeFile(JSON.stringify(receipt, null, 2) + '\n');
      await result.sync();
    },
    async close() { await tail; await log.close(); await result.close(); },
  };
}
