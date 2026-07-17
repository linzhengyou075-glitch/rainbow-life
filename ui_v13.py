"""Rainbow Life V13 single UI/theme source.

This module intentionally replaces all historical V6-V12 CSS layers. Frontend and
admin import the same palette, animations, and component primitives so old rules
cannot override new screens.
"""

BUILD_ID = "V16-phase2-full-brand-ui-20260714"

THEME_JS = r"""
(function(){
  const body=document.body;
  const allowed=new Set([
    'auto','rainbow-starry','spring-day','spring-night','summer-day','summer-night',
    'autumn-day','autumn-night','winter-day','winter-night',
    'newyear','valentine','pride','halloween','christmas'
  ]);
  function cookie(name){
    const hit=document.cookie.split(';').map(v=>v.trim()).find(v=>v.startsWith(name+'='));
    return hit?decodeURIComponent(hit.slice(name.length+1)):'';
  }
  function taipeiParts(){
    return new Intl.DateTimeFormat('en-US',{
      timeZone:'Asia/Taipei',month:'numeric',day:'numeric',hour:'numeric',hour12:false
    }).formatToParts(new Date());
  }
  function read(type,parts){return Number((parts.find(x=>x.type===type)||{}).value||0)}
  function automaticTheme(){
    const p=taipeiParts(),m=read('month',p),d=read('day',p),h=read('hour',p);
    const season='rainbow';
    const period='starry';
    let festival='';
    if((m===1&&d<=7)||(m===2&&d<=7))festival='newyear';
    else if(m===2&&d>=10&&d<=16)festival='valentine';
    else if(m===6)festival='pride';
    else if(m===10&&d>=24)festival='halloween';
    else if(m===12&&d>=15)festival='christmas';
    return {season,period,festival};
  }
  function requestedTheme(){
    const fromBody=(body&&body.dataset.themeChoice)||'';
    const fromCookie=cookie('rb_theme');
    const value=(fromBody||fromCookie||'auto').toLowerCase();
    return allowed.has(value)?value:'auto';
  }
  function apply(){
    if(!body)return;
    const auto=automaticTheme();
    const selected=requestedTheme();
    let season=auto.season,period=auto.period,festival=auto.festival;
    if(selected.includes('-')){
      [season,period]=selected.split('-'); festival='';
    }else if(selected!=='auto'){
      festival=selected;
    }
    [...body.classList].filter(x=>x.startsWith('theme-')||x.startsWith('festival-')).forEach(x=>body.classList.remove(x));
    body.classList.add('theme-'+season+'-'+period);
    if(festival)body.classList.add('festival-'+festival);
    body.dataset.season=season;
    body.dataset.period=period;
    body.dataset.festival=festival;
    body.dataset.themeResolved=festival||season+'-'+period;
    window.dispatchEvent(new CustomEvent('rb-theme-applied',{detail:{selected,season,period,festival,resolved:body.dataset.themeResolved}}));
  }
  function reducedMotion(){return !!(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches)}
  function seedParticles(){
    if(reducedMotion()||document.querySelector('.theme-effects'))return;
    const layer=document.createElement('div');
    layer.className='theme-effects';
    for(let i=0;i<18;i++){
      const span=document.createElement('i');
      span.style.setProperty('--x',(Math.random()*100)+'vw');
      span.style.setProperty('--delay',(-Math.random()*14)+'s');
      span.style.setProperty('--duration',(9+Math.random()*12)+'s');
      span.style.setProperty('--size',(12+Math.random()*18)+'px');
      layer.appendChild(span);
    }
    document.body.appendChild(layer);
  }
  function animateMeters(){
    const meters=document.querySelectorAll('.home-exp-track i,.me-exp-track i,.luck-fill,.progress span,.neon-level span,.classic-exp-fill,.panel-progress-fill');
    meters.forEach((el,index)=>{
      const raw=el.dataset.progress||el.style.width||getComputedStyle(el).width;
      let target=parseFloat(raw);
      if(!Number.isFinite(target))return;
      target=Math.max(0,Math.min(100,target));
      el.style.setProperty('--meter-target',target+'%');
      el.style.width=reducedMotion()?target+'%':'0%';
      if(!reducedMotion())requestAnimationFrame(()=>setTimeout(()=>{el.style.width=target+'%';el.classList.add('meter-ready')},90+index*45));
    });
  }
  function revealCards(){
    const selector='.home-player-card,.home-announcement-card,.home-theme-card,.home-quick-card,.home-sign-card,.home-vip-note,.me-profile-card,.me-card,.me-assets-grid,.notice-center-hero,.notice-center-item,.card,.classic-card,.phase3-shop-card,.phase3-rank-row';
    const nodes=[...document.querySelectorAll(selector)].filter((el,i,a)=>a.indexOf(el)===i);
    nodes.forEach((el,i)=>{el.classList.add('phase4d-reveal');el.style.setProperty('--reveal-delay',Math.min(i,9)*55+'ms')});
    if(reducedMotion()||!('IntersectionObserver' in window)){nodes.forEach(el=>el.classList.add('is-visible'));return}
    const io=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('is-visible');io.unobserve(entry.target)}}),{threshold:.08,rootMargin:'0px 0px -25px'});
    nodes.forEach(el=>io.observe(el));
  }
  function themePulse(){
    if(reducedMotion()||!body)return;
    body.classList.remove('phase4d-theme-pulse');void body.offsetWidth;body.classList.add('phase4d-theme-pulse');
    setTimeout(()=>body.classList.remove('phase4d-theme-pulse'),650);
  }
  function countUp(){
    if(reducedMotion())return;
    document.querySelectorAll('[data-count],.ach-summary b,.shop6-wallet b').forEach(el=>{
      const text=(el.dataset.count||el.textContent||'').replace(/,/g,'').trim();
      const target=Number(text.replace(/[^0-9.-]/g,''));
      if(!Number.isFinite(target)||target<0||el.dataset.counted==='1')return;
      el.dataset.counted='1'; const suffix=(el.textContent.match(/[^0-9,.-]+$/)||[''])[0];
      const start=performance.now(),duration=650;
      const tick=now=>{const t=Math.min(1,(now-start)/duration),v=Math.round(target*(1-Math.pow(1-t,3)));el.textContent=v.toLocaleString()+suffix;if(t<1)requestAnimationFrame(tick)};
      requestAnimationFrame(tick);
    });
  }
  function enableTilt(){
    if(reducedMotion()||!window.matchMedia('(hover:hover)').matches)return;
    document.querySelectorAll('.home-player-card,.me-profile-card,.ach-card,.shop6-card,.classic-card').forEach(card=>{
      card.classList.add('v15-tilt');
      card.addEventListener('pointermove',e=>{const r=card.getBoundingClientRect(),x=(e.clientX-r.left)/r.width-.5,y=(e.clientY-r.top)/r.height-.5;card.style.setProperty('--rx',(-y*3.2)+'deg');card.style.setProperty('--ry',(x*4.2)+'deg')});
      card.addEventListener('pointerleave',()=>{card.style.setProperty('--rx','0deg');card.style.setProperty('--ry','0deg')});
    });
  }
  function celebrate(){
    if(reducedMotion())return;
    const params=new URLSearchParams(location.search),notice=document.querySelector('.ach-notice,.notice,.flash');
    const should=params.get('celebrate')==='1'||(notice&&/已領取|恭喜|JACKPOT|升級/.test(notice.textContent||''));
    if(!should)return;
    const layer=document.createElement('div');layer.className='v15-confetti';
    const icons=['🌈','✨','⭐','🎉','💎'];
    for(let i=0;i<30;i++){const n=document.createElement('i');n.textContent=icons[i%icons.length];n.style.setProperty('--cx',(Math.random()*100)+'vw');n.style.setProperty('--cd',(Math.random()*.8)+'s');n.style.setProperty('--cr',((Math.random()*540)-270)+'deg');layer.appendChild(n)}
    document.body.appendChild(layer);setTimeout(()=>layer.remove(),2600);
    if(navigator.vibrate)navigator.vibrate([30,20,60]);
  }
  function pageTransitions(){
    if(reducedMotion())return;
    document.body.classList.add('v15-page-ready');
    document.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(!a||a.target==='_blank'||a.hasAttribute('download')||a.href.startsWith('javascript:')||a.origin!==location.origin)return;document.body.classList.add('v15-page-leave')},{passive:true});
  }
  window.RainbowTheme={apply,automaticTheme,set(value){if(!allowed.has(value))value='auto';document.cookie='rb_theme='+encodeURIComponent(value)+';path=/;max-age=31536000;samesite=lax';if(body)body.dataset.themeChoice=value;apply();themePulse();}};
  document.addEventListener('click',e=>{const el=e.target.closest('[data-theme-preview]');if(!el)return;window.RainbowTheme.set(el.dataset.themePreview||'auto');});
  apply(); seedParticles(); animateMeters(); revealCards(); countUp(); enableTilt(); celebrate(); pageTransitions();
  setInterval(apply,60000);
  window.addEventListener('storage',apply);
  document.addEventListener('pointerdown',e=>{
    const el=e.target.closest('a,button,.tile,.panel-link,.classic-quick a');
    if(!el)return;
    el.classList.remove('v13-tap'); void el.offsetWidth; el.classList.add('v13-tap');
    setTimeout(()=>el.classList.remove('v13-tap'),320);
  },{passive:true});
})();
"""

CSS = r"""
:root{
 --page:#f6fbff;--page2:#eef7ff;--surface:rgba(255,255,255,.90);--surface2:#fff;
 --text:#183449;--muted:#617b8b;--accent:#159ac7;--accent2:#58d9ff;--line:#9bddec;
 --soft:#dcf5fb;--good:#168a66;--danger:#c83c5b;--shadow:0 14px 34px rgba(20,75,105,.14);
 --rainbow:linear-gradient(90deg,#ff62ad,#ffad56,#ffe46b,#61dc91,#4acfff,#8d72ff,#e56bd9);
 --decor:'🌈';--season:'夏海';--period:'白天';--particle:'🫧';
}
body.theme-spring-day{--page:#fff8fb;--page2:#f3fff2;--surface:rgba(255,255,255,.91);--text:#4b2940;--muted:#806477;--accent:#df5aa6;--accent2:#ff9cca;--line:#efbfdc;--soft:#fde7f3;--decor:'🌸';--season:'春櫻';--period:'白天';--particle:'🌸'}
body.theme-spring-night{--page:#17102c;--page2:#26153c;--surface:rgba(38,22,61,.90);--surface2:#24173a;--text:#fff6fc;--muted:#d9bfd3;--accent:#bd57e6;--accent2:#ff75bd;--line:#8c579c;--soft:#362047;--decor:'🌸';--season:'夜櫻';--period:'夜晚';--particle:'✨'}
body.theme-summer-day{--page:#f1fcff;--page2:#eaf9ff;--surface:rgba(255,255,255,.90);--text:#123f50;--muted:#537986;--accent:#169ec9;--accent2:#54ddff;--line:#91dcea;--soft:#dff7fb;--decor:'🌊';--season:'夏海';--period:'白天';--particle:'🫧'}
body.theme-summer-night{--page:#07162d;--page2:#0c2440;--surface:rgba(13,35,62,.91);--surface2:#102640;--text:#f3fcff;--muted:#b5d0dc;--accent:#3976d8;--accent2:#42ddff;--line:#34799a;--soft:#15314e;--decor:'🌊';--season:'星海';--period:'夜晚';--particle:'✨'}
body.theme-autumn-day{--page:#fff8ed;--page2:#fff1d8;--surface:rgba(255,255,255,.91);--text:#573317;--muted:#826548;--accent:#dc7b1c;--accent2:#ffba54;--line:#edc07b;--soft:#fff0d5;--decor:'🍁';--season:'秋楓';--period:'白天';--particle:'🍂'}
body.theme-autumn-night{--page:#1b1017;--page2:#2d1720;--surface:rgba(45,24,34,.91);--surface2:#301b26;--text:#fff8e9;--muted:#dbc1a7;--accent:#bd6230;--accent2:#ffad43;--line:#8d5141;--soft:#41252b;--decor:'🍁';--season:'秋楓';--period:'夜晚';--particle:'🍂'}
body.theme-winter-day{--page:#f7fcff;--page2:#edf8ff;--surface:rgba(255,255,255,.92);--text:#254353;--muted:#607b8a;--accent:#469fce;--accent2:#8ae0ff;--line:#acdceb;--soft:#e3f5fb;--decor:'❄️';--season:'冬雪';--period:'白天';--particle:'❄️'}
body.theme-winter-night{--page:#081526;--page2:#10233d;--surface:rgba(15,32,55,.92);--surface2:#10243d;--text:#f5fbff;--muted:#b8cad8;--accent:#5b75d7;--accent2:#72d9ff;--line:#476d92;--soft:#182e49;--decor:'❄️';--season:'極光雪夜';--period:'夜晚';--particle:'❄️'}
body.festival-christmas{--decor:'🎄';--accent:#d43c54;--accent2:#38a56b}
body.festival-halloween{--decor:'🎃';--accent:#e77c21;--accent2:#8e55d8}
body.festival-valentine{--decor:'💖';--accent:#e84e91;--accent2:#ff99c1}
body.festival-pride{--decor:'🌈';--accent:#8757e8;--accent2:#2bcbd9}
body.festival-newyear{--decor:'🧧';--accent:#d82936;--accent2:#f3b63d;--particle:'✨'}
*{box-sizing:border-box}html{min-height:100%;background:var(--page)}body{margin:0;min-height:100vh;font-family:system-ui,-apple-system,'Noto Sans TC',sans-serif;color:var(--text);background:linear-gradient(150deg,var(--page),var(--page2));transition:background .45s,color .45s;overflow-x:hidden}
body:before{content:'';position:fixed;inset:0;pointer-events:none;z-index:-2;background:radial-gradient(circle at 12% 8%,color-mix(in srgb,var(--accent2) 22%,transparent),transparent 28%),radial-gradient(circle at 90% 20%,color-mix(in srgb,var(--accent) 20%,transparent),transparent 32%),radial-gradient(circle at 40% 95%,color-mix(in srgb,#ff73c5 14%,transparent),transparent 28%);animation:v13Aurora 10s ease-in-out infinite alternate}
body:after{content:var(--particle);position:fixed;left:5%;top:13%;font-size:22px;opacity:.34;pointer-events:none;z-index:-1;filter:drop-shadow(0 0 8px var(--accent2));animation:v13Particle 12s linear infinite}

.theme-effects{position:fixed;inset:0;z-index:-1;pointer-events:none;overflow:hidden}
.theme-effects i{position:absolute;left:var(--x);top:-12vh;width:var(--size);height:var(--size);display:block;opacity:.52;animation:themeFall var(--duration) linear infinite;animation-delay:var(--delay);filter:drop-shadow(0 0 8px color-mix(in srgb,var(--accent2) 50%,transparent))}
.theme-effects i:before{content:var(--particle);font-style:normal;font-size:var(--size)}
body[data-period='night'] .theme-effects i{opacity:.72;animation-name:themeTwinkleFall}
body.festival-christmas .theme-effects i:before{content:'❄️'}
body.festival-halloween .theme-effects i:before{content:'🎃'}
body.festival-valentine .theme-effects i:before{content:'💗'}
body.festival-pride .theme-effects i:before{content:'✨'}
body.festival-newyear .theme-effects i:before{content:'✨'}
body.festival-newyear:before{background:radial-gradient(circle at 20% 15%,rgba(255,210,76,.30),transparent 23%),radial-gradient(circle at 80% 18%,rgba(255,69,88,.24),transparent 28%),linear-gradient(145deg,var(--page),var(--page2))}
body.festival-christmas:before{background:radial-gradient(circle at 15% 10%,rgba(255,255,255,.32),transparent 22%),radial-gradient(circle at 90% 15%,rgba(50,180,110,.20),transparent 28%),linear-gradient(145deg,var(--page),var(--page2))}
body.festival-halloween:before{background:radial-gradient(circle at 20% 15%,rgba(255,126,33,.24),transparent 25%),radial-gradient(circle at 82% 20%,rgba(134,76,218,.23),transparent 28%),linear-gradient(145deg,var(--page),var(--page2))}
body.festival-valentine:before{background:radial-gradient(circle at 18% 12%,rgba(255,100,160,.25),transparent 26%),radial-gradient(circle at 82% 18%,rgba(255,184,216,.25),transparent 28%),linear-gradient(145deg,var(--page),var(--page2))}

a{text-decoration:none;color:inherit}button,input,textarea,select{font:inherit}.top{position:sticky;top:0;z-index:20;padding:16px 18px 26px;color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent2));box-shadow:0 8px 24px color-mix(in srgb,var(--accent) 30%,transparent)}.top h1{margin:0;font-size:21px}.wrap{max-width:760px;margin:-13px auto 0;padding:0 13px 104px}.login{min-height:100vh;display:grid;place-items:center;padding:20px}
.card,.classic-card,.panel-section,.panel-banner,.tile,.stat,.fortune-item,.item-card,.summary-item,.feature-tile,.chat-stat{position:relative;background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:22px;box-shadow:var(--shadow);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);transition:.25s ease;overflow:hidden}.card,.classic-card,.panel-section{padding:17px;margin-bottom:14px}.card:before,.classic-card:before,.panel-section:before,.panel-banner:before{content:var(--decor);position:absolute;right:10px;top:7px;font-size:22px;opacity:.72;filter:drop-shadow(0 2px 6px color-mix(in srgb,var(--accent) 45%,transparent))}.card:after,.classic-card:after,.panel-section:after,.panel-banner:after{content:'';position:absolute;inset:0;border-radius:inherit;padding:1px;background:var(--rainbow);background-size:260% 100%;animation:v13Border 7s linear infinite;pointer-events:none;opacity:.42;-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude}
.muted,.welcome-note,.item-main p,.empty,small{color:var(--muted)}.badge,.mini-pill,.panel-pill,.profile-chip,.tab{display:inline-flex;align-items:center;padding:5px 10px;border-radius:999px;background:var(--soft);color:var(--text);border:1px solid var(--line);font-size:12px;font-weight:800}.notice{padding:12px 14px;border-radius:15px;background:color-mix(in srgb,var(--soft) 75%,var(--surface));border:1px solid var(--line);color:var(--text);margin-bottom:14px}.btn,.admin,.action,.shop-buy,.panel-btn,.save-btn,.profile-action{border:0;border-radius:15px;padding:12px 15px;background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff!important;font-weight:900;cursor:pointer;box-shadow:0 8px 20px color-mix(in srgb,var(--accent) 30%,transparent);transition:.18s}.btn-soft{background:var(--soft)!important;color:var(--text)!important;border:1px solid var(--line)}.v13-tap{animation:v13Tap .32s ease}.progress,.neon-level,.classic-exp-track,.panel-progress-track,.luck-track{height:12px;background:var(--soft);border:1px solid var(--line);border-radius:999px;overflow:hidden}.progress span,.neon-level span,.classic-exp-fill,.panel-progress-fill,.luck-fill{display:block;height:100%;background:var(--rainbow);background-size:260% 100%;animation:v13Bar 3s linear infinite;box-shadow:0 0 12px var(--accent2)}
.stats,.classic-stats,.panel-metrics{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.stat,.classic-stat,.panel-metric{padding:14px;text-align:center;background:color-mix(in srgb,var(--surface) 90%,var(--soft));border:1px solid var(--line);border-radius:18px;color:var(--text)}.stat b,.classic-stat b,.panel-metric b{display:block;font-size:21px;margin-top:5px}.menu,.quick,.classic-quick-grid,.panel-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.tile,.quick a,.classic-quick a,.panel-link{display:flex;align-items:center;gap:10px;padding:14px;background:color-mix(in srgb,var(--surface) 88%,var(--soft));border:1px solid var(--line);border-radius:17px;color:var(--text);min-height:62px}.ico,.qico,.panel-icon{font-size:25px;filter:drop-shadow(0 0 7px color-mix(in srgb,var(--accent) 45%,transparent));animation:v13Bob 3s ease-in-out infinite}.bottom,.classic-bottom,.mobile-bottom{position:fixed;z-index:50;bottom:0;left:0;right:0;display:grid;grid-template-columns:repeat(5,1fr);padding:8px 5px max(8px,env(safe-area-inset-bottom));background:color-mix(in srgb,var(--surface2) 93%,transparent);border-top:1px solid var(--line);box-shadow:0 -8px 24px rgba(0,0,0,.12);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)}.bottom a,.classic-bottom a,.mobile-bottom a{text-align:center;font-size:11px;color:var(--muted);padding:3px}.bottom span,.classic-bottom span,.mobile-bottom span{display:block;font-size:21px;margin-bottom:2px}.bottom .on,.classic-bottom .on,.mobile-bottom .on{color:var(--accent);font-weight:900;text-shadow:0 0 12px color-mix(in srgb,var(--accent2) 60%,transparent)}
/* classic/player compatibility */
.classic-shell{max-width:760px;margin:auto;padding:14px 13px 108px}.classic-title{text-align:center;font-size:26px;font-weight:950;margin:8px 0 18px;color:var(--text)}.classic-hero,.classic-level,.classic-quick,.classic-sign{padding:20px}.classic-profile,.hero,.panel-user{display:flex;align-items:center;gap:15px}.classic-avatar,.avatar,.panel-avatar,.neon-avatar,.card-avatar{width:96px;height:96px;flex:0 0 96px;border-radius:50%;display:grid;place-items:center;font-size:48px;background:var(--surface2);border:5px solid transparent;background-image:linear-gradient(var(--surface2),var(--surface2)),conic-gradient(#ff62ad,#ffad56,#ffe46b,#61dc91,#4acfff,#8d72ff,#e56bd9,#ff62ad);background-origin:border-box;background-clip:padding-box,border-box;box-shadow:0 0 24px color-mix(in srgb,var(--accent) 45%,transparent);animation:v13Avatar 3.5s ease-in-out infinite}.classic-name,.panel-name{font-size:29px;font-weight:950}.classic-badges,.hero-meta,.panel-sub{display:flex;gap:7px;flex-wrap:wrap;margin:8px 0}.classic-level-head,.classic-exp-meta,.summary-row,.rank-row,.task-row,.settings-list a,.settings-static{display:flex;justify-content:space-between;gap:12px;align-items:center}.classic-exp-meta{font-size:12px;color:var(--muted);margin:10px 0 6px}.classic-stat .ico{font-size:30px}.classic-sign{display:flex;justify-content:space-between;align-items:center;color:var(--good);font-weight:900}.classic-admin{display:block;text-align:center;margin-bottom:16px}.panel-banner{padding:20px}.panel-strip,.achievement-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.panel-mini,.achievement-badge{padding:10px 5px;text-align:center;border-radius:15px;background:var(--soft);border:1px solid var(--line)}.section-head,.panel-title{display:flex;justify-content:space-between;align-items:center;margin:15px 2px 9px}.summary-list{display:grid;gap:7px}.summary-row{padding:10px 0;border-bottom:1px solid var(--line)}.tabs{display:flex;gap:8px;overflow:auto}.item-card{display:flex;gap:12px;align-items:center}.item-icon{width:52px;height:52px;flex:0 0 auto;display:grid;place-items:center;border-radius:16px;background:var(--soft);font-size:27px}.item-main{min-width:0;flex:1}.item-main h3{margin:0 0 5px}.qty{font-weight:900;color:var(--accent)}.rank-row{display:grid;grid-template-columns:38px 1fr auto;padding:11px 0;border-bottom:1px solid var(--line)}.rank-me{background:var(--soft);border-radius:13px;padding:11px 8px}.empty{text-align:center;padding:24px}.fortune-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.fortune-item{padding:14px;min-height:104px}.wheel-meta{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.wheel-meta div,.history-item,.result,.reward-strip{background:var(--surface);border:1px solid var(--line);border-radius:16px;color:var(--text)}.field label{display:block;font-size:13px;font-weight:900;margin:10px 0 5px}.field input,.field textarea,.field select,input,textarea,select{width:100%;padding:12px 13px;border-radius:13px;border:1px solid var(--line);background:var(--surface2);color:var(--text)}

/* V14 homepage: player card, announcement carousel and 3x3 quick grid */
.home-v14-page{background:linear-gradient(160deg,color-mix(in srgb,var(--page) 94%,#fff4e7),var(--page2))}
.home-v14-shell{max-width:760px;margin:0 auto;padding:12px 12px 118px}
.home-brandbar{display:flex;align-items:center;justify-content:space-between;padding:8px 5px 14px;color:var(--text)}
.home-brandbar>div{display:grid;grid-template-columns:auto 1fr;column-gap:8px;align-items:center}.home-brandbar b{font-size:20px;font-weight:950}.home-brandbar small{grid-column:2;font-size:9px;letter-spacing:1.6px;color:var(--muted)}.home-brandbar>a{width:42px;height:42px;display:grid;place-items:center;border:1px solid var(--line);border-radius:50%;background:var(--surface);box-shadow:var(--shadow)}.home-brand-rainbow{grid-row:1/3;font-size:31px}
.home-player-card,.home-announcement-card,.home-quick-card,.home-sign-card,.home-vip-note{position:relative;border:1px solid color-mix(in srgb,var(--line) 82%,#efc7a8);border-radius:23px;background:color-mix(in srgb,var(--surface) 94%,#fff8ee);box-shadow:var(--shadow);overflow:hidden;margin-bottom:13px;backdrop-filter:blur(15px)}
.home-player-card:after,.home-announcement-card:after,.home-quick-card:after{content:'';position:absolute;inset:0;border-radius:inherit;padding:1px;background:var(--rainbow);opacity:.28;pointer-events:none;-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude}
.home-player-card{padding:17px;background:linear-gradient(135deg,color-mix(in srgb,var(--surface) 90%,#fff0f4),color-mix(in srgb,var(--surface) 92%,var(--soft)))}
.home-player-top{display:flex;gap:13px;align-items:center;position:relative}.home-avatar{width:82px;height:82px;flex:0 0 82px;border-radius:50%;display:grid;place-items:center;overflow:hidden;font-size:38px;border:4px solid transparent;background-image:linear-gradient(var(--surface2),var(--surface2)),var(--rainbow);background-origin:border-box;background-clip:padding-box,border-box;box-shadow:0 8px 22px color-mix(in srgb,var(--accent) 24%,transparent)}.home-avatar img{width:100%;height:100%;object-fit:cover}.home-player-main{min-width:0;padding-right:82px}.home-player-name{font-size:24px;font-weight:950;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.home-player-name span{font-size:15px;color:var(--accent)}.home-player-meta,.home-player-title{font-size:12px;color:var(--muted);margin-top:4px}.home-player-meta{display:flex;gap:7px;flex-wrap:wrap}.home-player-meta span,.home-vip-chip{padding:4px 8px;border-radius:999px;background:var(--soft);border:1px solid var(--line);font-weight:850;color:var(--text)}.home-vip-chip{position:absolute;right:0;top:0;font-size:11px}.home-exp-head,.home-exp-foot{display:flex;justify-content:space-between;gap:10px;font-size:11px;margin-top:14px;color:var(--muted)}.home-exp-head b{color:var(--text)}.home-exp-track{height:11px;margin-top:6px;border-radius:999px;background:var(--soft);border:1px solid var(--line);overflow:hidden}.home-exp-track i{display:block;height:100%;border-radius:inherit;background:var(--rainbow);background-size:240% 100%;animation:v13Bar 3s linear infinite;box-shadow:0 0 12px var(--accent2)}.home-exp-foot{margin-top:5px}
.home-section-title{display:flex;align-items:center;justify-content:space-between;padding:15px 16px 10px;font-weight:950}.home-section-title small{font-size:11px;color:var(--muted)}.home-announcement-stage{position:relative;min-height:105px;margin:0 12px}.home-announcement-slide{position:absolute;inset:0;display:grid;grid-template-columns:42px 1fr auto;gap:11px;align-items:center;padding:12px;border:1px solid var(--line);border-radius:17px;background:color-mix(in srgb,var(--soft) 58%,var(--surface));opacity:0;transform:translateX(18px);pointer-events:none;transition:.35s ease}.home-announcement-slide.is-active{opacity:1;transform:none;pointer-events:auto}.announcement-icon{font-size:29px}.announcement-text{min-width:0}.announcement-text b{font-size:15px}.announcement-text p{margin:5px 0 0;font-size:12px;line-height:1.55;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.announcement-tag{font-size:10px;font-weight:900;color:#fff;background:linear-gradient(90deg,var(--accent),var(--accent2));padding:5px 7px;border-radius:8px}.home-announcement-dots{display:flex;justify-content:center;gap:6px;padding:10px 0 13px}.home-dot{width:7px;height:7px;border:0;border-radius:999px;padding:0;background:var(--line);transition:.25s}.home-dot.is-active{width:21px;background:var(--accent)}
.home-stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:13px}.home-stats-grid>div{text-align:center;padding:12px 5px;border:1px solid var(--line);border-radius:17px;background:var(--surface);box-shadow:0 8px 20px color-mix(in srgb,var(--accent) 10%,transparent)}.home-stats-grid span{font-size:23px}.home-stats-grid b{display:block;font-size:17px;margin:3px 0}.home-stats-grid small{font-size:10px}
.home-daily-divider{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:15px 0 9px;padding-top:13px;border-top:1px solid color-mix(in srgb,var(--line) 78%,transparent)}.home-daily-divider span{font-size:14px;font-weight:950}.home-daily-divider small{padding:5px 9px;border-radius:999px;background:var(--soft);border:1px solid var(--line);font-size:10px;color:var(--accent);font-weight:900}.home-daily-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}.home-daily-grid>div{text-align:center;padding:10px 3px;border:1px solid color-mix(in srgb,var(--line) 88%,transparent);border-radius:15px;background:color-mix(in srgb,var(--surface) 76%,transparent);box-shadow:inset 0 1px 0 rgba(255,255,255,.18)}.home-daily-grid span{font-size:20px}.home-daily-grid b{display:block;font-size:15px;margin:2px 0}.home-daily-grid small{display:block;font-size:9px;white-space:nowrap}.home-daily-sign{display:grid;grid-template-columns:48px 1fr auto;align-items:center;gap:10px;margin-top:10px;padding:10px;border-radius:16px;background:color-mix(in srgb,var(--soft) 72%,transparent);border:1px solid var(--line)}.home-daily-sign>div:nth-child(2) b{font-size:13px}.home-daily-sign p{margin:3px 0 0;font-size:10px;color:var(--muted)}.home-daily-sign>a{padding:7px 10px;border-radius:11px;background:var(--surface);border:1px solid var(--line);font-size:11px;font-weight:900;color:var(--accent)}.home-daily-sign.is-done{background:color-mix(in srgb,#44c994 13%,var(--surface))}
.home-sign-card{display:grid;grid-template-columns:54px 1fr auto;align-items:center;gap:12px;padding:13px 15px}.home-calendar{width:49px;height:49px;border-radius:13px;display:grid;place-items:center;background:linear-gradient(150deg,var(--accent),var(--accent2));color:#fff}.home-calendar b{font-size:19px;line-height:1}.home-calendar small{color:#fff;font-size:9px}.home-sign-card>div:nth-child(2) b{font-size:14px}.home-sign-card p{margin:4px 0 0;font-size:11px;color:var(--muted)}.home-sign-card>a{padding:8px 11px;border-radius:12px;background:var(--soft);border:1px solid var(--line);font-size:12px;font-weight:900;color:var(--accent)}.home-sign-card.is-done{--good-soft:color-mix(in srgb,#44c994 18%,var(--surface));background:var(--good-soft)}
.home-quick-card{padding-bottom:14px}.home-quick-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;padding:0 13px}.home-quick-item{min-height:84px;display:flex;flex-direction:column;justify-content:center;align-items:center;gap:7px;text-align:center;border:1px solid var(--line);border-radius:17px;background:color-mix(in srgb,var(--surface) 88%,var(--soft));font-size:12px;font-weight:850;transition:.2s}.home-quick-item:active{transform:scale(.95)}.home-quick-icon{font-size:29px;filter:drop-shadow(0 5px 8px color-mix(in srgb,var(--accent) 25%,transparent));animation:v13Bob 3s ease-in-out infinite}.home-vip-note{display:grid;grid-template-columns:42px 1fr auto;align-items:center;gap:10px;padding:12px 14px}.home-vip-note>span{font-size:29px}.home-vip-note div{display:flex;flex-direction:column}.home-vip-note small{font-size:10px}.home-vip-note a,.home-admin-button{padding:9px 12px;border-radius:13px;background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff!important;font-size:12px;font-weight:900}.home-admin-button{display:block;text-align:center;margin:0 0 14px;padding:13px}

/* V14 Phase 2: global seasonal/day-night switcher */
.home-theme-card{position:relative;margin-bottom:13px;padding:13px 14px;border:1px solid var(--line);border-radius:21px;background:var(--surface);box-shadow:var(--shadow);overflow:hidden}
.home-theme-card:before{content:var(--decor);position:absolute;right:14px;top:9px;font-size:34px;opacity:.22;filter:drop-shadow(0 3px 8px color-mix(in srgb,var(--accent) 35%,transparent))}
.home-theme-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.home-theme-head b{font-size:14px}.home-theme-head small{font-size:11px;color:var(--muted)}
.home-theme-switch{display:grid;grid-template-columns:repeat(5,1fr);gap:7px}.home-theme-switch button,.home-theme-switch a{border:1px solid var(--line);border-radius:13px;background:color-mix(in srgb,var(--soft) 72%,var(--surface));color:var(--text);min-height:48px;padding:6px 3px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;font-size:10px;font-weight:850;cursor:pointer}.home-theme-switch span{font-size:18px}.home-theme-switch [data-theme-preview].active{color:#fff;border-color:transparent;background:linear-gradient(135deg,var(--accent),var(--accent2));box-shadow:0 7px 16px color-mix(in srgb,var(--accent) 25%,transparent)}
.theme-status-pill{display:inline-flex;align-items:center;gap:5px;padding:5px 9px;border-radius:999px;background:color-mix(in srgb,var(--soft) 80%,var(--surface));border:1px solid var(--line);font-size:11px;font-weight:850}
.appearance-hero{padding:18px;border-radius:22px;margin-bottom:13px;background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 20%,var(--surface)),color-mix(in srgb,var(--accent2) 17%,var(--surface)));border:1px solid var(--line);box-shadow:var(--shadow)}.appearance-hero h2{margin:0 0 7px}.appearance-hero p{margin:0;color:var(--muted);line-height:1.65;font-size:13px}
.switch-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.theme-tile{position:relative;border:1px solid var(--line);border-radius:18px;padding:16px 8px;background:color-mix(in srgb,var(--surface) 94%,var(--soft));color:var(--text);box-shadow:0 8px 18px color-mix(in srgb,var(--accent) 8%,transparent);cursor:pointer;transition:.22s}.theme-tile:hover,.theme-tile:active{transform:translateY(-2px)}.theme-tile.active{border-color:var(--accent);background:linear-gradient(145deg,color-mix(in srgb,var(--accent) 17%,var(--surface)),color-mix(in srgb,var(--accent2) 14%,var(--surface)));box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 17%,transparent),0 10px 24px color-mix(in srgb,var(--accent) 18%,transparent)}.theme-tile.active:after{content:'✓ 使用中';position:absolute;right:7px;top:7px;border-radius:999px;padding:3px 6px;background:var(--accent);color:#fff;font-size:9px;font-weight:900}.theme-tile b{display:block;margin-top:6px}
body[data-period='night'] .home-brandbar,body[data-period='night'] .classic-bottom{background:color-mix(in srgb,var(--surface2) 90%,transparent)}
body[data-period='night'] .home-player-card,body[data-period='night'] .home-announcement-card,body[data-period='night'] .home-quick-card,body[data-period='night'] .home-theme-card{box-shadow:0 15px 40px rgba(0,0,0,.28),0 0 24px color-mix(in srgb,var(--accent2) 10%,transparent)}

@media(max-width:430px){.home-v14-shell{padding-left:9px;padding-right:9px}.home-player-card{padding:14px}.home-avatar{width:72px;height:72px;flex-basis:72px}.home-player-main{padding-right:62px}.home-player-name{font-size:21px}.home-vip-chip{max-width:74px;text-align:center;white-space:normal}.home-stats-grid{gap:6px}.home-stats-grid>div{padding:10px 2px}.home-stats-grid b{font-size:15px}.home-daily-grid{gap:5px}.home-daily-grid>div{padding:9px 1px}.home-daily-grid b{font-size:14px}.home-daily-sign{grid-template-columns:44px 1fr auto;padding:9px 8px}.home-daily-sign .home-calendar{width:42px;height:42px}.home-quick-grid{gap:7px;padding:0 10px}.home-quick-item{min-height:78px}.announcement-text p{-webkit-line-clamp:2}}


/* V14 Phase 3: shop, ranking, fortune and wheel card system */
.phase3-hero{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:19px;border-radius:24px;margin-bottom:12px;background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 88%,#26106e),color-mix(in srgb,var(--accent2) 82%,#ff8f4d));color:#fff;box-shadow:0 15px 32px color-mix(in srgb,var(--accent) 25%,transparent);overflow:hidden;position:relative}.phase3-hero:after{content:var(--decor);position:absolute;right:23%;bottom:-22px;font-size:82px;opacity:.14;transform:rotate(-12deg)}.phase3-hero small{font-size:10px;letter-spacing:1.8px;font-weight:900;opacity:.85}.phase3-hero h2{margin:4px 0 3px;font-size:24px}.phase3-hero p{margin:0;font-size:12px;opacity:.88}.phase3-balance,.phase3-rank-summary{min-width:105px;padding:11px 12px;border-radius:16px;background:rgba(255,255,255,.17);border:1px solid rgba(255,255,255,.24);backdrop-filter:blur(8px);text-align:center}.phase3-balance span,.phase3-rank-summary span{display:block;font-size:10px;opacity:.85}.phase3-balance b,.phase3-rank-summary b{display:block;margin-top:4px;font-size:18px}.phase3-tabs{display:flex;gap:7px;overflow-x:auto;padding:2px 1px 10px;scrollbar-width:none}.phase3-tabs::-webkit-scrollbar{display:none}.phase3-tab{flex:0 0 auto;padding:9px 12px;border-radius:999px;border:1px solid var(--line);background:var(--surface);font-size:11px;font-weight:900;color:var(--muted)}.phase3-tab.on{color:#fff;border-color:transparent;background:linear-gradient(90deg,var(--accent),var(--accent2));box-shadow:0 7px 16px color-mix(in srgb,var(--accent) 22%,transparent)}
.phase3-shop-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.phase3-shop-card{position:relative;display:grid;grid-template-columns:58px 1fr;gap:11px;padding:14px;border:1px solid var(--line);border-radius:21px;background:var(--surface);box-shadow:0 9px 22px color-mix(in srgb,var(--accent) 9%,transparent);overflow:hidden}.phase3-shop-icon{width:56px;height:56px;border-radius:17px;display:grid;place-items:center;font-size:29px;background:linear-gradient(145deg,var(--soft),color-mix(in srgb,var(--accent2) 15%,var(--surface)));border:1px solid var(--line)}.phase3-shop-info{min-width:0}.phase3-shop-info h3{font-size:15px;margin:6px 0 4px}.phase3-shop-info p{font-size:11px;line-height:1.45;color:var(--muted);margin:0 0 8px;min-height:31px}.phase3-shop-info strong{font-size:14px;color:var(--accent)}.phase3-chip{font-size:9px;padding:3px 6px;border-radius:999px;background:var(--soft);color:var(--accent);font-weight:900}.phase3-shop-card form{grid-column:1/-1}.phase3-buy{width:100%;border:0;border-radius:13px;padding:10px;background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff;font-weight:900}.phase3-buy.disabled{background:var(--soft);color:var(--muted);box-shadow:none}
.phase3-rank-list{border:1px solid var(--line);border-radius:22px;background:var(--surface);overflow:hidden;box-shadow:var(--shadow)}.phase3-rank-row{display:grid;grid-template-columns:38px 42px minmax(0,1fr) auto;align-items:center;gap:9px;padding:12px 13px;border-bottom:1px solid var(--line)}.phase3-rank-row:last-child{border-bottom:0}.phase3-rank-row.rank-me{background:linear-gradient(90deg,color-mix(in srgb,var(--accent) 14%,var(--surface)),color-mix(in srgb,var(--accent2) 10%,var(--surface)))}.phase3-rank-no{font-size:17px;font-weight:950;text-align:center}.phase3-rank-avatar{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:var(--soft);border:1px solid var(--line)}.phase3-rank-name{min-width:0}.phase3-rank-name b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.phase3-rank-name small,.phase3-rank-value small{display:block;color:var(--muted);font-size:10px;margin-top:2px}.phase3-rank-value{text-align:right}.phase3-rank-value b{font-size:13px}
.fortune-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.fortune-item{transition:.2s}.fortune-item:hover{transform:translateY(-2px)}.wheel-shell{animation:phase3WheelGlow 3s ease-in-out infinite}.notice{border-radius:15px!important;border:1px solid color-mix(in srgb,#44c994 45%,var(--line))!important;background:color-mix(in srgb,#44c994 13%,var(--surface))!important}
@keyframes phase3WheelGlow{0%,100%{filter:drop-shadow(0 0 5px color-mix(in srgb,var(--accent2) 25%,transparent))}50%{filter:drop-shadow(0 0 18px color-mix(in srgb,var(--accent2) 55%,transparent))}}
@media(max-width:560px){.phase3-shop-grid{grid-template-columns:1fr}.phase3-hero{padding:16px}.phase3-hero h2{font-size:21px}.phase3-balance,.phase3-rank-summary{min-width:88px;padding:9px}.phase3-rank-row{grid-template-columns:32px 38px minmax(0,1fr) auto;padding:11px 9px}.phase3-rank-avatar{width:35px;height:35px}.phase3-rank-value b{font-size:12px}}


/* V14 Phase 4A: personal center */
.me-v14-page{min-height:100vh}.me-v14-shell{max-width:720px;margin:auto;padding:10px 12px 112px}.me-topbar{display:grid;grid-template-columns:44px 1fr 44px;align-items:center;margin-bottom:12px}.me-topbar>a{width:40px;height:40px;display:grid;place-items:center;border:1px solid var(--line);border-radius:50%;background:var(--surface);box-shadow:var(--shadow);font-size:24px}.me-topbar>div{text-align:center}.me-topbar b{display:block;font-size:21px;font-weight:950}.me-topbar small{display:block;font-size:9px;letter-spacing:2px;color:var(--muted);margin-top:2px}
.me-profile-card,.me-card{position:relative;border:1px solid var(--line);border-radius:24px;background:color-mix(in srgb,var(--surface) 94%,var(--soft));box-shadow:var(--shadow);overflow:hidden;margin-bottom:12px}.me-profile-card{padding:18px}.me-cover-glow{position:absolute;left:-15%;right:-15%;top:-90px;height:190px;background:conic-gradient(from 90deg,var(--accent),var(--accent2),#ffd45c,#5fe1a1,#53c7ff,var(--accent));filter:blur(42px);opacity:.22;pointer-events:none}.me-profile-head{position:relative;display:flex;gap:15px;align-items:center}.me-avatar{width:92px;height:92px;flex:0 0 92px;border-radius:50%;display:grid;place-items:center;overflow:hidden;font-size:43px;border:5px solid transparent;background-image:linear-gradient(var(--surface2),var(--surface2)),var(--rainbow);background-origin:border-box;background-clip:padding-box,border-box;box-shadow:0 9px 24px color-mix(in srgb,var(--accent) 28%,transparent);animation:v13Avatar 3.5s ease-in-out infinite}.me-avatar img{width:100%;height:100%;object-fit:cover}.me-identity{min-width:0}.me-identity h1{margin:0;font-size:25px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.me-identity p{margin:8px 0 0;font-size:12px;color:var(--muted);line-height:1.55}.me-chips{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}.me-chips span{padding:4px 7px;border:1px solid var(--line);border-radius:999px;background:var(--soft);font-size:10px;font-weight:900}.me-chips span.vip{color:#fff;border-color:transparent;background:linear-gradient(90deg,#7d54ff,#ec5bd7)}.me-title-row,.me-exp-meta,.me-exp-foot{display:flex;justify-content:space-between;gap:10px;align-items:center}.me-title-row{position:relative;margin-top:16px;padding:11px 12px;border-radius:15px;background:var(--soft);border:1px solid var(--line);font-size:12px}.me-title-row b{color:var(--accent)}.me-exp-meta{margin-top:14px;font-size:11px}.me-exp-meta span,.me-exp-foot{color:var(--muted)}.me-exp-track{height:12px;margin-top:6px;border:1px solid var(--line);border-radius:999px;background:var(--soft);overflow:hidden}.me-exp-track i{display:block;height:100%;border-radius:inherit;background:var(--rainbow);background-size:240% 100%;animation:v13Bar 3s linear infinite;box-shadow:0 0 14px var(--accent2)}.me-exp-foot{font-size:10px;margin-top:5px}
.me-assets-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}.me-assets-grid>div{text-align:center;padding:13px 4px;border-radius:18px;border:1px solid var(--line);background:var(--surface);box-shadow:0 8px 20px color-mix(in srgb,var(--accent) 9%,transparent)}.me-assets-grid span{font-size:24px}.me-assets-grid b{display:block;font-size:16px;margin:4px 0}.me-assets-grid small{font-size:10px}.me-card{padding:16px}.me-section-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.me-section-title b{font-size:15px}.me-section-title small,.me-section-title a{font-size:10px;color:var(--muted)}.me-section-title a{padding:5px 9px;border:1px solid var(--line);border-radius:999px;background:var(--soft);font-weight:900;color:var(--accent)}.me-today-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.me-today-grid>div{text-align:center;padding:12px 5px;border-radius:16px;background:var(--soft);border:1px solid var(--line)}.me-today-grid span{font-size:23px}.me-today-grid b{display:block;margin:4px 0;font-size:14px}.me-today-grid small{font-size:9px}.me-info-list>div:not(.me-section-title){display:flex;justify-content:space-between;align-items:center;gap:15px;padding:13px 2px;border-top:1px solid var(--line);font-size:13px}.me-info-list>div:not(.me-section-title) b{text-align:right;font-size:12px;color:var(--muted)}.me-quick-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.me-quick-item{min-height:78px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;border:1px solid var(--line);border-radius:17px;background:color-mix(in srgb,var(--surface) 84%,var(--soft));font-size:11px;font-weight:900;transition:.18s}.me-quick-item span{font-size:27px;animation:v13Bob 3s ease-in-out infinite}.me-quick-item:active{transform:scale(.95)}
@media(max-width:430px){.me-v14-shell{padding-left:9px;padding-right:9px}.me-profile-card{padding:15px}.me-avatar{width:78px;height:78px;flex-basis:78px}.me-identity h1{font-size:21px}.me-assets-grid{gap:6px}.me-assets-grid>div{padding:11px 2px}.me-assets-grid b{font-size:14px}.me-card{padding:13px}.me-quick-grid{gap:6px}.me-quick-item{min-height:73px}}


/* V14 Phase 4B: announcement center */
.home-section-more{font-size:10px;color:var(--accent);font-weight:950;padding:5px 9px;border:1px solid var(--line);border-radius:999px;background:var(--soft)}
.notice-center-shell{max-width:720px;margin:auto;padding:10px 12px 112px}.notice-center-topbar{display:grid;grid-template-columns:44px 1fr auto;align-items:center;gap:8px;margin-bottom:12px}.notice-center-topbar>a{width:40px;height:40px;display:grid;place-items:center;border:1px solid var(--line);border-radius:50%;background:var(--surface);box-shadow:var(--shadow);font-size:24px}.notice-center-topbar>div{text-align:center}.notice-center-topbar b{display:block;font-size:21px;font-weight:950}.notice-center-topbar small{display:block;font-size:9px;letter-spacing:2px;color:var(--muted);margin-top:2px}.notice-center-topbar button{border:1px solid var(--line);border-radius:999px;background:var(--surface);color:var(--accent);font-size:10px;font-weight:950;padding:9px 11px;box-shadow:var(--shadow)}
.notice-center-hero{position:relative;display:flex;align-items:center;justify-content:space-between;gap:14px;padding:20px;border-radius:24px;margin-bottom:12px;color:#fff;background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 90%,#ff6e6e),color-mix(in srgb,var(--accent2) 88%,#735cff));box-shadow:0 16px 38px color-mix(in srgb,var(--accent) 26%,transparent);overflow:hidden}.notice-center-hero:after{content:'';position:absolute;width:170px;height:170px;border-radius:50%;right:-60px;top:-80px;background:rgba(255,255,255,.17)}.notice-center-hero div{position:relative;z-index:1}.notice-center-hero span{font-size:35px}.notice-center-hero h1{margin:6px 0 4px;font-size:23px}.notice-center-hero p{margin:0;font-size:11px;line-height:1.6;opacity:.88}.notice-center-hero strong{position:relative;z-index:1;min-width:52px;height:52px;border-radius:18px;display:grid;place-items:center;background:rgba(255,255,255,.2);border:1px solid rgba(255,255,255,.35);font-size:22px;backdrop-filter:blur(8px)}
.notice-center-list{display:grid;gap:10px}.notice-center-item{position:relative;display:grid;grid-template-columns:50px 1fr 10px;gap:12px;align-items:start;padding:15px;border:1px solid var(--line);border-radius:21px;background:color-mix(in srgb,var(--surface) 95%,var(--soft));box-shadow:var(--shadow);cursor:pointer;transition:.2s ease;outline:0}.notice-center-item:active{transform:scale(.985)}.notice-center-item:focus-visible{box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 32%,transparent),var(--shadow)}.notice-center-icon{width:48px;height:48px;border-radius:16px;display:grid;place-items:center;background:var(--soft);border:1px solid var(--line);font-size:27px}.notice-center-copy{min-width:0}.notice-center-meta{display:flex;justify-content:space-between;gap:10px;align-items:center}.notice-center-meta span{padding:4px 7px;border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff;font-size:9px;font-weight:950}.notice-center-meta small{font-size:9px;color:var(--muted)}.notice-center-copy h2{font-size:15px;margin:8px 0 4px}.notice-center-copy p{margin:0;color:var(--muted);font-size:11px;line-height:1.65}.notice-unread-dot{width:9px;height:9px;border-radius:50%;background:#ff486b;box-shadow:0 0 0 4px color-mix(in srgb,#ff486b 16%,transparent);margin-top:7px;transition:.2s}.notice-center-item.is-read{opacity:.72}.notice-center-item.is-read .notice-unread-dot{opacity:0;transform:scale(0)}
@media(max-width:430px){.notice-center-shell{padding-left:9px;padding-right:9px}.notice-center-topbar{grid-template-columns:40px 1fr auto}.notice-center-topbar button{padding:8px 9px}.notice-center-hero{padding:17px}.notice-center-hero h1{font-size:20px}.notice-center-item{grid-template-columns:45px 1fr 8px;padding:13px;gap:10px}.notice-center-icon{width:43px;height:43px;font-size:24px}.notice-center-meta small{display:none}}



/* V14 Phase 4D: motion, seasonal atmosphere and animated meters */
.phase4d-reveal{opacity:0;transform:translate3d(0,18px,0) scale(.985);transition:opacity .52s ease var(--reveal-delay,0ms),transform .52s cubic-bezier(.2,.75,.25,1) var(--reveal-delay,0ms)}
.phase4d-reveal.is-visible{opacity:1;transform:none}
.home-exp-track i,.me-exp-track i,.luck-fill,.progress span,.neon-level span,.classic-exp-fill,.panel-progress-fill{width:0;transition:width 1.05s cubic-bezier(.2,.8,.25,1);will-change:width}
.meter-ready{filter:saturate(1.08)}
body.phase4d-theme-pulse:after{animation:v13Particle 12s linear infinite,phase4dThemeFlash .65s ease}
body.phase4d-theme-pulse .home-theme-card,body.phase4d-theme-pulse .appearance-hero{animation:phase4dThemeCard .65s ease}
.home-announcement-slide.is-active .announcement-icon{animation:phase4dNoticeIcon .55s cubic-bezier(.2,.8,.25,1)}
.home-announcement-slide.is-active .announcement-text{animation:phase4dNoticeCopy .45s ease}
.home-quick-item,.me-quick-item,.theme-tile,.notice-center-item,.phase3-shop-card,.phase3-rank-row{transform-origin:center;transition:transform .2s ease,box-shadow .2s ease,border-color .2s ease}
@media(hover:hover){.home-quick-item:hover,.me-quick-item:hover,.theme-tile:hover,.notice-center-item:hover,.phase3-shop-card:hover,.phase3-rank-row:hover{transform:translateY(-3px);box-shadow:0 16px 34px color-mix(in srgb,var(--accent) 18%,transparent);border-color:color-mix(in srgb,var(--accent) 58%,var(--line))}}
body[data-period='night']:before{animation-duration:14s;filter:saturate(1.08)}
body[data-period='night'] .home-avatar,body[data-period='night'] .me-avatar{box-shadow:0 10px 28px color-mix(in srgb,var(--accent2) 34%,transparent),0 0 28px color-mix(in srgb,var(--accent) 14%,transparent)}
@keyframes phase4dThemeFlash{0%{opacity:.15;transform:scale(.6) rotate(-20deg)}55%{opacity:.75;transform:scale(1.35) rotate(12deg)}100%{opacity:.34;transform:scale(1) rotate(0)}}
@keyframes phase4dThemeCard{0%{transform:scale(.98)}50%{transform:scale(1.018);box-shadow:0 0 34px color-mix(in srgb,var(--accent2) 30%,transparent)}100%{transform:none}}
@keyframes phase4dNoticeIcon{0%{transform:scale(.65) rotate(-12deg);opacity:.2}70%{transform:scale(1.12) rotate(5deg)}100%{transform:none;opacity:1}}
@keyframes phase4dNoticeCopy{0%{transform:translateX(10px);opacity:0}100%{transform:none;opacity:1}}

/* V14 Phase 4C: unified site shell, cards, navigation and spacing */
body.phase4c-page,body.unified-rainbow-page,body.classic-rainbow-home{--page-gutter:12px;--page-width:760px}
.unified-top,.top{position:sticky;top:0;z-index:40;padding:14px max(16px,env(safe-area-inset-left)) 25px;color:#fff;background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 94%,#5b56e8),color-mix(in srgb,var(--accent2) 90%,#ff69b5));box-shadow:0 10px 28px color-mix(in srgb,var(--accent) 28%,transparent);backdrop-filter:blur(18px)}
.unified-top h1,.top h1{max-width:var(--page-width);margin:auto;font-size:20px;line-height:1.35;font-weight:950;letter-spacing:.2px}
.unified-wrap,.wrap{width:min(100%,var(--page-width));margin:-12px auto 0;padding:0 var(--page-gutter) 112px;position:relative;z-index:2}
.unified-wrap>.card:first-child,.wrap>.card:first-child{margin-top:0}
.card,.classic-card,.phase3-shop-card,.phase3-rank-row,.me-card,.notice-center-item{border-radius:21px;background:color-mix(in srgb,var(--surface) 94%,var(--soft));border:1px solid color-mix(in srgb,var(--line) 86%,transparent);box-shadow:0 12px 30px color-mix(in srgb,var(--accent) 11%,transparent);backdrop-filter:blur(16px)}
.card h2,.card h3,.classic-card h2,.classic-card h3{margin-top:0}.card p:last-child,.classic-card p:last-child{margin-bottom:0}
.bottom,.classic-bottom,.mobile-bottom{padding:7px max(5px,env(safe-area-inset-left)) max(8px,env(safe-area-inset-bottom));background:color-mix(in srgb,var(--surface2) 91%,transparent);border-top:1px solid color-mix(in srgb,var(--line) 88%,transparent)}
.bottom a,.classic-bottom a,.mobile-bottom a{position:relative;border-radius:13px;padding:5px 2px;transition:.18s ease}.bottom a:active,.classic-bottom a:active,.mobile-bottom a:active{transform:scale(.94);background:var(--soft)}
.bottom .on:after,.classic-bottom .on:after,.mobile-bottom .on:after{content:'';position:absolute;left:28%;right:28%;bottom:0;height:3px;border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent2));box-shadow:0 0 9px var(--accent2)}
.phase3-hero{border-radius:24px!important;margin-bottom:12px!important}.phase3-tabs{position:sticky;top:72px;z-index:15;padding:6px;background:color-mix(in srgb,var(--surface2) 86%,transparent);border:1px solid var(--line);border-radius:17px;backdrop-filter:blur(16px);box-shadow:0 8px 22px color-mix(in srgb,var(--accent) 10%,transparent)}
.phase3-tab{white-space:nowrap}.phase3-shop-grid,.phase3-rank-list{display:grid;gap:10px}.phase3-shop-card,.phase3-rank-row{margin:0!important}
.item-card{display:grid;grid-template-columns:52px 1fr auto;align-items:center;gap:12px}.item-icon{width:50px;height:50px;border-radius:16px;display:grid;place-items:center;background:var(--soft);border:1px solid var(--line);font-size:27px}.item-main h3{margin:0 0 4px}.qty{font-size:17px;font-weight:950;color:var(--accent)}
.settings-list,.me-info-list{overflow:hidden}.settings-list a,.settings-static,.me-info-list>div:not(.me-section-title){transition:.18s ease}.settings-list a:active{background:var(--soft);transform:scale(.99)}
.notice,.shop-msg{box-shadow:0 8px 20px color-mix(in srgb,var(--accent) 9%,transparent)}
.action,.btn,.admin,.phase3-buy,.save-btn{min-height:46px;touch-action:manipulation}
input,select,textarea{width:100%;border:1px solid var(--line);border-radius:14px;background:var(--surface2);color:var(--text);padding:12px;outline:none}input:focus,select:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 16%,transparent)}
@media(max-width:560px){body.phase4c-page,body.unified-rainbow-page,body.classic-rainbow-home{--page-gutter:9px}.unified-top,.top{padding-top:13px}.unified-top h1,.top h1{font-size:19px}.unified-wrap,.wrap{padding-bottom:106px}.phase3-tabs{top:66px;overflow-x:auto;display:flex;scrollbar-width:none}.phase3-tabs::-webkit-scrollbar{display:none}.phase3-tab{flex:0 0 auto}.item-card{grid-template-columns:46px 1fr auto;padding:13px}.item-icon{width:44px;height:44px;font-size:24px}}

/* admin compatibility */
.layout{display:grid;grid-template-columns:220px 1fr;min-height:calc(100vh - 64px)}.side{background:var(--surface);padding:18px 12px;border-right:1px solid var(--line)}.side a{display:block;padding:12px 14px;border-radius:12px;margin:4px 0}.side a:hover,.side a.on{background:var(--soft);color:var(--accent);font-weight:800}.main{padding:24px;max-width:1400px;width:100%;margin:auto}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.metric b{font-size:28px;display:block;margin-top:8px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}.formgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.tablewrap{overflow:auto;border-radius:16px;border:1px solid var(--line)}table{width:100%;border-collapse:collapse;background:var(--surface);color:var(--text)}th,td{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{background:var(--soft);font-size:13px}.hero{padding:20px;border-radius:24px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;box-shadow:var(--shadow)}.feature-grid,.summary-strip,.chat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.feature-tile,.summary-item,.chat-stat{padding:14px;text-align:center}.brand{font-size:20px;font-weight:900}
.v13-build{font-size:10px;color:var(--muted);text-align:center;padding:7px 0 84px}
@keyframes themeFall{0%{transform:translate3d(0,-12vh,0) rotate(0)}100%{transform:translate3d(8vw,120vh,0) rotate(360deg)}}@keyframes themeTwinkleFall{0%{transform:translate3d(0,-12vh,0) scale(.7);opacity:.1}50%{opacity:.9}100%{transform:translate3d(-6vw,120vh,0) scale(1.15);opacity:.15}}@keyframes v13Aurora{to{transform:scale(1.06) translate(2%,1%);filter:hue-rotate(10deg)}}@keyframes v13Particle{0%{transform:translate(0,0) rotate(0)}50%{transform:translate(78vw,62vh) rotate(180deg)}100%{transform:translate(12vw,90vh) rotate(360deg)}}@keyframes v13Border{to{background-position:260% 0}}@keyframes v13Bar{to{background-position:260% 0}}@keyframes v13Bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}@keyframes v13Avatar{0%,100%{transform:translateY(0)}50%{transform:translateY(-5px)}}@keyframes v13Tap{0%{transform:scale(1)}45%{transform:scale(.94)}100%{transform:scale(1)}}
@media(max-width:900px){.layout{grid-template-columns:1fr}.side{display:none}.main{padding:14px 13px 95px}.grid,.feature-grid,.summary-strip,.chat-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:560px){.wrap,.classic-shell{padding-left:11px;padding-right:11px}.classic-avatar,.avatar,.panel-avatar,.neon-avatar,.card-avatar{width:82px;height:82px;flex-basis:82px;font-size:42px}.classic-name,.panel-name{font-size:24px}.menu,.quick,.classic-quick-grid,.panel-grid{grid-template-columns:repeat(2,1fr)}.formgrid{grid-template-columns:1fr}.grid{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;scroll-behavior:auto!important;transition:none!important}}
"""

# Phase 5 admin optimization styles
CSS += r'''
.admin-notice-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.admin-notice-card{padding:16px;border:1px solid var(--line);border-radius:20px;background:var(--surface);box-shadow:var(--shadow)}.admin-notice-card>div:first-child{display:flex;justify-content:space-between;align-items:center}.admin-notice-card h3{margin:12px 0 6px}.admin-notice-card p{white-space:pre-wrap;color:var(--muted);line-height:1.65}.admin-notice-card .toolbar{margin-top:12px}.flash.danger{background:#fde8ed;color:#a62d4b}@media(max-width:700px){.admin-notice-grid{grid-template-columns:1fr}.side{overflow:auto}.main{padding-bottom:110px}}
'''

CSS += r'''

/* V15 Phase 6: Rainbow Store 2.0 */
.shop6-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;padding:20px;margin-bottom:12px;border-radius:26px;color:#fff;background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 96%,#6258ef),color-mix(in srgb,var(--accent2) 92%,#ff65b0));box-shadow:0 18px 38px color-mix(in srgb,var(--accent) 28%,transparent);overflow:hidden;position:relative}.shop6-hero:after{content:'🛍️';position:absolute;right:24%;top:-16px;font-size:94px;opacity:.11;transform:rotate(-12deg)}.shop6-hero small{font-weight:950;letter-spacing:1.4px;opacity:.82}.shop6-hero h2{font-size:27px;margin:4px 0}.shop6-hero p{margin:0 0 10px;opacity:.9}.shop6-wallet{min-width:145px;padding:13px 15px;border-radius:19px;background:#ffffff25;border:1px solid #ffffff45;backdrop-filter:blur(12px);text-align:right}.shop6-wallet span,.shop6-wallet a{display:block;color:#fff;font-size:11px}.shop6-wallet b{display:block;font-size:21px;margin:3px 0}.shop6-vip-badge{display:inline-flex;padding:6px 10px;border-radius:999px;background:#ffffff25;border:1px solid #ffffff40;color:#fff;font-size:11px;font-weight:900}.shop6-vip-badge.guest{text-decoration:none}.shop6-tools{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(135px,.7fr) auto;gap:9px;align-items:end;padding:12px;margin-bottom:10px;border:1px solid var(--line);border-radius:19px;background:color-mix(in srgb,var(--surface) 94%,transparent);box-shadow:var(--shadow)}.shop6-tools label span{display:block;font-size:10px;font-weight:900;color:var(--muted);margin:0 0 5px 3px}.shop6-tools button{height:44px;border:0;border-radius:13px;padding:0 17px;color:#fff;font-weight:950;background:linear-gradient(90deg,var(--accent),var(--accent2))}.shop6-tabs{position:sticky;top:68px;z-index:16;display:flex;gap:7px;overflow-x:auto;padding:7px;margin-bottom:11px;border:1px solid var(--line);border-radius:18px;background:color-mix(in srgb,var(--surface2) 88%,transparent);backdrop-filter:blur(16px);scrollbar-width:none}.shop6-tabs::-webkit-scrollbar{display:none}.shop6-tab{flex:0 0 auto;padding:8px 12px;border-radius:12px;color:var(--muted);font-size:12px;font-weight:900}.shop6-tab.on{color:#fff;background:linear-gradient(90deg,var(--accent),var(--accent2));box-shadow:0 7px 16px color-mix(in srgb,var(--accent) 25%,transparent)}.shop6-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.shop6-card{display:grid;grid-template-columns:58px 1fr;gap:11px;padding:14px;border:1px solid var(--line);border-radius:22px;background:var(--surface);box-shadow:0 11px 26px color-mix(in srgb,var(--accent) 11%,transparent);transition:.2s}.shop6-icon{width:56px;height:56px;border-radius:18px;display:grid;place-items:center;font-size:29px;background:linear-gradient(145deg,var(--soft),color-mix(in srgb,var(--accent2) 15%,var(--surface)));border:1px solid var(--line)}.shop6-main{min-width:0}.shop6-main h3{font-size:15px;margin:7px 0 4px}.shop6-main p{font-size:11px;line-height:1.5;color:var(--muted);margin:0 0 9px;min-height:33px}.shop6-tags{display:flex;gap:4px;flex-wrap:wrap}.shop6-tag{padding:3px 6px;border-radius:999px;background:var(--soft);color:var(--accent);font-size:9px;font-weight:950}.shop6-tag.owned{background:#2bbf7b20;color:#15945b}.shop6-tag.vip{background:#ffd45b28;color:#aa7100}.shop6-price{display:flex;align-items:end;justify-content:space-between;gap:7px}.shop6-price strong{font-size:14px;color:var(--accent)}.shop6-price small{font-size:9px;color:var(--muted)}.shop6-card form{grid-column:1/-1}.shop6-buy{width:100%;border:0;border-radius:13px;padding:11px;background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff;font-weight:950}.shop6-buy.disabled{background:var(--soft);color:var(--muted);box-shadow:none}.shop6-history{margin-top:13px}.shop6-history-head,.shop6-history>div{display:flex;align-items:center;justify-content:space-between;gap:10px}.shop6-history-head{margin-bottom:8px}.shop6-history-head h3{margin:0}.shop6-history-head a{font-size:11px;color:var(--accent);font-weight:900}.shop6-history>div:not(.shop6-history-head){padding:9px 0;border-top:1px solid var(--line);font-size:12px}.shop6-history>div b{color:var(--accent)}
@media(hover:hover){.shop6-card:hover{transform:translateY(-3px);box-shadow:0 17px 34px color-mix(in srgb,var(--accent) 18%,transparent);border-color:color-mix(in srgb,var(--accent) 55%,var(--line))}}
@media(max-width:620px){.shop6-hero{padding:17px;align-items:stretch}.shop6-hero h2{font-size:22px}.shop6-wallet{min-width:110px;padding:10px}.shop6-wallet b{font-size:17px}.shop6-tools{grid-template-columns:1fr 1fr}.shop6-tools button{grid-column:1/-1}.shop6-grid{grid-template-columns:1fr}}
@media(max-width:390px){.shop6-hero{display:block}.shop6-wallet{margin-top:12px;text-align:left}.shop6-tools{grid-template-columns:1fr}.shop6-tools button{grid-column:auto}.shop6-card{grid-template-columns:52px 1fr;padding:12px}.shop6-icon{width:50px;height:50px}}
'''


CSS += r'''
/* V15 Phase 7 - Achievement system */
.ach-shell{max-width:760px;margin:0 auto;padding:18px 14px 112px}.ach-top{display:flex;align-items:center;gap:14px;margin:4px 0 16px}.ach-top>a{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;text-decoration:none;color:var(--text);font-size:31px;background:var(--surface);border:1px solid var(--line)}.ach-top>div{flex:1}.ach-top h1{margin:0;font-size:25px}.ach-top p{margin:4px 0 0;color:var(--muted);font-size:13px}.ach-top>span{font-size:30px}.ach-notice{padding:13px 15px;margin-bottom:12px;border-radius:16px;background:color-mix(in srgb,#42d59a 18%,var(--surface));border:1px solid color-mix(in srgb,#42d59a 55%,var(--line));font-weight:800}.ach-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}.ach-summary div{text-align:center;padding:15px 8px;border-radius:18px;background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow)}.ach-summary b{display:block;font-size:25px}.ach-summary span{font-size:12px;color:var(--muted)}.ach-list{display:grid;gap:12px}.ach-card{display:flex;gap:13px;padding:16px;border-radius:21px;background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow);position:relative;overflow:hidden}.ach-card.done{border-color:color-mix(in srgb,#ffd75f 70%,var(--line));box-shadow:0 10px 30px color-mix(in srgb,#ffd75f 16%,transparent)}.ach-card.claimed{opacity:.78}.ach-icon{width:58px;height:58px;flex:0 0 58px;display:grid;place-items:center;border-radius:18px;background:linear-gradient(145deg,var(--soft),color-mix(in srgb,var(--accent) 22%,var(--surface)));font-size:30px}.ach-main{min-width:0;flex:1}.ach-head{display:flex;align-items:center;justify-content:space-between;gap:8px}.ach-head h3{margin:0;font-size:17px}.ach-head span{font-size:12px;font-weight:900;color:var(--accent)}.ach-main p{margin:5px 0 10px;font-size:13px;color:var(--muted)}.ach-track{height:9px;border-radius:999px;background:var(--soft);overflow:hidden}.ach-track i{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,var(--accent),#ffd45e);box-shadow:0 0 12px color-mix(in srgb,var(--accent) 55%,transparent)}.ach-meta{display:flex;justify-content:space-between;gap:8px;margin:8px 0 11px;font-size:12px}.ach-meta b{color:var(--text)}.ach-btn{width:100%;border:0;border-radius:13px;padding:11px 12px;font-weight:950;color:white;background:linear-gradient(90deg,var(--accent),#8b68ff);cursor:pointer}.ach-btn.locked{background:var(--soft);color:var(--muted)}.ach-btn.claimed{background:color-mix(in srgb,#42d59a 22%,var(--surface));color:#28b77e}.ach-main form{margin:0}@media(max-width:430px){.ach-card{padding:14px}.ach-icon{width:50px;height:50px;flex-basis:50px;font-size:26px}.ach-meta{display:block}.ach-meta b{display:block;margin-top:4px}}

'''


CSS += r'''
/* V15 Phase 8 - visual upgrade */
body{opacity:0;transform:translateY(5px)}body.v15-page-ready{opacity:1;transform:none;transition:opacity .28s ease,transform .28s ease}body.v15-page-leave{opacity:.35;transform:translateY(4px)}
.v15-tilt{transform:perspective(900px) rotateX(var(--rx,0deg)) rotateY(var(--ry,0deg));transform-style:preserve-3d;will-change:transform;transition:transform .18s ease,box-shadow .22s ease}.v15-tilt>*{transform:translateZ(1px)}
.v15-confetti{position:fixed;inset:0;z-index:9999;pointer-events:none;overflow:hidden}.v15-confetti i{position:absolute;left:var(--cx);top:-12vh;font-style:normal;font-size:22px;animation:v15Confetti 2.35s cubic-bezier(.16,.84,.44,1) var(--cd) forwards;filter:drop-shadow(0 4px 5px #0003)}
.ach-card.done:before,.shop6-card:before,.home-player-card:before,.me-profile-card:before{content:'';position:absolute;inset:-45%;pointer-events:none;background:linear-gradient(105deg,transparent 43%,rgba(255,255,255,.46) 49%,transparent 55%);transform:translateX(-70%) rotate(9deg);animation:v15Sheen 5.5s ease-in-out infinite}.ach-card.done,.shop6-card,.home-player-card,.me-profile-card{isolation:isolate}
.ach-icon,.shop6-icon,.home-quick-card .home-quick-icon,.classic-bottom a.on span{animation:v15Float 3.2s ease-in-out infinite}.classic-bottom a.on{position:relative}.classic-bottom a.on:after{content:'';position:absolute;left:24%;right:24%;bottom:3px;height:3px;border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent2));box-shadow:0 0 12px var(--accent2);animation:v15NavGlow 1.8s ease-in-out infinite}
button:not(:disabled),a.tile,.home-quick-card,.panel-link{position:relative;overflow:hidden}button:not(:disabled):after,a.tile:after,.home-quick-card:after,.panel-link:after{content:'';position:absolute;inset:0;pointer-events:none;background:radial-gradient(circle at var(--tap-x,50%) var(--tap-y,50%),rgba(255,255,255,.38),transparent 36%);opacity:0;transition:opacity .25s}button:not(:disabled):active:after,a.tile:active:after,.home-quick-card:active:after,.panel-link:active:after{opacity:1}
@keyframes v15Confetti{0%{transform:translateY(-10vh) rotate(0) scale(.7);opacity:0}10%{opacity:1}100%{transform:translateY(116vh) rotate(var(--cr)) scale(1.15);opacity:.05}}@keyframes v15Sheen{0%,68%{transform:translateX(-78%) rotate(9deg)}88%,100%{transform:translateX(78%) rotate(9deg)}}@keyframes v15Float{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}@keyframes v15NavGlow{0%,100%{opacity:.55;transform:scaleX(.72)}50%{opacity:1;transform:scaleX(1)}}
@media(prefers-reduced-motion:reduce){body{opacity:1;transform:none}.v15-tilt{transform:none!important}.v15-confetti{display:none}.ach-card.done:before,.shop6-card:before,.home-player-card:before,.me-profile-card:before{display:none}}
'''


CSS += r"""
/* V16 Phase 2 - full mascot brand integration */
.rb-brand-hero{position:relative;display:flex;align-items:center;justify-content:space-between;gap:14px;max-width:760px;margin:0 auto 12px;padding:14px 16px;border-radius:0 0 24px 24px;color:#fff;background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 94%,#6d5cff),color-mix(in srgb,var(--accent2) 92%,#ff70b8));box-shadow:0 14px 32px color-mix(in srgb,var(--accent) 24%,transparent);overflow:hidden}
.rb-brand-copy{min-width:0;position:relative;z-index:2}.rb-brand-copy b{display:block;font-size:20px;font-weight:950;letter-spacing:.3px}.rb-brand-copy small{display:block;margin-top:2px;font-size:10px;letter-spacing:1.5px;opacity:.82}.rb-brand-mascot{width:112px;height:72px;object-fit:contain;filter:drop-shadow(0 8px 12px #5e2f6a45);animation:rbMascotFloat 3s ease-in-out infinite;transform-origin:center bottom}.rb-brand-mini{display:flex;align-items:center;gap:9px;margin-bottom:12px}.rb-brand-mini img{width:58px;height:42px;object-fit:contain;filter:drop-shadow(0 5px 8px #57395c35)}
.rb-mascot-panel{position:relative;overflow:hidden}.rb-mascot-panel:after{content:'';position:absolute;right:-8px;bottom:-5px;width:128px;height:92px;background:url('/player/assets/rainbow-mascot.png') center/contain no-repeat;opacity:.18;pointer-events:none}.rb-empty{display:grid;place-items:center;text-align:center;min-height:170px;padding:24px}.rb-empty img{width:150px;max-width:55%;margin-bottom:8px;filter:drop-shadow(0 9px 12px #6c3f7425)}
.rb-page-footer{max-width:760px;margin:12px auto 90px;padding:14px 16px;display:flex;align-items:center;justify-content:center;gap:10px;color:var(--muted);font-size:11px}.rb-page-footer img{width:84px;height:50px;object-fit:contain;opacity:.9}.rb-page-footer b{color:var(--accent)}
.home-mascot-stage{position:relative;display:grid;grid-template-columns:1fr 155px;align-items:center;gap:10px;margin:0 0 12px;padding:18px;border-radius:25px;color:#fff;background:linear-gradient(135deg,#ff79b5,#8177ff 55%,#53cfea);box-shadow:0 17px 35px #7c5bba35;overflow:hidden}.home-mascot-stage:before{content:'';position:absolute;inset:0;background:radial-gradient(circle at 18% 20%,#fff7 0 2px,transparent 3px),radial-gradient(circle at 77% 32%,#fff6 0 3px,transparent 4px);background-size:42px 42px,63px 63px;opacity:.7}.home-mascot-stage>div{position:relative;z-index:2}.home-mascot-stage small{font-size:10px;letter-spacing:1.7px;opacity:.85}.home-mascot-stage h2{margin:5px 0 4px;font-size:24px}.home-mascot-stage p{margin:0;font-size:12px;opacity:.9}.home-mascot-stage img{position:relative;z-index:2;width:155px;height:105px;object-fit:contain;filter:drop-shadow(0 12px 12px #4c315844);animation:rbMascotFloat 3s ease-in-out infinite}
.card-preview{position:relative;overflow:hidden}.card-preview>.rb-card-mascot{width:146px;height:90px;object-fit:contain;margin:-4px auto 7px;display:block;filter:drop-shadow(0 9px 12px #69406928)}
.admin-brand-mascot{width:82px;height:48px;object-fit:contain;vertical-align:middle;margin-right:8px;filter:drop-shadow(0 6px 7px #38234435)}
@keyframes rbMascotFloat{0%,100%{transform:translateY(1px) rotate(-1deg)}50%{transform:translateY(-6px) rotate(1deg)}}
@media(max-width:560px){.rb-brand-hero{border-radius:0 0 20px 20px;padding:12px 13px}.rb-brand-mascot{width:92px;height:60px}.home-mascot-stage{grid-template-columns:1fr 112px;padding:15px}.home-mascot-stage img{width:115px;height:82px}.home-mascot-stage h2{font-size:20px}.rb-page-footer{margin-bottom:82px}.admin-brand-mascot{width:62px;height:38px}}
@media(prefers-reduced-motion:reduce){.rb-brand-mascot,.home-mascot-stage img{animation:none!important}}
"""


CSS += r"""
/* V20 Rainbow Crown Starfield global visual layer
   Visual-only overrides: feature flows, button locations and meter animation logic remain unchanged. */
body.theme-rainbow-starry,
body.theme-spring-day,body.theme-spring-night,
body.theme-summer-day,body.theme-summer-night,
body.theme-autumn-day,body.theme-autumn-night,
body.theme-winter-day,body.theme-winter-night{
 --page:#060720;--page2:#16072f;--surface:rgba(13,18,54,.90);--surface2:#10173d;
 --text:#fffaff;--muted:#c9c9ec;--accent:#b76cff;--accent2:#43dfff;
 --line:rgba(194,132,255,.46);--soft:rgba(94,64,164,.25);--good:#5df0bd;--danger:#ff7193;
 --shadow:0 16px 38px rgba(2,4,28,.45),0 0 25px rgba(178,93,255,.12);
 --decor:'👑';--season:'彩虹星空';--period:'';--particle:'✦';
 --theme-text:#fffaff;--theme-muted:#c9c9ec;--theme-accent:#b76cff;--theme-soft:rgba(91,66,160,.24);
}
html{background:#050619!important}
body{font-family:'Noto Sans TC','PingFang TC','Microsoft JhengHei',system-ui,-apple-system,sans-serif!important;font-weight:500;letter-spacing:.02em;text-rendering:optimizeLegibility;-webkit-font-smoothing:antialiased}
body.theme-rainbow-starry,
body.theme-spring-day,body.theme-spring-night,
body.theme-summer-day,body.theme-summer-night,
body.theme-autumn-day,body.theme-autumn-night,
body.theme-winter-day,body.theme-winter-night{
 background:
 radial-gradient(circle at 50% -8%,rgba(255,194,80,.22),transparent 24%),
 radial-gradient(circle at 9% 19%,rgba(41,127,255,.28),transparent 30%),
 radial-gradient(circle at 90% 22%,rgba(255,50,193,.23),transparent 31%),
 radial-gradient(circle at 55% 92%,rgba(92,45,255,.24),transparent 38%),
 linear-gradient(155deg,#050619 0%,#0a1030 48%,#21072e 100%)!important;
 color:var(--text)!important;
}
body:before{background:
 radial-gradient(circle at 12% 8%,rgba(30,117,255,.32),transparent 23%),
 radial-gradient(circle at 86% 15%,rgba(255,48,189,.26),transparent 27%),
 radial-gradient(circle at 48% 85%,rgba(112,55,255,.24),transparent 30%),
 repeating-radial-gradient(circle at 22% 18%,rgba(255,255,255,.92) 0 1px,transparent 1.7px 42px)!important;
 opacity:.82;animation:v13Aurora 13s ease-in-out infinite alternate!important}
body:after{content:'✦  ✧  ✦';left:auto;right:6%;top:9%;font-size:24px;letter-spacing:13px;color:#fff7bf;opacity:.62;filter:drop-shadow(0 0 7px #fff) drop-shadow(0 0 14px #d15cff);animation:rbCrownStars 4.2s ease-in-out infinite!important}

/* Global crown / rainbow / crystal ornament */
.unified-top,.top,.rb-brand-hero,.home-mascot-stage,.phase3-hero,.notice-center-hero,.appearance-hero{
 position:relative;overflow:hidden;border-color:rgba(255,214,105,.48)!important;
 background:linear-gradient(135deg,rgba(42,17,91,.97),rgba(18,38,101,.96) 48%,rgba(82,12,93,.96))!important;
 box-shadow:0 12px 35px rgba(3,3,28,.48),0 0 26px rgba(178,91,255,.22),inset 0 1px rgba(255,255,255,.12)!important;
}
.unified-top:before,.top:before,.rb-brand-hero:before,.phase3-hero:before,.notice-center-hero:before,.appearance-hero:before{
 content:'♛';position:absolute;left:50%;top:-14px;transform:translateX(-50%);font-size:42px;line-height:1;
 color:#ffe678;text-shadow:0 0 7px #fff,0 0 14px #ff8a32,0 0 23px #d94dff;opacity:.9;pointer-events:none
}
.unified-top:after,.top:after,.rb-brand-hero:after,.phase3-hero:after,.notice-center-hero:after,.appearance-hero:after{
 content:'';position:absolute;left:8%;right:8%;bottom:-20px;height:48px;border-radius:50% 50% 0 0;
 border-top:4px solid transparent;background:linear-gradient(90deg,#ff66ae,#ffab50,#ffe765,#64e79a,#4edfff,#8777ff,#f26fd5) border-box;
 -webkit-mask:linear-gradient(#000 0 0) padding-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;
 filter:drop-shadow(0 0 8px rgba(111,216,255,.85));opacity:.78;pointer-events:none
}

/* Cards become crystal panels with subtle wing corners */
.card,.classic-card,.home-player-card,.home-announcement-card,.home-quick-card,.home-sign-card,.home-vip-note,
.me-profile-card,.me-card,.me-assets-grid,.notice-center-item,.phase3-shop-card,.phase3-rank-row,.shop6-card,
.panel,.admin-card,.stat,.toolbar,.settings-list,.summary-list,.card-preview{
 position:relative;background:linear-gradient(145deg,rgba(21,29,73,.93),rgba(38,19,78,.88))!important;
 color:#fffaff!important;border:1px solid rgba(191,130,255,.45)!important;
 box-shadow:0 14px 34px rgba(2,3,28,.38),0 0 20px rgba(167,83,255,.10),inset 0 1px rgba(255,255,255,.09)!important;
}
.card:before,.classic-card:before,.home-player-card:before,.me-profile-card:before,.notice-center-item:before,.shop6-card:before,
.panel:before,.admin-card:before,.card-preview:before{
 content:'◇';position:absolute;left:10px;top:8px;color:#fff4a8;font-size:13px;text-shadow:0 0 6px #fff,0 0 12px #c253ff;opacity:.72;pointer-events:none
}
.card:after,.classic-card:after,.home-player-card:after,.me-profile-card:after,.notice-center-item:after,.shop6-card:after,
.panel:after,.admin-card:after,.card-preview:after{
 content:'🪽';position:absolute;right:8px;bottom:5px;font-size:20px;filter:drop-shadow(0 0 8px #ad68ff);opacity:.13;pointer-events:none
}

/* Readable typography */
h1,h2,h3,.brand,.rb-brand-copy b,.home-player-name,.me-profile-name,.panel-title{color:#fff!important;text-shadow:0 1px 2px #05051c,0 0 9px rgba(119,220,255,.42);font-weight:950!important}
.unified-top h1,.top h1,.rb-brand-copy b,.phase3-hero h2,.appearance-hero h1{
 background:linear-gradient(90deg,#fff,#ffe984 22%,#ff95cf 45%,#7cecff 72%,#fff);
 -webkit-background-clip:text;background-clip:text;color:transparent!important;text-shadow:none!important;
 filter:drop-shadow(0 0 7px rgba(129,204,255,.38))
}
p,li,label,.item-main,.settings-list,.me-info-list,.summary-row,.history-row{color:#f4f1ff!important}
small,.muted,.sub,.meta,.hint,.card p,.classic-card p,.settings-list small,.summary-row span{color:#c9c9ec!important;opacity:1!important}
b,strong,.qty,.value,.stat b,.card-stat b,.home-stat b{color:#fff!important;text-shadow:0 0 7px rgba(111,218,255,.25)}
.role-owner,.owner,.leader,.leader-badge,[data-role='owner']{color:#ffe47d!important;text-shadow:0 0 7px #ffb03a!important}

/* Inputs / controls */
input,select,textarea{background:rgba(6,11,38,.82)!important;color:#fff!important;border-color:rgba(187,130,255,.48)!important}
input::placeholder,textarea::placeholder{color:#aaa9d0!important}
button,.btn,.action,.save-btn,.phase3-buy,.shop6-buy,.admin{
 color:#fff!important;border-color:rgba(255,214,103,.35)!important;
 background:linear-gradient(100deg,#7a48f4,#d449c7 48%,#2dcce2)!important;
 box-shadow:0 8px 22px rgba(109,65,235,.33),inset 0 1px rgba(255,255,255,.20)!important;
 text-shadow:0 1px 2px rgba(0,0,0,.35);font-weight:900!important
}

/* Keep meter geometry and animation; only outer crystal treatment changes */
.home-exp-track,.me-exp-track,.luck-track,.progress,.classic-exp-track,.panel-progress{
 border:1px solid rgba(191,139,255,.48)!important;box-shadow:inset 0 0 9px rgba(3,4,30,.72),0 0 9px rgba(92,210,255,.16)!important
}

/* Navigation and admin shell */
.bottom,.classic-bottom,.mobile-bottom,.side{
 background:linear-gradient(180deg,rgba(8,11,39,.97),rgba(20,8,46,.98))!important;
 border-color:rgba(170,111,255,.45)!important;box-shadow:0 -8px 26px rgba(2,3,24,.42)!important
}
.bottom a,.classic-bottom a,.mobile-bottom a,.side a{color:#d8d7f6!important}
.bottom .on,.classic-bottom .on,.mobile-bottom .on,.side a.on{color:#fff!important;text-shadow:0 0 8px #69dcff}
.layout{background:transparent!important}.main{background:transparent!important}

/* Global ornament at page content start and end */
.unified-wrap:before,.wrap:before,.main:before{
 content:'✦  ◇  👑  ◇  ✦';display:block;text-align:center;margin:4px auto 10px;color:#ffe37c;
 letter-spacing:8px;font-size:13px;text-shadow:0 0 7px #fff,0 0 14px #d65fff;opacity:.76
}
.rb-page-footer:before{content:'🪽  💎  🪽';color:#f9dfff;text-shadow:0 0 10px #8d71ff;margin-right:8px}

@keyframes rbCrownStars{0%,100%{transform:translateY(0) scale(.9);opacity:.35}50%{transform:translateY(5px) scale(1.08);opacity:.9}}
@media(max-width:560px){
 .unified-top:before,.top:before,.rb-brand-hero:before,.phase3-hero:before,.notice-center-hero:before,.appearance-hero:before{font-size:32px;top:-10px}
 .card:after,.classic-card:after,.home-player-card:after,.me-profile-card:after,.notice-center-item:after,.shop6-card:after,.panel:after,.admin-card:after{font-size:16px;opacity:.10}
 h1{font-size:1.45rem}.unified-wrap:before,.wrap:before,.main:before{letter-spacing:5px;font-size:11px}
}
@media(prefers-reduced-motion:reduce){body:after{animation:none!important}}
"""

CSS += r"""
/* V20 Phase 1: member personal center layout refresh. Existing theme variables and effects remain active. */
.me-v20-page{min-height:100vh}.me-v20-shell{width:min(100%,760px);margin:0 auto;padding:10px 11px 116px}.me-v20-topbar{display:grid;grid-template-columns:44px 1fr 44px;align-items:center;margin-bottom:12px}.me-v20-topbar>a{width:40px;height:40px;display:grid;place-items:center;border-radius:50%;border:1px solid var(--line);background:var(--surface);box-shadow:var(--shadow);font-size:23px}.me-v20-topbar>div{text-align:center}.me-v20-topbar b{display:block;font-size:21px;font-weight:950}.me-v20-topbar small{display:block;margin-top:2px;font-size:9px;letter-spacing:2px;color:var(--muted)}
.me-v20-profile,.me-v20-card{position:relative;overflow:hidden;margin-bottom:12px;border:1px solid var(--line);border-radius:25px;background:color-mix(in srgb,var(--surface) 94%,var(--soft));box-shadow:var(--shadow);backdrop-filter:blur(16px)}.me-v20-profile{padding:18px}.me-v20-aurora{position:absolute;inset:-85px -20% auto;height:190px;background:conic-gradient(from 100deg,var(--accent),var(--accent2),#ffe16e,#65e8a5,#58d9ff,var(--accent));filter:blur(45px);opacity:.26;pointer-events:none;animation:v13Bar 8s linear infinite}.me-v20-profile-main{position:relative;display:flex;align-items:center;gap:15px}.me-v20-avatar{width:94px;height:94px;flex:0 0 94px;display:grid;place-items:center;overflow:hidden;border-radius:50%;font-size:43px;border:5px solid transparent;background-image:linear-gradient(var(--surface2),var(--surface2)),var(--rainbow);background-origin:border-box;background-clip:padding-box,border-box;box-shadow:0 10px 27px color-mix(in srgb,var(--accent) 33%,transparent);animation:v13Avatar 3.5s ease-in-out infinite}.me-v20-avatar img{width:100%;height:100%;object-fit:cover}.me-v20-identity{min-width:0}.me-v20-identity>small{font-size:9px;letter-spacing:1.8px;color:var(--muted)}.me-v20-identity h1{margin:3px 0 0;font-size:25px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.me-v20-identity p{margin:8px 0 0;font-size:12px;line-height:1.5;color:var(--muted)}.me-v20-badges{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}.me-v20-badges span{padding:4px 7px;border:1px solid var(--line);border-radius:999px;background:var(--soft);font-size:10px;font-weight:900}.me-v20-badges .vip{color:#fff;border-color:transparent;background:linear-gradient(90deg,#7d54ff,#ec5bd7)}.me-v20-title{position:relative;display:flex;justify-content:space-between;gap:10px;margin-top:16px;padding:11px 12px;border:1px solid var(--line);border-radius:15px;background:var(--soft);font-size:12px}.me-v20-title b{color:var(--accent);text-align:right}.me-v20-exp-head,.me-v20-exp-foot{display:flex;justify-content:space-between;align-items:center;gap:10px}.me-v20-exp-head{margin-top:14px;font-size:11px}.me-v20-exp-head span,.me-v20-exp-foot{color:var(--muted)}.me-v20-exp{height:12px;margin-top:6px;overflow:hidden;border:1px solid var(--line);border-radius:999px;background:var(--soft)}.me-v20-exp i{display:block;height:100%;border-radius:inherit;background:var(--rainbow);background-size:240% 100%;box-shadow:0 0 14px var(--accent2);animation:v13Bar 3s linear infinite}.me-v20-exp-foot{margin-top:5px;font-size:10px}
.me-v20-assets{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:12px}.me-v20-assets>div{text-align:center;padding:13px 3px;border:1px solid var(--line);border-radius:18px;background:var(--surface);box-shadow:0 8px 20px color-mix(in srgb,var(--accent) 9%,transparent)}.me-v20-assets span{font-size:24px}.me-v20-assets b{display:block;margin:4px 0;font-size:16px}.me-v20-assets small{font-size:10px;color:var(--muted)}
.me-v20-card{padding:16px}.me-v20-section-title{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}.me-v20-section-title>div{display:flex;align-items:center;gap:7px}.me-v20-section-title>div>span{font-size:20px}.me-v20-section-title b{font-size:15px}.me-v20-section-title small,.me-v20-section-title a{font-size:10px;color:var(--muted)}.me-v20-section-title a{padding:5px 9px;border:1px solid var(--line);border-radius:999px;background:var(--soft);font-weight:900;color:var(--accent)}.me-v20-today{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.me-v20-today>div{text-align:center;padding:12px 4px;border:1px solid var(--line);border-radius:16px;background:var(--soft)}.me-v20-today span{font-size:23px}.me-v20-today b{display:block;margin:4px 0;font-size:14px}.me-v20-today small{font-size:9px;color:var(--muted)}.me-v20-info>div:not(.me-v20-section-title){display:flex;justify-content:space-between;align-items:center;gap:15px;padding:13px 2px;border-top:1px solid var(--line);font-size:13px}.me-v20-info>div:not(.me-v20-section-title) b{text-align:right;font-size:12px;color:var(--muted)}.me-v20-quick{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.me-v20-quick .me-quick-item{min-height:81px}.me-v20-notice p{margin:0;padding:13px 14px;border:1px solid var(--line);border-radius:16px;background:var(--soft);font-size:13px;line-height:1.65;white-space:pre-line}
@media(max-width:430px){.me-v20-shell{padding-left:9px;padding-right:9px}.me-v20-profile{padding:15px}.me-v20-avatar{width:80px;height:80px;flex-basis:80px}.me-v20-identity h1{font-size:21px}.me-v20-assets{gap:6px}.me-v20-assets>div{padding:11px 2px}.me-v20-assets b{font-size:14px}.me-v20-card{padding:13px}.me-v20-today,.me-v20-quick{gap:6px}.me-v20-quick .me-quick-item{min-height:75px}}

"""
