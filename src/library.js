import { randomUUID } from 'node:crypto';
import { stat, readFile, writeFile, rename, rm } from 'node:fs/promises';
import path from 'node:path';

// Import through the site's file input: its normal scanner, duplicate matching,
// library storage and error reporting remain authoritative. No ZIP extraction
// or game detection is duplicated here.
export async function validateZipPaths(paths) {
  if (!Array.isArray(paths) || !paths.length || paths.length > 256) throw new Error('Provide 1–256 ZIP paths');
  const result = [...new Set(paths.map(file => path.resolve(file)))];
  for (const file of result) {
    if (!/\.zip$/i.test(file) || path.basename(file).startsWith('.')) throw new Error(`Not a game ZIP: ${file}`);
    if (!(await stat(file)).isFile()) throw new Error(`Not a regular file: ${file}`);
  }
  return result;
}

export async function readLibrary(dataDir) {
  try {
    const saved = JSON.parse(await readFile(path.join(dataDir, 'local-library.json'), 'utf8'));
    if (saved.schema_version !== 1 || !Array.isArray(saved.paths) || !Array.isArray(saved.games)) {
      throw new Error('Invalid local library manifest');
    }
    return saved;
  } catch (error) {
    if (error.code === 'ENOENT') return { schema_version: 1, paths: [], games: [] };
    throw error;
  }
}

export async function importZipFiles(page, paths, timeout = 600000) {
  const input = page.locator('input[type="file"][multiple][accept*=".zip"]').first();
  await input.waitFor({ state: 'attached', timeout: 30000 });
  // Hydration/storage loading disables Add a game until importRoots is ready.
  await page.getByRole('button', { name: /^Add a game/ }).waitFor({ state: 'visible', timeout: 30000 });
  await page.waitForFunction(() => {
    const button = document.querySelector('.library-add-card');
    return button && !button.disabled;
  }, null, { timeout: 30000 });
  await input.setInputFiles(paths, { timeout: 30000 });
  const confirm = page.getByRole('button', { name: 'Confirm', exact: true });
  await confirm.waitFor({ state: 'visible', timeout });
  const summary = await page.locator('#game-import-summary').innerText();
  const items = await page.locator('.game-import-items li').evaluateAll(rows => rows.map(row => ({
    name: row.querySelector('strong')?.textContent,
    message: row.querySelector('p')?.textContent,
  })));
  await confirm.click();
  await page.locator('#game-import-summary').waitFor({ state: 'hidden', timeout: 5000 });
  const games = await page.locator('#library .poster-entry[data-library-source-ids] a').evaluateAll(cards => cards
    .filter(card => JSON.parse(card.closest('[data-library-source-ids]').dataset.librarySourceIds).length)
    .map(card => ({ title: card.querySelector('.poster-title')?.textContent,
      href: card.getAttribute('href'), target: card.getAttribute('data-game-target') || null })));
  const used = new Set();
  for (const game of games) {
    const pathname = new URL(game.href, page.url()).pathname;
    const base = (pathname === '/' ? game.title : pathname.slice(1)).toLowerCase().normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    let slug = base, suffix = 2;
    while (used.has(slug)) slug = `${base}-${suffix++}`;
    used.add(slug);
    Object.assign(game, { slug, skipIntro: false, source: 'local_import' });
  }
  return { summary, items, games };
}

export async function saveLibrary(dataDir, previous, paths, result) {
  const saved = { schema_version: 1, imported_at: new Date().toISOString(),
    paths: [...new Set([...previous.paths, ...paths])], games: result.games,
    last_import: { summary: result.summary, items: result.items } };
  const destination = path.join(dataDir, 'local-library.json');
  const temporary = `${destination}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, JSON.stringify(saved, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
    await rename(temporary, destination);
  } finally {
    await rm(temporary, { force: true });
  }
  return saved;
}
