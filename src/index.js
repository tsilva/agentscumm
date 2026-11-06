#!/usr/bin/env node
import { parseArgs } from 'node:util';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { GameRuntime, games } from './runtime.js';
import { actionSchema } from './actions.js';
import { imageContent } from './history.js';

const { values } = parseArgs({ options: {
  'no-viewer': { type: 'boolean', default: false },
  'base-url': { type: 'string' }, 'data-dir': { type: 'string' }, instance: { type: 'string' },
  width: { type: 'string' }, height: { type: 'string' }, fps: { type: 'string' },
  'buffer-seconds': { type: 'string' },
} });
const runtime = new GameRuntime({ viewer: !values['no-viewer'], baseUrl: values['base-url'],
  dataDir: values['data-dir'], instance: values.instance, width: values.width ? Number(values.width) : undefined,
  height: values.height ? Number(values.height) : undefined, fps: values.fps ? Number(values.fps) : undefined,
  bufferSeconds: values['buffer-seconds'] ? Number(values['buffer-seconds']) : undefined });

const server = new McpServer({ name: 'agentscumm', version: '0.1.0' }, {
  instructions: 'Start a ScummWEB game with session(operation="start"), poll status(wait_ms=10000) until phase=ready, then observe and act. '
    + 'Ready screenshots show only the game; dimensions can change with menus. Coordinates are screenshot-relative CSS pixels. Supply session_id and a unique request_id for each action sequence; '
    + 'reuse the same request_id only to retrieve the identical request result after an uncertain response. '
    + 'Input delivery is not proof of puzzle success. Use observe(source="history") for missed events. The spectator is read-only. '
    + 'Each server owns one game. Separate server processes run independently in parallel; status identifies this instance and its data directory.',
});

const text = value => ({ type: 'text', text: JSON.stringify(value) });
function tool(name, description, inputSchema, callback, readOnly = false) {
  server.registerTool(name, { description, inputSchema,
    annotations: { readOnlyHint: readOnly, destructiveHint: false, openWorldHint: !readOnly } },
  async (args, extra) => {
    try { return await callback(args, extra.signal); }
    catch (error) { return { isError: true, content: [text({ error: error.message, status: runtime.status() })] }; }
  });
}

tool('list_games', 'List known ScummWEB game slugs. Launch also accepts future catalog slugs.', {},
  async () => ({ content: [text({ baseUrl: runtime.baseUrl.origin, games })] }), true);

tool('session', 'Manage the owned game session: start returns immediately; close preserves saves and clears history; open_viewer opens the read-only spectator. Only start accepts game/mode/restart. Starting the active game reuses it unless restart=true. Poll status for readiness.', {
  operation: z.enum(['start', 'close', 'open_viewer']),
  game: z.string().optional().describe('Start only; defaults to flight-of-the-amazon-queen.'),
  mode: z.enum(['skip_intro', 'beginning', 'continue']).optional().describe('Start only; defaults to skip_intro.'),
  restart: z.boolean().optional().describe('Start only; defaults to false.'),
}, async ({ operation, ...args }) => {
  if (operation !== 'start' && Object.values(args).some(value => value !== undefined)) {
    throw new Error('game, mode and restart are valid only for session(operation="start")');
  }
  if (operation === 'start') return { content: [text(await runtime.start(args))] };
  if (operation === 'close') return { content: [text(await runtime.closeGame())] };
  return { content: [text({ viewerUrl: await runtime.viewer.open() })] };
});

tool('status', 'Check renderer/runtime health, startup progress, connection, history coverage, spectator and recent actions. Optional wait_ms waits up to 25 seconds while loading. Ready means rendered startup content; inspect the screen for menus or intros.', {
  wait_ms: z.number().int().min(0).max(25000).default(0),
}, async ({ wait_ms }) => ({ content: [text(await runtime.waitReady(wait_ms))] }), true);

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

tool('act', 'Execute at most 32 ordered click/move/key/drag/wait/screenshot steps, including up to 8 explicit screenshots, with a 20-second hold/wait budget. Returns each requested screenshot with its step index plus a final screenshot and per-step status. Stops on failure and retains partial captures; no conditional execution. Reuse request_id only with identical arguments to retrieve the cached result without repeating input (last 100 requests).', {
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

let shuttingDown = false;
async function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  await runtime.close();
  await server.close();
}
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => {
  void shutdown().then(() => process.exit(0)).catch(error => { console.error(error.message); process.exit(1); });
});
process.stdin.once('end', () => { void shutdown().catch(error => console.error(error.message)); });
await server.connect(new StdioServerTransport());
