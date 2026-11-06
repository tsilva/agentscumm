import http from 'node:http';

export async function startFixture({ navigationDelay = 0, startupDelay = 600, documentDelay = 0 } = {}) {
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', 'text/html');
    if (request.url.startsWith('/scummvm.html')) {
      const mode = new URL(request.url, 'http://fixture').searchParams.get('mode');
      const html = `<!doctype html><html><style>body{margin:0;background:black}canvas{display:block;width:100vw;height:100vh}</style>
        <div id="scummvm-cursor-grab-hint" style="position:fixed;inset:0;background:magenta;z-index:100">Click the game to grab the cursor.</div><div id="status">Loading fixture</div><canvas id="canvas" width="320" height="240" tabindex="0"></canvas><script>
        window.events=[];window.__scummwebReadyState={state:'pending',target:'queen'};
        const canvas=document.getElementById('canvas'),ctx=canvas.getContext('2d');
        function paint(){ctx.fillStyle='#28523b';ctx.fillRect(0,0,320,240);ctx.fillStyle='#e9dc9b';ctx.font='18px sans-serif';ctx.fillText('Synthetic adventure',20,40);ctx.fillText('Inputs: '+events.length,20,90);ctx.fillRect((Date.now()/40)%260,160,20,20)}
        setInterval(paint,80);paint();
        for(const type of ['mousedown','mouseup','mousemove','keydown','keyup'])canvas.addEventListener(type,e=>{events.push({type,x:e.offsetX,y:e.offsetY,button:e.button,key:e.key});paint();${mode === 'abort-on-click' ? "if(type==='mousedown')globalThis.ABORT=true;" : ''}});
        setTimeout(()=>{document.getElementById('status').style.display='none';window.__scummwebSkipIntroSeedStatus={state:'ready',target:'queen'};
          window.__scummwebReadyState={state:'ready',target:'queen',reason:${JSON.stringify(mode === 'fallback' ? 'rendered-frame-timeout' : 'rendered-frame-detected')}};
          ${mode === 'error' ? "document.getElementById('status').className='error';document.getElementById('status').textContent='Fixture startup error';" : ''}
        },${startupDelay});
        </script></html>`;
      if (documentDelay) {
        response.write('<!doctype html>');
        setTimeout(() => response.end(html.replace('<!doctype html>', '')), documentDelay);
      } else response.end(html);
    } else if (/^\/[a-z0-9-]+\/play/.test(request.url)) {
      const mode = request.url.includes('broken') ? 'error' : request.url.includes('fallback') ? 'fallback' : request.url.includes('abort-on-click') ? 'abort-on-click' : 'normal';
      setTimeout(() => response.end(`<!doctype html><html><style>body{margin:0}iframe{display:block;width:100vw;height:100vh;border:0}</style>
        <div class="game-route-controls" style="position:fixed;inset:0 0 auto;height:64px;background:magenta;z-index:10"><button>Exit game</button><button>Fullscreen</button></div>
        <div class="game-route-bottom-actions" style="position:fixed;bottom:0;z-index:10"><button>Skip intro</button></div>
        <iframe data-scummvm-route-frame="true" src="/scummvm.html?mode=${mode}"></iframe></html>`), navigationDelay);
    } else response.writeHead(404).end('Not found');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { url: `http://127.0.0.1:${server.address().port}`, close: () => new Promise(resolve => server.close(resolve)) };
}
