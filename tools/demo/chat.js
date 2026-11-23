// Chat-panel overlay for the README demo: prompt typed into a chat, tool calls as chips that stream into the viewport.
window.CHAT = {
  W: 400,
  css() {
    const s = document.createElement('style');
    s.textContent = `
    body.demo-split{width:calc(100vw - 400px)!important;position:relative}
    #chat{position:fixed;top:0;right:0;bottom:0;width:400px;z-index:99990;background:#faf9f5;color:#3d3929;
      font:15px/1.55 Georgia,'Times New Roman',serif;display:flex;flex-direction:column;border-left:1px solid #e8e5da;
      transform:translateX(100%);transition:transform .7s cubic-bezier(.2,.8,.2,1)}
    #chat.in{transform:none}
    #chat .hd{padding:14px 18px;border-bottom:1px solid #ece9df;display:flex;align-items:center;gap:10px}
    #chat .hd .spark{font:22px/1 system-ui,'Segoe UI Symbol',sans-serif;color:#d97757;width:24px;text-align:center}
    #chat .hd b{font:600 17px Georgia,serif;color:#1f1e1d}
    #chat .hd span{font:12px system-ui,'Segoe UI',sans-serif;color:#87867f}
    #chat .hd .mcp{margin-left:auto;font:11.5px system-ui,'Segoe UI',sans-serif;color:#5e5d59;background:#f0eee6;border:1px solid #e3e0d5;
      border-radius:999px;padding:3px 10px;display:flex;align-items:center;gap:6px}
    
    #chat .log{flex:1;overflow:hidden;padding:18px 18px 8px;display:flex;flex-direction:column;gap:9px;justify-content:flex-end}
    #chat .me{align-self:flex-end;max-width:90%;background:#f0eee6;border-radius:14px;padding:10px 14px;color:#1f1e1d;
      font:14.5px/1.5 system-ui,'Segoe UI',sans-serif;animation:cUp .45s cubic-bezier(.2,.8,.2,1) both}
    #chat .ai{max-width:98%;animation:cUp .4s both}
    #chat .think{display:flex;align-items:center;gap:8px;font:italic 14.5px Georgia,serif}
    #chat .think .g{font:18px/1 system-ui,'Segoe UI Symbol',sans-serif;color:#d97757;width:18px;text-align:center;font-style:normal}
    #chat .think .w{background:linear-gradient(90deg,#a3a198 0%,#a3a198 35%,#3d3929 50%,#a3a198 65%,#a3a198 100%);
      background-size:250% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:cShine 1.8s linear infinite}
    @keyframes cShine{from{background-position:120% 0}to{background-position:-120% 0}}
    #chat .chip{display:flex;align-items:center;gap:8px;background:#fff;border:1px solid #e8e5da;border-radius:9px;padding:6px 10px;
      font:12.5px ui-monospace,Consolas,monospace;color:#3d3929;animation:cIn .35s cubic-bezier(.2,.8,.2,1) both;position:relative;overflow:hidden}
    .oswx-cube{width:15px;height:15px;flex:none;display:inline-block;position:relative;vertical-align:-3px}
    .oswx-cube i{position:absolute;inset:0;clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%);background:linear-gradient(135deg,#e3324b,#8d0b1f)}
    .oswx-cube .t{clip-path:polygon(50% 0,100% 25%,50% 50%,0 25%);background:#ff6b80}
    .oswx-cube .r{clip-path:polygon(100% 25%,100% 75%,50% 100%,50% 50%);background:#8d0b1f}
    #chat .chip .nm{color:#1f1e1d;font-weight:600}#chat .chip .ar{color:#87867f;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #chat .chip .ok{margin-left:auto;color:#5aa469;opacity:0;transition:opacity .3s;font-family:system-ui,sans-serif}#chat .chip.done .ok{opacity:1}
    #chat .chip::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(217,119,87,.18),transparent);
      transform:translateX(-100%);animation:cSweep .9s .1s ease-out}
    @keyframes cSweep{to{transform:translateX(100%)}}
    @keyframes cIn{from{opacity:0;transform:translateY(8px)}}
    @keyframes cUp{from{opacity:0;transform:translateY(16px)}}
    #chat .msg{animation:cUp .4s both;color:#1f1e1d}
    #chat .msg b{font-weight:600}
    #chat .inp{margin:10px 14px 16px;background:#fff;border:1px solid #e3e0d5;border-radius:16px;padding:12px 14px;min-height:80px;
      display:flex;flex-direction:column;box-shadow:0 2px 10px rgba(60,50,30,.06);transition:border-color .3s, box-shadow .3s;
      font:14.5px/1.5 system-ui,'Segoe UI',sans-serif;color:#1f1e1d}
    #chat .inp.focus{border-color:#d9cfc2;box-shadow:0 4px 18px rgba(60,50,30,.10)}
    #chat .inp .tx{flex:1;white-space:pre-wrap}#chat .inp .ph{color:#a3a198}
    #chat .inp .cur{display:inline-block;width:1.5px;height:1.1em;background:#1f1e1d;vertical-align:-2px;animation:cBlink 1s steps(1) infinite}
    @keyframes cBlink{50%{opacity:0}}
    #chat .inp .row{display:flex;align-items:center;gap:8px;margin-top:6px}
    #chat .inp .tool{display:flex;align-items:center;gap:6px;font-size:12px;color:#87867f;border:1px solid #e8e5da;border-radius:8px;padding:2px 8px}
    #chat .inp .send{margin-left:auto;width:32px;height:32px;border-radius:9px;background:#e6c2b4;display:grid;place-items:center;transition:background .2s,transform .2s}
    #chat .inp.focus .send{background:#d97757}
    #chat .inp .send.go{background:#c6613f;transform:scale(1.12)}
    .flow-dot{position:fixed;z-index:99995;width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%;pointer-events:none;
      background:radial-gradient(circle,#fff 0%,#f0a585 35%,rgba(217,119,87,0) 70%);box-shadow:0 0 18px 6px rgba(217,119,87,.55)}
    #flow-ring{position:fixed;z-index:99994;pointer-events:none;border-radius:16px;border:2px solid rgba(217,119,87,0);transition:none}
    .toast{display:none!important}`;
    document.head.append(s);
    const c = document.createElement('div'); c.id = 'chat';
    c.innerHTML = `<div class="hd"><div class="spark">✻</div><div><b>Claude</b><br><span>MountingPlate</span></div><div class="mcp"><span class="oswx-cube"><i></i><i class="t"></i><i class="r"></i></span>openswx · MCP</div></div>
      <div class="log"></div>
      <div class="inp"><div class="tx"><span class="ph">Reply to Claude...</span></div><div class="row"><span class="tool"><span class="oswx-cube"><i></i><i class="t"></i><i class="r"></i></span> openswx</span><div class="send"><svg width="14" height="14" viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7" stroke="#fff" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg></div></div></div>`;
    document.body.after(c);
    const r = document.createElement('div'); r.id = 'flow-ring'; document.body.after(r);
  },
  logo(url) {  // an official logo supplied by the user (claude-logo.svg next to the recorder), in place of the spinner glyph
    this._logo = url; const img = `<img src="${url}" style="width:22px;height:22px;display:block">`;
    document.querySelector('#chat .hd .spark').innerHTML = img;
  },
  open() { document.body.classList.add('demo-split'); window.dispatchEvent(new Event('resize')); document.getElementById('chat').classList.add('in'); },
  focus() { document.querySelector('#chat .inp').classList.add('focus'); },
  type(t) { document.querySelector('#chat .inp .tx').innerHTML = ''; const s = document.createElement('span'); s.textContent = t;
    const cur = document.createElement('span'); cur.className = 'cur'; document.querySelector('#chat .inp .tx').append(s, cur); },
  send(text) {
    const b = document.querySelector('#chat .send'); b.classList.add('go'); setTimeout(() => b.classList.remove('go'), 300);
    const inp = document.querySelector('#chat .inp'); inp.classList.remove('focus');
    inp.querySelector('.tx').innerHTML = '<span class="ph">Reply to Claude...</span>';
    const m = document.createElement('div'); m.className = 'me'; m.textContent = text; this.log().append(m);
  },
  log() { return document.querySelector('#chat .log'); },
  think(text) { const d = document.createElement('div'); d.className = 'ai think';
    d.innerHTML = '<span class="g">✻</span><span class="w"></span>'; d.querySelector('.w').textContent = text;
    this.log().append(d); this._think = d; this.trim();
    const G = ['·', '✢', '✳', '✶', '✻', '✽', '✻', '✶', '✳', '✢'], hs = document.querySelector('#chat .hd .spark'); let i = 0;
    clearInterval(this._spin); this._spin = setInterval(() => { i = (i + 1) % G.length; const g = this._think && this._think.querySelector('.g');
      if (this._logo) { if (g) g.style.transform = `rotate(${i * 36}deg)`; return; }
      if (g) g.textContent = G[i]; hs.textContent = G[i]; }, 120);
    if (this._logo) d.querySelector('.g').innerHTML = `<img src="${this._logo}" style="width:16px;height:16px;display:block">`; },
  thinkText(text) { if (this._think) this._think.querySelector('.w').textContent = text; },
  chip(name, args) {
    const d = document.createElement('div'); d.className = 'chip';
    d.innerHTML = `<span class="oswx-cube"><i></i><i class="t"></i><i class="r"></i></span><span class="nm"></span><span class="ar"></span><span class="ok">✓</span>`;
    d.querySelector('.nm').textContent = name; d.querySelector('.ar').textContent = args;
    if (this._think) this._think.before(d); else this.log().append(d);
    this.trim(); this.flow(d); return d;
  },
  done() { document.querySelectorAll('#chat .chip:not(.done)').forEach((c) => c.classList.add('done')); },
  say(html) { if (this._think) { this._think.remove(); this._think = null; } clearInterval(this._spin); if (!this._logo) document.querySelector('#chat .hd .spark').textContent = '✻';
    const d = document.createElement('div'); d.className = 'ai msg'; d.innerHTML = html; this.log().append(d); this.trim(); },
  trim() { const log = this.log(); while (log.scrollHeight > log.clientHeight + 2 && log.children.length > 2) {
    const first = [...log.children].find((c) => !c.classList.contains('me')) || log.children[0]; first.remove(); } },
  fit(factor = 0.78) {  // fit the model, then zoom in a little closer than the app's own margin
    const vp = window.app.vp, h00 = vp.viewHeight; vp.zoomFit(false);  // (for the centre)
    const b = vp.bbox, diag = Math.hypot(b.max.x - b.min.x, b.max.y - b.min.y, b.max.z - b.min.z);
    const h1 = Math.max(diag / (vp.w / vp.h), diag * 0.62) * 1.08 / factor;  // fits at any orbit angle
    const h0 = this._h || h00, t0 = performance.now(); this._h = h1 * factor;
    vp.viewHeight = h0;
    vp.anim2 = (t) => { const k = Math.min(1, (t - t0) / 600), e = 1 - Math.pow(1 - k, 3);
      vp.viewHeight = h0 + (h1 * factor - h0) * e; vp._placeCamera(); vp._updateProjection(); if (k >= 1) vp.anim2 = null; };
  },
  flow(from) {  // glowing pulses from the chip into the middle of the model view
    const a = from.getBoundingClientRect(), g = document.getElementById('graphics').getBoundingClientRect();
    const x0 = a.left + 8, y0 = a.top + a.height / 2, x1 = g.left + g.width * 0.5, y1 = g.top + g.height * 0.48;
    const cx = (x0 + x1) / 2, cy = Math.min(y0, y1) - 160;
    for (let k = 0; k < 7; k++) {
      const d = document.createElement('div'); d.className = 'flow-dot'; document.body.after(d);
      const pts = []; for (let i = 0; i <= 24; i++) { const t = i / 24, u = 1 - t;
        pts.push({ left: `${u * u * x0 + 2 * u * t * cx + t * t * x1}px`, top: `${u * u * y0 + 2 * u * t * cy + t * t * y1}px`,
          opacity: t < 0.85 ? 1 - k * 0.12 : (1 - t) * 6, transform: `scale(${1 - k * 0.11})` }); }
      d.animate(pts, { duration: 650, delay: k * 28, easing: 'cubic-bezier(.45,0,.2,1)', fill: 'both' }).onfinish = () => d.remove();
    }
    const r = document.getElementById('flow-ring');
    r.style.left = `${g.left + 10}px`; r.style.top = `${g.top + 10}px`; r.style.width = `${g.width - 20}px`; r.style.height = `${g.height - 20}px`;
    r.animate([{ borderColor: 'rgba(255,74,99,0)', boxShadow: 'inset 0 0 0 rgba(255,74,99,0)' },
               { borderColor: 'rgba(255,74,99,.55)', boxShadow: 'inset 0 0 40px rgba(255,74,99,.25)' },
               { borderColor: 'rgba(255,74,99,0)', boxShadow: 'inset 0 0 0 rgba(255,74,99,0)' }],
              { duration: 700, delay: 520, easing: 'ease-out' });
  },
};

// ---------------------------------------------------------------- stage: two floating windows on a backdrop
// The chat opens alone, centred, with a greeting; once the prompt is sent it glides left and the openswx
// window floats in beside it. (The openswx page itself is the second window: <body> is framed and placed.)
Object.assign(CHAT, {
  BIG: { left: 460, top: 110, width: 1000, height: 860 },
  SIDE: { left: 64, top: 150, width: 560, height: 780 },
  APP: { left: 664, top: 108, width: 1170, height: 864 },
  stageCss() {
    const s = document.createElement('style');
    const A = this.APP;
    s.textContent = `
    html.stage{background:${DEMO.BG} fixed;overflow:hidden}
    html.stage body{position:fixed!important;left:${A.left}px;top:${A.top}px;width:${A.width}px!important;height:${A.height}px!important;
      border-radius:14px;overflow:hidden;box-shadow:0 1px 0 rgba(255,255,255,.6) inset,0 0 0 1px rgba(40,35,25,.10),0 30px 70px rgba(50,20,28,.20),0 8px 20px rgba(50,20,28,.08);
      opacity:.012;transform:translateX(90px) scale(.965);transition:opacity .7s ease,transform .9s cubic-bezier(.2,.8,.2,1)}
    html.stage body.shown{opacity:1;transform:none;animation:fl2 7s ease-in-out infinite}
    html.stage #chat{left:${this.BIG.left}px;top:${this.BIG.top}px;width:${this.BIG.width}px;height:${this.BIG.height}px;bottom:auto;right:auto;
      border:0;border-radius:16px;overflow:hidden;opacity:0;transform:translateY(24px) scale(.98);
      box-shadow:0 0 0 1px rgba(40,35,25,.10),0 30px 70px rgba(50,20,28,.18),0 8px 20px rgba(50,20,28,.07);
      transition:opacity .6s ease,transform .8s cubic-bezier(.2,.8,.2,1),left .9s cubic-bezier(.65,0,.25,1),top .9s cubic-bezier(.65,0,.25,1),
        width .9s cubic-bezier(.65,0,.25,1),height .9s cubic-bezier(.65,0,.25,1)}
    html.stage #chat.in{opacity:1;transform:none;animation:fl1 6s ease-in-out infinite}
    html.stage #chat.side{left:${this.SIDE.left}px;top:${this.SIDE.top}px;width:${this.SIDE.width}px;height:${this.SIDE.height}px}
    @keyframes fl1{50%{translate:0 -5px}}
    @keyframes fl2{50%{translate:0 4px}}
    #chat .greet{position:absolute;left:0;right:0;top:34%;display:flex;flex-direction:column;align-items:center;gap:18px;transition:opacity .4s,transform .5s}
    #chat .greet img,#chat .greet .gl{width:54px;height:54px;font-size:46px;color:#d97757;text-align:center;line-height:54px}
    #chat .greet h1{margin:0;font:400 38px/1.2 Georgia,'Times New Roman',serif;color:#1f1e1d;letter-spacing:-.3px}
    #chat.sent .greet{opacity:0;transform:translateY(-30px);pointer-events:none}
    html.stage #chat .inp{transition:margin .9s cubic-bezier(.65,0,.25,1),border-color .3s,box-shadow .3s}
    html.stage #chat:not(.side) .inp{margin:10px 90px 28px}
    #chat .hd{transition:opacity .5s}
    html.stage #chat:not(.sent) .hd{opacity:0}
    html.stage #flow-ring{display:none}
    .flow-dot{background:radial-gradient(circle,#fff 0%,#f3a27f 30%,rgba(217,119,87,0) 70%)!important;box-shadow:0 0 14px 4px rgba(217,119,87,.45)!important}`;
    document.head.append(s);
  },
  stage() {
    this.stageCss();
    document.documentElement.classList.add('stage');
    const log = this.log(), g = document.createElement('div'); g.className = 'greet';
    g.innerHTML = (this._logo ? `<img src="${this._logo}">` : '<div class="gl">✻</div>') + '<h1>What are we designing today?</h1>';
    document.getElementById('chat').append(g);
    window.dispatchEvent(new Event('resize'));
  },
  showChat() { document.getElementById('chat').classList.add('in'); },
  sent() { document.getElementById('chat').classList.add('sent'); },
  split() {
    document.getElementById('chat').classList.add('side');
    setTimeout(() => document.body.classList.add('shown'), 250);
  },
});
