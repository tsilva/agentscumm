import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { GameRuntime } from '../src/runtime.js';
import { claimDataDir } from '../src/instances.js';
import { startFixture } from './fixture.js';

test('default instances are isolated and named instances have stable storage', () => {
  const first = new GameRuntime({ viewer: false });
  const second = new GameRuntime({ viewer: false });
  assert.notEqual(first.instanceId, second.instanceId);
  assert.notEqual(first.dataDir, second.dataDir);
  assert.equal(path.basename(first.dataDir), first.instanceId);
  const named = new GameRuntime({ instance: 'agent-a', viewer: false });
  assert.equal(named.dataDir, new GameRuntime({ instance: 'agent-a', viewer: false }).dataDir);
  assert.notEqual(named.dataDir, new GameRuntime({ instance: 'agent-b', viewer: false }).dataDir);
  assert.equal(named.status().instanceId, 'agent-a');
  assert.equal(named.status().dataDir, named.dataDir);
  for (const instance of ['', '../escape', '/absolute', 'A', 'a/b', 'x'.repeat(65)]) {
    assert.throws(() => new GameRuntime({ instance }), /Instance must/);
  }
  assert.throws(() => new GameRuntime({ instance: 'agent-a', dataDir: '/tmp/unused' }), /either/);
  assert.equal(new GameRuntime({ dataDir: './custom-profile' }).dataDir, path.resolve('custom-profile'));
});

test('explicit profile storage preserves saves across server lifetimes', { timeout: 30000 }, async t => {
  const fixture = await startFixture();
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-persistent-'));
  const runtimes = [];
  t.after(async () => {
    await Promise.all(runtimes.map(runtime => runtime.close()));
    await fixture.close();
    await rm(dataDir, { recursive: true, force: true });
  });
  for (let index = 0; index < 2; index++) {
    const runtime = new GameRuntime({ dataDir, baseUrl: fixture.url, viewer: false, fps: 1 });
    runtimes.push(runtime);
    await runtime.start();
    const status = await runtime.waitReady(10000);
    assert.equal(status.phase, 'ready', status.message);
    if (index === 0) await runtime.shell.evaluate(() => localStorage.setItem('synthetic-save', 'saved-progress'));
    else assert.equal(await runtime.shell.evaluate(() => localStorage.getItem('synthetic-save')), 'saved-progress');
    await runtime.close();
  }
});

test('directory ownership rejects concurrent claimants and recovers a dead process', { timeout: 10000 }, async t => {
  const directory = await mkdtemp(path.join(tmpdir(), 'agentscumm-owner-'));
  let owner;
  t.after(async () => { owner?.kill('SIGKILL'); await rm(directory, { recursive: true, force: true }); });
  const claims = await Promise.allSettled([claimDataDir(directory), claimDataDir(directory)]);
  assert.ok(claims.filter(result => result.status === 'fulfilled').length <= 1, 'At most one simultaneous contender can own the directory');
  for (const result of claims) if (result.status === 'fulfilled') await result.value();
  const moduleUrl = new URL('../src/instances.js', import.meta.url).href;
  owner = spawn(process.execPath, ['--input-type=module', '-e',
    `import { claimDataDir } from ${JSON.stringify(moduleUrl)};
     await claimDataDir(process.argv[1]); console.log('claimed'); setInterval(() => {}, 1000);`, directory],
  { stdio: ['ignore', 'pipe', 'pipe'] });
  const [output] = await once(owner.stdout, 'data');
  assert.equal(output.toString().trim(), 'claimed');
  await assert.rejects(claimDataDir(directory), /already in use/);
  const exited = once(owner, 'exit');
  owner.kill('SIGKILL');
  await exited;
  owner = null;
  const release = await claimDataDir(directory);
  await assert.rejects(claimDataDir(directory), /already in use/);
  await release();
  await (await claimDataDir(directory))();
});
