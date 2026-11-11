#!/usr/bin/env node
// Public launcher. All gameplay and mechanical MCP traffic goes through
// ScummBench, which connects directly to the ScummWEB backbone.
import { spawn } from 'node:child_process';
const child = spawn('scummbench', ['mcp', '--', ...process.argv.slice(2)], { stdio: 'inherit' });
let spawnFailed = false;
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.once('error', () => {
  spawnFailed = true;
  process.stderr.write('ScummBench is required. Install its checkout with uv tool install --editable /path/to/scummbench. No direct backend fallback is allowed.\n');
  process.exitCode = 1;
});
child.once('close', (code, signal) => { process.exitCode = spawnFailed ? 1 : code ?? (signal === 'SIGINT' ? 130 : 1); });
