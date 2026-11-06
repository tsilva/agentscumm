import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const root = path.resolve(import.meta.dirname, '..');
const evidence = path.join(root, '.internal/evidence/mcp-live');
await mkdir(evidence, { recursive: true });
const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-live-'));
const client = new Client({ name: 'agentscumm-live-smoke', version: '1.0' });
const call = (name, args = {}) => client.callTool({ name, arguments: args });
const info = result => JSON.parse(result.content.find(item => item.type === 'text').text);
const saveImage = async (result, name) => {
  const image = result.content.find(item => item.type === 'image');
  assert.ok(image, 'Tool must return an MCP image');
  await writeFile(path.join(evidence, name), Buffer.from(image.data, 'base64'));
};
try {
  await client.connect(new StdioClientTransport({ command: process.execPath, cwd: root,
    args: ['src/index.js', '--data-dir', dataDir] }));
  const launched = info(await call('launch_game'));
  let status;
  for (let attempt = 0; attempt < 15; attempt++) {
    status = info(await call('wait_for_ready', { timeout_ms: 10000 }));
    console.error(`Launch: ${status.phase} / ${status.bootPhase} / ${status.loadingText || ''}`);
    await saveImage(await call('observe'), 'startup.jpg');
    if (status.phase === 'error') console.error(JSON.stringify(status));
    if (status.phase !== 'loading') break;
  }
  assert.equal(status.phase, 'ready', status.message);
  assert.equal(status.viewerOpen, true, status.viewerError || 'Viewer must open');
  const observed = await call('observe');
  const frame = info(observed);
  assert.equal(frame.width, 960); assert.equal(frame.height, 720);
  await saveImage(observed, 'initial.jpg');
  const sequence = { session_id: launched.sessionId, request_id: 'live-controls', frame_id: frame.id,
    actions: [{ type: 'move', x: 480, y: 360 }, { type: 'click', x: 480, y: 360 },
      { type: 'wait', milliseconds: 1500 }, { type: 'key', key: 'F5' }, { type: 'wait', milliseconds: 500 }] };
  const acted = await call('act', sequence);
  assert.equal(acted.isError, false, acted.content[0].text);
  await saveImage(acted, 'after-input.jpg');
  const repeated = info(await call('act', sequence));
  assert.equal(repeated.replayed, true);
  const exitMenu = await call('act', { session_id: launched.sessionId, request_id: 'live-dismiss-menu',
    actions: [{ type: 'key', key: 'Escape' }, { type: 'wait', milliseconds: 1200 }] });
  assert.equal(exitMenu.isError, false);
  await saveImage(exitMenu, 'after-menu.jpg');
  const history = await call('review', { seconds: 10, count: 4 });
  assert.equal(history.content.filter(item => item.type === 'image').length, 4);
  const final = info(await call('session_status'));
  const summary = { verifiedAt: new Date().toISOString(), phase: final.phase, game: final.game,
    dimensions: final.dimensions, viewerOpen: final.viewerOpen, history: final.history,
    captureFailures: final.captureFailures, readyReason: final.readyReason,
    actions: final.recentActions, idempotentReplay: repeated.replayed };
  await writeFile(path.join(evidence, 'summary.json'), JSON.stringify(summary, null, 2));
  console.error(JSON.stringify(summary, null, 2));
} finally {
  await client.close();
  await rm(dataDir, { recursive: true, force: true });
}
