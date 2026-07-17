import datetime
import hashlib
import hmac
import html
import json
import os
import secrets
import base64
from urllib.parse import urlencode, quote
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from database import get_connection

TZ = datetime.timezone(datetime.timedelta(hours=8))
WEB_SECRET = os.getenv('RAINBOW_WEB_SECRET') or os.getenv('LINE_CHANNEL_SECRET') or 'rainbow-life-change-me'
OWNER_USER_ID = os.getenv('RAINBOW_OWNER_USER_ID', '').strip()
TOKEN_TTL = 60 * 60 * 12
PLAYER_COOKIE = 'rainbow_player'
ADMIN_COOKIE = 'rainbow_admin'
SESSION_TTL = 60 * 60 * 12

router = APIRouter()


def _now_ts():
    return int(datetime.datetime.now(TZ).timestamp())


def _sign(user_id, group_id, exp):
    raw = f'{user_id}|{group_id}|{exp}'.encode('utf-8')
    return hmac.new(WEB_SECRET.encode('utf-8'), raw, hashlib.sha256).hexdigest()


def _public_base_url():
    raw = (os.getenv('PUBLIC_BASE_URL') or os.getenv('RENDER_EXTERNAL_URL') or os.getenv('APP_BASE_URL') or '').strip()
    if not raw:
        host = (os.getenv('RENDER_EXTERNAL_HOSTNAME') or '').strip()
        if host:
            raw = 'https://' + host
    if raw and not raw.startswith(('http://','https://')):
        raw = 'https://' + raw
    return raw.rstrip('/')


def make_access_url(user_id, group_id, path='/player'):
    base = _public_base_url()
    if not base:
        return ''
    exp = _now_ts() + TOKEN_TTL
    sep = '&' if '?' in path else '?'
    query = urlencode({'uid': user_id, 'gid': group_id, 'exp': exp, 'sig': _sign(user_id, group_id, exp)})
    return f'{base}{path}{sep}{query}'


def make_player_entry_url(group_id, target='/player'):
    base = _public_base_url()
    if not base or not group_id or group_id == 'PRIVATE':
        return ''
    safe_target = target if str(target).startswith('/player') else '/player'
    return f"{base}/player/entry?{urlencode({'gid':str(group_id),'target':safe_target})}"


def _token_secret(kind='player'):
    if kind == 'admin':
        return os.getenv('ADMIN_WEB_SECRET') or os.getenv('LINE_CHANNEL_SECRET') or WEB_SECRET
    return WEB_SECRET


def _encode_token(data, kind='player'):
    payload = base64.urlsafe_b64encode(json.dumps(data,separators=(',',':')).encode()).decode().rstrip('=')
    sig = hmac.new(_token_secret(kind).encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + sig


def _decode_token(token, kind='player'):
    try:
        payload, sig = token.split('.',1)
        expected = hmac.new(_token_secret(kind).encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected): return None
        data = json.loads(base64.urlsafe_b64decode((payload + '=' * (-len(payload)%4)).encode()).decode())
        if int(data.get('exp',0)) < _now_ts(): return None
        return data
    except Exception:
        return None


def _session_auth(request):
    for cookie, kind in ((PLAYER_COOKIE,'player'),(ADMIN_COOKIE,'admin')):
        data = _decode_token(request.cookies.get(cookie,''), kind)
        if data and data.get('uid') and data.get('gid'):
            return str(data['uid']), str(data['gid'])
    return None


def _auth(request: Request):
    session = _session_auth(request)
    if session:
        return session
    uid = str(request.query_params.get('uid') or request.headers.get('x-rainbow-user') or '').strip()
    gid = str(request.query_params.get('gid') or request.headers.get('x-rainbow-group') or '').strip()
    exp = str(request.query_params.get('exp') or '').strip()
    sig = str(request.query_params.get('sig') or '').strip()
    if not uid or not gid or not exp or not sig:
        raise HTTPException(status_code=401, detail='登入連結不完整，請回到 LINE 重新開啟個人中心。')
    try:
        exp_i = int(exp)
    except ValueError:
        raise HTTPException(status_code=401, detail='登入連結格式錯誤。')
    if exp_i < _now_ts():
        raise HTTPException(status_code=401, detail='登入連結已過期，請回到 LINE 重新開啟。')
    if not hmac.compare_digest(sig, _sign(uid, gid, exp_i)):
        raise HTTPException(status_code=401, detail='登入驗證失敗。')
    return uid, gid


def _oauth_ready():
    return bool(os.getenv('LINE_LOGIN_CHANNEL_ID') and os.getenv('LINE_LOGIN_CHANNEL_SECRET') and _public_base_url())


def _oauth_callback_url():
    return _public_base_url() + '/player/oauth/callback'


def _line_authorize_url(gid, target):
    state = _encode_token({'gid':gid,'target':target,'kind':'line_oauth_state','exp':_now_ts()+600})
    params = {'response_type':'code','client_id':os.getenv('LINE_LOGIN_CHANNEL_ID',''),'redirect_uri':_oauth_callback_url(),'state':state,'scope':'openid profile'}
    return 'https://access.line.me/oauth2/v2.1/authorize?' + urlencode(params)


def _exchange_line_code(code):
    payload = urlencode({'grant_type':'authorization_code','code':code,'redirect_uri':_oauth_callback_url(),'client_id':os.getenv('LINE_LOGIN_CHANNEL_ID',''),'client_secret':os.getenv('LINE_LOGIN_CHANNEL_SECRET','')}).encode()
    req = UrlRequest('https://api.line.me/oauth2/v2.1/token', data=payload, headers={'Content-Type':'application/x-www-form-urlencoded'}, method='POST')
    try:
        with urlopen(req, timeout=15) as r: return json.loads(r.read().decode())
    except Exception: return {}


def _verify_id_token(id_token):
    payload = urlencode({'id_token':id_token,'client_id':os.getenv('LINE_LOGIN_CHANNEL_ID','')}).encode()
    req = UrlRequest('https://api.line.me/oauth2/v2.1/verify', data=payload, headers={'Content-Type':'application/x-www-form-urlencoded'}, method='POST')
    try:
        with urlopen(req, timeout=15) as r: return json.loads(r.read().decode())
    except Exception: return {}


def _role(group_id, user_id):
    if OWNER_USER_ID and user_id == OWNER_USER_ID:
        return 'owner'
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute('SELECT role FROM admins WHERE group_id=%s AND user_id=%s', (group_id, user_id))
            row = c.fetchone() or {}
            role = str(row.get('role') or '').lower()
            if role in ('owner', 'superadmin', 'system_owner'):
                return 'owner'
            if role in ('leader', 'group_owner', '群長'):
                return 'leader'
            if row:
                return 'admin'
    finally:
        conn.close()
    return 'member'


def _line_profile(line_bot_api, group_id, user_id, fallback_name):
    name = fallback_name or 'Rainbow 成員'
    picture = ''
    try:
        profile = line_bot_api.get_group_member_profile(group_id, user_id)
        name = getattr(profile, 'display_name', None) or name
        picture = getattr(profile, 'picture_url', None) or ''
    except Exception:
        try:
            profile = line_bot_api.get_profile(user_id)
            name = getattr(profile, 'display_name', None) or name
            picture = getattr(profile, 'picture_url', None) or ''
        except Exception:
            pass
    return name, picture


def ensure_web_tables():
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS web_announcements (
                id BIGSERIAL PRIMARY KEY, group_id TEXT NOT NULL, title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS web_calendar_events (
                id BIGSERIAL PRIMARY KEY, group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                event_date TEXT NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS web_profile_settings (
                group_id TEXT NOT NULL, user_id TEXT NOT NULL, bio TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL DEFAULT '', theme TEXT NOT NULL DEFAULT 'rainbow-cosmos',
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,user_id)
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_web_events_user_date ON web_calendar_events(group_id,user_id,event_date)')
            c.execute("""CREATE TABLE IF NOT EXISTS web_avatar_frames (
                frame_key TEXT PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL DEFAULT 0,
                vip_only BOOLEAN NOT NULL DEFAULT FALSE, owner_only BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS web_user_frames (
                group_id TEXT NOT NULL, user_id TEXT NOT NULL, frame_key TEXT NOT NULL,
                equipped BOOLEAN NOT NULL DEFAULT FALSE, acquired_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,user_id,frame_key)
            )""")
            for frame in [('rainbow_basic','🌈 彩虹星光框',0,False,False),('star_guard','✨ 星曜守護框',1200,False,False),('ice_crystal','❄️ 冰晶彩虹框',1800,False,False),('diamond_crown','💎 VIP鑽石框',0,True,False),('leader_glory','👑 Owner榮耀框',0,False,True)]:
                c.execute("""INSERT INTO web_avatar_frames(frame_key,name,price,vip_only,owner_only)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(frame_key) DO NOTHING""", frame)
        conn.commit()
    finally:
        conn.close()


def _player_data(line_bot_api, group_id, user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute('SELECT * FROM players WHERE group_id=%s AND user_id=%s', (group_id, user_id))
            p = c.fetchone()
            if not p:
                raise HTTPException(status_code=404, detail='找不到成員資料，請先在群組內使用一次機器人。')
            c.execute('SELECT COUNT(*) AS total FROM sign_records WHERE group_id=%s AND user_id=%s', (group_id, user_id))
            total_sign = int((c.fetchone() or {}).get('total') or 0)
            c.execute('SELECT bio,region,theme FROM web_profile_settings WHERE group_id=%s AND user_id=%s', (group_id, user_id))
            profile = c.fetchone() or {}
            c.execute('SELECT id,title,content,created_at FROM web_announcements WHERE group_id=%s AND is_active=1 ORDER BY id DESC LIMIT 5', (group_id,))
            announcements = [dict(x) for x in c.fetchall()]
            c.execute('SELECT id,event_date,title,note FROM web_calendar_events WHERE group_id=%s AND user_id=%s ORDER BY event_date ASC LIMIT 30', (group_id, user_id))
            events = [dict(x) for x in c.fetchall()]
    finally:
        conn.close()
    name, picture = _line_profile(line_bot_api, group_id, user_id, p.get('name'))
    role = _role(group_id, user_id)
    level = max(1, int(p.get('level') or 1))
    exp = int(p.get('exp') or 0)
    level_exp = int(p.get('level_exp') or (exp % 100))
    needed = max(100, int(100 * (1.15 ** max(level - 1, 0))))
    vip_until = str(p.get('vip_until') or '')
    is_vip = int(p.get('is_vip') or 0) == 1
    return {
        'user_id': user_id, 'group_id': group_id, 'name': name, 'picture_url': picture,
        'role': role, 'level': level, 'exp': exp, 'level_exp': level_exp, 'exp_needed': needed,
        'vip': is_vip, 'vip_until': vip_until, 'title': str(p.get('custom_title') or '彩虹旅人'),
        'birthday': str(p.get('birthday') or '尚未設定'), 'streak': int(p.get('streak_count') or 0),
        'total_sign': total_sign, 'today_messages': int(p.get('today_msg_count') or 0),
        'today_stickers': int(p.get('today_sticker_count') or 0),
        'fortune': str(p.get('fortune_level') or '尚未占卜'), 'fortune_message': str(p.get('fortune_message') or ''),
        'bio': str(profile.get('bio') or ''), 'region': str(profile.get('region') or ''),
        'theme': str(profile.get('theme') or 'rainbow-cosmos'),
        'announcements': announcements, 'events': events,
        'equipped_frame': _equipped_frame(group_id, user_id),
    }



def _equipped_frame(group_id, user_id):
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute('SELECT frame_key FROM web_user_frames WHERE group_id=%s AND user_id=%s AND equipped=TRUE LIMIT 1',(group_id,user_id))
            row=c.fetchone() or {}
            return row.get('frame_key') or 'rainbow_basic'
    except Exception:
        return 'rainbow_basic'
    finally:
        conn.close()

def _query_suffix(request):
    return '?' + urlencode({k: request.query_params[k] for k in ('uid','gid','exp','sig') if request.query_params.get(k)})


def _dashboard_html():
    return r'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#120b38"><title>Rainbow Life</title>
<style>
:root{--panel:rgba(24,16,67,.82);--line:rgba(197,172,255,.38);--muted:#d6cff0}*{box-sizing:border-box}body{margin:0;color:#fff;font-family:system-ui,-apple-system,"Noto Sans TC",sans-serif;background:#07051a;min-height:100vh;overflow-x:hidden}body:before{content:"";position:fixed;inset:-25%;z-index:-3;background:radial-gradient(circle at 15% 18%,rgba(70,155,255,.32),transparent 26%),radial-gradient(circle at 80% 20%,rgba(255,86,192,.28),transparent 25%),radial-gradient(circle at 50% 85%,rgba(144,82,255,.34),transparent 28%),linear-gradient(145deg,#07051a,#16073b 55%,#07152b);animation:cosmos 14s ease-in-out infinite alternate}.stars{position:fixed;inset:0;z-index:-2;pointer-events:none;background-image:radial-gradient(circle,#fff 1px,transparent 1.6px),radial-gradient(circle,rgba(151,216,255,.9) 1px,transparent 1.8px);background-size:42px 42px,73px 73px;opacity:.22;animation:stars 28s linear infinite}@keyframes cosmos{to{transform:scale(1.07) rotate(2deg);filter:hue-rotate(15deg)}}@keyframes stars{to{background-position:220px 330px,310px 250px}}@keyframes spin{to{transform:rotate(360deg)}}@keyframes float{50%{transform:translateY(-7px)}}@keyframes shimmer{to{background-position:220% center}}.app{display:grid;grid-template-columns:230px minmax(0,1fr);min-height:100vh}.side{padding:22px 14px;border-right:1px solid rgba(155,129,255,.24);background:rgba(8,5,29,.73);backdrop-filter:blur(22px);position:sticky;top:0;height:100vh}.logo{font-size:22px;font-weight:900;margin:0 12px 24px;background:linear-gradient(90deg,#79d8ff,#d98cff,#ff8dcf,#ffe17b);background-size:220% auto;-webkit-background-clip:text;color:transparent;animation:shimmer 4s linear infinite}.nav-title{font-size:11px;letter-spacing:.14em;color:#aaa0d3;margin:20px 12px 8px}.nav button{width:100%;border:1px solid transparent;color:#f4efff;background:transparent;text-align:left;padding:12px 13px;border-radius:14px;margin:3px 0;font-size:14px}.nav button.active,.nav button:hover{background:linear-gradient(90deg,rgba(122,80,255,.78),rgba(233,74,185,.72));border-color:rgba(255,255,255,.16)}.main{padding:22px;max-width:1300px;width:100%;margin:auto}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}.top h2{margin:0;font-size:20px}.badge{padding:8px 13px;border-radius:999px;background:rgba(42,29,100,.72);border:1px solid var(--line);font-size:12px}.card{border:1px solid var(--line);background:linear-gradient(155deg,rgba(31,21,80,.84),rgba(15,11,48,.80));border-radius:22px;padding:17px;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 18px 50px rgba(0,0,0,.23);backdrop-filter:blur(17px)}.card h3{margin:0 0 12px}.version-mark{font-size:11px;color:#cfc4ff;text-align:right;margin:-6px 2px 8px;opacity:.75}.dashboard-carousel{display:none;margin-bottom:14px;background:linear-gradient(125deg,rgba(70,30,130,.92),rgba(105,35,125,.84))}.dashboard-carousel.show{display:block}.carousel-head{display:flex;justify-content:space-between;align-items:center}.dots{display:flex;gap:5px}.dot{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.3)}.dot.active{width:18px;border-radius:8px;background:#fff}.slide{min-height:118px;display:none;align-items:center;gap:14px}.slide.active{display:flex}.slide-icon{font-size:42px;width:62px;height:62px;display:grid;place-items:center;border-radius:18px;background:rgba(255,255,255,.1)}.slide b{font-size:22px}.slide small{display:block;color:var(--muted);margin-bottom:5px}.hero{position:relative;overflow:hidden;min-height:300px;padding:27px;display:grid;grid-template-columns:210px 1fr;gap:25px;align-items:center;background:linear-gradient(125deg,rgba(34,21,88,.94),rgba(62,38,143,.84) 52%,rgba(137,48,147,.79))}.avatar-wrap{position:relative;display:grid;place-items:center;animation:float 5s ease-in-out infinite}.avatar-wrap:before{content:"";position:absolute;width:212px;height:212px;border-radius:50%;background:conic-gradient(#6ed8ff,#9a72ff,#ff68c8,#ffd86a,#6ed8ff);animation:spin 7s linear infinite;box-shadow:0 0 40px rgba(177,111,255,.7)}.avatar-wrap.leader:before,.avatar-wrap.owner:before{width:222px;height:222px;background:conic-gradient(#ffe98a,#ffb32f,#ff6dcc,#8edfff,#fff4a8,#ffe98a);box-shadow:0 0 24px #ffd45f,0 0 55px rgba(255,93,203,.8)}.avatar-wrap.leader:after,.avatar-wrap.owner:after{content:"✦  ✧  ✦";position:absolute;z-index:5;bottom:-25px;color:#ffe688;font-size:20px;letter-spacing:10px;text-shadow:0 0 10px #fff,0 0 20px #ffbd42;animation:float 2.5s ease-in-out infinite}.avatar{position:relative;z-index:2;width:194px;height:194px;border-radius:50%;object-fit:cover;object-position:center;background:#17103f;border:7px solid #100b33}.crown{position:absolute;z-index:6;top:-35px;left:50%;transform:translateX(-50%);font-size:49px;filter:drop-shadow(0 0 12px #ffd75a);animation:float 2.7s ease-in-out infinite}.hero-info{min-width:0}.eyebrow{font-size:11px;letter-spacing:.16em;color:#d7ccff}.hero h1{font-size:35px;margin:4px 0 8px;background:linear-gradient(90deg,#fff,#cfbaff,#ff9fd8);-webkit-background-clip:text;color:transparent}.title-pill{display:inline-flex;padding:7px 12px;border:1px solid rgba(255,255,255,.18);border-radius:999px;background:rgba(12,8,43,.42)}.personal-card{margin-top:14px;background:linear-gradient(135deg,rgba(43,27,105,.92),rgba(95,34,119,.82));min-height:160px}.personal-carousel{margin-top:17px}.personal-slide{display:none}.personal-slide.active{display:block}.personal-slide small{color:var(--muted)}.personal-value{font-size:20px;font-weight:800;margin:5px 0}.level-row{display:flex;justify-content:space-between;gap:10px}.progress{height:13px;background:rgba(7,5,30,.72);border-radius:99px;overflow:hidden;margin-top:9px;border:1px solid rgba(255,255,255,.09)}.progress i{display:block;min-width:10px;height:100%;background:linear-gradient(90deg,#62c7ff,#9b74ff,#ff68c8,#ffd86a);background-size:220% auto;animation:shimmer 3s linear infinite;box-shadow:0 0 16px rgba(255,104,200,.8)}.grid{display:grid;grid-template-columns:1.25fr .95fr;gap:14px;margin-top:14px}.announcement{min-height:220px;position:relative;overflow:hidden;background:linear-gradient(130deg,rgba(48,29,117,.92),rgba(120,42,125,.80))}.announcement .boy{position:absolute;right:-10px;bottom:-35px;width:220px;filter:drop-shadow(0 14px 18px rgba(0,0,0,.32));animation:float 5s ease-in-out infinite}.announcement-content{max-width:64%;min-height:130px}.announcement-content h2{font-size:23px}.announcement-content p{line-height:1.7}.daily-slide{display:none;min-height:58px;padding:14px;border-radius:16px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08)}.daily-slide.active{display:block}.daily-slide small{color:var(--muted)}.daily-slide b{display:block;font-size:18px;margin-top:5px}.quick-card{margin-top:14px}.quick{display:grid;grid-template-columns:repeat(7,1fr);gap:9px}.quick button{border:1px solid rgba(178,153,255,.28);background:linear-gradient(155deg,rgba(37,25,96,.9),rgba(25,18,67,.82));color:white;border-radius:17px;padding:15px 6px;font-size:12px}.quick span{display:block;font-size:25px;margin-bottom:6px}.admin-zone,.owner-zone{display:none;margin-top:14px}.admin-zone.show,.owner-zone.show{display:block}.btn{border:0;border-radius:12px;padding:10px 14px;background:linear-gradient(90deg,#744dff,#e14cb9);color:#fff}.events,.admin-list{padding:0}.events li,.admin-list li{list-style:none;padding:11px 0;border-bottom:1px solid rgba(117,99,190,.35)}.sub{color:var(--muted)}.modal{display:none;position:fixed;inset:0;z-index:50;background:rgba(2,1,12,.75);place-items:center;padding:18px}.modal.show{display:grid}.dialog{width:min(520px,100%);max-height:80vh;overflow:auto;background:#171044;border:1px solid var(--line);border-radius:22px;padding:20px}.dialog input,.dialog textarea{width:100%;margin:7px 0;padding:12px;border-radius:12px;border:1px solid var(--line);background:#0c082b;color:#fff}.toast{display:none;position:fixed;left:50%;bottom:85px;transform:translateX(-50%);z-index:70;background:#20155b;border:1px solid var(--line);padding:11px 16px;border-radius:99px}.bottom{display:none}@media(max-width:760px){.app{display:block}.side{display:none}.main{padding:12px 12px 85px}.top h2{font-size:17px}.hero{grid-template-columns:1fr;text-align:center;padding:24px 17px}.avatar{width:174px;height:174px}.avatar-wrap:before{width:190px;height:190px}.avatar-wrap.leader:before,.avatar-wrap.owner:before{width:202px;height:202px}.grid{grid-template-columns:1fr}.announcement-content{max-width:66%}.announcement .boy{width:170px}.quick{grid-template-columns:repeat(4,1fr)}.bottom{display:grid;grid-template-columns:repeat(3,1fr);position:fixed;left:0;right:0;bottom:0;z-index:30;background:rgba(9,6,31,.94);backdrop-filter:blur(18px);border-top:1px solid var(--line);padding:8px 8px calc(8px + env(safe-area-inset-bottom))}.bottom button{border:0;background:transparent;color:#fff}.bottom span{display:block;font-size:21px}}
</style></head><body><div class="stars"></div><div class="app"><aside class="side"><div class="logo">🌈 Rainbow Life</div><div class="nav"><button class="active" onclick="go('home')">🏠 個人中心</button><button onclick="go('calendar')">📅 行事曆</button><button onclick="action('frame')">🖼️ 頭像框</button><button onclick="action('fortune')">🔮 今日運勢</button><div class="nav-title">管理中心</div><button class="admin-menu" onclick="go('admin')">🛠️ 管理中心</button><div class="nav-title owner-menu">Rainbow Life 控制台</div><button class="owner-menu" onclick="go('owner')">👑 系統最高權限</button></div></aside><main class="main"><header class="top"><h2>👑 Rainbow Life 個人中心</h2><div class="badge" id="roleBadge">載入中</div></header><section id="home"><div class="version-mark">V2 個人中心輪播修正版</div><section class="card dashboard-carousel" id="leaderDashboard"><div class="carousel-head"><h3>👑 群長儀表板輪播</h3><div class="dots" id="dashDots"></div></div><div id="dashSlides"><div class="slide active"><div class="slide-icon">👑</div><div><small>群長儀表板</small><b>資料載入中</b><div>正在讀取群組資訊</div></div></div></div></section><section class="card hero"><div class="avatar-wrap" id="avatarWrap"><span class="crown" id="crown">👑</span><img class="avatar" id="avatar" alt="LINE 大頭照"></div><div class="hero-info"><div class="eyebrow">RAINBOW COSMOS PROFILE</div><h1 id="name">Rainbow</h1><div class="title-pill">🌈 <span id="title">Rainbow Life</span></div></div></section><section class="card personal-card"><div class="carousel-head"><h3>👤 個人資訊輪播</h3><div class="dots" id="personalDots"></div></div><div id="personalSlides"><div class="personal-slide active"><small>個人資訊</small><div class="personal-value">資料載入中</div><div>正在同步你的最新資料</div></div></div></section><div class="grid"><section class="card announcement"><h3>📢 公告輪播</h3><div class="announcement-content" id="announcement"></div><div class="dots" id="announcementDots"></div><img class="boy" src="/rainbow-static/rainbow_life_boy.png"></section><section class="card"><h3>✨ 每日訊息輪播</h3><div id="dailySlides"></div><div class="dots" id="dailyDots" style="margin-top:12px"></div></section></div><section class="card quick-card"><h3>⚡ 快捷功能</h3><div class="quick"><button onclick="action('frame')"><span>🖼️</span>頭像框</button><button onclick="action('achievement')"><span>🏆</span>成就</button><button onclick="action('fortune')"><span>🔮</span>運勢</button><button onclick="go('calendar')"><span>📅</span>行事曆</button><button onclick="unavailable('商店')"><span>🛍️</span>商店</button><button onclick="unavailable('轉盤')"><span>🎡</span>轉盤</button><button onclick="editProfile()"><span>⚙️</span>設定</button></div></section></section><section id="calendar" class="card" style="display:none"><h3>📅 我的行事曆</h3><button class="btn" onclick="eventModal()">新增提醒</button><ul class="events" id="eventList"></ul></section><section id="admin" class="card admin-zone"><h3>🛠️ 管理中心</h3><div class="quick"><button onclick="adminAction('members')"><span>👥</span>成員管理</button><button onclick="announcementModal()"><span>📢</span>公告管理</button><button onclick="adminAction('frames')"><span>🖼️</span>頭像框</button><button onclick="adminAction('titles')"><span>🏅</span>稱號管理</button><button onclick="adminAction('permissions')"><span>🛡️</span>權限管理</button><button onclick="adminAction('settings')"><span>⚙️</span>系統設定</button><button onclick="adminAction('logs')"><span>📋</span>操作紀錄</button></div><div id="adminResult"></div></section><section id="owner" class="card owner-zone"><h3>👑 Rainbow Life 控制台</h3><div class="quick"><button onclick="ownerAction('overview')"><span>📊</span>系統總覽</button><button onclick="ownerAction('groups')"><span>🌈</span>所有群組</button><button onclick="ownerAction('leaders')"><span>👑</span>群長管理</button><button onclick="ownerAction('global')"><span>📢</span>全站公告</button></div><div id="ownerResult"></div></section></main></div><nav class="bottom"><button onclick="go('home')"><span>🏠</span>首頁</button><button onclick="go('calendar')"><span>📅</span>行事曆</button><button onclick="go(DATA&&DATA.role==='owner'?'owner':(DATA&&DATA.role!=='member'?'admin':'home'))"><span>👤</span>我的</button></nav><div class="modal" id="modal"><div class="dialog" id="dialog"></div></div><div class="toast" id="toast"></div>
<script>
let DATA=null,timers=[];const Q=location.search;function api(path,opt={}){return fetch(path+Q,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{})}}).then(async r=>{let j=await r.json();if(!r.ok)throw new Error(j.detail||'操作失敗');return j})}function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function toast(t){let e=document.getElementById('toast');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',2200)}function unavailable(n){toast(n+'功能尚未開放')}function closeModal(){document.getElementById('modal').classList.remove('show')}function go(id){['home','calendar','admin','owner'].forEach(x=>document.getElementById(x).style.display=x===id?'block':'none');if(id==='admin')loadAdmin();if(id==='owner')ownerAction('overview')}function carousel(slides,dots,interval=4500){if(!slides.length)return;let i=0;function show(n){slides.forEach((e,k)=>e.classList.toggle('active',k===n));dots.forEach((e,k)=>e.classList.toggle('active',k===n))}show(0);if(slides.length>1)timers.push(setInterval(()=>{i=(i+1)%slides.length;show(i)},interval))}function makeDots(id,n){let e=document.getElementById(id);e.innerHTML=Array.from({length:n},()=>'<i class="dot"></i>').join('');return [...e.children]}function compact(n){n=Number(n)||0;return n>=1e15?(n/1e15).toFixed(2)+'Q':n>=1e12?(n/1e12).toFixed(2)+'T':n>=1e9?(n/1e9).toFixed(2)+'B':n>=1e6?(n/1e6).toFixed(2)+'M':n.toLocaleString()}async function action(name){try{let j=await api('/api/rainbow/feature/'+name,{method:name==='fortune'?'POST':'GET'});showFeature(name,j);if(name==='fortune'){DATA=await api('/api/rainbow/me');renderPersonal();renderDaily()}}catch(e){toast(e.message)}}function showFeature(name,j){let h='<h3>'+esc(j.title||name)+'</h3>';if(j.message)h+='<p style="white-space:pre-wrap">'+esc(j.message)+'</p>';if(j.items)h+='<ul class="admin-list">'+j.items.map(x=>'<li><b>'+esc(x.name||x.title_name||x.item_name)+'</b>'+(x.description?'<br><span class="sub">'+esc(x.description)+'</span>':'')+(name==='frame'?'<br><button class="btn" onclick="equipFrame('+JSON.stringify(x.frame_key)+')">套用</button>':'')+'</li>').join('')+'</ul>';document.getElementById('dialog').innerHTML=h+'<button class="btn" onclick="closeModal()">關閉</button>';document.getElementById('modal').classList.add('show')}async function equipFrame(n){try{let j=await api('/api/rainbow/frame/equip',{method:'POST',body:JSON.stringify({frame_key:n})});toast(j.message);closeModal()}catch(e){toast(e.message)}}function roleText(r){return {owner:'👑 Rainbow Life Owner',leader:'👑 群長',admin:'🛡️ 管理員',member:'👤 一般成員'}[r]||r}function renderPersonal(){let pct=Math.min(100,Math.max(1,Math.round(DATA.level_exp/Math.max(1,DATA.exp_needed)*100)));let items=[['⭐ 等級與經驗','LV.'+DATA.level,'<div class="level-row"><small>目前 '+compact(DATA.level_exp)+'</small><small>需要 '+compact(DATA.exp_needed)+'</small></div><div class="progress"><i style="width:'+pct+'%"></i></div>'],['💎 VIP 狀態',DATA.vip?(DATA.vip_until||'永久 VIP'):'一般會員','成就達標後可永久解鎖 VIP'],['🏆 成就進度',DATA.achievement_stage||'持續累積中','完成條件後會自動升級'],['🌈 目前稱號',DATA.title||'彩虹旅人','頭像框：'+(DATA.equipped_frame||'rainbow_basic')],['🎂 生日資訊',DATA.birthday||'尚未設定','連續簽到 '+DATA.streak+' 天'],['🔮 今日運勢',DATA.fortune||'尚未占卜',esc(DATA.fortune_message||'點選快捷鍵查看今日運勢')],['💬 今日活躍',DATA.today_messages+' 則訊息','貼圖 '+DATA.today_stickers+' 張']];let box=document.getElementById('personalSlides');box.innerHTML=items.map(x=>'<div class="personal-slide"><small>'+x[0]+'</small><div class="personal-value">'+esc(x[1])+'</div><div>'+x[2]+'</div></div>').join('');carousel([...box.children],makeDots('personalDots',items.length),4300)}async function renderDashboard(){if(DATA.role==='member')return;document.getElementById('leaderDashboard').classList.add('show');let d={members:'--',vip:'--',admins:'--',today_messages:DATA.today_messages};try{d=await api('/api/rainbow/admin/overview')}catch(e){}let items=[['👥','群組成員',d.members+' 人','目前群組成員總數'],['🤖','機器人狀態','正常運行','Rainbow Life 服務正常'],['💬','今日聊天',d.today_messages+' 則','群組今日活躍統計'],['💎','VIP 成員',d.vip+' 人','已解鎖 VIP 的成員'],['🛡️','管理團隊',d.admins+' 人','群長與管理員'],['🎂','生日提醒',DATA.birthday==='尚未設定'?'尚未設定':'已設定','個人生日：'+DATA.birthday],['📢','最新公告',(DATA.announcements||[]).length?'有新公告':'目前無公告','向左輪播查看公告內容']];let box=document.getElementById('dashSlides');box.innerHTML=items.map(x=>'<div class="slide"><div class="slide-icon">'+x[0]+'</div><div><small>'+x[1]+'</small><b>'+esc(x[2])+'</b><div>'+esc(x[3])+'</div></div></div>').join('');document.getElementById('leaderDashboard').classList.add('show');carousel([...box.children],makeDots('dashDots',items.length),4600)}function renderAnnouncements(){let a=(DATA.announcements||[]).filter(x=>!/(轉盤|輪盤|抽獎)/.test((x.title||'')+(x.content||'')));if(!a.length)a=[{title:'全新主題上線',content:'歡迎回到 Rainbow Life 個人中心。'}];let box=document.getElementById('announcement');box.innerHTML=a.map(x=>'<div class="personal-slide"><h2>'+esc(x.title)+'</h2><p>'+esc(x.content)+'</p></div>').join('');carousel([...box.children],makeDots('announcementDots',a.length),5200)}function renderDaily(){let items=[['🔮 今日運勢',DATA.fortune||'尚未占卜'],['💬 今日聊天',DATA.today_messages+' 則'],['🖼️ 今日貼圖',DATA.today_stickers+' 張'],['🔥 連續簽到',DATA.streak+' 天'],['🎂 生日資訊',DATA.birthday||'尚未設定']];let box=document.getElementById('dailySlides');box.innerHTML=items.map(x=>'<div class="daily-slide"><small>'+x[0]+'</small><b>'+esc(x[1])+'</b></div>').join('');carousel([...box.children],makeDots('dailyDots',items.length),4100)}async function load(){try{DATA=await api('/api/rainbow/me');document.getElementById('name').textContent=DATA.name;document.getElementById('title').textContent=DATA.title;document.getElementById('roleBadge').textContent=roleText(DATA.role);document.getElementById('avatar').src=DATA.picture_url||'/rainbow-static/rainbow_life_boy.png';document.getElementById('avatarWrap').classList.add(DATA.role);if(DATA.role==='member'){document.querySelectorAll('.admin-menu,.owner-menu').forEach(e=>e.style.display='none');document.getElementById('crown').style.display='none'}else{document.getElementById('admin').classList.add('show')}if(DATA.role==='owner'){document.querySelectorAll('.owner-menu').forEach(e=>e.style.display='block');document.getElementById('owner').classList.add('show')}try{renderDashboard()}catch(e){console.error('dashboard',e)}try{renderPersonal()}catch(e){console.error('personal',e)}try{renderAnnouncements()}catch(e){console.error('announcement',e)}try{renderDaily()}catch(e){console.error('daily',e)}try{renderEvents()}catch(e){console.error('events',e)}}catch(e){document.body.innerHTML='<div style="padding:40px;color:white;text-align:center"><h2>無法開啟 Rainbow Life</h2><p>'+esc(e.message)+'</p><p>請回到 LINE 群組重新點選「個人中心」。</p></div>'}}function renderEvents(){document.getElementById('eventList').innerHTML=(DATA.events||[]).map(e=>'<li><b>'+esc(e.event_date)+'</b>　'+esc(e.title)+'<br><span class="sub">'+esc(e.note)+'</span></li>').join('')||'<li>目前沒有提醒事項</li>'}function eventModal(){document.getElementById('dialog').innerHTML='<h3>新增提醒</h3><input id="ed" type="date"><input id="et" placeholder="提醒標題"><textarea id="en" placeholder="備註"></textarea><button class="btn" onclick="saveEvent()">儲存</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveEvent(){try{await api('/api/rainbow/calendar',{method:'POST',body:JSON.stringify({event_date:ed.value,title:et.value,note:en.value})});closeModal();toast('已儲存提醒');DATA=await api('/api/rainbow/me');renderEvents()}catch(e){toast(e.message)}}function editProfile(){document.getElementById('dialog').innerHTML='<h3>個人設定</h3><input id="bio" placeholder="自我介紹" value="'+esc(DATA.bio)+'"><input id="region" placeholder="地區" value="'+esc(DATA.region)+'"><button class="btn" onclick="saveProfile()">儲存</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveProfile(){try{await api('/api/rainbow/profile',{method:'POST',body:JSON.stringify({bio:bio.value,region:region.value})});closeModal();toast('個人設定已儲存')}catch(e){toast(e.message)}}function announcementModal(){document.getElementById('dialog').innerHTML='<h3>新增群組公告</h3><input id="at" placeholder="公告標題"><textarea id="ac" placeholder="公告內容"></textarea><button class="btn" onclick="saveAnnouncement()">發布</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveAnnouncement(){try{await api('/api/rainbow/admin/announcement',{method:'POST',body:JSON.stringify({title:at.value,content:ac.value})});closeModal();toast('公告已發布');DATA=await api('/api/rainbow/me');renderAnnouncements()}catch(e){toast(e.message)}}async function loadAdmin(){if(!DATA||DATA.role==='member')return;let j=await api('/api/rainbow/admin/overview');document.getElementById('adminResult').innerHTML='<ul class="admin-list"><li>👥 成員總數：<b>'+j.members+'</b></li><li>💎 VIP：<b>'+j.vip+'</b></li><li>🛡️ 管理人員：<b>'+j.admins+'</b></li><li>💬 今日聊天：<b>'+j.today_messages+'</b></li></ul>'}async function adminAction(k){if(k==='members'){let j=await api('/api/rainbow/admin/members');document.getElementById('adminResult').innerHTML='<ul class="admin-list">'+j.items.map(x=>'<li>'+esc(x.name)+'　Lv.'+x.level+'</li>').join('')+'</ul>'}else{let j=await api('/api/rainbow/admin/'+k);document.getElementById('adminResult').innerHTML='<pre style="white-space:pre-wrap">'+esc(JSON.stringify(j,null,2))+'</pre>'}}async function ownerAction(k){if(!DATA||DATA.role!=='owner')return;let j=await api('/api/rainbow/owner/'+k);document.getElementById('ownerResult').innerHTML='<pre style="white-space:pre-wrap">'+esc(JSON.stringify(j,null,2))+'</pre>'}load();
</script></body></html>'''

def register_rainbow_web(app, line_bot_api):
    # 不讓資料庫短暫連線失敗阻止 Render 啟動；實際開啟頁面/API 時仍會回報連線錯誤。
    try:
        ensure_web_tables()
    except Exception as exc:
        print(f'[Rainbow Life] web table initialization deferred: {exc}')
    assets_dir = os.path.join(os.path.dirname(__file__), 'rainbow_static')
    try:
        app.mount('/rainbow-static', StaticFiles(directory=assets_dir), name='rainbow-static')
    except RuntimeError:
        pass

    @app.get('/player', response_class=HTMLResponse)
    async def player_page(request: Request):
        _auth(request)
        return HTMLResponse(_dashboard_html(), headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0','Pragma':'no-cache','Expires':'0'})

    @app.get('/player/entry')
    async def player_entry(request: Request, gid: str = '', target: str = '/player'):
        # 新舊入口皆支援：舊簽章直接通過；只有 gid 的共用入口則走 LINE Login。
        if request.query_params.get('uid') and request.query_params.get('sig'):
            _auth(request)
            return RedirectResponse('/player' + _query_suffix(request), status_code=302)
        gid = str(gid or '').strip()
        target = target if str(target).startswith('/player') else '/player'
        if not gid:
            raise HTTPException(400, '入口缺少群組資訊，請回 LINE 群組重新開啟。')
        if not _oauth_ready():
            raise HTTPException(503, 'LINE Login 尚未設定完整。')
        response = RedirectResponse(_line_authorize_url(gid, target), status_code=302)
        response.delete_cookie(PLAYER_COOKIE, path='/')
        return response

    @app.get('/player/oauth/callback')
    async def player_oauth_callback(code: str = '', state: str = '', error: str = ''):
        data = _decode_token(state, 'player')
        if not data or data.get('kind') != 'line_oauth_state' or error or not code:
            raise HTTPException(401, 'LINE 登入已取消或驗證失敗。')
        token_data = _exchange_line_code(code)
        verified = _verify_id_token(str(token_data.get('id_token') or ''))
        uid = str(verified.get('sub') or '').strip()
        gid = str(data.get('gid') or '').strip()
        target = str(data.get('target') or '/player')
        if not uid or not gid:
            raise HTTPException(401, '無法取得 LINE 登入身分。')
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(PLAYER_COOKIE, _encode_token({'uid':uid,'gid':gid,'kind':'session','exp':_now_ts()+SESSION_TTL},'player'), httponly=True, secure=True, samesite='lax', max_age=SESSION_TTL, path='/')
        return response

    @app.get('/admin')
    async def admin_redirect(request: Request):
        uid, gid = _auth(request)
        if _role(gid, uid) == 'member':
            raise HTTPException(403, '你沒有管理權限。')
        return RedirectResponse('/player' + _query_suffix(request) + '#admin', status_code=302)

    @app.get('/admin/access')
    async def admin_access(request: Request, token: str = ''):
        # 支援舊 admin_web token 與新版簽章網址。
        if token:
            data = _decode_token(token, 'admin')
            if not data or data.get('kind') != 'access':
                raise HTTPException(401, '管理連結已失效。')
            uid, gid = str(data.get('uid') or ''), str(data.get('gid') or '')
            if not uid or not gid or _role(gid, uid) == 'member':
                raise HTTPException(403, '你沒有管理權限。')
            response = RedirectResponse('/player#admin', status_code=303)
            response.set_cookie(ADMIN_COOKIE, _encode_token({'uid':uid,'gid':gid,'kind':'session','exp':_now_ts()+SESSION_TTL},'admin'), httponly=True, secure=True, samesite='lax', max_age=SESSION_TTL, path='/')
            return response
        uid, gid = _auth(request)
        if _role(gid, uid) == 'member':
            raise HTTPException(403, '你沒有管理權限。')
        return RedirectResponse('/admin' + _query_suffix(request), status_code=302)

    @app.get('/api/rainbow/me')
    async def api_me(request: Request):
        uid, gid = _auth(request)
        return JSONResponse(jsonable_encoder(_player_data(line_bot_api, gid, uid)), headers={'Cache-Control': 'no-store'})

    @app.get('/api/rainbow/feature/{name}')
    async def feature_get(name: str, request: Request):
        uid, gid = _auth(request)
        if name == 'achievement':
            return {'title': '🏆 成就系統', 'message': '五階成就系統將於 Phase 2 正式啟用。'}
        if name == 'frame':
            return {'title': '🖼️ 頭像效果', 'message': '一般會員使用主題光環；群長、管理員、VIP 與成就將套用不同專屬框。'}
        raise HTTPException(404, '找不到功能。')

    @app.post('/api/rainbow/feature/{name}')
    async def feature_post(name: str, request: Request):
        uid, gid = _auth(request)
        if name != 'fortune':
            raise HTTPException(404, '找不到功能。')
        today = datetime.datetime.now(TZ).date().isoformat()
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT last_fortune_date FROM players WHERE group_id=%s AND user_id=%s FOR UPDATE', (gid, uid))
                player = c.fetchone() or {}
                fortunes = [
                    ('★★★★★ 大吉', '今天的彩虹能量非常旺盛，適合主動出擊。'),
                    ('★★★★☆ 吉', '人際與工作運順利，保持好心情。'),
                    ('★★★☆☆ 平', '穩穩完成今天的事情，就是最好的進展。'),
                    ('★★☆☆☆ 小凶', '放慢腳步，多確認一次能避開小失誤。'),
                ]
                if str(player.get('last_fortune_date') or '') == today:
                    c.execute('SELECT fortune_level,fortune_message FROM players WHERE group_id=%s AND user_id=%s', (gid, uid))
                    row = c.fetchone() or {}
                    return {'title': '🔮 今日運勢', 'message': f"{row.get('fortune_level') or '今日運勢'}\n{row.get('fortune_message') or ''}"}
                level, message = fortunes[int(hashlib.sha256(f'{uid}|{today}|fortune'.encode()).hexdigest(), 16) % len(fortunes)]
                c.execute('UPDATE players SET last_fortune_date=%s,fortune_level=%s,fortune_message=%s WHERE group_id=%s AND user_id=%s', (today, level, message, gid, uid))
                conn.commit()
                return {'title': '🔮 今日運勢', 'message': level + '\n' + message}
        finally:
            conn.close()

    @app.post('/api/rainbow/frame/equip')
    async def web_equip_frame(request: Request):
        uid,gid=_auth(request); payload=await request.json(); key=str(payload.get('frame_key') or ''); role=_role(gid,uid); data=_player_data(line_bot_api,gid,uid); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT * FROM web_avatar_frames WHERE frame_key=%s AND is_active=TRUE',(key,)); frame=c.fetchone()
                if not frame: raise HTTPException(404,'找不到頭像框。')
                if frame.get('owner_only') and role!='owner': raise HTTPException(403,'這是 Owner 專屬頭像框。')
                if frame.get('vip_only') and not data['vip']: raise HTTPException(403,'這是 VIP 專屬頭像框。')
                c.execute('UPDATE web_user_frames SET equipped=FALSE WHERE group_id=%s AND user_id=%s',(gid,uid))
                c.execute("INSERT INTO web_user_frames(group_id,user_id,frame_key,equipped) VALUES(%s,%s,%s,TRUE) ON CONFLICT(group_id,user_id,frame_key) DO UPDATE SET equipped=TRUE",(gid,uid,key)); conn.commit()
        finally: conn.close()
        return {'ok':True,'message':'頭像框已套用。'}

    @app.post('/api/rainbow/profile')
    async def api_profile(request: Request):
        uid, gid = _auth(request); payload = await request.json()
        bio = str(payload.get('bio') or '')[:300]; region = str(payload.get('region') or '')[:80]
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute('''INSERT INTO web_profile_settings(group_id,user_id,bio,region,updated_at) VALUES(%s,%s,%s,%s,CURRENT_TIMESTAMP)
                ON CONFLICT(group_id,user_id) DO UPDATE SET bio=EXCLUDED.bio,region=EXCLUDED.region,updated_at=CURRENT_TIMESTAMP''',(gid,uid,bio,region))
            conn.commit()
        finally: conn.close()
        return {'ok': True}

    @app.post('/api/rainbow/calendar')
    async def api_calendar(request: Request):
        uid, gid = _auth(request); p = await request.json()
        d = str(p.get('event_date') or '')[:10]; title = str(p.get('title') or '').strip()[:80]; note = str(p.get('note') or '')[:300]
        if not d or not title: raise HTTPException(400, '請填寫日期與提醒標題。')
        conn=get_connection()
        try:
            with conn.cursor() as c: c.execute('INSERT INTO web_calendar_events(group_id,user_id,event_date,title,note) VALUES(%s,%s,%s,%s,%s)',(gid,uid,d,title,note))
            conn.commit()
        finally: conn.close()
        return {'ok':True}

    def require_admin(request):
        uid,gid=_auth(request); r=_role(gid,uid)
        if r=='member': raise HTTPException(403,'沒有管理權限。')
        return uid,gid,r

    @app.get('/api/rainbow/admin/overview')
    async def admin_overview(request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT COUNT(*) total, COUNT(*) FILTER(WHERE COALESCE(is_vip,0)=1) vip, COALESCE(SUM(today_msg_count),0) today_messages FROM players WHERE group_id=%s',(gid,)); a=c.fetchone() or {}
                c.execute('SELECT COUNT(*) admins FROM admins WHERE group_id=%s',(gid,)); b=c.fetchone() or {}
        finally: conn.close()
        return {'members':int(a.get('total') or 0),'vip':int(a.get('vip') or 0),'today_messages':int(a.get('today_messages') or 0),'admins':int(b.get('admins') or 0),'role':r}

    @app.get('/api/rainbow/admin/members')
    async def admin_members(request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT user_id,name,level,is_vip FROM players WHERE group_id=%s ORDER BY level DESC,exp DESC LIMIT 200',(gid,)); rows=c.fetchall()
        finally: conn.close()
        return {'items':[dict(x) for x in rows]}

    @app.get('/api/rainbow/admin/{section}')
    async def admin_section(section: str, request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                if section=='titles': c.execute('SELECT title_name,is_vip FROM titles ORDER BY is_vip,title_name')
                elif section=='frames': c.execute('SELECT frame_key,name,price,vip_only,owner_only,is_active FROM web_avatar_frames ORDER BY price')
                elif section=='permissions': c.execute('SELECT user_id,role FROM admins WHERE group_id=%s ORDER BY role,user_id',(gid,))
                elif section=='logs': return {'section':'logs','items':[],'message':'操作紀錄將於 V2 後台重建。'}
                elif section=='settings': return {'group_id':gid,'message':'群組設定已連接；敏感設定仍由原機器人設定指令管理。'}
                else: raise HTTPException(404,'找不到管理功能。')
                return {'section':section,'items':[dict(x) for x in c.fetchall()]}
        finally: conn.close()

    @app.post('/api/rainbow/admin/announcement')
    async def admin_announcement(request: Request):
        uid,gid,r=require_admin(request); p=await request.json(); title=str(p.get('title') or '').strip()[:100]; content=str(p.get('content') or '').strip()[:1000]
        if not title or not content: raise HTTPException(400,'請填寫公告標題與內容。')
        conn=get_connection()
        try:
            with conn.cursor() as c: c.execute('INSERT INTO web_announcements(group_id,title,content,created_by) VALUES(%s,%s,%s,%s)',(gid,title,content,uid))
            conn.commit()
        finally: conn.close()
        return {'ok':True}

    def require_owner(request):
        uid,gid=_auth(request)
        if _role(gid,uid)!='owner': raise HTTPException(403,'僅 Rainbow Life Owner 可使用。')
        return uid,gid

    @app.get('/api/rainbow/owner/overview')
    async def owner_overview(request: Request):
        require_owner(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT COUNT(DISTINCT group_id) groups,COUNT(*) members FROM players'); row=c.fetchone() or {}
        finally: conn.close()
        return {k:int(row.get(k) or 0) for k in ('groups','members')}

    @app.get('/api/rainbow/owner/groups')
    async def owner_groups(request: Request):
        require_owner(request); conn=get_connection()
        try:
            with conn.cursor() as c: c.execute('SELECT group_id,COUNT(*) members FROM players GROUP BY group_id ORDER BY members DESC LIMIT 100'); rows=c.fetchall()
        finally: conn.close()
        return {'groups':[dict(x) for x in rows]}

    @app.get('/api/rainbow/owner/leaders')
    async def owner_leaders(request: Request):
        require_owner(request); conn=get_connection()
        try:
            with conn.cursor() as c: c.execute("SELECT group_id,user_id,role FROM admins WHERE LOWER(COALESCE(role,'')) IN ('owner','leader','group_owner','群長') ORDER BY group_id"); rows=c.fetchall()
        finally: conn.close()
        return {'leaders':[dict(x) for x in rows]}

    @app.get('/api/rainbow/owner/global')
    async def owner_global(request: Request):
        require_owner(request)
        return {'status':'ready','message':'全站公告管理入口已建立'}
