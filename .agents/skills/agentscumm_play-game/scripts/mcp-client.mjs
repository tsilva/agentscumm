import { parseArgs } from 'node:util';
import { createInterface } from 'node:readline';
import { execFileSync } from 'node:child_process';
import { appendFile, lstat, mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const root = await realpath(path.resolve(import.meta.dirname, '../../../..'));
const { values } = parseArgs({ options: {
  'run-dir': { type: 'string' }, resume: { type: 'boolean', default: false },
  'no-viewer': { type: 'boolean', default: false }, 'base-url': { type: 'string' },
  'stop-condition': { type: 'string' }, 'evaluation-log': { type: 'string' },
  'result-file': { type: 'string' }, 'telemetry-ms': { type: 'string' },
} });
if (!values['run-dir']) throw new Error('Provide --run-dir inside .internal/<game-id>/<run-id>/');
const runDir = path.resolve(root, values['run-dir']);
const relative = path.relative(root, runDir).split(path.sep).join('/');
if (!/^\.internal\/[a-z0-9]+(?:-[a-z0-9]+)*\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(relative)) {
  throw new Error('Run directory must be exactly .internal/<game-id>/<run-id>/ in the AgentSCUMM repository');
}
const git = args => execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
if (await realpath(git(['rev-parse', '--show-toplevel'])) !== root) throw new Error('Skill is not inside its owning Git repository');
if (!(await readFile(path.join(root, '.gitignore'), 'utf8')).split(/\r?\n/).includes('/.internal/')) throw new Error('Root .gitignore must contain /.internal/');
if (!git(['check-ignore', relative]) || git(['ls-files', '--', '.internal'])) throw new Error('.internal must be Git-ignored and contain no tracked files');
for (const part of ['.internal', relative.split('/').slice(0, 2).join('/'), relative, `${relative}/screenshots`]) {
  const directory = path.join(root, part);
  const existing = await lstat(directory).catch(error => { if (error.code === 'ENOENT') return null; throw error; });
  if (existing?.isSymbolicLink() || (existing && !existing.isDirectory())) throw new Error(`Unsafe run directory: ${part}`);
  if (!existing) await mkdir(directory, { mode: 0o700 });
}
for (const name of ['events.jsonl', 'browser']) {
  const existing = await lstat(path.join(runDir, name)).catch(error => { if (error.code === 'ENOENT') return null; throw error; });
  if (existing?.isSymbolicLink() || (name === 'events.jsonl' && existing && !existing.isFile())) throw new Error(`Unsafe run artifact: ${name}`);
}
const browserDir = path.join(runDir, 'browser');
if (!values.resume && await lstat(browserDir).catch(() => null)) throw new Error('Run already has browser data; use --resume or select a new run ID');

const client = new Client({ name: 'agentscumm_play-game', version: '1.0' });
const serverArgs = [path.join(root, 'src/index.js'), '--data-dir', browserDir];
if (values['no-viewer']) serverArgs.push('--no-viewer');
if (values['base-url']) serverArgs.push('--base-url', values['base-url']);
for (const option of ['stop-condition', 'evaluation-log', 'result-file', 'telemetry-ms']) {
  if (values[option] !== undefined) serverArgs.push(`--${option}`, values[option]);
}
const input = createInterface({ input: process.stdin });
const lines = input[Symbol.asyncIterator]();
let closing;
function close() {
  closing ||= (async () => { input.close(); await client.close(); })();
  return closing;
}
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => {
  void close().then(() => process.exit(0)).catch(error => { console.error(error.message); process.exit(1); });
});
try {
  await client.connect(new StdioClientTransport({ command: process.execPath, args: serverArgs, cwd: root }));
  const transport = client.transport;
  const onclose = transport.onclose;
  transport.onclose = () => { onclose?.(); input.close(); };
  console.log(JSON.stringify({ ready: true, runDir, pid: process.pid,
    tools: (await client.listTools()).tools.map(tool => tool.name) }));
  for await (const line of lines) {
    if (!line.trim()) continue;
    if (line.trim() === 'quit') break;
    let request;
    try {
      request = JSON.parse(line);
      const result = await client.callTool(request);
      const content = [];
      for (const item of result.content) {
        if (item.type === 'image') {
          const extension = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp' }[item.mimeType];
          if (!extension) throw new Error(`Unsupported screenshot format: ${item.mimeType}`);
          const file = path.join(runDir, 'screenshots', `${Date.now()}-${randomUUID()}.${extension}`);
          await writeFile(file, Buffer.from(item.data, 'base64'), { flag: 'wx', mode: 0o600 });
          content.push({ image: file, mimeType: item.mimeType });
        } else if (item.type === 'text') {
          try { content.push(JSON.parse(item.text)); } catch { content.push(item.text); }
        }
      }
      const output = { name: request.name, isError: Boolean(result.isError), content };
      await appendFile(path.join(runDir, 'events.jsonl'), JSON.stringify({ timestamp: new Date().toISOString(), request, result: output }) + '\n', { mode: 0o600 });
      console.log(JSON.stringify(output));
    } catch (error) {
      const output = { name: request?.name, isError: true, error: error.message };
      await appendFile(path.join(runDir, 'events.jsonl'), JSON.stringify({ timestamp: new Date().toISOString(), request, result: output }) + '\n', { mode: 0o600 });
      console.log(JSON.stringify(output));
    }
  }
} finally {
  await close();
  if (values['stop-condition'] !== undefined) {
    const resultFile = path.resolve(values['result-file'] || path.join(browserDir, 'result.json'));
    let success = false;
    try { success = JSON.parse(await readFile(resultFile, 'utf8')).success === true; } catch { /* An absent receipt is never success. */ }
    console.log(JSON.stringify({ event: 'evaluation-ended', success, resultFile }));
    if (!success) process.exitCode = 1;
  }
}
