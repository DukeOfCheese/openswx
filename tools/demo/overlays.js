// Title cards, lockup, captions, cursor and camera orbit for the README demo (light, crisp, launch-video style).
window.DEMO = {
  BG: 'radial-gradient(900px 620px at 22% 18%,rgba(227,50,75,.10) 0%,rgba(227,50,75,0) 60%),' +  // openswx red, faint
      'radial-gradient(1000px 700px at 85% 85%,rgba(141,11,31,.07) 0%,rgba(141,11,31,0) 65%),' +
      'linear-gradient(165deg,#fbfbfc 0%,#f0f1f3 50%,#e4e6ea 100%)',
  css() {
    const s = document.createElement('style');
    s.textContent = `
    #demo-card{position:fixed;inset:0;z-index:100000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;
      background:${this.BG};color:#14171c;font-family:'Segoe UI Variable Display','Segoe UI',system-ui,sans-serif;transition:opacity .6s ease}
    #demo-card.out{opacity:0}
    #demo-card{animation:dcPush 6s linear both}
    @keyframes dcPush{from{background-size:100% 100%}to{background-size:112% 112%}}
    #demo-card .scene>*{animation-fill-mode:both}
    #demo-card .scene:not(.gone):not(.wait){animation:dcZoom 5s cubic-bezier(.3,0,.3,1) both}
    @keyframes dcZoom{from{scale:1}to{scale:1.05}}
    #demo-card .scene{display:flex;flex-direction:column;align-items:center;gap:22px;position:absolute;transition:opacity .5s ease,transform .7s cubic-bezier(.2,.8,.2,1)}
    #demo-card .scene.gone{opacity:0;transform:translateY(-18px) scale(.98)}
    #demo-card .scene.wait{opacity:0;transform:translateY(18px) scale(.98)}
    .dc-logo{display:flex;align-items:center;gap:26px;animation:dcRise .9s cubic-bezier(.2,.8,.2,1) both}
    .cube{position:relative;display:inline-block;flex:none}
    .cube i{position:absolute;inset:0;clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%);background:linear-gradient(135deg,#ff4a63,#b0102a)}
    .cube .t{clip-path:polygon(50% 0,100% 25%,50% 50%,0 25%);background:#ff6b80}
    .cube .r{clip-path:polygon(100% 25%,100% 75%,50% 100%,50% 50%);background:#8d0b1f}
    .dc-logo .cube{width:104px;height:104px}
    .dc-name{font-size:96px;font-weight:600;letter-spacing:-2px;line-height:1}
    .dc-name span{color:#d61f3c}
    .pill{display:inline-flex;align-items:center;gap:12px;padding:10px 22px;border-radius:999px;background:rgba(255,255,255,.55);
      border:1px solid rgba(255,255,255,.9);box-shadow:0 8px 30px rgba(60,20,30,.10),0 1px 0 rgba(255,255,255,.8) inset;
      backdrop-filter:blur(12px);font-size:22px;color:#3b4350;animation:dcRise .8s .35s cubic-bezier(.2,.8,.2,1) both}
    .lockup{font-size:40px;font-weight:600;color:#14171c;padding:22px 40px;gap:26px}
    .lockup .cube{width:46px;height:46px}
    .lockup img{width:46px;height:46px}
    .lockup .sep{width:1.5px;height:48px;background:rgba(20,23,28,.18)}
    .lockup .it{display:flex;align-items:center;gap:16px}
    .lockup .team{display:flex}
    .lockup .team i{width:34px;height:34px;border-radius:50%;border:3px solid #fff;margin-left:-12px;display:grid;place-items:center;
      font:700 14px 'Segoe UI',system-ui,sans-serif;color:#fff;font-style:normal;box-shadow:0 2px 6px rgba(40,20,30,.18)}
    .lockup .team i:first-child{margin-left:0}
    @keyframes dcRise{from{opacity:0;transform:translateY(22px);filter:blur(4px)}to{opacity:1;transform:none;filter:none}}
    #demo-cc{position:fixed;left:0;right:0;bottom:30px;z-index:100001;text-align:center;pointer-events:none}
    #demo-cc span{display:inline-block;padding:9px 20px;border-radius:12px;background:rgba(20,23,28,.82);color:#fff;border-bottom:2px solid #e3324b;
      font:500 24px 'Segoe UI Variable Display','Segoe UI',system-ui,sans-serif;letter-spacing:-.2px;backdrop-filter:blur(8px);
      transition:opacity .35s ease,transform .45s cubic-bezier(.2,.8,.2,1)}
    #demo-cc span.off{opacity:0;transform:translateY(10px)}
    #demo-cursor{position:fixed;z-index:100002;width:26px;height:26px;pointer-events:none;left:0;top:0;opacity:0;
      transition:opacity .3s, left 1s cubic-bezier(.45,0,.2,1), top 1s cubic-bezier(.45,0,.2,1); filter:drop-shadow(0 2px 3px rgba(0,0,0,.3))}
    #demo-cursor.click{transform:scale(.85)}
    .toast{display:none!important}`;
    document.head.append(s);
  },
  card(logo) {  // the openswx title, then the "openswx | Claude | your team" lockup
    const d = document.createElement('div'); d.id = 'demo-card';
    const cube = '<span class="cube"><i></i><i class="t"></i><i class="r"></i></span>';
    d.innerHTML = `<div class="scene s1"><div class="dc-logo">${cube}<div class="dc-name">open<span>swx</span></div></div>
        <div class="pill">Open-source parametric CAD&nbsp;&nbsp;·&nbsp;&nbsp;MCP server built in</div></div>
      <div class="scene s2 wait"><div class="pill lockup"><span class="it">${cube}openswx</span><span class="sep"></span>
        <span class="it">${logo ? `<img src="${logo}">` : '<span style="color:#d97757">✻</span>'}Claude</span><span class="sep"></span>
        <span class="it t3"><span class="team"><i style="background:#3f7be0">S</i><i style="background:#2e9e6a">M</i><i style="background:#e08a2e">A</i></span>your team</span></div></div>`;
    document.getElementById('demo-card')?.remove(); document.body.after(d);
  },
  lockup() { const d = document.getElementById('demo-card'); d.querySelector('.s1').classList.add('gone'); d.querySelector('.s2').classList.remove('wait'); },
  endCard(line1, line2) {
    const d = document.createElement('div'); d.id = 'demo-card'; d.style.opacity = 0;
    const cube = '<span class="cube"><i></i><i class="t"></i><i class="r"></i></span>';
    d.innerHTML = `<div class="scene"><div class="dc-logo">${cube}<div class="dc-name">open<span>swx</span></div></div>
      <div class="pill">${line1}</div><div class="pill" style="animation-delay:.6s;font-size:20px">${line2}</div></div>`;
    document.body.after(d); requestAnimationFrame(() => { d.style.opacity = 1; });
  },
  cardOut() { const d = document.getElementById('demo-card'); d.classList.add('out'); setTimeout(() => d.remove(), 700); },
  cc(text) {  // a caption at the bottom, one short line per scene
    let d = document.getElementById('demo-cc');
    if (!d) { d = document.createElement('div'); d.id = 'demo-cc'; d.innerHTML = '<span class="off"></span>'; document.body.after(d); }
    const s = d.querySelector('span');
    if (!text) { s.classList.add('off'); return; }
    s.classList.add('off');
    setTimeout(() => { s.textContent = text; s.classList.remove('off'); }, s.textContent ? 300 : 0);
  },
  cursor(x, y, show = true) {
    let c = document.getElementById('demo-cursor');
    if (!c) { c = document.createElement('div'); c.id = 'demo-cursor';
      c.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2l15 11-6.5 1.2L16 21l-3 1.4-3.6-6.9L4 20z" fill="#111" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
      document.body.after(c); }
    c.style.left = `${x}px`; c.style.top = `${y}px`; c.style.opacity = show ? 1 : 0;
  },
  click() { const c = document.getElementById('demo-cursor'); c.classList.add('click'); setTimeout(() => c.classList.remove('click'), 160); },
  orbit(speed) {  // degrees per second around the vertical axis
    const vp = window.app.vp; this.orbitSpeed = speed;
    if (this.orbiting) return; this.orbiting = true;
    let a = 0, last = performance.now();
    const tick = (t) => {
      a += Math.min(t - last, 1000 / 30) / 1000 * this.orbitSpeed * Math.PI / 180; last = t;  // (a stalled frame pauses, never jumps)
      const r = Math.hypot(0.8, 1.0), a0 = Math.atan2(0.8, 1.0) + a;
      vp._setView(null, false, [r * Math.sin(a0), 0.6, r * Math.cos(a0)], [0, 1, 0]);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  },
};
