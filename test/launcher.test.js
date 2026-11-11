import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
test('public launcher fails closed when ScummBench is missing', () => {
  const result = spawnSync(process.execPath, ['src/index.js'], { cwd: root, env: { PATH: '' }, encoding: 'utf8', timeout: 5000 });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /ScummBench is required/);
  assert.equal(result.stdout, '');
});
test('internal backend rejects a direct unmanaged launch', () => {
  const result = spawnSync(process.execPath, ['src/server.js'], { cwd: root, env: { PATH: '' }, encoding: 'utf8', timeout: 5000 });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /internal AgentSCUMM backend/);
});
