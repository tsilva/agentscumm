#!/usr/bin/env node
import { parseArgs } from 'node:util';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import metadata from '../package.json' with { type: 'json' };
import { GameRuntime } from './runtime.js';
import { actionSchema } from './actions.js';
import { imageContent } from './history.js';
import path from 'node:path';
import { RunEvaluation, evaluationReporter } from './evaluation.js';
import { telemetryQuery } from './telemetry.js';
import { findGameZips } from './library.js';

const { values } = parseArgs({ options: {
  'no-viewer': { type: 'boolean', default: false },
  'base-url': { type: 'string' }, 'data-dir': { type: 'string' }, instance: { type: 'string' },
  'import-games': { type: 'string' }, 'import-only': { type: 'boolean' },
  width: { type: 'string' }, height: { type: 'string' }, fps: { type: 'string' },
  'buffer-seconds': { type: 'string' },
  'run-id': { type: 'string' }, 'legacy-sky-slot': { type: 'string' },
  'stop-condition': { type: 'string' }, 'evaluation-log': { type: 'string' },
  'result-file': { type: 'string' }, 'telemetry-ms': { type: 'string' }, telemetry: { type: 'boolean' },
} });
const evaluationEnabled = ['stop-condition', 'evaluation-log', 'result-file', 'telemetry-ms'].some(key => values[key] !== undefined);
if (values['import-only'] && (values['import-games'] === undefined || evaluationEnabled)) {
  throw new Error('--import-only requires --import-games DIR and cannot be used with evaluation options');
}
if (values['import-games'] === '') throw new Error('--import-games requires a directory');
const startupZips = values['import-games'] === undefined ? undefined : await findGameZips(values['import-games']);
const evaluation = evaluationEnabled ? new RunEvaluation({ condition: values['stop-condition'],
  intervalMs: values['telemetry-ms'] === undefined ? undefined : Number(values['telemetry-ms']) }) : undefined;
const runtime = new GameRuntime({ viewer: !values['no-viewer'], baseUrl: values['base-url'],
  dataDir: values['data-dir'], instance: values.instance, width: values.width ? Number(values.width) : undefined,
  height: values.height ? Number(values.height) : undefined, fps: values.fps ? Number(values.fps) : undefined,
  bufferSeconds: values['buffer-seconds'] ? Number(values['buffer-seconds']) : undefined, evaluation,
  telemetry: values.telemetry, runId: values['run-id'] === undefined ? undefined : z.uuid().parse(values['run-id']),
  legacySkySlot: values['legacy-sky-slot'] === undefined ? undefined : z.coerce.number().int().min(1).max(999).parse(values['legacy-sky-slot']) });
const evaluationLog = path.resolve(values['evaluation-log'] || path.join(runtime.dataDir, 'evaluation.jsonl'));
const resultFile = path.resolve(values['result-file'] || path.join(runtime.dataDir, 'result.json'));
const reporter = evaluation ? await evaluationReporter(evaluationLog, resultFile) : undefined;

const { name, version } = metadata;
const server = new McpServer({ name, version }, {
  instructions: 'Start a ScummWEB game with session(operation="start"), poll status(wait_ms=10000) until phase=ready, then observe and act. '
    + 'Ready screenshots show only the game; dimensions can change with menus. Coordinates are screenshot-relative CSS pixels. Supply session_id and a unique request_id for each action sequence; '
    + 'reuse the same request_id only to retrieve the identical request result after an uncertain response. '
    + 'Input delivery is not proof of puzzle success. Use observe(source="history") for missed events. The spectator is read-only. '
    + 'Each server owns one game. Separate server processes run independently in parallel; status identifies this instance and its data directory.',
});

const text = value => ({ type: 'text', text: JSON.stringify(value) });
const registeredTools = [];
function tool(name, description, inputSchema, callback, readOnly = false) {
  server.registerTool(name, { description, inputSchema,
    annotations: { readOnlyHint: readOnly, destructiveHint: false, openWorldHint: !readOnly } },
  async (args, extra) => {
    try { return await callback(args, extra.signal); }
    catch (error) { return { isError: true, content: [text({ error: error.message, ...error.toolDetails, status: runtime.status() })] }; }
  });
  registeredTools.push(name);
}

tool('list_games', 'List known ScummWEB game slugs. Launch also accepts future catalog slugs.', {},
  async () => ({ content: [text({ baseUrl: runtime.baseUrl.origin, games: await runtime.listGames() })] }), true);

tool('library', 'Import local game ZIPs through ScummWEB’s normal file importer in this instance. Starts an asynchronous job; poll operation=status for the site’s results. Close any active game first. ZIPs remain at their original paths and must stay available for relaunch. Imported entries appear in list_games.', {
  operation: z.enum(['import', 'status']).default('status'),
  paths: z.array(z.string().min(1)).min(1).max(256).optional(),
}, async ({ operation, paths }) => {
  if (operation === 'status' && paths !== undefined) throw new Error('paths requires operation="import"');
  return { content: [text(await runtime.library({ operation, paths }))] };
});

tool('session', 'Manage the owned game session: start returns immediately; close preserves saves and clears history; open_viewer opens the read-only spectator. Starting the active game reuses it unless restart=true. Poll status for readiness. Pass the same run_id with mode=continue to reopen its save scope in the same instance.', {
  operation: z.enum(['start', 'close', 'open_viewer']),
  game: z.string().optional().describe('Start only; defaults to flight-of-the-amazon-queen.'),
  mode: z.enum(['skip_intro', 'beginning', 'continue']).optional().describe('Start only; defaults to skip_intro.'),
  restart: z.boolean().optional().describe('Start only; defaults to false.'),
  run_id: z.uuid().optional().describe('Start only; a new game gets a fresh run UUID by default.'),
}, async ({ operation, ...args }) => {
  if (operation !== 'start' && Object.values(args).some(value => value !== undefined)) {
    throw new Error('game, mode, restart and run_id are valid only for session(operation="start")');
  }
  if (operation === 'start') return { content: [text(await runtime.start(args))] };
  if (operation === 'close') return { content: [text(await runtime.closeGame())] };
  return { content: [text({ viewerUrl: await runtime.viewer.open() })] };
});

tool('status', 'Check renderer/runtime health, startup progress, connection, history coverage, spectator and recent actions. nativeControls lists the available native actions or gives the reason they are unavailable. Optional wait_ms waits up to 25 seconds while loading. Ready means rendered startup content; inspect the screen for menus or intros.', {
  wait_ms: z.number().int().min(0).max(25000).default(0),
}, async ({ wait_ms }) => ({ content: [text(await runtime.waitReady(wait_ms))] }), true);

if (values.telemetry) {
  tool('telemetry', 'Query native room and variable bank metadata for the current session. Choose a bank to read a bounded page of indexed values or sorted keys and values. Availability depends on the engine and game state.', telemetryQuery,
    async args => ({ content: [text(await runtime.telemetry(args))] }), true);
}

tool('observe', 'Read game images without input. fresh (default) captures now, including loading/error screens; frames retrieves 1–8 exact frame_ids; history samples recent frames using seconds/count or action_id. Unavailable frames are errors, never replaced by fresh images. Loading/error captures are unbuffered.', {
  source: z.enum(['fresh', 'frames', 'history']).default('fresh'),
  frame_ids: z.array(z.string()).min(1).max(8).optional(),
  seconds: z.number().min(0.1).max(300).optional(),
  count: z.number().int().min(1).max(8).optional(),
  action_id: z.string().optional(),
}, async ({ source, frame_ids, seconds, count, action_id }) => {
  if (source !== 'frames' && frame_ids !== undefined) throw new Error('frame_ids requires source="frames"');
  if (source !== 'history' && [seconds, count, action_id].some(value => value !== undefined)) {
    throw new Error('seconds, count and action_id require source="history"');
  }
  if (source === 'fresh') return { content: imageContent({ ...await runtime.observe(), source }) };
  if (source === 'frames') {
    if (!frame_ids) throw new Error('source="frames" requires frame_ids');
    const frames = frame_ids.map(id => {
      const frame = runtime.history.get(id);
      if (!frame) throw new Error(`Frame expired or does not exist: ${id}`);
      return frame;
    });
    return { content: frames.flatMap(frame => imageContent({ ...frame, source })) };
  }
  const frames = runtime.history.review({ seconds: seconds ?? 15, count: count ?? 4, actionId: action_id });
  return { content: [text({ source, selectedFrames: frames.length, history: runtime.history.status() }),
    ...frames.flatMap(frame => imageContent({ ...frame, source }))] };
}, true);

tool('act', 'Execute up to 32 ordered input, wait or screenshot steps, including up to 8 screenshots and 20 seconds of input holds/waits. A compatible ScummWEB run can also pause, resume, wait_ticks (advance exact native updates and hold), save(id) and load(id) using run-local integer save slots. Check status.nativeControls. A batch containing an unsupported native step is rejected before input with code NATIVE_CONTROL_UNSUPPORTED. Steps stop on failure. Reuse request_id only with identical arguments to retrieve the cached result without repeating input (last 100 requests).', {
  session_id: z.string().uuid(), request_id: z.string().min(1).max(100),
  frame_id: z.string().optional(), actions: z.array(actionSchema).min(1).max(32),
  after_ms: z.number().int().min(0).max(3000).default(200),
}, async (args, signal) => {
  const { frame, screenshots, ...result } = await runtime.act(args, signal);
  return { isError: result.action.state === 'failed', content: [
    text({ ...result, selectedFrames: screenshots.length + Number(Boolean(frame)),
      screenshots: screenshots.map(frame => ({ step: frame.step, frame_id: frame.id })) }),
    ...screenshots.flatMap(imageContent), ...(frame ? imageContent(frame) : []),
  ] };
});

Object.assign(runtime.sourceRevision, { mcp_version: version, mcp_tools: registeredTools.sort() });

let shuttingDown = false;
async function shutdown(exitCode = 0, terminal) {
  if (shuttingDown) return;
  shuttingDown = true;
  if (terminal) {
    // The goal signal aborts an active wait/key hold. Let its partial action ledger finish first.
    await runtime.queue.tail;
    let frame;
    try { frame = await runtime.capture('evaluation-stop'); } catch { /* Failure receipts can lack an image. */ }
    await reporter.finish({ ...terminal, session_id: runtime.sessionId, game: runtime.game,
      source_revision: runtime.sourceRevision, action_sequences: runtime.actionSequences }, frame);
    process.stderr.write(JSON.stringify({ event: terminal.event, success: terminal.success, result_file: resultFile }) + '\n');
    await new Promise(resolve => setImmediate(resolve));
  } else if (reporter) {
    process.exitCode = 1;
    await reporter.finish({ event: 'failure', success: false, reason: 'launcher_closed_before_condition',
      ...(evaluation.progress && { progress: evaluation.progress }) });
  }
  await runtime.close();
  await server.close();
  await reporter?.close();
  if (terminal) process.exit(exitCode);
}
evaluation?.on('telemetry', event => { void reporter.append(event).catch(error => evaluation.fail(error)); });
evaluation?.once('terminal', result => {
  void shutdown(result.success ? 0 : 1, result).catch(error => {
    process.stderr.write(`Evaluation shutdown failed: ${error.message}\n`);
    process.exit(1);
  });
});
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => {
  void shutdown().then(() => process.exit(evaluation ? 1 : 0)).catch(error => { console.error(error.message); process.exit(1); });
});
if (startupZips) await runtime.library({ operation: 'import', paths: startupZips });
if (values['import-only']) {
  try {
    await runtime.libraryTask;
    const result = await runtime.library();
    console.log(JSON.stringify({ instanceId: runtime.instanceId, dataDir: runtime.dataDir, ...result }));
    if (result.state === 'failed') process.exitCode = 1;
  } finally {
    await shutdown();
  }
} else {
  process.stdin.once('end', () => { void shutdown().catch(error => console.error(error.message)); });
  await server.connect(new StdioServerTransport());
}
