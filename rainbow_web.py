import datetime
import hashlib
import hmac
import html
import re
from html.parser import HTMLParser
import json
import os
import secrets
import base64
import threading
import time
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

_WEATHER_CACHE = {}
_WEATHER_LOCK = threading.Lock()
WEATHER_CACHE_SECONDS = 30
_FAMILY_CACHE = {}
_FAMILY_LOCK = threading.Lock()
FAMILY_CACHE_SECONDS = 30
FAMILY_EVENT_URL = 'https://www.family.com.tw/Marketing/zh/Event'
_SEVEN_CACHE = {}
_SEVEN_LOCK = threading.Lock()
SEVEN_CACHE_SECONDS = 30
SEVEN_EVENT_URL = 'https://www.7-11.com.tw/special/article_new.aspx?item=Event_E001'
_MCD_CACHE = {}
_MCD_LOCK = threading.Lock()
MCD_CACHE_SECONDS = 30
MCD_EVENT_URL = 'https://www.mcdonalds.com/tw/zh-tw.html'
OFFICIAL_INFO_CACHE_SECONDS = 30
_OFFICIAL_INFO_CACHE = {}
_OFFICIAL_INFO_LOCK = threading.Lock()
OFFICIAL_INFO_SOURCES = {
    'tra': {'title':'台鐵最新公告','url':'https://www.railway.gov.tw/tra-tip-web/tip/tip009/tip911/newsList','source':'國營臺灣鐵路股份有限公司'},
    'thsr': {'title':'高鐵最新公告','url':'https://www.thsrc.com.tw/ArticleContent/cc283668-bfd4-4e33-9f5d-788f5d7e3f80','source':'台灣高鐵'},
    'pokemon_go': {'title':'Pokémon GO 官方更新','url':'https://pokemongolive.com/zh_hant/post/','source':'Pokémon GO 官方網站'},
    'aov': {'title':'傳說對決官方更新','url':'https://moba.garena.tw/news/','source':'Garena 傳說對決官方網站'},
}
CWA_DATASET_ID = os.getenv('CWA_WEATHER_DATASET', 'F-D0047-089').strip()
CWA_WARNING_DATASET_ID = os.getenv('CWA_WARNING_DATASET', 'W-C0033-001').strip()
CWA_WARNING_URL = 'https://www.cwa.gov.tw/V8/C/W/Warning.html'


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

.life-push{position:relative;margin-top:14px;overflow:hidden;padding:18px;background:linear-gradient(145deg,rgba(30,20,88,.86),rgba(91,34,139,.80));border-color:rgba(226,205,255,.46);box-shadow:0 22px 60px rgba(7,3,31,.38),inset 0 1px 0 rgba(255,255,255,.13)}.life-push:before{content:"";position:absolute;inset:-1px;pointer-events:none;background:radial-gradient(circle at 12% 10%,rgba(102,210,255,.17),transparent 28%),radial-gradient(circle at 88% 15%,rgba(255,111,205,.17),transparent 28%)}.life-push-head{position:relative;display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:13px}.life-push-head h3{margin:0;font-size:19px;background:linear-gradient(90deg,#8fe6ff,#ceb6ff,#ff9ed8,#ffe48a);background-size:220% auto;-webkit-background-clip:text;color:transparent;animation:shimmer 4s linear infinite}.life-push-note{font-size:11px;color:#e7deff;padding:6px 10px;border:1px solid rgba(255,255,255,.13);border-radius:999px;background:rgba(12,8,43,.30)}.life-head-tools{display:flex;align-items:center;gap:7px;flex-wrap:wrap;justify-content:flex-end}.life-sync-badge{font-size:11px;color:#effcff;padding:6px 10px;border:1px solid rgba(125,229,255,.26);border-radius:999px;background:rgba(10,33,65,.44)}.life-sync-badge.warn{color:#fff3c4;border-color:rgba(255,218,112,.35);background:rgba(86,57,12,.42)}.life-sync-badge.error{color:#ffd5e5;border-color:rgba(255,120,169,.34);background:rgba(91,20,50,.42)}.life-refresh{height:30px;padding:0 11px;border:1px solid rgba(255,255,255,.18);border-radius:999px;background:rgba(18,12,61,.52);color:#fff;font-size:11px;font-weight:800;cursor:pointer}.life-refresh:disabled{opacity:.55;cursor:wait}.life-refresh:not(:disabled):hover{background:rgba(125,77,220,.62)}.life-carousel{position:relative;overflow:hidden;border-radius:22px;touch-action:pan-y;border:1px solid rgba(255,255,255,.16);box-shadow:0 16px 35px rgba(6,3,28,.28)}.life-track{display:flex;transition:transform .52s cubic-bezier(.22,.75,.25,1)}.life-slide{position:relative;isolation:isolate;min-width:100%;min-height:196px;padding:20px;overflow:hidden;background:linear-gradient(135deg,rgba(18,12,61,.78),rgba(60,26,102,.67));display:grid;grid-template-columns:72px minmax(0,1fr);gap:16px;align-items:center}.life-slide:before{content:"";position:absolute;z-index:-1;inset:0;background:radial-gradient(circle at 85% 15%,rgba(255,255,255,.12),transparent 31%)}.life-slide:after{content:"";position:absolute;z-index:-1;left:0;right:0;bottom:0;height:3px;background:linear-gradient(90deg,#67dcff,#9b75ff,#ff75cc,#ffe17c);opacity:.8}.life-card-weather{background:linear-gradient(135deg,rgba(20,49,100,.86),rgba(63,38,130,.75))}.life-card-family{background:linear-gradient(135deg,rgba(24,89,74,.80),rgba(34,67,124,.74))}.life-card-seven{background:linear-gradient(135deg,rgba(26,94,82,.78),rgba(45,54,121,.76))}.life-card-mcd{background:linear-gradient(135deg,rgba(121,51,39,.77),rgba(101,37,89,.72))}.life-card-tra,.life-card-thsr{background:linear-gradient(135deg,rgba(27,63,111,.82),rgba(61,47,130,.75))}.life-card-holiday{background:linear-gradient(135deg,rgba(105,56,111,.80),rgba(129,64,72,.73))}.life-card-rainbow{background:linear-gradient(135deg,rgba(61,39,132,.82),rgba(140,48,126,.76))}.life-push-icon{font-size:39px;width:66px;height:66px;border-radius:21px;display:grid;place-items:center;background:linear-gradient(145deg,rgba(255,255,255,.18),rgba(255,255,255,.07));border:1px solid rgba(255,255,255,.18);box-shadow:inset 0 1px 0 rgba(255,255,255,.22),0 12px 26px rgba(0,0,0,.20);animation:float 5.5s ease-in-out infinite}.life-copy{min-width:0}.life-copy>b{display:block;font-size:21px;margin-bottom:8px;letter-spacing:.02em;text-shadow:0 2px 13px rgba(0,0,0,.26)}.life-push-status{min-height:43px;font-size:14px;color:#f8f4ff;line-height:1.6}.life-push-status strong{font-size:15px}.life-meta{display:block;margin-top:8px;color:#ddd3ff;font-size:11px}.official-btn{display:inline-flex;align-items:center;justify-content:center;margin-top:12px;padding:9px 15px;border:1px solid rgba(255,255,255,.20);border-radius:999px;color:#fff;text-decoration:none;background:linear-gradient(90deg,rgba(102,79,255,.94),rgba(225,72,185,.91));box-shadow:0 8px 20px rgba(92,42,181,.28);font-weight:800;font-size:13px;cursor:pointer;transition:transform .18s ease,filter .18s ease,box-shadow .18s ease}.official-btn:hover,.official-btn:focus-visible{transform:translateY(-2px);filter:brightness(1.1);box-shadow:0 11px 26px rgba(126,64,221,.38);outline:none}.life-progress{height:4px;margin:11px 3px 0;border-radius:99px;overflow:hidden;background:rgba(255,255,255,.10)}.life-progress i{display:block;width:0;height:100%;border-radius:99px;background:linear-gradient(90deg,#69dfff,#a877ff,#ff72ca,#ffe279);box-shadow:0 0 12px rgba(255,117,204,.85)}.life-progress i.run{animation:lifeProgress 5s linear forwards}@keyframes lifeProgress{from{width:0}to{width:100%}}.life-carousel.paused+.life-progress i{animation-play-state:paused}.life-controls{position:relative;display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:12px}.life-dots{display:flex;align-items:center;gap:6px;min-width:0;flex-wrap:wrap}.life-dot{width:7px;height:7px;padding:0;border:0;border-radius:99px;background:rgba(255,255,255,.30);transition:.2s;cursor:pointer}.life-dot.active{width:24px;background:linear-gradient(90deg,#7de2ff,#e08cff,#ffe58a);box-shadow:0 0 11px rgba(217,140,255,.75)}.life-nav{display:flex;align-items:center;gap:7px}.life-nav button{min-width:34px;height:34px;border:1px solid rgba(255,255,255,.17);border-radius:12px;background:rgba(12,8,43,.48);color:#fff;font-size:21px;cursor:pointer}.life-nav .life-play{width:auto;padding:0 11px;font-size:12px;font-weight:800}.life-nav button:hover{background:rgba(123,81,223,.62)}.loading:after{content:"";display:inline-block;width:12px;height:12px;margin-left:8px;border:2px solid rgba(255,255,255,.28);border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite}@media(max-width:760px){.life-push{padding:14px}.life-push-head{align-items:flex-start;flex-direction:column}.life-head-tools{width:100%;justify-content:space-between}.life-sync-badge{flex:1}.life-refresh{flex:none}.life-push-note{max-width:142px;text-align:right;line-height:1.35}.life-slide{min-height:220px;padding:17px;grid-template-columns:58px minmax(0,1fr);gap:12px}.life-push-icon{width:54px;height:54px;border-radius:17px;font-size:31px}.life-copy>b{font-size:18px}.life-push-status{font-size:13px}.official-btn{width:100%;margin-top:13px}.life-controls{align-items:flex-start}.life-dots{padding-top:8px}.life-nav{flex-shrink:0}}@media(prefers-reduced-motion:reduce){.life-track,.official-btn{transition:none}.life-push-icon,.life-progress i.run{animation:none}}
</style></head><body><div class="stars"></div><div class="app"><aside class="side"><div class="logo">🌈 Rainbow Life</div><div class="nav"><button class="active" onclick="go('home')">🏠 個人中心</button><button onclick="go('calendar')">📅 行事曆</button><button onclick="action('frame')">🖼️ 頭像框</button><button onclick="action('fortune')">🔮 今日運勢</button><div class="nav-title">管理中心</div><button class="admin-menu" onclick="go('admin')">🛠️ 管理中心</button><div class="nav-title owner-menu">Rainbow Life 控制台</div><button class="owner-menu" onclick="go('owner')">👑 系統最高權限</button></div></aside><main class="main"><header class="top"><h2>👑 Rainbow Life 個人中心</h2><div class="badge" id="roleBadge">載入中</div></header><section id="home"><div class="version-mark">V2 智慧生活資訊中心・Phase 8 手機遊戲官方推播</div><section class="card dashboard-carousel" id="leaderDashboard"><div class="carousel-head"><h3>👑 群長儀表板輪播</h3><div class="dots" id="dashDots"></div></div><div id="dashSlides"><div class="slide active"><div class="slide-icon">👑</div><div><small>群長儀表板</small><b>資料載入中</b><div>正在讀取群組資訊</div></div></div></div></section><section class="card hero"><div class="avatar-wrap" id="avatarWrap"><span class="crown" id="crown">👑</span><img class="avatar" id="avatar" alt="LINE 大頭照"></div><div class="hero-info"><div class="eyebrow">RAINBOW COSMOS PROFILE</div><h1 id="name">Rainbow</h1><div class="title-pill">🌈 <span id="title">Rainbow Life</span></div></div></section><section class="card personal-card"><div class="carousel-head"><h3>👤 個人資訊輪播</h3><div class="dots" id="personalDots"></div></div><div id="personalSlides"><div class="personal-slide active"><small>個人資訊</small><div class="personal-value">資料載入中</div><div>正在同步你的最新資料</div></div></div></section><div class="grid"><section class="card announcement"><h3>📢 公告輪播</h3><div class="announcement-content" id="announcement"></div><div class="dots" id="announcementDots"></div><img class="boy" src="/rainbow-static/rainbow_life_boy.png"></section><section class="card"><h3>✨ 每日訊息輪播</h3><div id="dailySlides"></div><div class="dots" id="dailyDots" style="margin-top:12px"></div></section></div><section class="card life-push" id="lifePushCenter"><div class="life-push-head"><h3>🌈 智慧生活資訊中心</h3><div class="life-head-tools"><span class="life-sync-badge" id="lifeSyncBadge">正在同步全部資訊…</span><button class="life-refresh" id="lifeRefreshBtn" type="button" onclick="refreshAllLifeInfo(true)">↻ 立即更新</button></div></div><div class="life-carousel" id="lifeCarousel"><div class="life-track" id="lifeTrack"><article class="life-slide life-card-weather" id="weatherSlide" data-priority="100"><div class="life-push-icon">🚨</div><div class="life-copy"><b>氣象局即時警報</b><div class="life-push-status loading" id="weatherStatus">資訊更新中！</div><small class="life-meta" id="weatherMeta">正在取得官方資訊</small><a class="official-btn" id="weatherOfficial" href="https://www.cwa.gov.tw/V8/C/W/Warning.html" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-family" id="familySlide" data-priority="80"><div class="life-push-icon">🏪</div><div class="life-copy"><b>全家活動</b><div class="life-push-status loading" id="familyStatus">資訊更新中！</div><small class="life-meta" id="familyMeta">正在取得官方資訊</small><a class="official-btn" id="familyOfficial" href="https://www.family.com.tw/Marketing/zh/News" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-seven" id="sevenSlide" data-priority="40"><div class="life-push-icon">7️⃣</div><div class="life-copy"><b>7-ELEVEN</b><div class="life-push-status loading" id="sevenStatus">資訊更新中！</div><small class="life-meta" id="sevenMeta">正在取得官方資訊</small><a class="official-btn" id="sevenOfficial" href="https://www.7-11.com.tw/event/index.aspx" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-mcd" id="mcdSlide" data-priority="40"><div class="life-push-icon">🍔</div><div class="life-copy"><b>麥當勞</b><div class="life-push-status loading" id="mcdStatus">資訊更新中！</div><small class="life-meta" id="mcdMeta">正在取得官方資訊</small><a class="official-btn" id="mcdOfficial" href="https://www.mcdonalds.com/tw/zh-tw/whats-hot.html" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-tra" id="traSlide" data-priority="45"><div class="life-push-icon">🚆</div><div class="life-copy"><b>台鐵最新公告</b><div class="life-push-status loading" id="traStatus">資訊更新中！</div><small class="life-meta" id="traMeta">正在取得官方資訊</small><a class="official-btn" id="traOfficial" href="https://www.railway.gov.tw/tra-tip-web/tip/tip009/tip911/newsList" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-thsr" id="thsrSlide" data-priority="45"><div class="life-push-icon">🚄</div><div class="life-copy"><b>高鐵最新公告</b><div class="life-push-status loading" id="thsrStatus">資訊更新中！</div><small class="life-meta" id="thsrMeta">正在取得官方資訊</small><a class="official-btn" id="thsrOfficial" href="https://www.thsrc.com.tw/ArticleContent/cc283668-bfd4-4e33-9f5d-788f5d7e3f80" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-game" id="pokemon_goSlide" data-priority="55"><div class="life-push-icon">🎮</div><div class="life-copy"><b>Pokémon GO 官方更新</b><div class="life-push-status loading" id="pokemon_goStatus">資訊更新中！</div><small class="life-meta" id="pokemon_goMeta">正在取得官方最新消息</small><a class="official-btn" id="pokemon_goOfficial" href="https://pokemongolive.com/zh_hant/post/" target="_blank" rel="noopener">〔點擊今日頁面〕</a></div></article><article class="life-slide life-card-game" id="aovSlide" data-priority="55"><div class="life-push-icon">⚔️</div><div class="life-copy"><b>傳說對決官方更新</b><div class="life-push-status loading" id="aovStatus">資訊更新中！</div><small class="life-meta" id="aovMeta">正在取得官方最新消息</small><a class="official-btn" id="aovOfficial" href="https://moba.garena.tw/news/" target="_blank" rel="noopener">〔點擊今日頁面〕</a></div></article><article class="life-slide life-card-holiday" id="holidaySlide" data-priority="70"><div class="life-push-icon">🎉</div><div class="life-copy"><b>節日生活提醒</b><div class="life-push-status" id="holidayStatus">正在確認近期節日</div><small class="life-meta" id="holidayMeta">依日期自動更新</small><a class="official-btn" id="holidayOfficial" href="https://www.dgpa.gov.tw/information?uid=30&pid=11633" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-rainbow" id="rainbowNoticeSlide" data-priority="60"><div class="life-push-icon">🌈</div><div class="life-copy"><b>Rainbow Life 系統公告</b><div class="life-push-status" id="rainbowNoticeStatus">目前沒有新的系統公告</div><small class="life-meta" id="rainbowNoticeMeta">Rainbow Life 即時公告</small><button class="official-btn" type="button" onclick="go('home')">〔查看更多〕</button></div></article></div></div><div class="life-progress" aria-hidden="true"><i id="lifeProgress"></i></div><div class="life-controls"><div class="life-dots" id="lifeDots"></div><div class="life-nav"><button type="button" onclick="moveLife(-1)" aria-label="上一則">‹</button><button type="button" class="life-play" id="lifePlay" onclick="toggleLifePlay()" aria-label="暫停輪播">⏸ 暫停</button><button type="button" onclick="moveLife(1)" aria-label="下一則">›</button></div></div></section><section class="card quick-card"><h3>⚡ 快捷功能</h3><div class="quick"><button onclick="action('frame')"><span>🖼️</span>頭像框</button><button onclick="action('achievement')"><span>🏆</span>成就</button><button onclick="action('fortune')"><span>🔮</span>運勢</button><button onclick="go('calendar')"><span>📅</span>行事曆</button><button onclick="unavailable('商店')"><span>🛍️</span>商店</button><button onclick="unavailable('轉盤')"><span>🎡</span>轉盤</button><button onclick="editProfile()"><span>⚙️</span>設定</button></div></section></section><section id="calendar" class="card" style="display:none"><h3>📅 我的行事曆</h3><button class="btn" onclick="eventModal()">新增提醒</button><ul class="events" id="eventList"></ul></section><section id="admin" class="card admin-zone"><h3>🛠️ 管理中心</h3><div class="quick"><button onclick="adminAction('members')"><span>👥</span>成員管理</button><button onclick="announcementModal()"><span>📢</span>公告管理</button><button onclick="adminAction('frames')"><span>🖼️</span>頭像框</button><button onclick="adminAction('titles')"><span>🏅</span>稱號管理</button><button onclick="adminAction('permissions')"><span>🛡️</span>權限管理</button><button onclick="adminAction('settings')"><span>⚙️</span>系統設定</button><button onclick="adminAction('logs')"><span>📋</span>操作紀錄</button></div><div id="adminResult"></div></section><section id="owner" class="card owner-zone"><h3>👑 Rainbow Life 控制台</h3><div class="quick"><button onclick="ownerAction('overview')"><span>📊</span>系統總覽</button><button onclick="ownerAction('groups')"><span>🌈</span>所有群組</button><button onclick="ownerAction('leaders')"><span>👑</span>群長管理</button><button onclick="ownerAction('global')"><span>📢</span>全站公告</button></div><div id="ownerResult"></div></section></main></div><nav class="bottom"><button onclick="go('home')"><span>🏠</span>首頁</button><button onclick="go('calendar')"><span>📅</span>行事曆</button><button onclick="go(DATA&&DATA.role==='owner'?'owner':(DATA&&DATA.role!=='member'?'admin':'home'))"><span>👤</span>我的</button></nav><div class="modal" id="modal"><div class="dialog" id="dialog"></div></div><div class="toast" id="toast"></div>
<script>
let DATA=null,timers=[];const Q=location.search;function api(path,opt={}){return fetch(path+Q,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{})}}).then(async r=>{let j=await r.json();if(!r.ok)throw new Error(j.detail||'操作失敗');return j})}function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function toast(t){let e=document.getElementById('toast');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',2200)}function unavailable(n){toast(n+'功能尚未開放')}function closeModal(){document.getElementById('modal').classList.remove('show')}function go(id){['home','calendar','admin','owner'].forEach(x=>document.getElementById(x).style.display=x===id?'block':'none');if(id==='admin')loadAdmin();if(id==='owner')ownerAction('overview')}function carousel(slides,dots,interval=4500){if(!slides.length)return;let i=0;function show(n){slides.forEach((e,k)=>e.classList.toggle('active',k===n));dots.forEach((e,k)=>e.classList.toggle('active',k===n))}show(0);if(slides.length>1)timers.push(setInterval(()=>{i=(i+1)%slides.length;show(i)},interval))}function makeDots(id,n){let e=document.getElementById(id);e.innerHTML=Array.from({length:n},()=>'<i class="dot"></i>').join('');return [...e.children]}function compact(n){n=Number(n)||0;return n>=1e15?(n/1e15).toFixed(2)+'Q':n>=1e12?(n/1e12).toFixed(2)+'T':n>=1e9?(n/1e9).toFixed(2)+'B':n>=1e6?(n/1e6).toFixed(2)+'M':n.toLocaleString()}async function action(name){try{let j=await api('/api/rainbow/feature/'+name,{method:name==='fortune'?'POST':'GET'});showFeature(name,j);if(name==='fortune'){DATA=await api('/api/rainbow/me');renderPersonal();renderDaily()}}catch(e){toast(e.message)}}function showFeature(name,j){let h='<h3>'+esc(j.title||name)+'</h3>';if(j.message)h+='<p style="white-space:pre-wrap">'+esc(j.message)+'</p>';if(j.items)h+='<ul class="admin-list">'+j.items.map(x=>'<li><b>'+esc(x.name||x.title_name||x.item_name)+'</b>'+(x.description?'<br><span class="sub">'+esc(x.description)+'</span>':'')+(name==='frame'?'<br><button class="btn" onclick="equipFrame('+JSON.stringify(x.frame_key)+')">套用</button>':'')+'</li>').join('')+'</ul>';document.getElementById('dialog').innerHTML=h+'<button class="btn" onclick="closeModal()">關閉</button>';document.getElementById('modal').classList.add('show')}async function equipFrame(n){try{let j=await api('/api/rainbow/frame/equip',{method:'POST',body:JSON.stringify({frame_key:n})});toast(j.message);closeModal()}catch(e){toast(e.message)}}function roleText(r){return {owner:'👑 Rainbow Life Owner',leader:'👑 群長',admin:'🛡️ 管理員',member:'👤 一般成員'}[r]||r}function renderPersonal(){let pct=Math.min(100,Math.max(1,Math.round(DATA.level_exp/Math.max(1,DATA.exp_needed)*100)));let items=[['⭐ 等級與經驗','LV.'+DATA.level,'<div class="level-row"><small>目前 '+compact(DATA.level_exp)+'</small><small>需要 '+compact(DATA.exp_needed)+'</small></div><div class="progress"><i style="width:'+pct+'%"></i></div>'],['💎 VIP 狀態',DATA.vip?(DATA.vip_until||'永久 VIP'):'一般會員','成就達標後可永久解鎖 VIP'],['🏆 成就進度',DATA.achievement_stage||'持續累積中','完成條件後會自動升級'],['🌈 目前稱號',DATA.title||'彩虹旅人','頭像框：'+(DATA.equipped_frame||'rainbow_basic')],['🎂 生日資訊',DATA.birthday||'尚未設定','連續簽到 '+DATA.streak+' 天'],['🔮 今日運勢',DATA.fortune||'尚未占卜',esc(DATA.fortune_message||'點選快捷鍵查看今日運勢')],['💬 今日活躍',DATA.today_messages+' 則訊息','貼圖 '+DATA.today_stickers+' 張']];let box=document.getElementById('personalSlides');box.innerHTML=items.map(x=>'<div class="personal-slide"><small>'+x[0]+'</small><div class="personal-value">'+esc(x[1])+'</div><div>'+x[2]+'</div></div>').join('');carousel([...box.children],makeDots('personalDots',items.length),4300)}async function renderDashboard(){if(DATA.role==='member')return;document.getElementById('leaderDashboard').classList.add('show');let d={members:'--',vip:'--',admins:'--',today_messages:DATA.today_messages};try{d=await api('/api/rainbow/admin/overview')}catch(e){}let items=[['👥','群組成員',d.members+' 人','目前群組成員總數'],['🤖','機器人狀態','正常運行','Rainbow Life 服務正常'],['💬','今日聊天',d.today_messages+' 則','群組今日活躍統計'],['💎','VIP 成員',d.vip+' 人','已解鎖 VIP 的成員'],['🛡️','管理團隊',d.admins+' 人','群長與管理員'],['🎂','生日提醒',DATA.birthday==='尚未設定'?'尚未設定':'已設定','個人生日：'+DATA.birthday],['📢','最新公告',(DATA.announcements||[]).length?'有新公告':'目前無公告','向左輪播查看公告內容']];let box=document.getElementById('dashSlides');box.innerHTML=items.map(x=>'<div class="slide"><div class="slide-icon">'+x[0]+'</div><div><small>'+x[1]+'</small><b>'+esc(x[2])+'</b><div>'+esc(x[3])+'</div></div></div>').join('');document.getElementById('leaderDashboard').classList.add('show');carousel([...box.children],makeDots('dashDots',items.length),4600)}function renderAnnouncements(){let a=(DATA.announcements||[]).filter(x=>!/(轉盤|輪盤|抽獎)/.test((x.title||'')+(x.content||'')));if(!a.length)a=[{title:'全新主題上線',content:'歡迎回到 Rainbow Life 個人中心。'}];let box=document.getElementById('announcement');box.innerHTML=a.map(x=>'<div class="personal-slide"><h2>'+esc(x.title)+'</h2><p>'+esc(x.content)+'</p></div>').join('');carousel([...box.children],makeDots('announcementDots',a.length),5200)}function renderDaily(){let items=[['🔮 今日運勢',DATA.fortune||'尚未占卜'],['💬 今日聊天',DATA.today_messages+' 則'],['🖼️ 今日貼圖',DATA.today_stickers+' 張'],['🔥 連續簽到',DATA.streak+' 天'],['🎂 生日資訊',DATA.birthday||'尚未設定']];let box=document.getElementById('dailySlides');box.innerHTML=items.map(x=>'<div class="daily-slide"><small>'+x[0]+'</small><b>'+esc(x[1])+'</b></div>').join('');carousel([...box.children],makeDots('dailyDots',items.length),4100)}async function load(){try{DATA=await api('/api/rainbow/me');document.getElementById('name').textContent=DATA.name;document.getElementById('title').textContent=DATA.title;document.getElementById('roleBadge').textContent=roleText(DATA.role);document.getElementById('avatar').src=DATA.picture_url||'/rainbow-static/rainbow_life_boy.png';document.getElementById('avatarWrap').classList.add(DATA.role);if(DATA.role==='member'){document.querySelectorAll('.admin-menu,.owner-menu').forEach(e=>e.style.display='none');document.getElementById('crown').style.display='none'}else{document.getElementById('admin').classList.add('show')}if(DATA.role==='owner'){document.querySelectorAll('.owner-menu').forEach(e=>e.style.display='block');document.getElementById('owner').classList.add('show')}try{renderDashboard()}catch(e){console.error('dashboard',e)}try{renderPersonal()}catch(e){console.error('personal',e)}try{renderAnnouncements()}catch(e){console.error('announcement',e)}try{renderDaily()}catch(e){console.error('daily',e)}try{renderEvents()}catch(e){console.error('events',e)}}catch(e){document.body.innerHTML='<div style="padding:40px;color:white;text-align:center"><h2>無法開啟 Rainbow Life</h2><p>'+esc(e.message)+'</p><p>請回到 LINE 群組重新點選「個人中心」。</p></div>'}}function renderEvents(){document.getElementById('eventList').innerHTML=(DATA.events||[]).map(e=>'<li><b>'+esc(e.event_date)+'</b>　'+esc(e.title)+'<br><span class="sub">'+esc(e.note)+'</span></li>').join('')||'<li>目前沒有提醒事項</li>'}function eventModal(){document.getElementById('dialog').innerHTML='<h3>新增提醒</h3><input id="ed" type="date"><input id="et" placeholder="提醒標題"><textarea id="en" placeholder="備註"></textarea><button class="btn" onclick="saveEvent()">儲存</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveEvent(){try{await api('/api/rainbow/calendar',{method:'POST',body:JSON.stringify({event_date:ed.value,title:et.value,note:en.value})});closeModal();toast('已儲存提醒');DATA=await api('/api/rainbow/me');renderEvents()}catch(e){toast(e.message)}}function editProfile(){document.getElementById('dialog').innerHTML='<h3>個人設定</h3><input id="bio" placeholder="自我介紹" value="'+esc(DATA.bio)+'"><input id="region" placeholder="地區" value="'+esc(DATA.region)+'"><button class="btn" onclick="saveProfile()">儲存</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveProfile(){try{await api('/api/rainbow/profile',{method:'POST',body:JSON.stringify({bio:bio.value,region:region.value})});closeModal();toast('個人設定已儲存');DATA=await api('/api/rainbow/me');loadWeather()}catch(e){toast(e.message)}}function announcementModal(){document.getElementById('dialog').innerHTML='<h3>新增群組公告</h3><input id="at" placeholder="公告標題"><textarea id="ac" placeholder="公告內容"></textarea><button class="btn" onclick="saveAnnouncement()">發布</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveAnnouncement(){try{await api('/api/rainbow/admin/announcement',{method:'POST',body:JSON.stringify({title:at.value,content:ac.value})});closeModal();toast('公告已發布');DATA=await api('/api/rainbow/me');renderAnnouncements()}catch(e){toast(e.message)}}async function loadAdmin(){if(!DATA||DATA.role==='member')return;let j=await api('/api/rainbow/admin/overview');document.getElementById('adminResult').innerHTML='<ul class="admin-list"><li>👥 成員總數：<b>'+j.members+'</b></li><li>💎 VIP：<b>'+j.vip+'</b></li><li>🛡️ 管理人員：<b>'+j.admins+'</b></li><li>💬 今日聊天：<b>'+j.today_messages+'</b></li></ul>'}async function adminAction(k){if(k==='members'){let j=await api('/api/rainbow/admin/members');document.getElementById('adminResult').innerHTML='<ul class="admin-list">'+j.items.map(x=>'<li>'+esc(x.name)+'　Lv.'+x.level+'</li>').join('')+'</ul>'}else{let j=await api('/api/rainbow/admin/'+k);document.getElementById('adminResult').innerHTML='<pre style="white-space:pre-wrap">'+esc(JSON.stringify(j,null,2))+'</pre>'}}async function ownerAction(k){if(!DATA||DATA.role!=='owner')return;let j=await api('/api/rainbow/owner/'+k);document.getElementById('ownerResult').innerHTML='<pre style="white-space:pre-wrap">'+esc(JSON.stringify(j,null,2))+'</pre>'}const LIFE_SOURCE_STATE={weather:'loading',family:'loading',seven:'loading',mcd:'loading',tra:'loading',thsr:'loading',pokemon_go:'loading',aov:'loading',holiday:'ok',rainbow:'ok'};let LIFE_REFRESHING=false;function markLifeSource(key,state){LIFE_SOURCE_STATE[key]=state;updateLifeSyncBadge()}function updateLifeSyncBadge(){let badge=document.getElementById('lifeSyncBadge');if(!badge)return;let values=Object.values(LIFE_SOURCE_STATE),ok=values.filter(x=>x==='ok').length,stale=values.filter(x=>x==='stale').length,error=values.filter(x=>x==='error').length,total=values.length;badge.classList.remove('warn','error');if(LIFE_REFRESHING){badge.textContent='正在同步全部資訊…';return}if(error){badge.classList.add('error');badge.textContent='已同步 '+ok+'/'+total+'・'+error+' 項等待重試';return}if(stale){badge.classList.add('warn');badge.textContent='已同步 '+ok+'/'+total+'・'+stale+' 項顯示備援';return}badge.textContent='● 全部 '+total+' 項已即時同步'}async function refreshAllLifeInfo(manual=false){if(LIFE_REFRESHING)return;LIFE_REFRESHING=true;let btn=document.getElementById('lifeRefreshBtn');if(btn){btn.disabled=true;btn.textContent='同步中…'}updateLifeSyncBadge();await Promise.allSettled([loadWeather(),loadFamily(),loadSeven(),loadMcd(),loadOfficial('tra','台鐵官方'),loadOfficial('thsr','高鐵官方'),loadOfficial('pokemon_go','Pokémon GO 官方'),loadOfficial('aov','傳說對決官方')]);loadHolidayNotice();loadRainbowNotice();LIFE_REFRESHING=false;if(btn){btn.disabled=false;btn.textContent='↻ 立即更新'}updateLifeSyncBadge();if(manual)toast('生活資訊已完成更新')}let LIFE_INDEX=0,LIFE_TIMER=null,LIFE_START_X=0,LIFE_PAUSED=false,LIFE_DRAGGING=false;function restartLifeProgress(){let bar=document.getElementById('lifeProgress');if(!bar)return;bar.classList.remove('run');void bar.offsetWidth;if(!LIFE_PAUSED)bar.classList.add('run')}function renderLifeCarousel(){let track=document.getElementById('lifeTrack'),slides=track?[...track.children]:[],dots=document.getElementById('lifeDots');if(!track||!slides.length)return;LIFE_INDEX=(LIFE_INDEX+slides.length)%slides.length;track.style.transform='translateX(-'+(LIFE_INDEX*100)+'%)';if(dots){dots.innerHTML=slides.map((_,i)=>'<button type="button" class="life-dot '+(i===LIFE_INDEX?'active':'')+'" onclick="goLife('+i+')" aria-label="切換到第 '+(i+1)+' 則" aria-current="'+(i===LIFE_INDEX?'true':'false')+'"></button>').join('')}restartLifeProgress()}function goLife(i){LIFE_INDEX=i;renderLifeCarousel();resetLifeTimer()}function moveLife(step){LIFE_INDEX+=step;renderLifeCarousel();resetLifeTimer()}function updateLifePlayButton(){let b=document.getElementById('lifePlay'),c=document.getElementById('lifeCarousel');if(c)c.classList.toggle('paused',LIFE_PAUSED);if(!b)return;b.textContent=LIFE_PAUSED?'▶ 播放':'⏸ 暫停';b.setAttribute('aria-label',LIFE_PAUSED?'播放輪播':'暫停輪播')}function pauseLife(){LIFE_PAUSED=true;clearInterval(LIFE_TIMER);LIFE_TIMER=null;let bar=document.getElementById('lifeProgress');if(bar)bar.classList.remove('run');updateLifePlayButton()}function playLife(){LIFE_PAUSED=false;updateLifePlayButton();resetLifeTimer()}function toggleLifePlay(){LIFE_PAUSED?playLife():pauseLife()}function resetLifeTimer(){clearInterval(LIFE_TIMER);LIFE_TIMER=null;restartLifeProgress();if(!LIFE_PAUSED)LIFE_TIMER=setInterval(()=>{LIFE_INDEX++;renderLifeCarousel()},5000)}function initLifeCarousel(){let c=document.getElementById('lifeCarousel');renderLifeCarousel();resetLifeTimer();updateLifePlayButton();if(!c)return;c.addEventListener('touchstart',e=>{LIFE_START_X=e.touches[0].clientX;LIFE_DRAGGING=true},{passive:true});c.addEventListener('touchend',e=>{if(!LIFE_DRAGGING)return;LIFE_DRAGGING=false;let dx=e.changedTouches[0].clientX-LIFE_START_X;if(Math.abs(dx)>45)moveLife(dx<0?1:-1)},{passive:true});c.addEventListener('mouseenter',()=>{if(!LIFE_PAUSED){clearInterval(LIFE_TIMER);LIFE_TIMER=null}});c.addEventListener('mouseleave',()=>{if(!LIFE_PAUSED)resetLifeTimer()});c.addEventListener('focusin',()=>{if(!LIFE_PAUSED){clearInterval(LIFE_TIMER);LIFE_TIMER=null}});c.addEventListener('focusout',()=>{if(!LIFE_PAUSED)resetLifeTimer()});document.addEventListener('visibilitychange',()=>{if(document.hidden){clearInterval(LIFE_TIMER);LIFE_TIMER=null}else if(!LIFE_PAUSED){resetLifeTimer()}})}function setOfficial(id,url){let a=document.getElementById(id);if(a&&url)a.href=url}async function loadWeather(){let status=document.getElementById('weatherStatus'),meta=document.getElementById('weatherMeta');if(!status)return;try{let w=await api('/api/rainbow/weather');setOfficial('weatherOfficial',w.url);if(w.needs_setting){status.classList.remove('loading');status.innerHTML='<strong>📍 請先設定警報地區</strong><br><button class="btn" onclick="editProfile()">立即設定</button>';meta.textContent='設定後將依所在地區顯示警特報';markLifeSource('weather','ok');return;}status.classList.remove('loading');status.innerHTML='<strong>'+esc(w.warning||'目前沒有發布任何氣象警特報')+'</strong>';meta.textContent=(w.region||'')+'｜更新 '+(w.updated_at||'')+(w.stale?'｜上次成功資料':'');markLifeSource('weather',w.stale?'stale':'ok');let ws=document.getElementById('weatherSlide');if(ws)ws.dataset.priority=w.has_warning?'120':'50';sortLifeSlides();if(w.has_warning){let track=document.getElementById('lifeTrack'),slide=status.closest('.life-slide');if(track&&slide&&track.firstElementChild!==slide){track.insertBefore(slide,track.firstElementChild);LIFE_INDEX=0;renderLifeCarousel();}}}catch(e){status.textContent='資訊更新中！';meta.textContent='下一次將自動重新取得';markLifeSource('weather','error');}}async function loadFamily(){let status=document.getElementById('familyStatus'),meta=document.getElementById('familyMeta');if(!status)return;try{let f=await api('/api/rainbow/familymart');setOfficial('familyOfficial',f.url);status.classList.remove('loading');status.innerHTML='<strong>'+esc(f.title||'資訊更新中！')+'</strong>'+(f.period?'<br>'+esc(f.period):'');meta.textContent='官方活動｜更新 '+esc(f.updated_at||'')+(f.stale?'｜上次成功資料':'');markLifeSource('family',f.stale?'stale':'ok');let fs=document.getElementById('familySlide');if(fs)fs.dataset.priority=f.is_kangkang5?'95':'65';sortLifeSlides();}catch(e){status.textContent='資訊更新中！';meta.textContent='下一次將自動重新取得';markLifeSource('family','error');}}async function loadSeven(){let status=document.getElementById('sevenStatus'),meta=document.getElementById('sevenMeta');if(!status)return;try{let f=await api('/api/rainbow/seven');setOfficial('sevenOfficial',f.url);status.classList.remove('loading');status.innerHTML='<strong>'+esc(f.title||'資訊更新中！')+'</strong>'+(f.period?'<br>'+esc(f.period):'');meta.textContent='官方活動｜更新 '+esc(f.updated_at||'')+(f.stale?'｜上次成功資料':'');markLifeSource('seven',f.stale?'stale':'ok');}catch(e){status.textContent='資訊更新中！';meta.textContent='下一次將自動重新取得';markLifeSource('seven','error');}}async function loadMcd(){let status=document.getElementById('mcdStatus'),meta=document.getElementById('mcdMeta');if(!status)return;try{let f=await api('/api/rainbow/mcdonalds');setOfficial('mcdOfficial',f.url);status.classList.remove('loading');status.innerHTML='<strong>'+esc(f.title||'資訊更新中！')+'</strong>'+(f.period?'<br>'+esc(f.period):'');meta.textContent='官方活動｜更新 '+esc(f.updated_at||'')+(f.stale?'｜上次成功資料':'');markLifeSource('mcd',f.stale?'stale':'ok');}catch(e){status.textContent='資訊更新中！';meta.textContent='下一次將自動重新取得';markLifeSource('mcd','error');}}async function loadOfficial(kind,label){let status=document.getElementById(kind+'Status'),meta=document.getElementById(kind+'Meta');if(!status)return;try{let f=await api('/api/rainbow/official/'+kind);setOfficial(kind+'Official',f.url);status.classList.remove('loading');status.innerHTML='<strong>'+esc(f.title||'資訊更新中！')+'</strong>';meta.textContent=label+'｜更新 '+esc(f.updated_at||'')+(f.stale?'｜上次成功資料':'');markLifeSource(kind,f.stale?'stale':'ok');}catch(e){status.textContent='資訊更新中！';meta.textContent='下一次將自動重新取得';markLifeSource(kind,'error');}}function loadExpandedOfficial(){loadOfficial('tra','台鐵官方');loadOfficial('thsr','高鐵官方');loadOfficial('pokemon_go','Pokémon GO 官方');loadOfficial('aov','傳說對決官方')}function sortLifeSlides(){let track=document.getElementById('lifeTrack');if(!track)return;let active=track.children[LIFE_INDEX],slides=[...track.children];slides.sort((a,b)=>Number(b.dataset.priority||0)-Number(a.dataset.priority||0));slides.forEach(x=>track.appendChild(x));LIFE_INDEX=Math.max(0,[...track.children].indexOf(active));renderLifeCarousel()}function loadHolidayNotice(){let s=document.getElementById('holidayStatus'),m=document.getElementById('holidayMeta'),slide=document.getElementById('holidaySlide');if(!s||!slide)return;let now=new Date(),month=now.getMonth()+1,day=now.getDate(),title='近期沒有特別節日提醒',detail='依日期自動更新',priority=25;if(month===1&&day<=5){title='🎆 元旦假期期間';detail='出門前請留意交通與營業時間';priority=70}else if((month===1&&day>=20)||(month===2&&day<=20)){title='🧧 春節生活提醒';detail='留意交通、採買與店家營業時間';priority=75}else if(month===4&&day<=7){title='🌿 清明連假提醒';detail='返鄉與掃墓請留意交通資訊';priority=70}else if(month===6&&day<=15){title='🐉 端午節生活提醒';detail='留意交通與節慶活動資訊';priority=70}else if((month===9&&day>=20)||(month===10&&day<=10)){title='🌕 中秋節生活提醒';detail='留意交通、活動與店家營業時間';priority=70}else if(month===12&&day>=20){title='🎄 聖誕與跨年活動提醒';detail='外出請留意交通與人潮資訊';priority=72}slide.dataset.priority=String(priority);s.innerHTML='<strong>'+title+'</strong>';m.textContent=detail+'｜'+now.toLocaleString('zh-TW',{hour12:false});sortLifeSlides()}function loadRainbowNotice(){let status=document.getElementById('rainbowNoticeStatus'),meta=document.getElementById('rainbowNoticeMeta'),slide=document.getElementById('rainbowNoticeSlide');if(!status||!slide)return;let items=(DATA&&DATA.announcements)||[];if(items.length){let item=items[0]||{};status.innerHTML='<strong>'+esc(item.title||'Rainbow Life 最新公告')+'</strong>'+(item.content?'<br>'+esc(item.content):'');meta.textContent='系統公告｜即時同步';slide.dataset.priority='85'}else{status.innerHTML='<strong>目前沒有新的系統公告</strong>';meta.textContent='Rainbow Life 即時公告';slide.dataset.priority='20'}sortLifeSlides()}setInterval(()=>refreshAllLifeInfo(false),30000);
load();initLifeCarousel();setTimeout(()=>refreshAllLifeInfo(false),500);
</script></body></html>'''

def _profile_region(group_id, user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute('SELECT region FROM web_profile_settings WHERE group_id=%s AND user_id=%s', (group_id, user_id))
            row = c.fetchone() or {}
            return str(row.get('region') or '').strip()
    finally:
        conn.close()


def _normalize_region(region):
    text = (region or '').replace('台', '臺').replace(' ', '')
    city = ''
    district = ''
    for suffix in ('市', '縣'):
        idx = text.find(suffix)
        if idx >= 0:
            city = text[:idx + 1]
            district = text[idx + 1:]
            break
    if not city:
        city = '臺中市'
        district = text or '太平區'
    if district and not district.endswith(('區', '鄉', '鎮', '市')):
        district += '區'
    return city, district or '太平區'


def _first_value(items, *keys):
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for key in keys:
            val = item.get(key)
            if val not in (None, ''):
                return val
    return None


def _weather_elements(location):
    result = {}
    for element in location.get('WeatherElement') or location.get('weatherElement') or []:
        name = str(element.get('ElementName') or element.get('elementName') or '')
        times = element.get('Time') or element.get('time') or []
        first = times[0] if times else {}
        values = first.get('ElementValue') or first.get('elementValue') or []
        value = _first_value(values, 'Temperature', 'ApparentTemperature', 'ProbabilityOfPrecipitation', 'Weather', 'UVIndex', 'Value')
        if value is None:
            value = _first_value(values, 'temperature', 'apparentTemperature', 'probabilityOfPrecipitation', 'weather', 'uvIndex', 'value')
        result[name] = value
    return result


def _walk_json(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_json(value)


def _fetch_cwa_weather(region):
    """取得中央氣象署即時警特報，依個人地區優先篩選。"""
    api_key = (os.getenv('CWA_API_KEY') or os.getenv('CWA_AUTHORIZATION') or '').strip()
    if not api_key:
        raise RuntimeError('CWA_API_KEY 未設定')
    city, district = _normalize_region(region)
    params = urlencode({'Authorization': api_key, 'format': 'JSON'})
    url = f'https://opendata.cwa.gov.tw/api/v1/rest/datastore/{CWA_WARNING_DATASET_ID}?{params}'
    req = UrlRequest(url, headers={'User-Agent': 'Rainbow-Life/2.0'})
    with urlopen(req, timeout=18) as response:
        payload = json.loads(response.read().decode('utf-8'))

    warning_words = ('颱風','豪雨','大雨','大雷雨','強風','長浪','低溫','高溫','濃霧','地震','警報','特報','即時訊息')
    candidates = []
    for obj in _walk_json(payload.get('records') or payload.get('Records') or payload):
        texts = []
        for key, value in obj.items():
            if isinstance(value, (str, int, float)) and value not in ('', None):
                label = str(key).lower()
                if any(k in label for k in ('title','headline','description','content','phenomena','event','status','location','area','info','text','name')):
                    texts.append(str(value))
        joined = ' '.join(texts).strip()
        if joined and any(word in joined for word in warning_words):
            candidates.append(joined)

    # 地區相關警報優先，其次顯示全臺重要警報。
    selected = ''
    for text in candidates:
        if city in text or district in text or city.replace('臺','台') in text:
            selected = text
            break
    if not selected and candidates:
        selected = candidates[0]
    selected = re.sub(r'\s+', ' ', selected).strip()
    if len(selected) > 180:
        selected = selected[:177] + '…'
    now = datetime.datetime.now(TZ)
    return {
        'ok': True,
        'region': f'{city}{district}',
        'has_warning': bool(selected),
        'warning': selected or '目前沒有發布任何氣象警特報',
        'url': CWA_WARNING_URL,
        'updated_at': now.strftime('%Y/%m/%d %H:%M'),
        'refresh_seconds': WEATHER_CACHE_SECONDS,
        'source': '中央氣象署'
    }


def _cached_weather(region):
    key = region or '臺中市太平區'
    now = time.time()
    with _WEATHER_LOCK:
        cached = _WEATHER_CACHE.get(key)
        if cached and now - cached['time'] < WEATHER_CACHE_SECONDS:
            return cached['data']
    try:
        data = _fetch_cwa_weather(key)
        with _WEATHER_LOCK:
            _WEATHER_CACHE[key] = {'time': now, 'data': data}
        return data
    except Exception:
        with _WEATHER_LOCK:
            cached = _WEATHER_CACHE.get(key)
            if cached:
                stale = dict(cached['data'])
                stale['stale'] = True
                stale['message'] = '目前顯示上一次成功取得的資訊'
                return stale
        raise


class _FamilyTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = 0
        self.tokens = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ('script', 'style', 'noscript'):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in ('script', 'style', 'noscript') and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        text = re.sub(r'\s+', ' ', html.unescape(data or '')).strip()
        if text:
            self.tokens.append(text)


def _fetch_familymart_event():
    req = UrlRequest(FAMILY_EVENT_URL, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; Rainbow-Life/2.0; +https://www.family.com.tw/)',
        'Accept-Language': 'zh-TW,zh;q=0.9'
    })
    with urlopen(req, timeout=18) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or 'utf-8'
    text = raw.decode(charset, errors='replace')
    parser = _FamilyTextParser()
    parser.feed(text)
    tokens = [t for t in parser.tokens if t not in ('Image', '最新活動', '全家便利商店-最新活動')]
    date_re = re.compile(r'^(?:\d{4}/\d{2}/\d{2}\s*-\s*\d{4}/\d{2}/\d{2}|長期活動)$')
    categories = {'主題活動','抽獎活動','支付優惠','便利快訊','會員優惠','鮮食優惠','商品優惠','最新活動'}
    title = ''
    period = ''
    for idx, token in enumerate(tokens):
        if not date_re.match(token):
            continue
        period = token
        for candidate in tokens[idx + 1:idx + 8]:
            if candidate in categories or date_re.match(candidate) or len(candidate) < 4:
                continue
            title = candidate
            break
        if title:
            break
    if not title:
        raise RuntimeError('暫時找不到全家最新活動')
    now = datetime.datetime.now(TZ)
    # 每週五至週二固定顯示康康5提醒；週三、週四顯示官方最新活動。
    is_kangkang5 = now.weekday() in (4, 5, 6, 0, 1)
    if is_kangkang5:
        title = '全家康康5優惠中☆'
        period = '每週五至週二'
    return {
        'ok': True,
        'title': title[:120],
        'is_kangkang5': is_kangkang5,
        'period': period,
        'url': FAMILY_EVENT_URL,
        'updated_at': now.strftime('%Y/%m/%d %H:%M'),
        'refresh_seconds': FAMILY_CACHE_SECONDS,
        'source': '全家便利商店官方網站'
    }


def _cached_familymart_event():
    now = time.time()
    with _FAMILY_LOCK:
        cached = _FAMILY_CACHE.get('latest')
        if cached and now - cached['time'] < FAMILY_CACHE_SECONDS:
            return cached['data']
    try:
        data = _fetch_familymart_event()
        with _FAMILY_LOCK:
            _FAMILY_CACHE['latest'] = {'time': now, 'data': data}
        return data
    except Exception:
        with _FAMILY_LOCK:
            cached = _FAMILY_CACHE.get('latest')
            if cached:
                stale = dict(cached['data']); stale['stale'] = True
                return stale
        raise



def _fetch_seven_event():
    req = UrlRequest(SEVEN_EVENT_URL, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; Rainbow-Life/2.0; +https://www.7-11.com.tw/)',
        'Accept-Language': 'zh-TW,zh;q=0.9'
    })
    with urlopen(req, timeout=18) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or 'utf-8'
    text = raw.decode(charset, errors='replace')
    parser = _FamilyTextParser()
    parser.feed(text)
    tokens = []
    ignored = {
        'Image','本期優惠','主題活動','精選美味','嚴選商品','便利生活',
        'CITY CAFE','CITY TEA','小七食堂','優惠','總覽','繁體中文',
        '產品與服務','聯合服務中心','專線電話：0800-008711'
    }
    for token in parser.tokens:
        token = re.sub(r'\s+', ' ', token).strip()
        if not token or token in ignored or len(token) < 4:
            continue
        if '食品業登錄字號' in token or token.startswith('Copyright'):
            continue
        tokens.append(token)
    title = ''
    period = ''
    date_re = re.compile(r'(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}).{0,12}(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2})')
    for idx, token in enumerate(tokens):
        if date_re.search(token):
            period = date_re.search(token).group(0)
            for candidate in tokens[max(0, idx - 4):idx + 5]:
                if candidate == token or date_re.search(candidate) or len(candidate) > 120:
                    continue
                if any(word in candidate for word in ('活動','優惠','新品','集點','咖啡','CITY')):
                    title = candidate
                    break
            if title:
                break
    if not title:
        for candidate in tokens:
            if len(candidate) <= 120 and any(word in candidate for word in ('活動','優惠','新品','集點')):
                title = candidate
                break
    if not title:
        title = '查看 7-ELEVEN 本期官方優惠'
    now = datetime.datetime.now(TZ)
    return {
        'ok': True,
        'title': title[:120],
        'period': period[:80],
        'url': SEVEN_EVENT_URL,
        'updated_at': now.strftime('%Y/%m/%d %H:%M'),
        'refresh_seconds': SEVEN_CACHE_SECONDS,
        'source': '7-ELEVEN 台灣官方網站'
    }


def _cached_seven_event():
    now = time.time()
    with _SEVEN_LOCK:
        cached = _SEVEN_CACHE.get('latest')
        if cached and now - cached['time'] < SEVEN_CACHE_SECONDS:
            return cached['data']
    try:
        data = _fetch_seven_event()
        with _SEVEN_LOCK:
            _SEVEN_CACHE['latest'] = {'time': now, 'data': data}
        return data
    except Exception:
        with _SEVEN_LOCK:
            cached = _SEVEN_CACHE.get('latest')
            if cached:
                stale = dict(cached['data']); stale['stale'] = True
                return stale
        raise

def _fetch_mcdonalds_event():
    req = UrlRequest(MCD_EVENT_URL, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; Rainbow-Life/2.0; +https://www.mcdonalds.com/tw/zh-tw.html)',
        'Accept-Language': 'zh-TW,zh;q=0.9'
    })
    with urlopen(req, timeout=18) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or 'utf-8'
    text = raw.decode(charset, errors='replace')
    parser = _FamilyTextParser()
    parser.feed(text)
    tokens = []
    ignored = {'了解更多','瞭解詳情','馬上探索、馬上行動！','麥當勞台灣官網首頁','現正推出'}
    for token in parser.tokens:
        token = re.sub(r'\s+', ' ', token).strip()
        if not token or token in ignored or len(token) < 4 or len(token) > 140:
            continue
        if token.startswith('Copyright') or '隱私權政策' in token or '網站使用條款' in token:
            continue
        tokens.append(token)
    title = ''
    keywords = ('優惠','新品','新登場','限定','活動','買一送一','APP','聯名','回歸','推出')
    for candidate in tokens:
        if any(word in candidate for word in keywords):
            title = candidate
            break
    if not title:
        title = '查看麥當勞最新官方活動'
    period = ''
    date_re = re.compile(r'(?:20\d{2}[./-])?\d{1,2}[./-]\d{1,2}.{0,12}(?:20\d{2}[./-])?\d{1,2}[./-]\d{1,2}')
    for token in tokens:
        found = date_re.search(token)
        if found:
            period = found.group(0)
            break
    now = datetime.datetime.now(TZ)
    return {
        'ok': True,
        'title': title[:120],
        'period': period[:80],
        'url': MCD_EVENT_URL,
        'updated_at': now.strftime('%Y/%m/%d %H:%M'),
        'refresh_seconds': MCD_CACHE_SECONDS,
        'source': '台灣麥當勞官方網站'
    }


def _cached_mcdonalds_event():
    now = time.time()
    with _MCD_LOCK:
        cached = _MCD_CACHE.get('latest')
        if cached and now - cached['time'] < MCD_CACHE_SECONDS:
            return cached['data']
    try:
        data = _fetch_mcdonalds_event()
        with _MCD_LOCK:
            _MCD_CACHE['latest'] = {'time': now, 'data': data}
        return data
    except Exception:
        with _MCD_LOCK:
            cached = _MCD_CACHE.get('latest')
            if cached:
                stale = dict(cached['data']); stale['stale'] = True
                return stale
        raise



def _fetch_official_info(kind):
    cfg = OFFICIAL_INFO_SOURCES[kind]
    req = UrlRequest(cfg['url'], headers={
        'User-Agent': 'Mozilla/5.0 (compatible; Rainbow-Life/2.0)',
        'Accept-Language': 'zh-TW,zh;q=0.9'
    })
    with urlopen(req, timeout=18) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or 'utf-8'
    text = raw.decode(charset, errors='replace')
    parser = _FamilyTextParser(); parser.feed(text)
    ignored = {'首頁','最新消息','更多','上一頁','下一頁',':::','網站導覽','回首頁'}
    keywords = {
        'tra': ('公告','營運','列車','延誤','異常','疏運','新聞'),
        'thsr': ('公告','營運','列車','異常','疏運','優惠','最新'),
        'pokemon_go': ('活動','更新','社群日','登場','季節','調查','團體戰','最新消息'),
        'aov': ('公告','活動','更新','版本','賽事','造型','系統','最新'),
    }[kind]
    title = ''
    for token in parser.tokens:
        token = re.sub(r'\s+', ' ', token).strip()
        if not token or token in ignored or len(token) < 5 or len(token) > 120:
            continue
        if any(k in token for k in keywords):
            title = token; break
    if not title:
        title = f"查看{cfg['title']}"
    now = datetime.datetime.now(TZ)
    return {'ok':True,'title':title[:120],'period':'','url':cfg['url'],
            'updated_at':now.strftime('%Y/%m/%d %H:%M'),'refresh_seconds':OFFICIAL_INFO_CACHE_SECONDS,
            'source':cfg['source']}

def _cached_official_info(kind):
    now = time.time()
    with _OFFICIAL_INFO_LOCK:
        cached = _OFFICIAL_INFO_CACHE.get(kind)
        if cached and now - cached['time'] < OFFICIAL_INFO_CACHE_SECONDS:
            return cached['data']
    try:
        data = _fetch_official_info(kind)
        with _OFFICIAL_INFO_LOCK:
            _OFFICIAL_INFO_CACHE[kind] = {'time': now, 'data': data}
        return data
    except Exception:
        with _OFFICIAL_INFO_LOCK:
            cached = _OFFICIAL_INFO_CACHE.get(kind)
            if cached:
                stale = dict(cached['data']); stale['stale'] = True
                return stale
        cfg = OFFICIAL_INFO_SOURCES[kind]
        return {'ok':False,'title':f"{cfg['title']}目前無法取得最新內容",'period':'',
                'url':cfg['url'],'updated_at':datetime.datetime.now(TZ).strftime('%Y/%m/%d %H:%M'),
                'refresh_seconds':OFFICIAL_INFO_CACHE_SECONDS,'source':cfg['source'],'stale':True}

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

    @app.get('/api/rainbow/weather')
    async def api_weather(request: Request):
        uid, gid = _auth(request)
        region = _profile_region(gid, uid)
        if not region:
            return {'ok': True, 'needs_setting': True, 'message': '請先設定天氣地區', 'refresh_seconds': WEATHER_CACHE_SECONDS}
        try:
            return _cached_weather(region)
        except Exception:
            return JSONResponse(status_code=503, content={
                'ok': False,
                'message': '資訊更新中！',
                'refresh_seconds': WEATHER_CACHE_SECONDS
            })


    @app.get('/api/rainbow/familymart')
    async def api_familymart(request: Request):
        _auth(request)
        try:
            return _cached_familymart_event()
        except Exception:
            return JSONResponse(status_code=503, content={
                'ok': False,
                'message': '資訊更新中！',
                'refresh_seconds': FAMILY_CACHE_SECONDS
            })

    @app.get('/api/rainbow/seven')
    async def api_seven(request: Request):
        _auth(request)
        try:
            return _cached_seven_event()
        except Exception:
            return JSONResponse(status_code=503, content={
                'ok': False,
                'message': '資訊更新中！',
                'refresh_seconds': SEVEN_CACHE_SECONDS
            })

    @app.get('/api/rainbow/mcdonalds')
    async def api_mcdonalds(request: Request):
        _auth(request)
        try:
            return _cached_mcdonalds_event()
        except Exception:
            return JSONResponse(status_code=503, content={
                'ok': False,
                'message': '資訊更新中！',
                'refresh_seconds': MCD_CACHE_SECONDS
            })

    @app.get('/api/rainbow/official/{kind}')
    async def api_official_info(request: Request, kind: str):
        _auth(request)
        if kind not in OFFICIAL_INFO_SOURCES:
            raise HTTPException(404, '找不到官方資訊來源。')
        return _cached_official_info(kind)

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
