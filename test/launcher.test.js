import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
test('public launcher fails closed when ScummBench is missing', () => {
  const result = spawnSync(process.execPath, ['src/index.js'], { cwd: root, env: { PATH: '' }, encoding: 'utf8', timeout: 5000 });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /ScummBench is required/);
  assert.equal(result.stdout, '');
});
test('retired backend rejects both unmanaged and formerly managed launches', () => {
  for (const env of [{ PATH: '' }, { PATH: '', SCUMMBENCH_MANAGED: '1' }]) {
    const result = spawnSync(process.execPath, ['src/server.js'], { cwd: root, env, encoding: 'utf8', timeout: 5000 });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /internal AgentSCUMM backend is retired/);
  }
});
test('public launcher consumes ScummBench without passing an AgentSCUMM backend', async t => {
  const directory = await mkdtemp(path.join(tmpdir(), 'agentscumm-launcher-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  await writeFile(path.join(directory, 'scummbench'), `#!${process.execPath}\nprocess.stdout.write(JSON.stringify(process.argv.slice(2)));\n`, { mode: 0o700 });
  const result = spawnSync(process.execPath, ['src/index.js', '--no-viewer', '--instance', 'fixture'],
    { cwd: root, env: { PATH: directory }, encoding: 'utf8', timeout: 5000 });
  assert.equal(result.status, 0);
  assert.deepEqual(JSON.parse(result.stdout), ['mcp', '--', '--no-viewer', '--instance', 'fixture']);
});
