import http from 'node:http';

export async function startFixture({ navigationDelay = 0, startupDelay = 600, documentDelay = 0, library = false, libraryContinue = false, evaluation = false, telemetry = false, nativeControls = false } = {}) {
  const requests = [];
  const server = http.createServer((request, response) => {
    requests.push(request.url);
    response.setHeader('Content-Type', 'text/html');
    if (library && (request.url === '/' || /^\/[a-z0-9-]+\/play/.test(request.url))) {
      response.end(`<!doctype html><html><body>
        <input type="file" accept=".zip,application/zip" aria-label="Choose save backup ZIP">
        <button class="library-add-card">Add a game</button>
        <input type="file" accept=".zip,application/zip" multiple>
        <section id="library"></section><section id="hero"></section>
        <dialog><p id="game-import-summary"></p><ul class="game-import-items"></ul><button onclick="this.parentElement.close()">Confirm</button></dialog>
        <script>
        document.querySelector('input[multiple]').onchange = event => {
          const files=[...event.target.files], rows=[], seen=new Set();
          let added=0,skipped=0,errors=0;
          for(const file of files){
            if(file.name==='invalid.zip'){errors++;rows.push('<li><strong>invalid.zip</strong><p>Error. Invalid ZIP</p></li>');continue;}
            const title=file.name.replace(/\\.zip$/,'');
            const href='/'+title.toLowerCase().replace(/[^a-z0-9]+/g,'-');
            if([...document.querySelectorAll('#library a')].some(a=>a.getAttribute('href')===href)){skipped++;continue;}
            const div=document.createElement('div');div.className='poster-entry';div.dataset.librarySourceIds=JSON.stringify([title]);
            const a=document.createElement('a');a.href=href;a.innerHTML='<span class="poster-title">'+title+'</span>';
            div.append(a);document.getElementById('library').append(div);added++;
            rows.push('<li><strong>'+file.name+'</strong><p>Added to your library</p></li>');
          }
          if(location.pathname.endsWith('/play')){
            document.getElementById('hero').innerHTML='<div class="hero-launch"><button class="launch-button" title="Play imported adventure">${libraryContinue ? 'Continue' : 'Play'}</button>${libraryContinue ? '<button class="start-fresh-button">Start from beginning</button>' : ''}</div>';
            for(const button of document.querySelectorAll('#hero button'))button.onclick=()=>{
              const kind=button.textContent==='Continue'?'continue':'beginning';
              document.body.innerHTML='<iframe style="position:fixed;inset:0;width:100vw;height:100vh;border:0" src="/scummvm.html?launch='+kind+'"></iframe>';
            };
          }
          document.getElementById('game-import-summary').textContent=added+' games added · '+skipped+' games skipped · '+errors+' errors';
          document.querySelector('.game-import-items').innerHTML=rows.join('');document.querySelector('dialog').showModal();event.target.value='';
        };
        </script></body></html>`);
    } else if (request.url.startsWith('/scummvm.html')) {
      const mode = new URL(request.url, 'http://fixture').searchParams.get('mode');
      const html = `<!doctype html><html><style>body{margin:0;background:black}canvas{display:block;width:100vw;height:100vh}</style>
        <div id="scummvm-cursor-grab-hint" style="position:fixed;inset:0;background:magenta;z-index:100">Click the game to grab the cursor.</div><div id="status">Loading fixture</div><canvas id="canvas" width="320" height="240" tabindex="0"></canvas><script>
        window.events=[];window.__scummwebReadyState={state:'pending',target:'queen'};
        ${nativeControls ? `
        const saveScope = new URL(location.href).searchParams.get('agentSaveScope');
        const slots = new Map();
        let tick = 0, paused = false, queuedInputs = 0;
        document.addEventListener('mousedown', () => paused ? queuedInputs++ : tick++);
        globalThis.scummwebAgentControls = {
          status: () => ({version: 1, saveScope, tick, paused, ${nativeControls === 'frame' ? "maxSaveSlot: 0, tickSource: 'frame', actions: ['pause', 'resume', 'wait_ticks']" : 'maxSaveSlot: 8'}}),
          run(operation, id) {
            switch (operation) {
              case 'pause': paused = true; break;
              case 'wait_ticks': tick += id; paused = true; break;
              case 'save': slots.set(id, tick); break;
              case 'resume': paused = false; tick += queuedInputs; queuedInputs = 0; break;
              case 'load':
                if (!slots.has(id)) return {error: 'Missing fixture save'};
                tick = slots.get(id);
                break;
              default: throw new Error('Unexpected fixture control: ' + operation);
            }
            return {tick, paused};
          }
        };
        ` : ''}
        ${telemetry ? `
        const query = new URL(location.href).searchParams;
        if (query.get('telemetry') === '1') {
          let snapshot = null;
          globalThis.scummwebTelemetry = {read: () => ({identity:{engine_id:'fixture',game_id:'unmapped'},snapshot})};
          if (!query.has('identityOnly')) {
            snapshot = {schema_version:1,room_id:17,sample_time_ms:123,banks:{vars:[0,12,-8,null,9], globals:{score:1.5, visited:1}}};
            document.addEventListener('mousedown', () => {
              snapshot = {schema_version:1,room_id:18,sample_time_ms:456,banks:{vars:[0,12,4,null,9]}};
            });
          }
        }
        ` : ''}
        ${evaluation && !['game_variables_only', 'progress'].includes(evaluation) ? `
        function room(id,ready){
          if(!globalThis.__scummwebEvaluationEnabled)return;
          const state={schema_version:1,engine_id:'queen',room_id:id,room_name:id===71?'Laundry':'Hotel',room_ready:ready,native_time_ms:performance.now()};
          globalThis.__scummwebEvaluationState=state;
          (globalThis.__scummwebEvaluationEvents ||= []).push(state);
        }
        room(70,true);
        setInterval(()=>{const s=globalThis.__scummwebEvaluationState;if(s)s.native_time_ms=performance.now()},50);
        document.addEventListener('mousedown',()=>{room(71,false);setTimeout(()=>room(71,true),150)});
        ` : evaluation ? `
        const bank = Array(${evaluation === 'progress' ? 211 : 94}).fill(0);
        if (globalThis.__scummwebEvaluationEnabled) globalThis.__scummwebEvaluationGameVariableEngine = 'queen';
        globalThis.__scummwebEvaluationGameVariableEvents = globalThis.__scummwebEvaluationEnabled ?
          [{event:'game_variables_baseline',banks:{game_state:bank},native_time_ms:performance.now()}] : undefined;
        document.addEventListener('mousedown',()=>{
          const queue = globalThis.__scummwebEvaluationGameVariableEvents;
          if (!queue) return;
          for (const [previous,value] of ${evaluation === 'progress' ? '[[0,1]]' : '[[0,1],[1,0]]'}) {
            queue.push({event:'game_variables_change',bank:'game_state',index:${evaluation === 'progress' ? 3 : 93},previous,value,native_time_ms:performance.now()});
          }
        });
        ` : ''}
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
      const query = new URL(request.url, 'http://fixture').searchParams;
      const saveScope = nativeControls ? query.get('agentSaveScope') : null;
      setTimeout(() => response.end(`<!doctype html><html><style>body{margin:0}iframe{display:block;width:100vw;height:100vh;border:0}</style>
        <div class="game-route-controls" style="position:fixed;inset:0 0 auto;height:64px;background:magenta;z-index:10"><button>Exit game</button><button>Fullscreen</button></div>
        <div class="game-route-bottom-actions" style="position:fixed;bottom:0;z-index:10"><button>Skip intro</button></div>
        ${request.url.includes('failed-download') ? '<div data-launch-overlay-state="error"><p data-launch-status>Could not download the game ZIP</p></div>' : ''}
        <iframe data-scummvm-route-frame="true" src="/scummvm.html?mode=${mode}${request.url.includes('identity-only') ? '&identityOnly' : ''}${query.get('telemetry') === '1' ? '&telemetry=1' : ''}${saveScope ? '&agentSaveScope=' + encodeURIComponent(saveScope) : ''}"></iframe></html>`), navigationDelay);
    } else response.writeHead(404).end('Not found');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { requests, url: `http://127.0.0.1:${server.address().port}`, close: () => new Promise(resolve => server.close(resolve)) };
}
