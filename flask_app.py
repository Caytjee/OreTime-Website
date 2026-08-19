from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import requests
import os
import re
import json
import time
import secrets
from datetime import date, datetime, timedelta

app = Flask(__name__)
app.secret_key = "gaming_saturday_secret"
app.permanent_session_lifetime = timedelta(days=30)

ADMINS = ['caytjee', 'torotera', 'schmelive']
_loc_cache = {'count': None, 'ts': 0}
_steam_cache = {'ts': 0, 'owned': {}, 'no_steam': []}
_started_at = time.time()
STEAM_CACHE_TTL = 300

STEAM_API_KEY = "2CDCC53DD6417C157A68B43C3C5C7B9B"
DISCORD_CLIENT_ID = "1538330044353617930"
DISCORD_CLIENT_SECRET = "ShZs293iRCqjRsqtBCdS8tre7fpil1Sj"
DISCORD_REDIRECT_URI = "http://localhost:5000/callback"

DISCORD_AUTH_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_URL = "https://discord.com/api/users/@me"
DEFAULT_AVATAR = "https://cdn.discordapp.com/embed/avatars/0.png"
HEX_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')

def get_db():
    conn = sqlite3.connect('database.db', timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, steam_id TEXT, theme TEXT DEFAULT 'pink')''')

    try: conn.execute('ALTER TABLE users ADD COLUMN owns_title INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN custom_title TEXT DEFAULT ""')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN avatar TEXT')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN discord_name TEXT')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN banner TEXT DEFAULT "#1a1a1a"')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN ore_coins INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN discord_status TEXT DEFAULT "offline"')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN discord_activity TEXT DEFAULT ""')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN owns_mvp INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN active_border TEXT DEFAULT ""')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN borders TEXT DEFAULT ""')
    except sqlite3.OperationalError: pass
    
    # NEU: Spalten für das Banner System
    try: conn.execute('ALTER TABLE users ADD COLUMN active_banner TEXT DEFAULT "default"')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN owned_banners TEXT DEFAULT ""')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN banner_config TEXT DEFAULT "{}"')
    except sqlite3.OperationalError: pass
    
    conn.execute('''CREATE TABLE IF NOT EXISTS votes (id INTEGER PRIMARY KEY, user_id INTEGER, target_date TEXT, game1 TEXT, game2 TEXT, game3 TEXT, UNIQUE(user_id, target_date))''')
    try: conn.execute('ALTER TABLE votes ADD COLUMN game1 TEXT')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE votes ADD COLUMN game2 TEXT')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE votes ADD COLUMN game3 TEXT')
    except sqlite3.OperationalError: pass
    try: conn.execute('ALTER TABLE votes ADD COLUMN multiplier INTEGER DEFAULT 1')
    except sqlite3.OperationalError: pass

    conn.execute('''CREATE TABLE IF NOT EXISTS beacons (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, game TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS wishlist (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, game_name TEXT, appid TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS radar (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, target_date TEXT, status TEXT, UNIQUE(user_id, target_date))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS games (id INTEGER PRIMARY KEY, name TEXT UNIQUE, steam_appid TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT UNIQUE, value TEXT)''')
    conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('voting_locked', 'false')")

    if conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0:
        default_games = [("Big Walk", "1478500"), ("Codenames", "non"), ("Meccha", "4704690"), ("PEAK", "3527290"), ("R.E.P.O.", "3241660")]
        conn.executemany("INSERT INTO games (name, steam_appid) VALUES (?, ?)", default_games)
    conn.commit()
    conn.close()

init_db()

def sanitize_hex(val, default):
    if isinstance(val, str) and HEX_RE.fullmatch(val.strip()):
        return val.strip()
    return default

def safe_avatar(value):
    return value if value else DEFAULT_AVATAR

def vote_multiplier(row):
    if row is None:
        return 1
    try:
        if 'multiplier' in row.keys() and row['multiplier']:
            return max(1, int(row['multiplier']))
    except (TypeError, ValueError, IndexError):
        pass
    return 1

def apply_vote_scores(scores, row):
    mult = vote_multiplier(row)
    for game, points in ((row['game1'], 3), (row['game2'], 2), (row['game3'], 1)):
        if game in scores:
            scores[game] += points * mult

def get_total_loc():
    now = time.time()
    if _loc_cache['count'] is not None and now - _loc_cache['ts'] < 60:
        return _loc_cache['count']
    loc = 0
    skip_dirs = {'.venv', 'venv', 'env', '__pycache__', 'node_modules', '.git'}
    for root, dirs, files in os.walk(os.path.abspath(os.path.dirname(__file__))):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for file in files:
            if file.endswith(('.py', '.html', '.css', '.js')):
                try:
                    with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                        loc += sum(1 for line in f)
                except Exception:
                    pass
    _loc_cache['count'] = loc
    _loc_cache['ts'] = now
    return loc

def get_all_games():
    conn = get_db()
    games = conn.execute("SELECT * FROM games ORDER BY name").fetchall()
    conn.close()
    return games

@app.context_processor
def inject_global_data():
    theme, steam_id, avatar = 'pink', '', 'https://cdn.discordapp.com/embed/avatars/0.png'
    conn = get_db()
    games = conn.execute("SELECT name, steam_appid FROM games").fetchall()
    game_appids = {g['name']: g['steam_appid'] for g in games}

    locked_row = conn.execute("SELECT value FROM config WHERE key = 'voting_locked'").fetchone()
    voting_locked = (locked_row['value'] == 'true') if locked_row else False

    if 'user_id' in session:
        if 'theme' in session: theme = session['theme']
        if 'steam_id' in session: steam_id = session['steam_id']
        if 'avatar' in session: avatar = session['avatar']

        if 'theme' not in session or 'steam_id' not in session or 'avatar' not in session:
            user = conn.execute("SELECT theme, steam_id, avatar FROM users WHERE id = ?", (session['user_id'],)).fetchone()
            if user:
                theme = user['theme'] if user['theme'] else 'pink'
                steam_id = user['steam_id'] if user['steam_id'] else ''
                avatar = safe_avatar(user['avatar'] if 'avatar' in user.keys() else None)
                session.update({'theme': theme, 'steam_id': steam_id, 'avatar': avatar})
    conn.close()
    return {'current_theme': theme, 'user_steam_id': steam_id, 'user_avatar': avatar or DEFAULT_AVATAR, 'game_appids': game_appids, 'voting_locked': voting_locked, 'total_loc': get_total_loc(), 'default_avatar': DEFAULT_AVATAR}

def get_next_two_saturdays():
    today = date.today()
    saturday1 = today + timedelta((5 - today.weekday()) % 7)
    return [saturday1.strftime('%Y-%m-%d'), (saturday1 + timedelta(days=7)).strftime('%Y-%m-%d')]

def get_past_saturdays(n=5):
    now = datetime.now()
    today = now.date()
    days_since_saturday = (today.weekday() - 5) % 7
    if days_since_saturday == 0 and now.hour < 20:
        days_since_saturday = 7
    last_saturday = today - timedelta(days=days_since_saturday)
    return [(last_saturday - timedelta(weeks=i)).strftime('%Y-%m-%d') for i in range(n)]

def get_winner_for_date(target_date):
    conn = get_db()
    rows = conn.execute('SELECT game1, game2, game3, multiplier FROM votes WHERE target_date = ?', (target_date,)).fetchall()
    conn.close()
    if not rows: return "TBD (No votes yet)"
    scores = {g['name']: 0 for g in get_all_games()}
    if not scores: return "TBD (No votes yet)"
    for row in rows:
        apply_vote_scores(scores, row)
    if all(v == 0 for v in scores.values()): return "TBD (No votes yet)"
    return max(scores, key=scores.get)

def collect_live_status():
    conn = get_db()
    users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    votes_count = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
    games_count = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    locked_row = conn.execute("SELECT value FROM config WHERE key = 'voting_locked'").fetchone()
    voting_locked = (locked_row['value'] == 'true') if locked_row else False
    next_sat = get_next_two_saturdays()[0]
    radar = {'yes': 0, 'maybe': 0, 'no': 0}
    for row in conn.execute(
        "SELECT status, COUNT(*) AS c FROM radar WHERE target_date = ? GROUP BY status",
        (next_sat,)
    ):
        if row['status'] in radar:
            radar[row['status']] = row['c']
    active_beacons = conn.execute(
        "SELECT COUNT(*) FROM beacons WHERE created_at >= datetime('now', '-2 hours')"
    ).fetchone()[0]
    conn.close()
    return {
        'users_count': users_count,
        'votes_count': votes_count,
        'games_count': games_count,
        'voting_locked': voting_locked,
        'next_sat': next_sat,
        'winner': get_winner_for_date(next_sat),
        'radar': radar,
        'active_beacons': active_beacons,
        'total_loc': get_total_loc(),
        'server_time': datetime.now().isoformat(timespec='seconds'),
        'uptime_seconds': int(time.time() - _started_at),
    }

@app.route('/')
def index():
    if 'user_id' in session: 
        return redirect(url_for('home'))
    return render_template('landing.html')

@app.route('/home')
def home():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    conn = get_db()
    active_beacons = conn.execute("SELECT * FROM beacons WHERE created_at >= datetime('now', '-2 hours') ORDER BY created_at DESC").fetchall()
    next_sat = get_next_two_saturdays()[0]
    past_sat = get_past_saturdays(1)[0]
    
    radar_raw = conn.execute("SELECT users.username, users.avatar, radar.status FROM radar JOIN users ON radar.user_id = users.id WHERE radar.target_date = ?", (next_sat,)).fetchall()
    radar_data = {'yes': [], 'no': [], 'maybe': []}
    user_status = None
    
    for r in radar_raw:
        if r['status'] not in radar_data:
            continue
        radar_data[r['status']].append({'username': r['username'], 'avatar': safe_avatar(r['avatar'])})
        if r['username'] == session.get('username'):
            user_status = r['status']
            
    conn.close()
    return render_template('home.html', next_sat=next_sat, winner=get_winner_for_date(next_sat), last_winner=get_winner_for_date(past_sat), active_beacons=active_beacons, radar_data=radar_data, user_status=user_status)

@app.route('/set_radar', methods=['POST'])
def set_radar():
    if 'user_id' not in session: return redirect(url_for('index'))
    status = request.form.get('status')
    target_date = get_next_two_saturdays()[0]
    
    if status in ['yes', 'no', 'maybe']:
        conn = get_db()
        existing = conn.execute("SELECT id FROM radar WHERE user_id = ? AND target_date = ?", (session['user_id'], target_date)).fetchone()
        if existing:
            conn.execute("UPDATE radar SET status = ? WHERE id = ?", (status, existing['id']))
        else:
            conn.execute("INSERT INTO radar (user_id, target_date, status) VALUES (?, ?, ?)", (session['user_id'], target_date, status))
        conn.commit()
        conn.close()
    return redirect(url_for('home'))

@app.route('/login')
def login():
    return redirect(f"{DISCORD_AUTH_URL}?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify")

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code: 
        return redirect(url_for('index'))
        
    data = {'client_id': DISCORD_CLIENT_ID, 'client_secret': DISCORD_CLIENT_SECRET, 'grant_type': 'authorization_code', 'code': code, 'redirect_uri': DISCORD_REDIRECT_URI}
    try:
        token_res = requests.post(DISCORD_TOKEN_URL, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=10).json()
        token = token_res.get('access_token')
        if not token:
            flash("Discord login failed. Please try again.", "error")
            return redirect(url_for('index'))
        user_data = requests.get(DISCORD_API_URL, headers={'Authorization': f'Bearer {token}'}, timeout=10).json()
        if not user_data.get('id') or not user_data.get('username'):
            flash("Could not load your Discord profile. Please try again.", "error")
            return redirect(url_for('index'))
    except Exception:
        flash("Discord login failed. Please try again.", "error")
        return redirect(url_for('index'))

    avatar_url = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png" if user_data.get('avatar') else "https://cdn.discordapp.com/embed/avatars/0.png"
    discord_global_name = user_data.get('global_name') or user_data.get('username')

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (user_data['username'],)).fetchone()
    if not user:
        conn.execute("INSERT INTO users (username, password, avatar, discord_name) VALUES (?, ?, ?, ?)", (user_data['username'], "discord_oauth", avatar_url, discord_global_name))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (user_data['username'],)).fetchone()
    else:
        conn.execute("UPDATE users SET avatar = ?, discord_name = ? WHERE username = ?", (avatar_url, discord_global_name, user_data['username']))
        conn.commit()

    session.permanent = True
    session.update({'user_id': user['id'], 'username': user['username'], 'theme': user['theme'] or 'pink', 'steam_id': user['steam_id'] or '', 'avatar': avatar_url, 'discord_name': discord_global_name})
    conn.close()
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/profile', methods=['POST'])
def profile():
    if 'user_id' not in session: return redirect(url_for('index'))
    theme = request.form.get('theme')
    steam_id = request.form.get('steam_id')
    discord_name = request.form.get('discord_name')
    custom_title = request.form.get('custom_title')
    
    active_border = request.form.get('active_border')
    active_banner = request.form.get('active_banner')
    
    b_conf = {
        'bg_color': sanitize_hex(request.form.get('bg_color'), '#1a1a1a'),
        'text_glow': sanitize_hex(request.form.get('text_glow'), '#ff0000'),
        'u_bg1': sanitize_hex(request.form.get('u_bg1'), '#ff0000'),
        'u_bg2': sanitize_hex(request.form.get('u_bg2'), '#000000'),
        'u_bg3': sanitize_hex(request.form.get('u_bg3'), '#0000ff'),
        'u_txt': sanitize_hex(request.form.get('u_txt'), '#00ffff'),
        'g_bg1': sanitize_hex(request.form.get('g_bg1'), '#ff0000'),
        'g_bg2': sanitize_hex(request.form.get('g_bg2'), '#ffff00'),
        'g_bg3': sanitize_hex(request.form.get('g_bg3'), '#00ff00'),
        'g_bg4': sanitize_hex(request.form.get('g_bg4'), '#00ffff'),
        'g_bg5': sanitize_hex(request.form.get('g_bg5'), '#0000ff'),
        'g_txt1': sanitize_hex(request.form.get('g_txt1'), '#ffffff'),
        'g_txt2': sanitize_hex(request.form.get('g_txt2'), '#aaaaaa')
    }
    
    conn = get_db()
    if theme: conn.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, session['user_id'])), session.update({'theme': theme})
    if steam_id is not None: conn.execute("UPDATE users SET steam_id = ? WHERE id = ?", (steam_id, session['user_id'])), session.update({'steam_id': steam_id})
    if discord_name is not None:
        conn.execute("UPDATE users SET discord_name = ? WHERE id = ?", (discord_name, session['user_id']))
        session['discord_name'] = discord_name
    if custom_title is not None: conn.execute("UPDATE users SET custom_title = ? WHERE id = ?", (custom_title, session['user_id']))
    
    if active_border is not None: conn.execute("UPDATE users SET active_border = ? WHERE id = ?", (active_border, session['user_id']))
    if active_banner is not None: conn.execute("UPDATE users SET active_banner = ? WHERE id = ?", (active_banner, session['user_id']))
    if any(k in request.form for k in ('bg_color', 'text_glow', 'u_bg1', 'g_bg1', 'active_banner')):
        conn.execute("UPDATE users SET banner_config = ? WHERE id = ?", (json.dumps(b_conf), session['user_id']))
    
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('home'))

@app.route('/api/player/<username>')
def api_player(username):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    # Neues SELECT Statement, das alle Banner-Spalten mitlädt
    user = conn.execute("SELECT id, username, steam_id, avatar, discord_name, banner, ore_coins, discord_status, discord_activity, owns_title, custom_title, active_border, borders, active_banner, owned_banners, banner_config FROM users WHERE username = ?", (username,)).fetchone()
    if not user:
        user = conn.execute("SELECT id, username, steam_id, avatar, discord_name, banner, ore_coins, discord_status, discord_activity, owns_title, custom_title, active_border, borders, active_banner, owned_banners, banner_config FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
        
    votes = conn.execute("SELECT COUNT(*) FROM votes WHERE user_id = ?", (user['id'],)).fetchone()[0]
    
    most_voted = conn.execute("SELECT game1, COUNT(game1) as count FROM votes WHERE user_id = ? AND game1 IS NOT NULL GROUP BY game1 ORDER BY count DESC LIMIT 1", (user['id'],)).fetchone()
    fav_game = most_voted['game1'] if most_voted else "None"
    
    steam_name = ""
    if user['steam_id']:
        try:
            res = requests.get(f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={STEAM_API_KEY}&steamids={user['steam_id']}", timeout=2).json()
            steam_name = res['response']['players'][0]['personaname']
        except Exception:
            steam_name = "Connected"
            
    # INFINITE COINS HACK FÜR FOUNDER (in API)
    display_coins = user['ore_coins'] or 0
    if (user['username'] or '').lower() == 'caytjee': 
        display_coins = 999999
            
    conn.close()
    
    return jsonify({
        'username': user['username'],
        'steam_id': user['steam_id'],
        'avatar': safe_avatar(user['avatar']),
        'discord_name': user['discord_name'] or user['username'],
        'steam_name': steam_name,
        'votes': votes,
        'fav_game': fav_game,
        'ore_coins': display_coins,
        'discord_status': user['discord_status'] or 'offline',
        'discord_activity': user['discord_activity'] or '',
        'owns_title': bool(user['owns_title']),
        'custom_title': user['custom_title'] or '',
        'borders': user['borders'] or '',
        'active_border': user['active_border'] or '',
        'owned_banners': user['owned_banners'] or '',
        'active_banner': user['active_banner'] or 'default',
        'banner_config': user['banner_config'] or '{}',
        'banner': user['banner'] or '#1a1a1a'
    })

@app.route('/events')
def events():
    if 'user_id' not in session: return redirect(url_for('index'))
    conn = get_db()
    saturdays, games_names = get_next_two_saturdays(), [g['name'] for g in get_all_games()]
    all_users = conn.execute("SELECT username, steam_id FROM users").fetchall()
    
    next_sat = saturdays[0]
    radar_raw = conn.execute("SELECT users.username, users.avatar, radar.status FROM radar JOIN users ON radar.user_id = users.id WHERE radar.target_date = ?", (next_sat,)).fetchall()
    radar_data = {'yes': [], 'no': [], 'maybe': []}
    for r in radar_raw:
        if r['status'] in radar_data:
            radar_data[r['status']].append({'username': r['username'], 'avatar': safe_avatar(r['avatar'])})

    now = time.time()
    if now - _steam_cache['ts'] < STEAM_CACHE_TTL and _steam_cache['ts'] > 0:
        owned_games, no_steam_users = _steam_cache['owned'], _steam_cache['no_steam']
    else:
        owned_games = {}
        no_steam_users = []
        for u in all_users:
            if u['steam_id']:
                try:
                    owned_games[u['username']] = [
                        g['appid'] for g in requests.get(
                            f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/?key={STEAM_API_KEY}&steamid={u['steam_id']}&format=json",
                            timeout=3
                        ).json().get('response', {}).get('games', [])
                    ]
                except Exception:
                    owned_games[u['username']] = []
            else:
                no_steam_users.append(u['username'])
        _steam_cache.update({'ts': now, 'owned': owned_games, 'no_steam': no_steam_users})

    steam_stats = {}
    for game in get_all_games():
        try: appid_int = int(game['steam_appid'])
        except (TypeError, ValueError): appid_int = None
        if game['steam_appid'] == "non" or not appid_int: steam_stats[game['name']] = {"is_steam": False}
        else:
            missing_users = list(no_steam_users)
            for u_name, appids in owned_games.items():
                if appid_int not in appids: missing_users.append(u_name)
            steam_stats[game['name']] = {"missing_count": len(missing_users), "missing_users": missing_users, "total_users": len(all_users), "is_steam": True}

    user_votes, saturday_stats = {}, {}
    for sat in saturdays:
        vote = conn.execute("SELECT game1, game2, game3 FROM votes WHERE user_id = ? AND target_date = ?", (session['user_id'], sat)).fetchone()
        user_votes[sat] = {'game1': vote['game1'], 'game2': vote['game2'], 'game3': vote['game3']} if vote else None
        scores = {g: 0 for g in games_names}
        for r in conn.execute("SELECT game1, game2, game3, multiplier FROM votes WHERE target_date = ?", (sat,)).fetchall():
            apply_vote_scores(scores, r)
        saturday_stats[sat] = dict(sorted(scores.items(), key=lambda i: i[1], reverse=True))
    conn.close()
    
    return render_template('events.html', saturdays=saturdays, games=games_names, user_votes=user_votes, saturday_stats=saturday_stats, steam_stats=steam_stats, radar_data=radar_data)

@app.route('/vote', methods=['POST'])
def vote():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    target_date = request.form.get('target_date')
    game1 = request.form.get('game1') or None
    game2 = request.form.get('game2') or None
    game3 = request.form.get('game3') or None
    
    selected_games = [g for g in [game1, game2, game3] if g]
    if not selected_games:
        flash("Please pick at least one game.", "error")
        return redirect(url_for('events'))
    if len(selected_games) != len(set(selected_games)):
        flash("Each rank has to be a different game.", "error")
        return redirect(url_for('events'))
    if target_date not in get_next_two_saturdays():
        flash("Invalid voting date.", "error")
        return redirect(url_for('events'))
    
    conn = get_db()
    locked_row = conn.execute("SELECT value FROM config WHERE key = 'voting_locked'").fetchone()
    if locked_row and locked_row['value'] == 'true':
        conn.close()
        flash("Voting is currently locked.", "error")
        return redirect(url_for('events'))

    valid_games = {g['name'] for g in get_all_games()}
    if any(g not in valid_games for g in selected_games):
        conn.close()
        flash("One of the selected games is no longer available.", "error")
        return redirect(url_for('events'))

    existing = conn.execute("SELECT id, multiplier FROM votes WHERE user_id = ? AND target_date = ?", (session['user_id'], target_date)).fetchone()
    multiplier = vote_multiplier(existing)
    user_row = conn.execute("SELECT owns_mvp FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    if user_row and user_row['owns_mvp']:
        multiplier = 2
        conn.execute("UPDATE users SET owns_mvp = 0 WHERE id = ?", (session['user_id'],))
    
    if existing:
        conn.execute("UPDATE votes SET game1 = ?, game2 = ?, game3 = ?, multiplier = ? WHERE user_id = ? AND target_date = ?", 
                     (game1, game2, game3, multiplier, session['user_id'], target_date))
    else:
        conn.execute("INSERT INTO votes (user_id, target_date, game1, game2, game3, multiplier) VALUES (?, ?, ?, ?, ?, ?)",
                     (session['user_id'], target_date, game1, game2, game3, multiplier))
        conn.execute("UPDATE users SET ore_coins = COALESCE(ore_coins, 0) + 10 WHERE id = ?", (session['user_id'],))
        
    conn.commit()
    conn.close()
    flash("Your votes have been saved successfully! 🚀", "success")
    return redirect(url_for('events'))

@app.route('/oretimers')
def oretimers():
    if 'user_id' not in session: return redirect(url_for('index'))
    conn = get_db()
    users = conn.execute("SELECT id, username, avatar, steam_id FROM users").fetchall()
    user_data = []
    for u in users:
        vote_count = conn.execute("SELECT COUNT(*) FROM votes WHERE user_id = ?", (u['id'],)).fetchone()[0]
        badges = []
        if u['username'] == 'caytjee': badges.append({'icon': '👑', 'title': 'Founder & Admin'})
        elif u['username'] == 'torotera': badges.append({'icon': '💻', 'title': 'Developer & Admin'})
        elif u['username'] == 'schmelive': badges.append({'icon': '🖥️', 'title': 'Server Host & Admin'})

        if u['steam_id']: badges.append({'icon': '🎮', 'title': 'Steam Connected'})
        if vote_count >= 5: badges.append({'icon': '🔥', 'title': 'Veteran Voter'})
        elif vote_count > 0: badges.append({'icon': '🗳️', 'title': 'Active Voter'})
        else: badges.append({'icon': '👻', 'title': 'Ghost (No Votes yet)'})
        user_data.append({'username': u['username'], 'avatar': safe_avatar(u['avatar']), 'badges': badges, 'vote_count': vote_count})
    conn.close()
    return render_template('oretimers.html', users=user_data)

@app.route('/history')
def history():
    if 'user_id' not in session: return redirect(url_for('index'))
    conn = get_db()
    dates = conn.execute("SELECT DISTINCT target_date FROM votes WHERE target_date < date('now') ORDER BY target_date DESC").fetchall()
    
    all_votes = conn.execute("SELECT game1, game2, game3, multiplier FROM votes").fetchall()
    scores = {}
    for row in all_votes:
        mult = vote_multiplier(row)
        for g, points in [(row['game1'], 3), (row['game2'], 2), (row['game3'], 1)]:
            if g: scores[g] = scores.get(g, 0) + points * mult
    top_games = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
    
    mvp = conn.execute("SELECT users.username, users.avatar, COUNT(votes.id) as vote_count FROM votes JOIN users ON votes.user_id = users.id GROUP BY users.id ORDER BY vote_count DESC LIMIT 1").fetchone()

    conn.close()
    return render_template('history.html', history_data=[{'date': r['target_date'], 'winner': get_winner_for_date(r['target_date'])} for r in dates], top_games=top_games, mvp=mvp)

@app.route('/shop')
def shop():
    if 'user_id' not in session: return redirect(url_for('index'))
    conn = get_db()
    
    # INFINITE COINS HACK FÜR FOUNDER (in Shop Ansicht)
    if session.get('username', '').lower() == 'caytjee':
        conn.execute("UPDATE users SET ore_coins = 999999 WHERE id = ?", (session['user_id'],))
        conn.commit()

    user = conn.execute("SELECT ore_coins FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    coins = user['ore_coins'] if user and user['ore_coins'] else 0
    conn.close()
    return render_template('shop.html', coins=coins)

@app.route('/api/buy', methods=['POST'])
def buy_item():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    item = data.get('item')
    
    conn = get_db()
    try:
        user = conn.execute("SELECT ore_coins, owns_title, owns_mvp, borders, owned_banners FROM users WHERE id = ?", (session['user_id'],)).fetchone()
        if not user:
            return jsonify({'error': 'User not found'}), 404
        coins = user['ore_coins'] if user['ore_coins'] else 0
        
        owned_borders = [b for b in (user['borders'] or '').split(',') if b]
        owned_banners = [b for b in (user['owned_banners'] or '').split(',') if b]

        prices = {
            'title': 50,
            'mvp': 100,
            'hacker': 50,
            'gold': 100,
            'cosmos': 200,
            'giga': 250,
            'standard': 0,
            'pro': 50,
            'ultra': 150,
            'giga_banner': 200
        }

        if item not in prices:
            return jsonify({'error': 'Invalid item'}), 400

        cost = prices[item]

        if item == 'title':
            if user['owns_title']: return jsonify({'error': 'Already owned'}), 400
            if coins < cost: return jsonify({'error': 'Not enough OC'}), 400
            conn.execute("UPDATE users SET ore_coins = ore_coins - ?, owns_title = 1 WHERE id = ?", (cost, session['user_id']))
            conn.commit()

        elif item == 'mvp':
            if user['owns_mvp']: return jsonify({'error': 'Already owned'}), 400
            if coins < cost: return jsonify({'error': 'Not enough OC'}), 400
            conn.execute("UPDATE users SET ore_coins = ore_coins - ?, owns_mvp = 1 WHERE id = ?", (cost, session['user_id']))
            conn.commit()

        elif item in ['hacker', 'gold', 'cosmos', 'giga']:
            if item in owned_borders:
                conn.execute("UPDATE users SET active_border = ? WHERE id = ?", (item, session['user_id']))
                conn.commit()
                return jsonify({'success': True, 'new_balance': coins, 'message': 'Equipped!'})
            if coins < cost: return jsonify({'error': 'Not enough OC'}), 400
            owned_borders.append(item)
            new_borders_string = ",".join(owned_borders)
            conn.execute("UPDATE users SET ore_coins = ore_coins - ?, borders = ?, active_border = ? WHERE id = ?", (cost, new_borders_string, item, session['user_id']))
            conn.commit()
                
        elif item in ['standard', 'pro', 'ultra', 'giga_banner']:
            if item in owned_banners:
                conn.execute("UPDATE users SET active_banner = ? WHERE id = ?", (item, session['user_id']))
                conn.commit()
                return jsonify({'success': True, 'new_balance': coins, 'message': 'Equipped!'})
            if cost > 0 and coins < cost:
                return jsonify({'error': 'Not enough OC'}), 400
            owned_banners.append(item)
            new_banners_string = ",".join(owned_banners)
            conn.execute("UPDATE users SET ore_coins = COALESCE(ore_coins, 0) - ?, owned_banners = ?, active_banner = ? WHERE id = ?", (cost, new_banners_string, item, session['user_id']))
            conn.commit()

        new_coins = conn.execute("SELECT ore_coins FROM users WHERE id = ?", (session['user_id'],)).fetchone()['ore_coins']
        return jsonify({'success': True, 'new_balance': new_coins, 'message': 'Purchased!'})
    finally:
        conn.close()

CASINO_ALLOWED_STAKES = (5, 10, 20, 50, 100, 250, 500)
# Weighted Ore Wheel: EV = 0.90 (house edge 10%)
# 48% lose 0x, 27% push 1x, 16% win 2x, 7% win 3x, 2% win 5x
CASINO_OUTCOMES = (
    (48, 0, 'lose'),
    (27, 1, 'push'),
    (16, 2, 'win'),
    (7, 3, 'win'),
    (2, 5, 'win'),
)

def _casino_roll():
    roll = secrets.SystemRandom().randrange(100)
    cumulative = 0
    for weight, multiplier, result in CASINO_OUTCOMES:
        cumulative += weight
        if roll < cumulative:
            return multiplier, result
    return 0, 'lose'

@app.route('/api/casino/play', methods=['POST'])
def casino_play():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    try:
        stake = int(data.get('stake'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid stake'}), 400
    if stake not in CASINO_ALLOWED_STAKES:
        return jsonify({'error': 'Stake not allowed'}), 400

    conn = get_db()
    try:
        user = conn.execute("SELECT ore_coins FROM users WHERE id = ?", (session['user_id'],)).fetchone()
        if not user:
            return jsonify({'error': 'User not found'}), 404
        coins = user['ore_coins'] if user['ore_coins'] else 0
        if coins < stake:
            return jsonify({'error': 'Not enough OC'}), 400

        deducted = conn.execute(
            "UPDATE users SET ore_coins = COALESCE(ore_coins, 0) - ? WHERE id = ? AND COALESCE(ore_coins, 0) >= ?",
            (stake, session['user_id'], stake),
        )
        if deducted.rowcount < 1:
            conn.rollback()
            return jsonify({'error': 'Not enough OC'}), 400

        multiplier, result = _casino_roll()
        payout = stake * multiplier
        if payout:
            conn.execute(
                "UPDATE users SET ore_coins = COALESCE(ore_coins, 0) + ? WHERE id = ?",
                (payout, session['user_id']),
            )
        conn.commit()
        new_balance = conn.execute("SELECT ore_coins FROM users WHERE id = ?", (session['user_id'],)).fetchone()['ore_coins']

        if result == 'lose':
            message = f'The house takes your {stake} OC.'
        elif result == 'push':
            message = f'Push! Your {stake} OC come back.'
        else:
            message = f'You won {payout} OC ({multiplier}x)!'

        return jsonify({
            'success': True,
            'result': result,
            'multiplier': multiplier,
            'payout': payout,
            'new_balance': new_balance,
            'message': message,
        })
    except Exception:
        conn.rollback()
        return jsonify({'error': 'Casino error'}), 500
    finally:
        conn.close()

@app.route('/api/discord_sync', methods=['POST'])
def discord_sync():
    data = request.get_json(silent=True) or {}
    discord_name = data.get('discord_name')
    status = data.get('status') 
    activity = data.get('activity') 
    
    if not discord_name: return jsonify({'error': 'No discord_name provided'}), 400
    
    conn = get_db()
    conn.execute("UPDATE users SET discord_status = ?, discord_activity = ? WHERE discord_name = ?", (status, activity, discord_name))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Updated {discord_name}"})

@app.route('/status')
def status():
    if 'user_id' not in session: return redirect(url_for('index'))
    snap = collect_live_status()
    return render_template('status.html', status_json=snap, **snap)

@app.route('/api/status')
def api_status():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(collect_live_status())

@app.route('/watchout', methods=['GET', 'POST'])
def watchout():
    if 'user_id' not in session: return redirect(url_for('index'))
    conn = get_db()
    
    if request.method == 'POST':
        game = request.form.get('game')
        if game:
            username = session.get('username')
            existing_beacon = conn.execute(
                "SELECT id FROM beacons WHERE username = ? AND created_at >= datetime('now', '-2 hours')",
                (username,)
            ).fetchone()
            if existing_beacon:
                conn.execute(
                    "UPDATE beacons SET game = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (game, existing_beacon['id'])
                )
            else:
                conn.execute("INSERT INTO beacons (username, game) VALUES (?, ?)", (username, game))
            conn.commit()
            
            webhook_url = "https://discord.com/api/webhooks/1538971342521765969/-kerPddnA1qS-sD5gPCTNiGr7TpUMEUGeCDX5FB_1z0DDCAEH5jEInwYuE8wOhuzkI6v"
            try:
                payload = {"content": f"🚨 **{username}** is looking for teammates for **{game}**!"}
                requests.post(webhook_url, json=payload, timeout=2)
            except Exception:
                pass 
            
    active_beacons = conn.execute("SELECT * FROM beacons WHERE created_at >= datetime('now', '-2 hours') ORDER BY created_at DESC").fetchall()
    games = [row['name'] for row in conn.execute("SELECT name FROM games").fetchall()]
    conn.close()
    
    return render_template('watchout.html', active_beacons=active_beacons, games=games)

@app.route('/delete_watchout/<int:beacon_id>', methods=['POST'])
def delete_watchout(beacon_id):
    if 'user_id' not in session: return redirect(url_for('index'))
    conn = get_db()
    conn.execute("DELETE FROM beacons WHERE id = ? AND username = ?", (beacon_id, session.get('username')))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('watchout'))

@app.route('/armory', methods=['GET', 'POST'])
def armory():
    if 'user_id' not in session: return redirect(url_for('index'))
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS armory (
                    user_id TEXT PRIMARY KEY, username TEXT, 
                    cpu TEXT, gpu TEXT, mouse TEXT, sens TEXT)''')
    
    try: conn.execute('ALTER TABLE armory ADD COLUMN keyboard TEXT')
    except sqlite3.OperationalError: pass
    
    if request.method == 'POST':
        cpu = request.form.get('cpu', '').strip()
        gpu = request.form.get('gpu', '').strip()
        mouse = request.form.get('mouse', '').strip()
        keyboard = request.form.get('keyboard', '').strip() 
        
        conn.execute('''INSERT INTO armory (user_id, username, cpu, gpu, mouse, keyboard) 
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                            username=excluded.username,
                            cpu=excluded.cpu,
                            gpu=excluded.gpu,
                            mouse=excluded.mouse,
                            keyboard=excluded.keyboard''', 
                     (str(session['user_id']), session.get('username'), cpu, gpu, mouse, keyboard))
        conn.commit()
        
    my_setup = conn.execute("SELECT * FROM armory WHERE user_id = ?", (str(session['user_id']),)).fetchone()
    setups = conn.execute("SELECT * FROM armory").fetchall()
    conn.close()
    return render_template('armory.html', setups=setups, my_setup=my_setup)

@app.route('/roulette')
def roulette():
    if 'user_id' not in session: return redirect(url_for('index'))
    conn = get_db()
    games = [row['name'] for row in conn.execute("SELECT name FROM games").fetchall()]
    conn.close()
    return render_template('roulette.html', games=games)

@app.route('/wishlist', methods=['GET', 'POST'])
def wishlist():
    if 'user_id' not in session: return redirect(url_for('index'))
    conn = get_db()
    
    success = False
    if request.method == 'POST':
        game_name = request.form.get('game_name')
        if game_name:
            conn.execute("INSERT INTO wishlist (user_id, game_name, appid) VALUES (?, ?, ?)", 
                         (session['user_id'], game_name, ''))
            conn.commit()
            success = True
            
    conn.close()
    return render_template('wishlist.html', success=success)

@app.route('/changelog')
def changelog():
    if 'user_id' not in session: return redirect(url_for('index'))
    return render_template('changelog.html')

@app.route('/admin')
def admin():
    if session.get('username') not in ADMINS: return redirect(url_for('home'))
    conn = get_db()
    
    conn.execute('''CREATE TABLE IF NOT EXISTS wishlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    user_id INTEGER, 
                    game_name TEXT, 
                    appid TEXT)''')
    conn.commit()
    
    users = conn.execute("SELECT username, steam_id FROM users").fetchall()
    games = conn.execute("SELECT * FROM games ORDER BY name").fetchall()
    wishlist_requests = conn.execute("SELECT * FROM wishlist ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('admin.html', users=users, games=games, wishlist_requests=wishlist_requests)

@app.route('/admin/add_game', methods=['POST'])
def add_game():
    if session.get('username') not in ADMINS: return redirect(url_for('home'))
    if request.form.get('game_name') and request.form.get('steam_appid'):
        conn = get_db()
        try: 
            conn.execute("INSERT INTO games (name, steam_appid) VALUES (?, ?)", (request.form.get('game_name'), request.form.get('steam_appid')))
            conn.commit()
        except sqlite3.IntegrityError:
            pass 
        conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/delete_game/<int:game_id>', methods=['POST'])
def delete_game(game_id):
    if session.get('username') not in ADMINS: return redirect(url_for('home'))
    conn = get_db()
    conn.execute("DELETE FROM games WHERE id = ?", (game_id,)), conn.commit(), conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/delete_wishlist/<int:req_id>', methods=['POST'])
def delete_wishlist(req_id):
    if session.get('username') not in ADMINS: return redirect(url_for('home'))
    conn = get_db()
    conn.execute("DELETE FROM wishlist WHERE id = ?", (req_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/toggle_lock', methods=['POST'])
def toggle_lock():
    if session.get('username') not in ADMINS: return redirect(url_for('home'))
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key = 'voting_locked'").fetchone()
    current = row['value'] if row else 'false'
    new_val = 'false' if current == 'true' else 'true'
    conn.execute("INSERT INTO config (key, value) VALUES ('voting_locked', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (new_val,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/clear_votes', methods=['POST'])
def clear_votes():
    if session.get('username') not in ADMINS: return redirect(url_for('admin'))
    conn = get_db()
    conn.execute("DELETE FROM votes"), conn.commit(), conn.close()
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)