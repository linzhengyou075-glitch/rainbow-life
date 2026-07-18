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
from shop import ensure_default_data, list_shop_items, get_user_inventory, buy_shop_item, list_titles, buy_title

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
        'coins': int(p.get('coins') or 0), 'tickets': int(p.get('wheel_spin_count') or 0),
        'vip': is_vip, 'vip_until': vip_until, 'title': str(p.get('custom_title') or '彩虹旅人'),
        'birthday': str(p.get('birthday') or '尚未設定'), 'streak': int(p.get('streak_count') or 0),
        'total_sign': total_sign, 'today_messages': int(p.get('today_msg_count') or 0),
        'today_stickers': int(p.get('today_sticker_count') or 0),
        'fortune': str(p.get('fortune_level') or '尚未占卜'), 'fortune_message': str(p.get('fortune_message') or ''),
        'wheel_done': str(p.get('last_wheel_date') or '') == datetime.datetime.now(TZ).date().isoformat(),
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
:root{--bg:#090622;--panel:#17103f;--panel2:#211659;--line:#5949a7;--pink:#ff62c7;--purple:#8d67ff;--blue:#68b6ff;--text:#fff;--muted:#c7c1e8}*{box-sizing:border-box}body{margin:0;color:var(--text);font-family:system-ui,-apple-system,"Noto Sans TC",sans-serif;background:radial-gradient(circle at 80% 0,#30206e 0,transparent 35%),linear-gradient(155deg,#08051f,#120936 58%,#0b1238);min-height:100vh}.stars{position:fixed;inset:0;pointer-events:none;background-image:radial-gradient(#fff 1px,transparent 1px);background-size:42px 42px;opacity:.12}.app{display:grid;grid-template-columns:240px minmax(0,1fr);min-height:100vh}.side{padding:22px 14px;border-right:1px solid #30276a;background:#0c0829d9;position:sticky;top:0;height:100vh}.logo{font-size:24px;font-weight:900;margin:0 12px 24px;background:linear-gradient(90deg,#7ecbff,#ff78cf,#ffd66b);-webkit-background-clip:text;color:transparent}.nav-title{font-size:12px;color:#a99fd2;margin:20px 12px 8px}.nav button{width:100%;border:0;color:#eee;background:transparent;text-align:left;padding:11px 13px;border-radius:12px;margin:2px 0;font-size:14px}.nav button:hover,.nav button.active{background:linear-gradient(90deg,#7f4dff,#d64aba)}.main{padding:20px;max-width:1500px;width:100%;margin:auto}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}.badge{padding:8px 13px;border-radius:999px;background:#2a1d64;border:1px solid #5c47a8;font-size:13px}.hero{position:relative;overflow:hidden;border:1px solid #654fd0;border-radius:24px;background:linear-gradient(110deg,#25165e,#36248b 55%,#8d3fa0);min-height:270px;padding:28px;display:grid;grid-template-columns:220px 1fr;gap:22px;align-items:center;box-shadow:0 16px 60px #0006}.hero:after{content:"";position:absolute;right:-40px;top:-20px;width:55%;height:140%;background:linear-gradient(135deg,transparent 30%,#ff5fb955 31%,#ffcf5d55 38%,#66d7ff55 45%,transparent 46%);transform:rotate(-7deg)}.avatar-wrap{position:relative;z-index:2}.avatar{width:190px;height:190px;border-radius:50%;object-fit:cover;background:#17103f;border:6px solid #c78cff;box-shadow:0 0 35px #c45fff}.crown{position:absolute;top:-22px;left:72px;font-size:42px}.hero-info{z-index:2}.hero h1{font-size:32px;margin:0 0 8px}.sub{color:var(--muted)}.progress{height:12px;background:#0c082b;border-radius:99px;overflow:hidden;margin:12px 0}.progress i{display:block;height:100%;background:linear-gradient(90deg,#6b73ff,#ff64c9);width:0}.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:16px}.stat,.card{border:1px solid #4a3c95;background:linear-gradient(155deg,#1c144a,#141038);border-radius:18px;padding:16px}.stat b{font-size:20px;display:block}.grid{display:grid;grid-template-columns:1.35fr .85fr;gap:14px;margin-top:14px}.card h3{margin:0 0 12px}.announcement{min-height:190px;background:linear-gradient(130deg,#34227a,#702f82);position:relative;overflow:hidden}.announcement .boy{position:absolute;right:0;bottom:-30px;width:220px;opacity:.92}.announcement p{max-width:65%;line-height:1.8}.quick{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.quick button{border:1px solid #5a4aac;background:#211758;color:white;border-radius:15px;padding:16px 8px;font-size:14px}.quick span{display:block;font-size:25px;margin-bottom:6px}.events li,.admin-list li{list-style:none;padding:10px 0;border-bottom:1px solid #3b3278}.admin-zone{display:none;background:linear-gradient(130deg,#321356,#5b1761);border-color:#a647ae}.admin-zone.show{display:block}.owner-zone{display:none;background:linear-gradient(130deg,#4a1b70,#882e75)}.owner-zone.show{display:block}.bottom{display:none}.modal{position:fixed;inset:0;background:#050315cc;display:none;place-items:center;padding:20px;z-index:20}.modal.show{display:grid}.dialog{max-width:520px;width:100%;background:#17103f;border:1px solid #6a55c0;border-radius:20px;padding:20px}.dialog input,.dialog textarea{width:100%;background:#0d0928;color:white;border:1px solid #443789;border-radius:10px;padding:11px;margin:6px 0}.btn{border:0;background:linear-gradient(90deg,#7757ff,#e44eb4);color:white;border-radius:10px;padding:11px 16px}.toast{position:fixed;right:20px;bottom:20px;background:#21185a;padding:12px 16px;border-radius:12px;display:none;z-index:50}
@media(max-width:760px){.app{display:block}.side{display:none}.main{padding:10px 10px 86px}.top{padding:5px}.top h2{font-size:18px}.hero{display:block;padding:16px;min-height:auto;border-radius:18px}.avatar{width:112px;height:112px;border-width:4px}.crown{left:38px;top:-20px}.hero h1{font-size:24px;margin-top:12px}.stats{grid-template-columns:repeat(3,1fr)}.stat{padding:11px}.stat b{font-size:16px}.grid{grid-template-columns:1fr}.quick{grid-template-columns:repeat(4,1fr)}.quick button{padding:12px 4px;font-size:11px}.announcement{min-height:160px}.announcement .boy{width:150px}.bottom{display:grid;position:fixed;left:0;right:0;bottom:0;grid-template-columns:repeat(4,1fr);background:#0c082df2;border-top:1px solid #403583;padding:8px 6px calc(8px + env(safe-area-inset-bottom));z-index:12}.bottom button{border:0;background:transparent;color:#c9c1ed;font-size:11px}.bottom span{display:block;font-size:22px}.desktop-only{display:none}}
</style></head><body><div class="stars"></div><div class="app"><aside class="side"><div class="logo">🌈 Rainbow Life</div><div class="nav"><button class="active" onclick="go('home')">🏠 個人中心</button><button onclick="go('calendar')">📅 行事曆</button><button onclick="action('shop')">🛒 商店</button><button onclick="action('bag')">🎒 背包</button><button onclick="action('frame')">🖼️ 頭像框</button><button onclick="action('fortune')">🔮 今日運勢</button><div class="nav-title">管理中心</div><button class="admin-menu" onclick="go('admin')">🛠️ 管理中心</button><div class="nav-title owner-menu">Rainbow Life 控制台</div><button class="owner-menu" onclick="go('owner')">👑 系統最高權限</button></div></aside><main class="main"><header class="top"><h2>👑 Rainbow Life 個人中心</h2><div class="badge" id="roleBadge">載入中</div></header><section id="home"><div class="hero"><div class="avatar-wrap"><span class="crown" id="crown">👑</span><img class="avatar" id="avatar" alt="LINE 大頭照"></div><div class="hero-info"><h1 id="name">Rainbow</h1><div class="sub" id="title">Rainbow Life</div><div style="margin-top:12px">⭐ LV.<b id="level">1</b>　<span id="expText"></span></div><div class="progress"><i id="expBar"></i></div><div class="stats"><div class="stat"><b id="coins">0</b><span>🌈 彩虹幣</span></div><div class="stat"><b id="tickets">0</b><span>🎟️ 抽獎券</span></div><div class="stat"><b id="vip">一般</b><span>💎 VIP</span></div><div class="stat"><b id="birthday">--</b><span>🎂 生日</span></div><div class="stat"><b id="streak">0</b><span>🔥 連續簽到</span></div></div></div></div><div class="grid"><section class="card announcement"><h3>📢 最新公告</h3><div id="announcement"><p>歡迎回到 Rainbow Life</p></div><img class="boy" src="/rainbow-static/rainbow_life_boy.png"></section><section class="card"><h3>✨ 今日資訊</h3><ul class="events"><li>🔮 今日運勢：<b id="fortune">尚未占卜</b></li><li>🎡 轉盤狀態：<b id="wheel">尚未完成</b></li><li>💬 今日聊天：<b id="messages">0</b></li><li>🖼️ 今日貼圖：<b id="stickers">0</b></li></ul></section></div><section class="card" style="margin-top:14px"><h3>⚡ 快捷功能</h3><div class="quick"><button onclick="action('shop')"><span>🛒</span>商店</button><button onclick="action('bag')"><span>🎒</span>背包</button><button onclick="action('frame')"><span>🖼️</span>頭像框</button><button onclick="action('title')"><span>🏅</span>稱號</button><button onclick="action('wheel')"><span>🎡</span>每日轉盤</button><button onclick="action('fortune')"><span>🔮</span>今日運勢</button><button onclick="go('calendar')"><span>📅</span>行事曆</button><button onclick="editProfile()"><span>⚙️</span>設定</button></div></section></section><section id="calendar" class="card" style="display:none"><h3>📅 我的行事曆</h3><button class="btn" onclick="eventModal()">新增提醒</button><ul class="events" id="eventList"></ul></section><section id="admin" class="card admin-zone"><h3>🛠️ 管理中心</h3><p class="sub">群長與管理員可管理目前群組。</p><div class="quick"><button onclick="adminAction('members')"><span>👥</span>成員管理</button><button onclick="announcementModal()"><span>📢</span>公告管理</button><button onclick="adminAction('shop')"><span>🛒</span>商店管理</button><button onclick="adminAction('frames')"><span>🖼️</span>頭像框</button><button onclick="adminAction('titles')"><span>🏅</span>稱號管理</button><button onclick="adminAction('permissions')"><span>🛡️</span>權限管理</button><button onclick="adminAction('settings')"><span>⚙️</span>系統設定</button><button onclick="adminAction('logs')"><span>📋</span>操作紀錄</button></div><div id="adminResult"></div></section><section id="owner" class="card owner-zone"><h3>👑 Rainbow Life 控制台</h3><p>系統最高權限專屬：全群組總覽、群長管理、全站公告、全站商品、系統統計與資料庫狀態。</p><div class="quick"><button onclick="ownerAction('overview')"><span>📊</span>系統總覽</button><button onclick="ownerAction('groups')"><span>🌈</span>所有群組</button><button onclick="ownerAction('leaders')"><span>👑</span>群長管理</button><button onclick="ownerAction('global')"><span>📢</span>全站公告</button></div><div id="ownerResult"></div></section></main></div><nav class="bottom"><button onclick="go('home')"><span>🏠</span>首頁</button><button onclick="action('shop')"><span>🛒</span>商店</button><button onclick="go('calendar')"><span>📅</span>行事曆</button><button onclick="go(DATA&&DATA.role==='owner'?'owner':(DATA&&DATA.role!=='member'?'admin':'home'))"><span>👤</span>我的</button></nav><div class="modal" id="modal"><div class="dialog" id="dialog"></div></div><div class="toast" id="toast"></div>
<script>
let DATA=null;const Q=location.search;function el(id){return document.getElementById(id)}function setText(id,v){const e=el(id);if(e)e.textContent=v}function setHtml(id,v){const e=el(id);if(e)e.innerHTML=v}function setSrc(id,v){const e=el(id);if(e){e.onerror=()=>{e.onerror=null;e.style.display='none'};e.src=v}}function setWidth(id,v){const e=el(id);if(e)e.style.width=v}function api(path,opt={}){return fetch(path+Q,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{})}}).then(async r=>{let j=await r.json();if(!r.ok)throw new Error(j.detail||'操作失敗');return j})}function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function toast(t){const e=el('toast');if(!e)return;e.textContent=t;e.style.display='block';setTimeout(()=>{if(e)e.style.display='none'},2200)}function closeModal(){const e=el('modal');if(e)e.classList.remove('show')}function go(id){['home','calendar','admin','owner'].forEach(x=>{const e=el(x);if(e)e.style.display=x===id?'block':'none'});if(id==='admin')loadAdmin();if(id==='owner')ownerAction('overview')}async function action(name){try{let j=await api('/api/rainbow/feature/'+name,{method:(name==='wheel'||name==='fortune')?'POST':'GET'});showFeature(name,j);if(name==='wheel'||name==='fortune'){DATA=await api('/api/rainbow/me');setText('coins',DATA.coins.toLocaleString());setText('fortune',DATA.fortune);setText('wheel',DATA.wheel_done?'已完成':'尚未完成')}}catch(e){toast(e.message)}}function showFeature(name,j){let h='<h3>'+esc(j.title||name)+'</h3>';if(j.message)h+='<p style="white-space:pre-wrap">'+esc(j.message)+'</p>';if(j.items)h+='<ul class="admin-list">'+j.items.map(x=>'<li><b>'+esc(x.name||x.title_name||x.item_name)+'</b>'+((x.price??null)!==null?'　🌈'+Number(x.price).toLocaleString():'')+(x.quantity?'　×'+x.quantity:'')+(x.description?'<br><span class="sub">'+esc(x.description)+'</span>':'')+(name==='shop'?'<br><button class="btn" onclick="buyItem('+JSON.stringify(x.name)+')">購買</button>':'')+(name==='title'?'<br><button class="btn" onclick="buyTitle('+JSON.stringify(x.title_name)+')">購買／套用</button>':'')+(name==='frame'?'<br><button class="btn" onclick="equipFrame('+JSON.stringify(x.frame_key)+')">購買／套用</button>':'')+'</li>').join('')+'</ul>';setHtml('dialog',h+'<button class="btn" onclick="closeModal()">關閉</button>');{const e=el('modal');if(e)e.classList.add('show')}}async function buyItem(n){try{let j=await api('/api/rainbow/shop/buy',{method:'POST',body:JSON.stringify({name:n})});toast(j.message);closeModal();DATA=await api('/api/rainbow/me');setText('coins',DATA.coins.toLocaleString())}catch(e){toast(e.message)}}async function buyTitle(n){try{let j=await api('/api/rainbow/title/buy',{method:'POST',body:JSON.stringify({name:n})});toast(j.message);closeModal();DATA=await api('/api/rainbow/me');setText('title',DATA.title);setText('coins',DATA.coins.toLocaleString())}catch(e){toast(e.message)}}async function equipFrame(n){try{let j=await api('/api/rainbow/frame/equip',{method:'POST',body:JSON.stringify({frame_key:n})});toast(j.message);closeModal()}catch(e){toast(e.message)}}function roleText(r){return {owner:'👑 Rainbow Life Owner',leader:'👑 群長',admin:'🛡️ 管理員',member:'👤 一般成員'}[r]||r}async function load(){try{DATA=await api('/api/rainbow/me');setText('name',DATA.name);setText('title',DATA.title);setText('level',DATA.level);setText('coins',DATA.coins.toLocaleString());setText('tickets',DATA.tickets);setText('vip',DATA.vip?(DATA.vip_until||'啟用中'):'一般');setText('birthday',DATA.birthday);setText('streak',DATA.streak+'天');setText('fortune',DATA.fortune);setText('wheel',DATA.wheel_done?'已完成':'尚未完成');setText('messages',DATA.today_messages);setText('stickers',DATA.today_stickers);setText('roleBadge',roleText(DATA.role));setSrc('avatar',DATA.picture_url||'/rainbow-static/rainbow_life_boy.png');let pct=Math.min(100,Math.round(DATA.level_exp/Math.max(1,DATA.exp_needed)*100));setWidth('expBar',pct+'%');setText('expText',DATA.level_exp.toLocaleString()+' / '+DATA.exp_needed.toLocaleString());if(DATA.role==='member'){document.querySelectorAll('.admin-menu,.owner-menu').forEach(e=>e.style.display='none');{const e=el('crown');if(e)e.style.display='none'}}else{{const e=el('admin');if(e)e.classList.add('show')}}if(DATA.role==='owner'){document.querySelectorAll('.owner-menu').forEach(e=>e.style.display='block');{const e=el('owner');if(e)e.classList.add('show')}}renderAnnouncements();renderEvents()}catch(e){document.body.innerHTML='<div style="padding:40px;color:white;text-align:center"><h2>無法開啟 Rainbow Life</h2><p>'+esc(e.message)+'</p><p>請回到 LINE 群組重新點選「個人中心」。</p></div>'}}function renderAnnouncements(){let a=DATA.announcements||[];setHtml('announcement',a.length?'<h2>'+esc(a[0].title)+'</h2><p>'+esc(a[0].content)+'</p>':'<h2>全新主題上線</h2><p>歡迎來到全新的 Rainbow Life 個人中心。</p>')}function renderEvents(){setHtml('eventList',(DATA.events||[]).map(e=>'<li><b>'+esc(e.event_date)+'</b>　'+esc(e.title)+'<br><span class="sub">'+esc(e.note)+'</span></li>').join('')||'<li>目前沒有提醒事項</li>')}function eventModal(){document.getElementById('dialog').innerHTML='<h3>新增提醒</h3><input id="ed" type="date"><input id="et" placeholder="提醒標題"><textarea id="en" placeholder="備註"></textarea><button class="btn" onclick="saveEvent()">儲存</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveEvent(){try{await api('/api/rainbow/calendar',{method:'POST',body:JSON.stringify({event_date:ed.value,title:et.value,note:en.value})});closeModal();toast('已儲存提醒');DATA=await api('/api/rainbow/me');renderEvents()}catch(e){toast(e.message)}}function editProfile(){document.getElementById('dialog').innerHTML='<h3>個人設定</h3><input id="bio" placeholder="自我介紹" value="'+esc(DATA.bio)+'"><input id="region" placeholder="地區" value="'+esc(DATA.region)+'"><button class="btn" onclick="saveProfile()">儲存</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveProfile(){try{await api('/api/rainbow/profile',{method:'POST',body:JSON.stringify({bio:bio.value,region:region.value})});closeModal();toast('個人設定已儲存')}catch(e){toast(e.message)}}function announcementModal(){document.getElementById('dialog').innerHTML='<h3>新增群組公告</h3><input id="at" placeholder="公告標題"><textarea id="ac" placeholder="公告內容"></textarea><button class="btn" onclick="saveAnnouncement()">發布</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveAnnouncement(){try{await api('/api/rainbow/admin/announcement',{method:'POST',body:JSON.stringify({title:at.value,content:ac.value})});closeModal();toast('公告已發布');DATA=await api('/api/rainbow/me');renderAnnouncements()}catch(e){toast(e.message)}}async function loadAdmin(){if(!DATA||DATA.role==='member')return;let j=await api('/api/rainbow/admin/overview');document.getElementById('adminResult').innerHTML='<ul class="admin-list"><li>👥 成員總數：<b>'+j.members+'</b></li><li>💎 VIP：<b>'+j.vip+'</b></li><li>🛡️ 管理人員：<b>'+j.admins+'</b></li><li>💬 今日聊天：<b>'+j.today_messages+'</b></li></ul>'}async function adminAction(k){if(k==='members'){let j=await api('/api/rainbow/admin/members');document.getElementById('adminResult').innerHTML='<ul class="admin-list">'+j.items.map(x=>'<li>'+esc(x.name)+'　Lv.'+x.level+'　🌈'+x.coins.toLocaleString()+'</li>').join('')+'</ul>'}else{let j=await api('/api/rainbow/admin/'+k);document.getElementById('adminResult').innerHTML='<pre style="white-space:pre-wrap">'+esc(JSON.stringify(j,null,2))+'</pre>'}}async function ownerAction(k){if(!DATA||DATA.role!=='owner')return;let j=await api('/api/rainbow/owner/'+k);document.getElementById('ownerResult').innerHTML='<pre style="white-space:pre-wrap">'+esc(JSON.stringify(j,null,2))+'</pre>'}load();
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
        return HTMLResponse(_dashboard_html())

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
        uid,gid=_auth(request)
        ensure_default_data()
        if name=='shop': return {'title':'🛒 彩虹商店','items':[dict(x) for x in list_shop_items(active_only=True)]}
        if name=='bag': return {'title':'🎒 我的背包','items':[dict(x) for x in get_user_inventory(gid,uid)]}
        if name=='title': return {'title':'🏅 稱號中心','items':[dict(x) for x in list_titles(include_vip=True)]}
        if name=='frame':
            conn=get_connection()
            try:
                with conn.cursor() as c:
                    c.execute('SELECT frame_key,name,price,vip_only,owner_only FROM web_avatar_frames WHERE is_active=TRUE ORDER BY price,name')
                    rows=[dict(x) for x in c.fetchall()]
            finally: conn.close()
            return {'title':'🖼️ 頭像框中心','items':rows}
        raise HTTPException(404,'找不到功能。')

    @app.post('/api/rainbow/feature/{name}')
    async def feature_post(name: str, request: Request):
        uid,gid=_auth(request); today=datetime.datetime.now(TZ).date().isoformat(); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT last_wheel_date,last_fortune_date FROM players WHERE group_id=%s AND user_id=%s FOR UPDATE',(gid,uid)); p=c.fetchone() or {}
                if name=='wheel':
                    if str(p.get('last_wheel_date') or '')==today: return {'title':'🎡 每日轉盤','message':'今天已經轉過囉！'}
                    rewards=[50,100,150,200,300,500]; reward=rewards[int(hashlib.sha256(f'{uid}|{today}|wheel'.encode()).hexdigest(),16)%len(rewards)]
                    c.execute('UPDATE players SET coins=COALESCE(coins,0)+%s,last_wheel_date=%s WHERE group_id=%s AND user_id=%s',(reward,today,gid,uid)); conn.commit()
                    return {'title':'🎡 每日轉盤','message':f'恭喜獲得 🌈 彩虹幣 +{reward}！'}
                if name=='fortune':
                    fortunes=[('★★★★★ 大吉','今天的彩虹能量非常旺盛，適合主動出擊。'),('★★★★☆ 吉','人際與工作運順利，保持好心情。'),('★★★☆☆ 平','穩穩完成今天的事情，就是最好的進展。'),('★★☆☆☆ 小凶','放慢腳步，多確認一次能避開小失誤。')]
                    if str(p.get('last_fortune_date') or '')==today:
                        c.execute('SELECT fortune_level,fortune_message FROM players WHERE group_id=%s AND user_id=%s',(gid,uid)); r=c.fetchone() or {}
                        return {'title':'🔮 今日運勢','message':f"{r.get('fortune_level') or '今日運勢'}\n{r.get('fortune_message') or ''}"}
                    level,msg=fortunes[int(hashlib.sha256(f'{uid}|{today}|fortune'.encode()).hexdigest(),16)%len(fortunes)]
                    c.execute('UPDATE players SET last_fortune_date=%s,fortune_level=%s,fortune_message=%s WHERE group_id=%s AND user_id=%s',(today,level,msg,gid,uid)); conn.commit()
                    return {'title':'🔮 今日運勢','message':level+'\n'+msg}
        finally: conn.close()
        raise HTTPException(404,'找不到功能。')

    @app.post('/api/rainbow/shop/buy')
    async def web_buy_shop(request: Request):
        uid,gid=_auth(request); payload=await request.json(); ok,msg,_=buy_shop_item(gid,uid,str(payload.get('name') or ''))
        if not ok: raise HTTPException(400,msg)
        return {'ok':True,'message':msg}

    @app.post('/api/rainbow/title/buy')
    async def web_buy_title(request: Request):
        uid,gid=_auth(request); payload=await request.json(); data=_player_data(line_bot_api,gid,uid); ok,msg=buy_title(gid,uid,str(payload.get('name') or ''),data['vip'])
        if not ok: raise HTTPException(400,msg)
        return {'ok':True,'message':msg}

    @app.post('/api/rainbow/frame/equip')
    async def web_equip_frame(request: Request):
        uid,gid=_auth(request); payload=await request.json(); key=str(payload.get('frame_key') or ''); role=_role(gid,uid); data=_player_data(line_bot_api,gid,uid); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT * FROM web_avatar_frames WHERE frame_key=%s AND is_active=TRUE',(key,)); frame=c.fetchone()
                if not frame: raise HTTPException(404,'找不到頭像框。')
                if frame.get('owner_only') and role!='owner': raise HTTPException(403,'這是 Owner 專屬頭像框。')
                if frame.get('vip_only') and not data['vip']: raise HTTPException(403,'這是 VIP 專屬頭像框。')
                c.execute('SELECT 1 FROM web_user_frames WHERE group_id=%s AND user_id=%s AND frame_key=%s',(gid,uid,key)); owned=c.fetchone(); price=int(frame.get('price') or 0)
                if not owned and price>0:
                    c.execute('UPDATE players SET coins=coins-%s WHERE group_id=%s AND user_id=%s AND coins>=%s',(price,gid,uid,price))
                    if c.rowcount==0: raise HTTPException(400,'彩虹幣不足。')
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
                c.execute('SELECT user_id,name,level,coins,is_vip FROM players WHERE group_id=%s ORDER BY level DESC,exp DESC LIMIT 200',(gid,)); rows=c.fetchall()
        finally: conn.close()
        return {'items':[dict(x) for x in rows]}

    @app.get('/api/rainbow/admin/{section}')
    async def admin_section(section: str, request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                if section=='shop': c.execute('SELECT name,category,price,item_type,is_active FROM shop_items ORDER BY category,price')
                elif section=='titles': c.execute('SELECT title_name,price,is_vip FROM titles ORDER BY is_vip,price')
                elif section=='frames': c.execute('SELECT frame_key,name,price,vip_only,owner_only,is_active FROM web_avatar_frames ORDER BY price')
                elif section=='permissions': c.execute('SELECT user_id,role FROM admins WHERE group_id=%s ORDER BY role,user_id',(gid,))
                elif section=='logs': c.execute('SELECT player_name,item_name,cost,created_at FROM purchase_history WHERE group_id=%s ORDER BY id DESC LIMIT 50',(gid,))
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
                c.execute('SELECT COUNT(DISTINCT group_id) groups,COUNT(*) members,COALESCE(SUM(coins),0) coins FROM players'); row=c.fetchone() or {}
        finally: conn.close()
        return {k:int(row.get(k) or 0) for k in ('groups','members','coins')}

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
