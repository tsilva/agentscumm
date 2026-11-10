import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { GameRuntime, repositoryRevision } from '../src/runtime.js';
import { startFixture } from './fixture.js';

const root = path.resolve(import.meta.dirname, '..');

test('launch results and logs preserve the server startup commit and dirty state', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'agentscumm-revision-'));
  const fixture = await startFixture();
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir: directory, viewer: false });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(directory, { recursive: true, force: true }); });
  const revision = runtime.sourceRevision;
  const expected = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  assert.equal(revision.commit_id, expected);
  assert.equal(typeof revision.dirty, 'boolean');
  assert.equal(revision.source, 'server_initialization');
  // Exercise launch metadata without starting another browser or touching a live game.
  runtime.ensureContext = async () => { throw new Error('Browser disabled for this test'); };
  const status = await runtime.start({ game: 'flight-of-the-amazon-queen', mode: 'beginning' });
  assert.deepEqual(status.sourceRevision, revision);
  await runtime.bootTask;
  const events = (await readFile(runtime.logPath, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
  const launch = events.find(event => event.type === 'launch');
  assert.deepEqual(launch.sourceRevision, revision);
});

test('a non-Git installation reports unknown provenance without blocking startup', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'agentscumm-no-git-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const revision = repositoryRevision(directory);
  assert.equal(revision.commit_id, null);
  assert.equal(revision.branch, null);
  assert.equal(revision.dirty, null);
});
