import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, readFile, symlink, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { GameRuntime } from '../src/runtime.js';
import { findGameZips, validateZipPaths } from '../src/library.js';
import { startFixture } from './fixture.js';

test('the CLI bootstraps a new profile with game ZIPs from a directory tree', { timeout: 30000 }, async t => {
  const folder = await mkdtemp(path.join(tmpdir(), 'agentscumm-library-cli-'));
  const fixture = await startFixture({ library: true });
  t.after(async () => { await fixture.close(); await rm(folder, { recursive: true, force: true }); });
  const library = path.join(folder, 'Game Collection');
  const empty = path.join(library, 'Empty');
  const nested = path.join(library, 'Nested');
  const profile = path.join(folder, 'instance');
  const zip = path.join(nested, 'Imported Adventure.zip');
  await mkdir(nested, { recursive: true });
  await mkdir(empty);
  await writeFile(zip, 'fixture game');
  await writeFile(path.join(library, '.metadata.zip'), 'ignored');
  await writeFile(path.join(empty, 'readme.txt'), 'ignored');
  await symlink(nested, path.join(library, 'linked'));
  await assert.rejects(findGameZips(empty), /No game ZIPs found/);

  const { stdout } = await promisify(execFile)(process.execPath, [
    'src/index.js', '--base-url', fixture.url, '--data-dir', profile, '--import-games', library, '--import-only',
  ], { timeout: 25000 });
  const result = JSON.parse(stdout);
  assert.equal(result.state, 'completed', result.error);
  assert.equal(result.file_count, 1);
  assert.equal(result.games[0].slug, 'imported-adventure');
  const saved = JSON.parse(await readFile(path.join(profile, 'local-library.json')));
  assert.deepEqual(saved.paths, [zip]);
});

test('local ZIP import, site errors, instance persistence and imported game launch', { timeout: 60000 }, async t => {
  const folder = await mkdtemp(path.join(tmpdir(), 'agentscumm-library-'));
  const fixture = await startFixture({ library: true, libraryContinue: true });
  const options = { baseUrl: fixture.url, dataDir: path.join(folder, 'instance'), viewer: false };
  let runtime = new GameRuntime(options);
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(folder, { recursive: true, force: true }); });
  const zip = path.join(folder, 'Elvira II The Jaws of Cerberus.zip');
  const bad = path.join(folder, 'invalid.zip');
  await writeFile(zip, 'fixture file'); await writeFile(bad, 'fixture invalid');
  await assert.rejects(validateZipPaths([]), /1–256/);
  await assert.rejects(validateZipPaths([folder]), /Not a game ZIP/);
  await assert.rejects(validateZipPaths([path.join(folder, 'missing.zip')]), /ENOENT/);
  await assert.rejects(validateZipPaths([path.join(folder, '._metadata.zip')]), /Not a game ZIP/);
  assert.deepEqual(await validateZipPaths([zip, zip]), [zip]);

  assert.equal((await runtime.library({ operation: 'import', paths: [zip, bad] })).state, 'importing');
  await assert.rejects(runtime.start(), /import to finish/);
  await assert.rejects(runtime.library({ operation: 'import', paths: [zip] }), /already running/);
  await runtime.libraryTask;
  let result = await runtime.library();
  assert.equal(result.state, 'completed', result.error);
  assert.match(result.summary, /1 games added.*1 errors/);
  assert.equal(result.items.find(item => item.name === 'invalid.zip').message, 'Error. Invalid ZIP');
  const entry = (await runtime.listGames()).find(game => game.slug === 'elvira-ii-the-jaws-of-cerberus');
  assert.equal(entry.title, 'Elvira II The Jaws of Cerberus');
  assert.equal(entry.source, 'local_import');
  const saved = JSON.parse(await readFile(path.join(options.dataDir, 'local-library.json')));
  assert.deepEqual(saved.paths, [zip, bad]);
  assert.equal((await readFile(zip, 'utf8')), 'fixture file', 'Source stays unchanged');
  await runtime.library({ operation: 'import', paths: [zip] }); await runtime.libraryTask;
  assert.match((await runtime.library()).summary, /0 games added.*1 games skipped/);
  await runtime.close();

  runtime = new GameRuntime(options);
  assert.ok((await runtime.listGames()).some(game => game.slug === entry.slug));
  const requestStart = fixture.requests.length;
  const started = await runtime.start({ game: entry.slug, mode: 'beginning' });
  const ready = await runtime.waitReady(20000);
  assert.equal(ready.phase, 'ready', ready.message);
  assert.equal(ready.sessionId, started.sessionId);
  assert.equal(new URL(ready.url).pathname, `/${entry.slug}/play`);
  assert.match(new URL(ready.url).searchParams.get('agentSaveScope'), /^[a-f0-9]{64}$/);
  assert.ok(fixture.requests.slice(requestStart).some(url => url.startsWith(`/${entry.slug}/play?`)));
  assert.ok(!fixture.requests.slice(requestStart).includes('/'), 'Launch never detours through the homepage');
  assert.match(runtime.shell.url(), /launch=beginning/);
  await assert.rejects(runtime.library({ operation: 'import', paths: [zip] }), /Close the active game/);
  await runtime.closeGame();
  await runtime.start({ game: entry.slug, mode: 'continue' });
  assert.equal((await runtime.waitReady(20000)).phase, 'ready');
  assert.match(runtime.shell.url(), /launch=continue/);
  assert.equal(runtime.gameUrl, `${fixture.url}/${entry.slug}/play#continue`);
  await runtime.closeGame();
  const secondZip = path.join(folder, 'Flight of the Amazon Queen.zip');
  await writeFile(secondZip, 'second fixture');
  await runtime.library({ operation: 'import', paths: [secondZip] });
  await runtime.libraryTask;
  await runtime.start({ game: 'flight-of-the-amazon-queen' });
  assert.equal((await runtime.waitReady(20000)).phase, 'ready');
  assert.equal(new URL(runtime.gameUrl).pathname, '/flight-of-the-amazon-queen/play');
  assert.notEqual(new URL(runtime.gameUrl).search, new URL(ready.url).search);
});


test('shutdown cancels an import during browser startup', { timeout: 15000 }, async t => {
  const folder = await mkdtemp(path.join(tmpdir(), 'agentscumm-import-close-'));
  const fixture = await startFixture({ library: true });
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir: path.join(folder, 'instance'), viewer: false });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(folder, { recursive: true, force: true }); });
  const zip = path.join(folder, 'Adventure.zip');
  await writeFile(zip, 'fixture');
  await runtime.library({ operation: 'import', paths: [zip] });
  await runtime.close();
  assert.equal((await runtime.library()).state, 'failed');
  assert.equal(runtime.context, null);
});
