import { chromium } from 'playwright';
import { randomUUID, createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdir, appendFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { setTimeout as sleep } from 'node:timers/promises';
import { FrameHistory } from './history.js';
import { SerialQueue, validateSequence } from './actions.js';
import { SpectatorViewer } from './viewer.js';
import { installHealthObserver, readShellState, bounded } from './health.js';
import { installDisplayObserver, displayBounds, captureBounds } from './display.js';

export const games = [
  ['beneath-a-steel-sky', 'sky', true],
  ['broken-sword-2-5-the-return-of-the-templars', 'sword25', false],
  ['drascula-the-vampire-strikes-back', 'drascula', true],
  ['dreamweb', 'dreamweb-cd', true],
  ['flight-of-the-amazon-queen', 'queen', true],
  ['lure-of-the-temptress', 'lure', true],
  ['nippon-safes-inc', 'nippon-amiga', false],
  ['the-secret-of-monkey-island-demo', 'monkey-demo', false],
].map(([slug, target, skipIntro]) => ({ slug, target, skipIntro }));

const projectRoot = fileURLToPath(new URL('../', import.meta.url));

export function repositoryRevision(root = projectRoot) {
  const git = (...args) => execFileSync('git', ['-C', root, ...args], {
    encoding: 'utf8', timeout: 2000, stdio: ['ignore', 'pipe', 'ignore'],
  }).trim();
  const revision = { repository: 'agentscumm', recorded_at: new Date().toISOString(),
    source: 'server_initialization', commit_id: null, branch: null, dirty: null };
  try {
    revision.commit_id = git('rev-parse', 'HEAD');
    revision.branch = git('branch', '--show-current') || null;
    revision.dirty = Boolean(git('status', '--porcelain', '--untracked-files=normal'));
  } catch { /* Non-Git installations retain explicit unknown revision fields. */ }
  return revision;
}

export class GameRuntime {
  constructor(options = {}) {
    this.sourceRevision = repositoryRevision();
    this.baseUrl = new URL(options.baseUrl || 'https://scummweb.tsilva.eu');
    if (!['http:', 'https:'].includes(this.baseUrl.protocol) || this.baseUrl.username || this.baseUrl.password) {
      throw new Error('Base URL must be HTTP(S), without credentials');
    }
    this.dimensions = { width: options.width ?? 960, height: options.height ?? 720 };
    for (const value of Object.values(this.dimensions)) {
      if (!Number.isInteger(value) || value < 320 || value > 1920) throw new Error('Dimensions must be integers between 320 and 1920');
    }
    this.fps = options.fps ?? 10;
    if (!Number.isFinite(this.fps) || this.fps < 1 || this.fps > 15) throw new Error('Capture FPS must be between 1 and 15');
    const seconds = options.bufferSeconds ?? 60;
    if (!Number.isFinite(seconds) || seconds < 1 || seconds > 300) throw new Error('Buffer duration must be between 1 and 300 seconds');
    this.dataDir = path.resolve(options.dataDir || path.join(projectRoot, '.agentscumm'));
    this.history = new FrameHistory({ seconds, maxFrames: Math.ceil(seconds * this.fps) + 100 });
    this.queue = new SerialQueue();
    this.phase = 'idle';
    this.actions = [];
    this.actionSequences = 0;
    this.requests = new Map();
    this.usedRequestIds = new Set();
    this.captureFailures = 0;
    this.pendingRequests = new Map();
    this.viewer = new SpectatorViewer(this);
    this.viewerMode = options.viewer ?? true;
    this.launchTimeoutMs = options.launchTimeoutMs ?? 120000;
    this.closing = false;
    this.shutdownSignal = new AbortController();
  }

  status() {
    const frame = this.phase === 'ready' ? this.history.latest() : null;
    return { phase: this.phase, sessionId: this.sessionId, game: this.game, url: this.gameUrl,
      sourceRevision: this.sourceRevision,
      dimensions: frame ? { width: frame.width, height: frame.height } : this.dimensions,
      viewportDimensions: this.dimensions, coordinateSpace: 'CSS pixels relative to the screenshot top-left',
      bootPhase: this.bootPhase, readyReason: this.readyReason, message: this.message,
      loadingText: this.loadingText, lastNetworkError: this.lastNetworkError,
      runtimeHealth: this.runtimeHealth,
      actionSequences: this.actionSequences,
      pendingRequests: [...this.pendingRequests.values()].slice(-5),
      connected: Boolean(this.context?.browser()?.isConnected() && this.page && !this.page.isClosed()),
      captureFpsRequested: this.fps, captureFailures: this.captureFailures,
      lastCaptureError: this.lastCaptureError, history: this.history.status(),
      viewerUrl: this.viewer.url, viewerOpen: this.viewer.isOpen, viewerError: this.viewerError,
      recentActions: this.actions.slice(-8), capabilities: ['click', 'move', 'key', 'drag', 'wait', 'screenshot', 'sequences', 'frame-history', 'spectator'] };
  }

  async log(type, payload = {}) {
    if (this.logPath) await appendFile(this.logPath, JSON.stringify({ timestamp: new Date().toISOString(), type,
      sessionId: this.sessionId, ...payload }) + '\n', { mode: 0o600 });
  }

  async start({ game = 'flight-of-the-amazon-queen', mode = 'skip_intro', restart = false } = {}) {
    return this.queue.run(async () => {
      if (this.closing) throw new Error('Runtime is shutting down');
      if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(game)) throw new Error('Game must be a ScummWEB catalog slug');
      if (this.game === game && ['loading', 'ready'].includes(this.phase) && !restart) return this.status();
      if (this.phase === 'loading') throw new Error('A game is loading; wait for it or close the session first');
      if (this.page) await this.stopPage();
      this.sessionId = randomUUID();
      this.game = game;
      this.phase = 'loading';
      this.bootPhase = 'starting-browser';
      this.message = null;
      this.readyReason = null;
      this.history.clear();
      this.actions = [];
      this.actionSequences = 0;
      this.requests.clear();
      this.usedRequestIds.clear();
      this.captureFailures = 0;
      this.lastCaptureError = null;
      this.pendingRequests.clear(); this.lastNetworkError = null; this.loadingText = null;
      this.runtimeHealth = null; this.healthTask = null; this.lastHealthCheck = null;
      await mkdir(path.join(this.dataDir, 'runs'), { recursive: true, mode: 0o700 });
      this.logPath = path.join(this.dataDir, 'runs', `${this.sessionId}.jsonl`);
      const catalogGame = games.find(entry => entry.slug === game);
      const url = new URL(`/${game}/play`, this.baseUrl);
      if (mode === 'skip_intro' && catalogGame?.skipIntro) url.hash = 'seed=0';
      if (mode === 'continue') url.hash = 'continue';
      this.gameUrl = url.toString();
      this.expectedTarget = catalogGame?.target;
      await this.log('launch', { game, mode, url: this.gameUrl, dimensions: this.dimensions,
        sourceRevision: this.sourceRevision });
      const sessionId = this.sessionId;
      // Launch asynchronously so MCP's tool timeout is independent of game downloads.
      this.bootTask = this.boot(sessionId).catch(async error => {
        if (this.sessionId !== sessionId || this.closing) return;
        this.phase = 'error'; this.message = error.message; this.readyReason = null;
        await this.log('launch-error', { message: error.message }).catch(() => {});
      });
      return this.status();
    });
  }

  async boot(sessionId) {
    // One monotonic budget covers browser startup, navigation and the runtime.
    // Wall-clock jumps (including host sleep) must not expire a fresh launch.
    const deadline = performance.now() + this.launchTimeoutMs;
    const remaining = () => {
      const value = deadline - performance.now();
      if (value <= 0) throw new Error(`Game startup timed out at ${this.bootPhase}`);
      return Math.ceil(value);
    };
    const startupProbe = operation => {
      const budget = remaining();
      return bounded(operation, Math.min(3000, budget), budget <= 3000
        ? `Game startup timed out at ${this.bootPhase}` : 'Game renderer did not respond during startup');
    };
    if (!this.context) {
      this.context = await chromium.launchPersistentContext(path.join(this.dataDir, 'chromium-profile'), {
        headless: true, viewport: this.dimensions, screen: this.dimensions, deviceScaleFactor: 1,
        acceptDownloads: false, timeout: remaining(),
      });
      this.context.setDefaultTimeout(5000);
      this.context.on('close', () => {
        this.context = null;
        if (!this.closing && !['idle', 'closed'].includes(this.phase)) {
          this.phase = 'disconnected'; this.message = 'Owned Chromium session closed';
        }
      });
    }
    if (this.sessionId !== sessionId || this.closing) return;
    for (const page of this.context.pages()) await page.close();
    this.page = await this.context.newPage();
    const page = this.page;
    await page.addInitScript(installHealthObserver);
    await page.addInitScript(installDisplayObserver);
    page.on('request', request => {
      const url = new URL(request.url());
      if (['http:', 'https:'].includes(url.protocol)) this.pendingRequests.set(request, { path: url.pathname, type: request.resourceType() });
    });
    page.on('requestfinished', request => this.pendingRequests.delete(request));
    page.on('requestfailed', request => {
      this.pendingRequests.delete(request);
      this.lastNetworkError = { path: new URL(request.url()).pathname, error: request.failure()?.errorText };
    });
    page.on('crash', () => { if (this.page === page) { this.phase = 'error'; this.message = 'Game renderer crashed'; } });
    page.on('close', () => { if (this.page === page && !this.closing && this.phase !== 'closed') {
      this.phase = 'disconnected'; this.message = 'Game page closed';
    } });
    this.bootPhase = 'navigating';
    await this.viewer.start();
    if (this.viewerMode) {
      try { await this.viewer.open(); } catch (error) { this.viewerError = error.message; }
    }
    const response = await page.goto(this.gameUrl, { waitUntil: 'domcontentloaded', timeout: remaining() });
    if (response && !response.ok()) throw new Error(`Game route returned HTTP ${response.status()}`);
    // ScummWEB's web-player controls are not part of the game or MCP input surface.
    await page.addStyleTag({ content: '.game-route-controls,.game-route-bottom-actions,.game-route-touch-cursor-pad-shell{display:none!important}' });
    let renderedSamples = 0, startupToken;
    while (performance.now() < deadline && this.sessionId === sessionId && !this.closing) {
      if (this.phase === 'disconnected' || this.phase === 'error') throw new Error(this.message);
      const shell = page.frames().find(frame => frame !== page.mainFrame() && /\/scummvm\.html(?:[?#]|$)/.test(frame.url()));
      if (shell) {
        if (this.shell !== shell) {
          this.shell = shell;
          renderedSamples = 0;
          // A frame URL can be visible before its HTML/head arrives. Styling
          // that initial document throws instead of waiting for game startup.
          this.bootPhase = 'loading-game-document';
          await shell.waitForLoadState('domcontentloaded', { timeout: remaining() });
          await shell.addStyleTag({ content: '#canvas{outline:none!important;box-shadow:none!important}#scummvm-cursor-grab-hint{display:none!important}' });
        }
        const state = await startupProbe(shell.evaluate(readShellState));
        if (state) {
          if (startupToken !== state.token) { renderedSamples = 0; startupToken = state.token; }
          this.bootPhase = state.ready?.state || 'loading-runtime';
          this.loadingText = state.status;
          if (state.failure || state.error || state.seed?.state === 'error') throw new Error(state.failure || state.seed?.message || state.status || 'Game startup failed');
          const seeded = new URL(this.gameUrl).hash === '#seed=0';
          if (state.ready?.state === 'ready' && (!this.expectedTarget || state.ready.target === this.expectedTarget) &&
            (!seeded || state.seed?.state === 'ready') && state.canvas && !state.loading) {
            if (state.ready.reason === 'rendered-frame-timeout') throw new Error('Runtime started but a rendered game frame was not confirmed');
            this.canvas = shell.locator('#canvas');
            const overlay = page.locator('[data-launch-overlay-state="visible"]');
            const overlayVisible = await overlay.isVisible().catch(() => false);
            const frame = await this.capture('startup-check');
            const bounds = await this.canvas.boundingBox();
            const rendered = !overlayVisible && bounds && await startupProbe(this.hasRenderedContent(frame, bounds));
            renderedSamples = rendered ? renderedSamples + 1 : 0;
            if (renderedSamples >= 2) {
              await this.canvas.focus();
              this.documentToken = state.token;
              this.readyReason = state.ready.reason;
              this.phase = 'ready';
              await this.checkHealth();
              if (this.phase !== 'ready') throw new Error(this.message);
              remaining();
              await this.capture('launch-ready');
              this.scheduleCapture(sessionId);
              await this.log('ready', { reason: this.readyReason, firstFrame: this.history.latest()?.id });
              return;
            }
          } else renderedSamples = 0;
        }
      }
      await sleep(150);
    }
    if (!this.closing && this.sessionId === sessionId) throw new Error(`Game startup timed out at ${this.bootPhase}`);
  }

  async hasRenderedContent(frame, bounds) {
    return this.page.evaluate(async ({ encoded, bounds }) => {
      const image = new Image();
      image.src = `data:image/jpeg;base64,${encoded}`;
      await image.decode();
      const sample = document.createElement('canvas');
      sample.width = 32; sample.height = 32;
      const context = sample.getContext('2d', { willReadFrequently: true });
      context.drawImage(image, bounds.x, bounds.y, bounds.width, bounds.height, 0, 0, 32, 32);
      const pixels = context.getImageData(0, 0, 32, 32).data;
      let visible = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i] + pixels[i + 1] + pixels[i + 2] > 36 && ++visible >= 48) return true;
      }
      return false;
    }, { encoded: frame.data.toString('base64'), bounds });
  }

  scheduleCapture(sessionId) {
    const tick = async () => {
      if (this.sessionId !== sessionId || this.closing || this.phase !== 'ready' || !this.page || this.page.isClosed()) return;
      const started = Date.now();
      try {
        if (this.lastHealthCheck === null || performance.now() - this.lastHealthCheck >= 1000) await this.checkHealth();
        if (this.phase !== 'ready') return;
        await this.capture('buffer'); this.lastCaptureError = null;
      }
      catch (error) { this.captureFailures++; this.lastCaptureError = error.message; }
      if (this.sessionId === sessionId && !this.closing) this.captureTimer = setTimeout(tick, Math.max(10, 1000 / this.fps - (Date.now() - started)));
    };
    this.captureTimer = setTimeout(tick, 10);
  }

  async capture(reason = 'observe') {
    // Tool observations must start AFTER their request/input, never reuse an older in-flight frame.
    while (this.captureTask) {
      if (reason === 'buffer') return this.captureTask;
      await this.captureTask.catch(() => {});
    }
    this.captureTask = (async () => {
      if (!this.page || this.page.isClosed()) throw new Error('No connected game page');
      const sessionId = this.sessionId;
      const phase = this.phase;
      const geometry = phase === 'ready' ? await this.gameGeometry() : null;
      const clip = geometry?.captureBounds;
      const data = await this.page.screenshot({ type: 'jpeg', quality: 90, scale: 'css', timeout: 3000,
        ...(clip ? { clip } : {}) });
      if (sessionId !== this.sessionId) throw new Error('Session changed during capture');
      const metadata = { sessionId, game: this.game, phase, reason,
        width: clip?.width ?? this.dimensions.width, height: clip?.height ?? this.dimensions.height,
        canvasBounds: geometry ? { ...geometry.canvasBounds, x: geometry.canvasBounds.x - clip.x,
          y: geometry.canvasBounds.y - clip.y } : null,
        displayBounds: clip ? { x: 0, y: 0, width: clip.width, height: clip.height } : null,
        captureBounds: clip ?? null, actionId: this.currentActionId || null,
        step: this.currentStep ?? null };
      if (phase === 'ready' && this.phase === 'ready') return this.history.add(data, metadata);
      // Startup checks and explicit loading/error observations never enter the rolling buffer.
      return { ...metadata, id: `unbuffered-${randomUUID()}`, timestamp: new Date().toISOString(),
        monotonicMs: performance.now(), mimeType: 'image/jpeg', data };
    })();
    try { return await this.captureTask; } finally { this.captureTask = null; }
  }

  async gameGeometry() {
    const canvasBounds = await this.canvas.boundingBox();
    if (!canvasBounds) throw new Error('Game canvas is not visible');
    const rendered = await bounded(this.canvas.evaluate(displayBounds, canvasBounds),
      3000, 'Game renderer did not respond during capture');
    return { canvasBounds, captureBounds: captureBounds(rendered, this.dimensions) };
  }

  async waitReady(timeoutMs = 25000) {
    const sessionId = this.sessionId;
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline && this.phase === 'loading' && this.sessionId === sessionId) await sleep(100);
    return this.inspectStatus();
  }

  async inspectStatus() {
    if (this.phase === 'ready') await this.checkHealth();
    return this.status();
  }

  async checkHealth() {
    if (this.healthTask) return this.healthTask;
    const sessionId = this.sessionId;
    const shell = this.shell;
    if (this.phase !== 'ready' || !shell) return;
    const task = (async () => {
      let state, failure, phase = 'error';
      try {
        state = await bounded(shell.evaluate(readShellState), 3000, 'Game renderer is unresponsive; restart the game to recover');
        if (state.token !== this.documentToken) {
          phase = 'disconnected'; failure = 'Game document changed; launch or reconnect explicitly before issuing input';
        } else failure = state.failure || (state.error ? state.status || 'Game runtime error' : null) ||
          (state.seed?.state === 'error' ? state.seed.message || 'Game checkpoint failed' : null);
      } catch (error) { failure = error.message; }
      if (this.sessionId !== sessionId || this.shell !== shell || this.phase !== 'ready') return;
      this.lastHealthCheck = performance.now();
      this.runtimeHealth = { checkedAt: Date.now(), state: failure ? 'failed' : 'responsive',
        message: failure || null, errors: state?.errors || [] };
      if (failure) {
        this.phase = phase; this.message = failure; this.readyReason = null;
        clearTimeout(this.captureTimer);
        await this.log('runtime-failure', this.runtimeHealth).catch(() => {});
      }
    })();
    this.healthTask = task;
    try { await task; } finally { if (this.healthTask === task) this.healthTask = null; }
  }

  async assertReady(sessionId) {
    if (sessionId !== this.sessionId) throw new Error('Session ID does not match the current game');
    if (this.phase !== 'ready' || !this.page || this.page.isClosed()) throw new Error(`Game is not ready (${this.phase})`);
    await this.checkHealth();
    if (this.phase !== 'ready') throw new Error(this.message || `Game is not ready (${this.phase})`);
    if (this.page.viewportSize()?.width !== this.dimensions.width || this.page.viewportSize()?.height !== this.dimensions.height) {
      this.phase = 'error'; throw new Error('Game viewport dimensions changed');
    }
  }

  async act({ session_id: sessionId, request_id: requestId, actions: rawActions, frame_id: frameId, after_ms: afterMs = 200 }, signal) {
    const inputSignal = signal ? AbortSignal.any([signal, this.shutdownSignal.signal]) : this.shutdownSignal.signal;
    return this.queue.run(async () => {
      if (sessionId !== this.sessionId) throw new Error('Session ID does not match the current game');
      const fingerprint = createHash('sha256').update(JSON.stringify({ rawActions, frameId, afterMs })).digest('hex');
      const cached = this.requests.get(requestId);
      if (cached) {
        if (cached.fingerprint !== fingerprint) throw new Error('Request ID was already used for different inputs');
        return { ...cached.result, replayed: true };
      }
      if (this.usedRequestIds.has(requestId)) throw new Error('Request result expired; input will not be repeated. Observe before choosing a new action');
      if (this.usedRequestIds.size >= 10000) throw new Error('Session request limit reached; close and relaunch before further input');
      inputSignal.throwIfAborted();
      await this.assertReady(sessionId);
      const box = (await this.gameGeometry()).captureBounds;
      if (frameId) {
        const frame = this.history.get(frameId);
        if (!frame || frame.sessionId !== sessionId || frame.phase !== 'ready') throw new Error('Observation expired or belongs to another session; observe again');
        if (JSON.stringify(frame.captureBounds) !== JSON.stringify(box)) throw new Error('Game display geometry changed; observe again before issuing input');
      }
      const actions = validateSequence(rawActions, box.width, box.height);
      const actionId = randomUUID();
      const record = { actionId, requestId, state: 'running', completed: 0, total: actions.length,
        startedAt: new Date().toISOString(), steps: [] };
      this.actions.push(record);
      this.actionSequences++;
      if (this.actions.length > 100) this.actions.shift();
      this.currentActionId = actionId;
      const result = { action: record, replayed: false, screenshots: [] };
      // Reserve the request before any input: uncertain delivery must never be retried automatically.
      this.requests.set(requestId, { fingerprint, result });
      this.usedRequestIds.add(requestId);
      while (this.requests.size > 100) this.requests.delete(this.requests.keys().next().value);
      try {
        await this.log('action-start', { ...record, inputs: actions });
        await this.capture('before-action');
        for (const [index, action] of actions.entries()) {
          inputSignal.throwIfAborted();
          await this.assertReady(sessionId);
          this.currentStep = index;
          const step = { index, type: action.type, state: 'issuing' };
          record.steps.push(step);
          const bounds = await this.canvas.boundingBox();
          if (!bounds) throw new Error('Game canvas is not visible');
          if (action.type === 'screenshot') {
            const frame = await this.capture('sequence-screenshot');
            result.screenshots.push(frame);
            step.frameId = frame.id;
            step.state = 'captured';
          } else {
            if ('x' in action && JSON.stringify((await this.gameGeometry()).captureBounds) !== JSON.stringify(box)) {
              throw new Error('Game display geometry changed during the sequence; observe again before issuing input');
            }
            await this.canvas.focus();
            await this.execute(action, box, inputSignal);
            step.state = 'delivered';
          }
          record.completed++;
          await this.log('action-step', { actionId, ...step });
          await this.assertReady(sessionId);
        }
        if (afterMs) await sleep(afterMs, undefined, { signal: inputSignal });
        await this.assertReady(sessionId);
        result.frame = await this.capture('after-action');
        record.state = 'completed';
      } catch (error) {
        record.state = 'failed'; record.error = error.message;
        const step = record.steps.at(-1);
        if (step?.state === 'issuing') step.state = step.type === 'screenshot' ? 'capture-failed' : 'delivery-uncertain';
        try { result.frame = await this.capture('failed-action'); } catch { /* Status remains available after renderer loss. */ }
      } finally {
        record.finishedAt = new Date().toISOString();
        this.currentActionId = null; this.currentStep = null;
        await this.log('action-finish', record).catch(() => {});
      }
      return result;
    });
  }

  async execute(action, box, signal = this.shutdownSignal.signal) {
    const page = this.page;
    const move = (x, y, steps = 1) => page.mouse.move(box.x + x, box.y + y, { steps });
    if (action.type === 'move') await move(action.x, action.y);
    if (action.type === 'wait') await sleep(action.milliseconds, undefined, { signal });
    if (action.type === 'click') {
      await move(action.x, action.y);
      await page.mouse.click(box.x + action.x, box.y + action.y, { button: action.button,
        clickCount: action.count, delay: action.hold_ms });
    }
    if (action.type === 'key') {
      await page.keyboard.down(action.key);
      try { await sleep(action.hold_ms, undefined, { signal }); } finally { await page.keyboard.up(action.key); }
    }
    if (action.type === 'drag') {
      await move(action.from_x, action.from_y);
      await page.mouse.down({ button: action.button });
      try { await move(action.x, action.y, action.steps); } finally { await page.mouse.up({ button: action.button }); }
    }
  }

  async observe() {
    return this.queue.run(async () => {
      if (!this.page || this.page.isClosed()) throw new Error('Launch a game first');
      if (this.phase === 'ready') {
        try { await this.assertReady(this.sessionId); }
        catch (error) { if (this.phase === 'ready') throw error; }
      }
      return this.capture();
    });
  }

  async stopPage() {
    clearTimeout(this.captureTimer);
    const page = this.page;
    this.page = null; this.canvas = null; this.shell = null;
    await page?.close();
    await this.captureTask?.catch(() => {});
  }

  async closeGame() {
    return this.queue.run(async () => {
      this.phase = 'closed';
      this.sessionId = null;
      await this.stopPage();
      await this.bootTask;
      this.history.clear();
      this.requests.clear();
      this.usedRequestIds.clear();
      await this.log('close');
      return this.status();
    });
  }

  async close() {
    this.closing = true;
    this.shutdownSignal.abort(new Error('Game runtime is shutting down'));
    clearTimeout(this.captureTimer);
    await this.stopPage();
    await this.bootTask;
    await this.queue.tail;
    await this.context?.close();
    this.context = null;
    await this.viewer.close();
    this.phase = 'closed';
  }
}
