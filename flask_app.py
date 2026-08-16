from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import sqlite3
import requests
from datetime import date, timedelta

app = Flask(__name__)
app.secret_key = "gaming_saturday_secret"

STEAM_API_KEY = "123456789"
DISCORD_CLIENT_ID = "123456789"
DISCORD_CLIENT_SECRET = "123456789" 
DISCORD_REDIRECT_URI = "https://your-website.???/callback"

DISCORD_AUTH_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_URL = "https://discord.com/api/users/@me"

def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, steam_id TEXT, theme TEXT DEFAULT 'pink')''')
    try: conn.execute('ALTER TABLE users ADD COLUMN avatar TEXT')
    except sqlite3.OperationalError: pass
    conn.execute('''CREATE TABLE IF NOT EXISTS votes (id INTEGER PRIMARY KEY, user_id INTEGER, target_date TEXT, game1 TEXT, game2 TEXT, game3 TEXT, UNIQUE(user_id, target_date))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS games (id INTEGER PRIMARY KEY, name TEXT UNIQUE, steam_appid TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT UNIQUE, value TEXT)''')
    conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('voting_locked', 'false')")

    if conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0:
        default_games = [("Big Walk", "1478500"), ("Codenames", "non"), ("Meccha", "4704690"), ("PEAK", "3527290"), ("R.E.P.O.", "3241660")]
        conn.executemany("INSERT INTO games (name, steam_appid) VALUES (?, ?)", default_games)
    conn.commit()
    conn.close()

init_db()

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
                avatar = user['avatar'] if 'avatar' in user.keys() and user['avatar'] else 'https://cdn.discordapp.com/embed/avatars/0.png'
                session.update({'theme': theme, 'steam_id': steam_id, 'avatar': avatar})
    conn.close()
    return {'current_theme': theme, 'user_steam_id': steam_id, 'user_avatar': avatar, 'game_appids': game_appids, 'voting_locked': voting_locked}

def get_next_two_saturdays():
    today = date.today()
    saturday1 = today + timedelta((5 - today.weekday()) % 7)
    return [saturday1.strftime('%Y-%m-%d'), (saturday1 + timedelta(days=7)).strftime('%Y-%m-%d')]

def get_past_saturdays(n=5):
    today = date.today()
    days_since_saturday = (today.weekday() - 5) % 7
    if days_since_saturday == 0 and today.hour < 20: days_since_saturday = 7
    last_saturday = today - timedelta(days=days_since_saturday)
    return [(last_saturday - timedelta(weeks=i)).strftime('%Y-%m-%d') for i in range(n)]

def get_winner_for_date(target_date):
    conn = get_db()
    rows = conn.execute('SELECT game1, game2, game3 FROM votes WHERE target_date = ?', (target_date,)).fetchall()
    conn.close()
    if not rows: return "TBD (No votes yet)"
    scores = {g['name']: 0 for g in get_all_games()}
    for row in rows:
        if row['game1'] in scores: scores[row['game1']] += 3
        if row['game2'] in scores: scores[row['game2']] += 2
        if row['game3'] in scores: scores[row['game3']] += 1
    if all(v == 0 for v in scores.values()): return "TBD (No votes yet)"
    return max(scores, key=scores.get)

@app.route('/')
@app.route('/home')
def home():
    if 'user_id' not in session: return redirect(url_for('login'))
    next_sat = get_next_two_saturdays()[0]
    past_sat = get_past_saturdays(1)[0]
    return render_template('home.html', next_sat=next_sat, winner=get_winner_for_date(next_sat), last_winner=get_winner_for_date(past_sat))

@app.route('/login')
def login():
    return redirect(f"{DISCORD_AUTH_URL}?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify")

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code: return "Fehler: Kein Code."
    data = {'client_id': DISCORD_CLIENT_ID, 'client_secret': DISCORD_CLIENT_SECRET, 'grant_type': 'authorization_code', 'code': code, 'redirect_uri': DISCORD_REDIRECT_URI}
    token = requests.post(DISCORD_TOKEN_URL, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'}).json()['access_token']
    user_data = requests.get(DISCORD_API_URL, headers={'Authorization': f'Bearer {token}'}).json()
    
    avatar_url = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png" if user_data.get('avatar') else "https://cdn.discordapp.com/embed/avatars/0.png"
    
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (user_data['username'],)).fetchone()
    if not user:
        conn.execute("INSERT INTO users (username, password, avatar) VALUES (?, ?, ?)", (user_data['username'], "discord_oauth", avatar_url))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (user_data['username'],)).fetchone()
    else:
        conn.execute("UPDATE users SET avatar = ? WHERE username = ?", (avatar_url, user_data['username']))
        conn.commit()
        
    session.update({'user_id': user['id'], 'username': user['username'], 'theme': user['theme'] or 'pink', 'steam_id': user['steam_id'] or '', 'avatar': avatar_url})
    conn.close()
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/profile', methods=['POST'])
def profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    theme, steam_id = request.form.get('theme'), request.form.get('steam_id')
    conn = get_db()
    if theme: conn.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, session['user_id'])), session.update({'theme': theme})
    if steam_id is not None: conn.execute("UPDATE users SET steam_id = ? WHERE id = ?", (steam_id, session['user_id'])), session.update({'steam_id': steam_id})
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('home'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    return render_template('dashboard.html', dashboard_data=[{'date': d, 'winner': get_winner_for_date(d)} for d in get_past_saturdays(5)])

@app.route('/events')
def events():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    saturdays, games_names = get_next_two_saturdays(), [g['name'] for g in get_all_games()]
    
    all_users = conn.execute("SELECT username, steam_id FROM users").fetchall()
    owned_games = {}
    no_steam_users = []
    
    for u in all_users:
        if u['steam_id']:
            try: 
                owned_games[u['username']] = [g['appid'] for g in requests.get(f"http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/?key={STEAM_API_KEY}&steamid={u['steam_id']}&format=json", timeout=3).json().get('response', {}).get('games', [])]
            except: 
                owned_games[u['username']] = []
        else:
            no_steam_users.append(u['username'])

    steam_stats = {}
    for game in get_all_games():
        try: appid_int = int(game['steam_appid'])
        except: appid_int = None
        
        if game['steam_appid'] == "non" or not appid_int: 
            steam_stats[game['name']] = {"is_steam": False}
        else: 
            missing_users = list(no_steam_users)
            for u_name, appids in owned_games.items():
                if appid_int not in appids:
                    missing_users.append(u_name)
            
            steam_stats[game['name']] = {
                "missing_count": len(missing_users),
                "missing_users": missing_users,
                "total_users": len(all_users),
                "is_steam": True
            }

    user_votes, saturday_stats = {}, {}
    for sat in saturdays:
        vote = conn.execute("SELECT game1, game2, game3 FROM votes WHERE user_id = ? AND target_date = ?", (session['user_id'], sat)).fetchone()
        user_votes[sat] = {'game1': vote['game1'], 'game2': vote['game2'], 'game3': vote['game3']} if vote else None
        scores = {g: 0 for g in games_names}
        for r in conn.execute("SELECT game1, game2, game3 FROM votes WHERE target_date = ?", (sat,)).fetchall():
            for i, p in enumerate([3, 2, 1]): 
                if r[f'game{i+1}'] in scores: scores[r[f'game{i+1}']] += p
        saturday_stats[sat] = dict(sorted(scores.items(), key=lambda i: i[1], reverse=True))
    conn.close()
    return render_template('events.html', saturdays=saturdays, games=games_names, user_votes=user_votes, saturday_stats=saturday_stats, steam_stats=steam_stats)

@app.route('/vote', methods=['POST'])
def vote():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db()
    locked_row = conn.execute("SELECT value FROM config WHERE key = 'voting_locked'").fetchone()
    if locked_row and locked_row['value'] == 'true':
        conn.close()
        return "Fehler: Die Abstimmung ist aktuell vom Admin gesperrt! <br><br> <a href='/events'>Zurück</a>"
    
    t_date, g1, g2, g3 = request.form['target_date'], request.form.get('game1'), request.form.get('game2'), request.form.get('game3')
    selected = [g for g in [g1, g2, g3] if g]
    if len(selected) != len(set(selected)): 
        conn.close()
        return "Fehler: Du darfst ein Spiel nicht mehrfach wählen! <br><br> <a href='/events'>Zurück</a>"
        
    conn.execute('''INSERT INTO votes (user_id, target_date, game1, game2, game3) VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, target_date) DO UPDATE SET game1=excluded.game1, game2=excluded.game2, game3=excluded.game3''', (session['user_id'], t_date, g1, g2, g3))
    conn.commit()
    conn.close()
    return redirect(url_for('events'))

@app.route('/oretimers')
def oretimers():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    users = conn.execute("SELECT id, username, avatar, steam_id FROM users").fetchall()
    user_data = []
    for u in users:
        vote_count = conn.execute("SELECT COUNT(*) FROM votes WHERE user_id = ?", (u['id'],)).fetchone()[0]
        badges = []
        if u['username'] == 'caytjee': badges.append({'icon': '👑', 'title': 'Founder & Admin'})
        elif u['username'] == 'torotera': badges.append({'icon': '💻', 'title': 'Developer & Admin'})
        
        if u['steam_id']: badges.append({'icon': '🎮', 'title': 'Steam Connected'})
        if vote_count >= 5: badges.append({'icon': '🔥', 'title': 'Veteran Voter'})
        elif vote_count > 0: badges.append({'icon': '🗳️', 'title': 'Active Voter'})
        else: badges.append({'icon': '👻', 'title': 'Ghost (No Votes yet)'})
        user_data.append({'username': u['username'], 'avatar': u['avatar'], 'badges': badges})
    conn.close()
    return render_template('oretimers.html', users=user_data)

@app.route('/history')
def history():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    dates = conn.execute("SELECT DISTINCT target_date FROM votes WHERE target_date < date('now') ORDER BY target_date DESC").fetchall()
    conn.close()
    return render_template('history.html', history_data=[{'date': r['target_date'], 'winner': get_winner_for_date(r['target_date'])} for r in dates])

@app.route('/admin')
def admin():
    if session.get('username') not in ['caytjee', 'torotera']: return redirect(url_for('home'))
    conn = get_db()
    users = conn.execute("SELECT username, steam_id FROM users").fetchall()
    games = conn.execute("SELECT * FROM games ORDER BY name").fetchall()
    conn.close()
    return render_template('admin.html', users=users, games=games)

@app.route('/admin/add_game', methods=['POST'])
def add_game():
    if session.get('username') not in ['caytjee', 'torotera']: return redirect(url_for('home'))
    if request.form.get('game_name') and request.form.get('steam_appid'):
        conn = get_db()
        try: conn.execute("INSERT INTO games (name, steam_appid) VALUES (?, ?)", (request.form.get('game_name'), request.form.get('steam_appid'))), conn.commit()
        except sqlite3.IntegrityError: pass
        conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/delete_game/<int:game_id>', methods=['POST'])
def delete_game(game_id):
    if session.get('username') not in ['caytjee', 'torotera']: return redirect(url_for('home'))
    conn = get_db()
    conn.execute("DELETE FROM games WHERE id = ?", (game_id,)), conn.commit(), conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/toggle_lock', methods=['POST'])
def toggle_lock():
    if session.get('username') not in ['caytjee', 'torotera']: return redirect(url_for('home'))
    conn = get_db()
    current = conn.execute("SELECT value FROM config WHERE key = 'voting_locked'").fetchone()['value']
    new_val = 'false' if current == 'true' else 'true'
    conn.execute("UPDATE config SET value = ? WHERE key = 'voting_locked'", (new_val,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/clear_votes', methods=['POST'])
def clear_votes():
    if session.get('username') != 'caytjee': return redirect(url_for('admin'))
    conn = get_db()
    conn.execute("DELETE FROM votes"), conn.commit(), conn.close()
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
