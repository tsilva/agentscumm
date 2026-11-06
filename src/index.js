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
  'base-url': { type: 'string' }, 'data-dir': { type: 'string' },
  width: { type: 'string' }, height: { type: 'string' }, fps: { type: 'string' },
  'buffer-seconds': { type: 'string' },
} });
const runtime = new GameRuntime({ viewer: !values['no-viewer'], baseUrl: values['base-url'],
  dataDir: values['data-dir'], width: values.width ? Number(values.width) : undefined,
  height: values.height ? Number(values.height) : undefined, fps: values.fps ? Number(values.fps) : undefined,
  bufferSeconds: values['buffer-seconds'] ? Number(values['buffer-seconds']) : undefined });

const server = new McpServer({ name: 'agentscumm', version: '0.1.0' }, {
  instructions: 'Launch a ScummWEB game, poll wait_for_ready until phase=ready, then observe and act. '
    + 'Coordinates are screenshot-relative CSS pixels. Supply session_id and a unique request_id for each action sequence; '
    + 'reuse the same request_id only to retrieve the identical request result after an uncertain response. '
    + 'Input delivery is not proof of puzzle success. Review recent frames for missed events. The spectator is read-only.',
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

tool('launch_game', 'Start an owned Chromium game session and read-only spectator. Returns immediately; poll wait_for_ready. Reusing an active game does not restart it unless restart=true.', {
  game: z.string().default('flight-of-the-amazon-queen'),
  mode: z.enum(['skip_intro', 'beginning', 'continue']).default('skip_intro'),
  restart: z.boolean().default(false),
}, async args => ({ content: [text(await runtime.start(args))] }));

tool('session_status', 'Inspect launch progress, connection, video-buffer coverage, viewer, and recent input results.', {},
  async () => ({ content: [text(runtime.status())] }), true);

tool('wait_for_ready', 'Wait up to 25 seconds for launch progress. phase=ready means a rendered startup frame, not necessarily that an intro/menu is finished. Repeat while loading.', {
  timeout_ms: z.number().int().min(0).max(25000).default(10000),
}, async ({ timeout_ms }) => ({ content: [text(await runtime.waitReady(timeout_ms))] }), true);

tool('observe', 'Capture a fresh game screenshot as an MCP image, with its frame ID, dimensions, phase and session ID. During loading the image shows the full player page.', {},
  async () => ({ content: imageContent(await runtime.observe()) }), true);

tool('act', 'Execute a serialized, bounded sequence of click/move/key/drag/wait inputs, then return a screenshot and per-step delivery status. Stops on failure; never automatically repeats input. Reuse request_id only with identical arguments to retrieve the cached result (last 100 requests in this session).', {
  session_id: z.string().uuid(), request_id: z.string().min(1).max(100),
  frame_id: z.string().optional(), actions: z.array(actionSchema).min(1).max(32),
  after_ms: z.number().int().min(0).max(3000).default(200),
}, async (args, signal) => {
  const { frame, ...result } = await runtime.act(args, signal);
  return { isError: result.action.state === 'failed', content: [text(result), ...(frame ? imageContent(frame) : [])] };
});

tool('review', 'Retrieve evenly spaced recent frames as timestamped MCP images. Optionally select frames associated with one action ID. Missing/evicted history is reported, never reconstructed.', {
  seconds: z.number().min(0.1).max(300).default(15),
  count: z.number().int().min(1).max(8).default(4), action_id: z.string().optional(),
}, async ({ seconds, count, action_id }) => {
  const frames = runtime.history.review({ seconds, count, actionId: action_id });
  return { content: [text({ selectedFrames: frames.length, history: runtime.history.status() }), ...frames.flatMap(imageContent)] };
}, true);

tool('get_frame', 'Retrieve one full-resolution retained frame by its ID.', { frame_id: z.string() }, async ({ frame_id }) => {
  const frame = runtime.history.get(frame_id);
  if (!frame) throw new Error('Frame expired or does not exist; use review or observe');
  return { content: imageContent(frame) };
}, true);

tool('open_viewer', 'Open or reopen the read-only live spectator window. Its mouse, keyboard and size cannot control the game.', {},
  async () => ({ content: [text({ viewerUrl: await runtime.viewer.open() })] }));

tool('close_game', 'Close the current game page without deleting its browser saves or cache. Buffered frames are cleared.', {},
  async () => ({ content: [text(await runtime.closeGame())] }));

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
