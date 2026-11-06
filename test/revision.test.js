import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { GameRuntime, repositoryRevision } from '../src/runtime.js';

const root = path.resolve(import.meta.dirname, '..');

test('launch results and logs preserve the server startup commit and dirty state', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'agentscumm-revision-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const runtime = new GameRuntime({ dataDir: directory, viewer: false });
  const revision = runtime.sourceRevision;
  const expected = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  assert.equal(revision.commit_id, expected);
  assert.equal(typeof revision.dirty, 'boolean');
  assert.equal(revision.source, 'server_initialization');
  // Exercise launch metadata without starting another browser or touching a live game.
  runtime.boot = async () => {};
  const status = await runtime.start({ game: 'synthetic-game', mode: 'beginning' });
  assert.deepEqual(status.sourceRevision, revision);
  const launch = JSON.parse((await readFile(runtime.logPath, 'utf8')).trim());
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
