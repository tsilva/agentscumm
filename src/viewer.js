import http from 'node:http';
import { randomBytes } from 'node:crypto';
import { chromium } from 'playwright';
import { MacOSSpectator } from './macos-app.js';
import { frameInfo } from './history.js';

// A separate image-only page: no game iframe, input endpoint, or browser-control connection.
const html = `<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentSCUMM</title><style>
*{box-sizing:border-box}html,body{margin:0;background:#000;height:100%;overflow:hidden}
body{display:grid;grid-template-rows:minmax(0,1fr) 24px}
#screen{display:block;width:100%;height:100%;object-fit:contain;image-rendering:pixelated;user-select:none;pointer-events:none}
#screen[hidden]{display:none}
footer{grid-row:2;padding:0 10px;background:#171717;color:#bbb;font:12px/24px system-ui,sans-serif;font-variant-numeric:tabular-nums}
</style><canvas id="screen" aria-label="Live game screen" hidden></canvas><footer>Actions: <span id="actions">0</span></footer>
<script>
let lastId, gameTitle='AgentSCUMM';
const screen=document.getElementById('screen'),context=screen.getContext('2d');
async function refresh(){
 try{
  const response=await fetch('status',{cache:'no-store'});if(!response.ok)throw Error('Session unavailable');
  const state=await response.json();
  document.getElementById('actions').textContent=String(state.actionSequences??0);
  const name=state.game?state.game.split('-').map((word,index)=>index>0&&['a','an','and','of','the','in'].includes(word)?word:word.charAt(0).toUpperCase()+word.slice(1)).join(' '):null;
  gameTitle=name?'AgentSCUMM - '+name:'AgentSCUMM';document.title=gameTitle;
  const info=state.frame,bounds=info?.displayBounds||info?.canvasBounds;
  if(!bounds){screen.hidden=true;lastId=null;}
  else if(info.id!==lastId){
   const frame=await fetch('frame/'+encodeURIComponent(info.id),{cache:'no-store'});
   if(frame.ok){const url=URL.createObjectURL(await frame.blob());
    try{const image=new Image();image.src=url;await image.decode();
     const x=Math.max(0,bounds.x),y=Math.max(0,bounds.y);
     const width=Math.min(image.naturalWidth,bounds.x+bounds.width)-x;
     const height=Math.min(image.naturalHeight,bounds.y+bounds.height)-y;
     if(width<=0||height<=0)throw Error('Game canvas is outside the captured frame');
     screen.width=Math.round(width);screen.height=Math.round(height);
     context.imageSmoothingEnabled=false;
     context.drawImage(image,x,y,width,height,0,0,screen.width,screen.height);
     screen.hidden=false;lastId=info.id;
     window.webkit?.messageHandlers?.gameSize?.postMessage({width:screen.width,height:screen.height,footerHeight:24});
    }finally{URL.revokeObjectURL(url);}
   }
  }
 }catch(error){document.title=gameTitle+' (Disconnected)';}
 setTimeout(refresh,100);
}
refresh();
</script></html>`;

export class SpectatorViewer {
  constructor(runtime) { this.runtime = runtime; this.token = randomBytes(24).toString('hex'); }

  get isOpen() {
    return this.native ? this.native.isOpen : Boolean(this.page && !this.page.isClosed());
  }

  start() {
    this.startTask ||= this.listen().catch(error => { this.startTask = null; throw error; });
    return this.startTask;
  }

  async listen() {
    if (this.server) return this.url;
    this.server = http.createServer((request, response) => {
      const address = this.server.address();
      if (!address) { response.writeHead(503).end(); return; }
      const expectedHost = `127.0.0.1:${address.port}`;
      let path;
      try { path = new URL(request.url, `http://${expectedHost}`).pathname; }
      catch { response.writeHead(400).end(); return; }
      response.setHeader('Cache-Control', 'no-store');
      response.setHeader('X-Content-Type-Options', 'nosniff');
      response.setHeader('Referrer-Policy', 'no-referrer');
      response.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
      response.setHeader('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'");
      if (request.headers.host !== expectedHost) { response.writeHead(403).end(); return; }
      if (request.method !== 'GET') { response.writeHead(405).end(); return; }
      const prefix = `/${this.token}/`;
      if (!path.startsWith(prefix)) { response.writeHead(404).end(); return; }
      const route = path.slice(prefix.length);
      if (route === '') { response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end(html); }
      else if (route === 'status') {
        const frame = this.runtime.history.latest();
        response.setHeader('Content-Type', 'application/json');
        response.end(JSON.stringify({ ...this.runtime.status(), frame: frame ? frameInfo(frame) : null }));
      }
      else if (route.startsWith('frame/')) {
        const frame = this.runtime.history.get(route.slice(6));
        if (!frame) { response.writeHead(404).end(); return; }
        response.setHeader('Content-Type', frame.mimeType); response.end(frame.data);
      } else response.writeHead(404).end();
    });
    await new Promise((resolve, reject) => {
      this.server.once('error', reject);
      this.server.listen(0, '127.0.0.1', resolve);
    });
    this.url = `http://127.0.0.1:${this.server.address().port}/${this.token}/`;
    return this.url;
  }

  open(options = {}) {
    this.openTask ||= this.openWindow(options).finally(() => { this.openTask = null; });
    return this.openTask;
  }

  async openWindow({ headless = false } = {}) {
    await this.start();
    if (this.isOpen) return this.url;
    const { width, height } = this.runtime.dimensions;
    if (process.platform === 'darwin' && !headless) {
      this.native = new MacOSSpectator();
      await this.native.open(this.url, { width, height });
      return this.url;
    }
    if (this.context) await this.context.close();
    this.native = null;
    this.context = await chromium.launchPersistentContext('', { headless, args: headless ? [] : [
      `--app=${this.url}`, `--window-size=${width},${height + 32}`,
    ], viewport: { width, height }, deviceScaleFactor: 1 });
    this.browser = this.context.browser();
    this.page = this.context.pages()[0] || await this.context.newPage();
    await this.page.goto(this.url);
    return this.url;
  }

  async close() {
    await this.openTask?.catch(() => {});
    await this.native?.close();
    this.native = null;
    await this.context?.close();
    this.browser = null;
    this.page = null;
    if (this.server) await new Promise(resolve => this.server.close(resolve));
    this.server = null;
    this.startTask = null;
  }
}
