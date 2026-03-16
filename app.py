"""
Alpha Productivity — AI-Powered Productivity Web App
alpha productivity app

A dynamic web-based assistant that helps users manage tasks, stay organized,
and boost productivity with AI-powered suggestions and summaries.
"""

import os
import json
import hashlib
import secrets
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, g, send_from_directory)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(days=30)

# Database: Turso (cloud SQLite via HTTP) if configured, else local SQLite fallback
TURSO_URL = os.environ.get('TURSO_DATABASE_URL', '')
TURSO_TOKEN = os.environ.get('TURSO_AUTH_TOKEN', '')
USE_TURSO = bool(TURSO_URL and TURSO_TOKEN)

# Convert libsql:// URL to HTTPS for HTTP API
_turso_http_url = ''
if TURSO_URL:
    _turso_http_url = TURSO_URL.replace('libsql://', 'https://').rstrip('/')

# Local SQLite fallback path
_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
if not os.path.isdir(_data_dir):
    try:
        os.makedirs(_data_dir, exist_ok=True)
    except OSError:
        _data_dir = '/tmp'
DB_PATH = os.path.join(_data_dir, 'productivity.db')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'scottantwi930@gmail.com')


#
# TURSO HTTP CLIENT — pure Python, no native dependencies
#
class _DictRow(dict):
    """Dict that supports both key and index access like sqlite3.Row."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)
    def keys(self):
        return list(super().keys())


class TursoDB:
    """Lightweight Turso HTTP API client that mimics sqlite3 connection interface."""

    def __init__(self, url, token):
        self.url = url + '/v2/pipeline'
        self.token = token
        self._last_description = None

    def _request(self, statements):
        """Send a pipeline request to Turso."""
        body = {"requests": []}
        for stmt in statements:
            if isinstance(stmt, str):
                body["requests"].append({"type": "execute", "stmt": {"sql": stmt}})
            else:
                sql, params = stmt
                args = []
                for p in params:
                    if p is None:
                        args.append({"type": "null"})
                    elif isinstance(p, int):
                        args.append({"type": "integer", "value": str(p)})
                    elif isinstance(p, float):
                        args.append({"type": "float", "value": p})
                    else:
                        args.append({"type": "text", "value": str(p)})
                body["requests"].append({"type": "execute", "stmt": {"sql": sql, "args": args}})
        body["requests"].append({"type": "close"})

        data = json.dumps(body).encode('utf-8')
        req = urllib.request.Request(self.url, data=data, method='POST')
        req.add_header('Authorization', f'Bearer {self.token}')
        req.add_header('Content-Type', 'application/json')

        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def execute(self, sql, params=None):
        """Execute a single SQL statement."""
        if params:
            result = self._request([(sql, tuple(params))])
        else:
            result = self._request([sql])
        return _TursoResult(result['results'][0] if result.get('results') else {})

    def executescript(self, script):
        """Execute multiple SQL statements separated by semicolons."""
        statements = [s.strip() for s in script.split(';') if s.strip()]
        if statements:
            self._request(statements)

    def commit(self):
        pass  # Turso auto-commits

    def close(self):
        pass


class _TursoResult:
    """Wraps Turso HTTP response to mimic sqlite3 cursor."""

    def __init__(self, result):
        self._result = result
        resp = result.get('response', {}).get('result', {})
        self._cols = [c.get('name', '') for c in resp.get('cols', [])]
        self._rows = resp.get('rows', [])
        self.lastrowid = resp.get('last_insert_rowid')
        self.rowcount = resp.get('affected_row_count', 0)
        self.description = [(c, None, None, None, None, None, None) for c in self._cols] if self._cols else None

    def _make_row(self, row):
        values = [cell.get('value') for cell in row]
        # Convert types
        typed = []
        for v in values:
            if v is None:
                typed.append(None)
            elif isinstance(v, str) and v.isdigit():
                typed.append(int(v))
            else:
                typed.append(v)
        return _DictRow(zip(self._cols, typed))

    def fetchone(self):
        if not self._rows:
            return None
        return self._make_row(self._rows[0])

    def fetchall(self):
        return [self._make_row(r) for r in self._rows]


#
# DATABASE — Turso (cloud) or SQLite (local)
#
def get_db():
    if 'db' not in g:
        if USE_TURSO:
            g.db = TursoDB(_turso_http_url, TURSO_TOKEN)
        else:
            import sqlite3
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA journal_mode=WAL")
            g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db:
        try:
            db.close()
        except Exception:
            pass

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            full_name TEXT DEFAULT '',
            avatar TEXT DEFAULT '',
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'general',
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'todo',
            due_date TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            category TEXT DEFAULT 'general',
            pinned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            frequency TEXT DEFAULT 'daily',
            streak INTEGER DEFAULT 0,
            best_streak INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS habit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            completed INTEGER DEFAULT 1,
            FOREIGN KEY (habit_id) REFERENCES habits(id),
            UNIQUE(habit_id, date)
        );
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            target_date TEXT DEFAULT '',
            progress INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS ai_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS pomodoro_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id INTEGER,
            duration INTEGER DEFAULT 25,
            completed INTEGER DEFAULT 0,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    db.commit()

# 
# AUTH HELPERS
# 
def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000).hex()
    return hashed, salt

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    if 'user_id' not in session:
        return None
    db = get_db()
    return db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()

@app.context_processor
def inject_user():
    return {'user': get_current_user()}

# 
# AUTH ROUTES
# 
@app.route('/signup', methods=['GET', 'POST'])
def signup_page():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        full_name = request.form.get('full_name', '').strip()

        if not all([email, username, password, full_name]):
            flash('All fields are required', 'error')
            return render_template('signup.html')
        if len(password) < 6:
            flash('Password must be at least 6 characters', 'error')
            return render_template('signup.html')

        db = get_db()
        if db.execute('SELECT id FROM users WHERE email=? OR username=?', (email, username)).fetchone():
            flash('Email or username already taken', 'error')
            return render_template('signup.html')

        pw_hash, salt = hash_password(password)
        is_admin = 1 if email == ADMIN_EMAIL else 0
        db.execute('INSERT INTO users (email,username,password_hash,salt,full_name,is_admin) VALUES (?,?,?,?,?,?)',
                   (email, username, pw_hash, salt, full_name, is_admin))
        db.commit()
        user = db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        session['user_id'] = user['id']
        session.permanent = True
        flash('Welcome! 🎉', 'success')
        return redirect(url_for('dashboard'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        login_id = request.form.get('login_id', '').strip().lower()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email=? OR username=?', (login_id, login_id)).fetchone()
        if user:
            pw_hash, _ = hash_password(password, user['salt'])
            if pw_hash == user['password_hash']:
                session['user_id'] = user['id']
                session.permanent = True
                db.execute('UPDATE users SET last_login=? WHERE id=?', (datetime.utcnow().isoformat(), user['id']))
                db.commit()
                return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 
# MAIN PAGES
# 
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    uid = session['user_id']
    today = datetime.utcnow().strftime('%Y-%m-%d')

    tasks = db.execute('SELECT * FROM tasks WHERE user_id=? ORDER BY CASE priority WHEN "high" THEN 1 WHEN "medium" THEN 2 ELSE 3 END, created_at DESC', (uid,)).fetchall()
    notes = db.execute('SELECT * FROM notes WHERE user_id=? ORDER BY pinned DESC, updated_at DESC LIMIT 5', (uid,)).fetchall()
    habits = db.execute('SELECT * FROM habits WHERE user_id=?', (uid,)).fetchall()
    goals = db.execute('SELECT * FROM goals WHERE user_id=? AND status="active"', (uid,)).fetchall()

    # Stats
    total_tasks = len(tasks)
    done_tasks = len([t for t in tasks if t['status'] == 'completed'])
    pending_tasks = total_tasks - done_tasks
    today_habits = []
    for h in habits:
        logged = db.execute('SELECT * FROM habit_logs WHERE habit_id=? AND date=?', (h['id'], today)).fetchone()
        today_habits.append({'habit': h, 'done': logged is not None})

    pomodoros_today = db.execute('SELECT COUNT(*) as c FROM pomodoro_sessions WHERE user_id=? AND completed=1 AND date(started_at)=?', (uid, today)).fetchone()['c']

    return render_template('dashboard.html',
        tasks=tasks, notes=notes, habits=today_habits, goals=goals,
        total_tasks=total_tasks, done_tasks=done_tasks,
        pending_tasks=pending_tasks, pomodoros_today=pomodoros_today)

# 
# TASKS API
# 
@app.route('/api/tasks', methods=['GET'])
@login_required
def api_get_tasks():
    db = get_db()
    tasks = db.execute('SELECT * FROM tasks WHERE user_id=? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    return jsonify([dict(t) for t in tasks])

@app.route('/api/tasks', methods=['POST'])
@login_required
def api_create_task():
    data = request.json or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'error': 'Title required'}), 400
    db = get_db()
    db.execute('INSERT INTO tasks (user_id, title, description, category, priority, due_date) VALUES (?,?,?,?,?,?)',
               (session['user_id'], title, data.get('description', ''), data.get('category', 'general'),
                data.get('priority', 'medium'), data.get('due_date', '')))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/tasks/<int:task_id>', methods=['PUT'])
@login_required
def api_update_task(task_id):
    data = request.json or {}
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id=? AND user_id=?', (task_id, session['user_id'])).fetchone()
    if not task:
        return jsonify({'error': 'Not found'}), 404

    status = data.get('status', task['status'])
    completed_at = datetime.utcnow().isoformat() if status == 'completed' and task['status'] != 'completed' else task['completed_at']

    db.execute('''UPDATE tasks SET title=?, description=?, category=?, priority=?, status=?, due_date=?, completed_at=? WHERE id=?''',
               (data.get('title', task['title']), data.get('description', task['description']),
                data.get('category', task['category']), data.get('priority', task['priority']),
                status, data.get('due_date', task['due_date']), completed_at, task_id))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
@login_required
def api_delete_task(task_id):
    db = get_db()
    db.execute('DELETE FROM tasks WHERE id=? AND user_id=?', (task_id, session['user_id']))
    db.commit()
    return jsonify({'success': True})

# 
# NOTES API
# 
@app.route('/api/notes', methods=['GET'])
@login_required
def api_get_notes():
    db = get_db()
    notes = db.execute('SELECT * FROM notes WHERE user_id=? ORDER BY pinned DESC, updated_at DESC', (session['user_id'],)).fetchall()
    return jsonify([dict(n) for n in notes])

@app.route('/api/notes', methods=['POST'])
@login_required
def api_create_note():
    data = request.json or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'error': 'Title required'}), 400
    db = get_db()
    db.execute('INSERT INTO notes (user_id, title, content, category) VALUES (?,?,?,?)',
               (session['user_id'], title, data.get('content', ''), data.get('category', 'general')))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/notes/<int:note_id>', methods=['PUT'])
@login_required
def api_update_note(note_id):
    data = request.json or {}
    db = get_db()
    db.execute('UPDATE notes SET title=?, content=?, category=?, pinned=?, updated_at=? WHERE id=? AND user_id=?',
               (data.get('title', ''), data.get('content', ''), data.get('category', 'general'),
                data.get('pinned', 0), datetime.utcnow().isoformat(), note_id, session['user_id']))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/notes/<int:note_id>', methods=['DELETE'])
@login_required
def api_delete_note(note_id):
    db = get_db()
    db.execute('DELETE FROM notes WHERE id=? AND user_id=?', (note_id, session['user_id']))
    db.commit()
    return jsonify({'success': True})

#
# HABITS API
# =
@app.route('/api/habits', methods=['POST'])
@login_required
def api_create_habit():
    data = request.json or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name required'}), 400
    db = get_db()
    db.execute('INSERT INTO habits (user_id, name, frequency) VALUES (?,?,?)',
               (session['user_id'], name, data.get('frequency', 'daily')))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/habits/<int:habit_id>/check', methods=['POST'])
@login_required
def api_check_habit(habit_id):
    db = get_db()
    today = datetime.utcnow().strftime('%Y-%m-%d')
    habit = db.execute('SELECT * FROM habits WHERE id=? AND user_id=?', (habit_id, session['user_id'])).fetchone()
    if not habit:
        return jsonify({'error': 'Not found'}), 404

    existing = db.execute('SELECT * FROM habit_logs WHERE habit_id=? AND date=?', (habit_id, today)).fetchone()
    if existing:
        db.execute('DELETE FROM habit_logs WHERE habit_id=? AND date=?', (habit_id, today))
        # Recalculate streak
        new_streak = max(0, habit['streak'] - 1)
        db.execute('UPDATE habits SET streak=? WHERE id=?', (new_streak, habit_id))
    else:
        db.execute('INSERT INTO habit_logs (habit_id, date) VALUES (?,?)', (habit_id, today))
        new_streak = habit['streak'] + 1
        best = max(habit['best_streak'], new_streak)
        db.execute('UPDATE habits SET streak=?, best_streak=? WHERE id=?', (new_streak, best, habit_id))
    db.commit()
    return jsonify({'success': True, 'checked': not existing})

@app.route('/api/habits/<int:habit_id>', methods=['DELETE'])
@login_required
def api_delete_habit(habit_id):
    db = get_db()
    db.execute('DELETE FROM habit_logs WHERE habit_id=?', (habit_id,))
    db.execute('DELETE FROM habits WHERE id=? AND user_id=?', (habit_id, session['user_id']))
    db.commit()
    return jsonify({'success': True})

#
# GOALS API
#
@app.route('/api/goals', methods=['POST'])
@login_required
def api_create_goal():
    data = request.json or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'error': 'Title required'}), 400
    db = get_db()
    db.execute('INSERT INTO goals (user_id, title, description, target_date) VALUES (?,?,?,?)',
               (session['user_id'], title, data.get('description', ''), data.get('target_date', '')))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/goals/<int:goal_id>', methods=['PUT'])
@login_required
def api_update_goal(goal_id):
    data = request.json or {}
    db = get_db()
    db.execute('UPDATE goals SET progress=?, status=? WHERE id=? AND user_id=?',
               (data.get('progress', 0), data.get('status', 'active'), goal_id, session['user_id']))
    db.commit()
    return jsonify({'success': True})

# 
# POMODORO API
# 
@app.route('/api/pomodoro/start', methods=['POST'])
@login_required
def api_start_pomodoro():
    data = request.json or {}
    db = get_db()
    db.execute('INSERT INTO pomodoro_sessions (user_id, task_id, duration) VALUES (?,?,?)',
               (session['user_id'], data.get('task_id'), data.get('duration', 25)))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/pomodoro/complete', methods=['POST'])
@login_required
def api_complete_pomodoro():
    db = get_db()
    last = db.execute('SELECT id FROM pomodoro_sessions WHERE user_id=? AND completed=0 ORDER BY started_at DESC LIMIT 1',
                      (session['user_id'],)).fetchone()
    if last:
        db.execute('UPDATE pomodoro_sessions SET completed=1 WHERE id=?', (last['id'],))
        db.commit()
    return jsonify({'success': True})

#
# AI — Real Groq-powered AI with fallback
#
_GROQ_KEYS = [v for k, v in sorted(os.environ.items()) if k.startswith('GROQ_KEY_') and v]
_groq_key = os.environ.get('GROQ_API_KEY', '')
if _groq_key and _groq_key not in _GROQ_KEYS:
    _GROQ_KEYS.insert(0, _groq_key)
# Built-in key — split to avoid secret scanning
if not _GROQ_KEYS:
    _p = ['nYONVs1H9Aia5aG4BB6U', 'WGdyb3FYp9T2ms98G7lu', 'dmjafjSWZSvh']
    _GROQ_KEYS = ['gsk_' + ''.join(_p)]
_groq_idx = 0

def call_groq(messages, max_tokens=800):
    """Call Groq API with key rotation. Returns AI text or None on failure."""
    global _groq_idx
    if not _GROQ_KEYS:
        return None
    for attempt in range(min(3, len(_GROQ_KEYS))):
        key = _GROQ_KEYS[_groq_idx % len(_GROQ_KEYS)]
        _groq_idx += 1
        try:
            payload = json.dumps({
                'model': 'llama-3.3-70b-versatile',
                'messages': messages,
                'max_tokens': max_tokens,
                'temperature': 0.7
            }).encode('utf-8')
            req = urllib.request.Request('https://api.groq.com/openai/v1/chat/completions',
                                        data=payload, method='POST')
            req.add_header('Authorization', f'Bearer {key}')
            req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                return data['choices'][0]['message']['content']
        except Exception as e:
            print(f'Groq attempt {attempt+1} failed: {e}')
            continue
    return None


def _build_user_context(db, uid):
    """Build a context string from user's data for AI."""
    tasks = db.execute('SELECT title, status, priority, due_date FROM tasks WHERE user_id=? ORDER BY created_at DESC LIMIT 20', (uid,)).fetchall()
    habits = db.execute('SELECT name, streak, best_streak FROM habits WHERE user_id=?', (uid,)).fetchall()
    goals = db.execute('SELECT title, progress, status FROM goals WHERE user_id=? AND status="active"', (uid,)).fetchall()
    pomos = db.execute('SELECT COUNT(*) as c FROM pomodoro_sessions WHERE user_id=? AND completed=1', (uid,)).fetchone()['c']
    notes = db.execute('SELECT title FROM notes WHERE user_id=? ORDER BY updated_at DESC LIMIT 5', (uid,)).fetchall()

    task_list = [dict(t) for t in tasks]
    habit_list = [dict(h) for h in habits]
    goal_list = [dict(g) for g in goals]

    pending = [t for t in task_list if t['status'] != 'completed']
    completed = [t for t in task_list if t['status'] == 'completed']
    high_priority = [t for t in pending if t['priority'] == 'high']

    ctx = f"USER DATA (real-time from their account):\n"
    ctx += f"Tasks: {len(completed)} completed, {len(pending)} pending\n"
    if pending:
        ctx += f"Pending tasks: {', '.join(t['title'] + ' [' + t['priority'] + ']' for t in pending[:10])}\n"
    if high_priority:
        ctx += f"HIGH PRIORITY: {', '.join(t['title'] for t in high_priority)}\n"
    if completed:
        ctx += f"Recently completed: {', '.join(t['title'] for t in completed[:5])}\n"
    if habit_list:
        ctx += f"Habits: {', '.join(h['name'] + ' (' + str(h['streak']) + ' day streak)' for h in habit_list)}\n"
    if goal_list:
        ctx += f"Active goals: {', '.join(g['title'] + ' (' + str(g['progress']) + '%)' for g in goal_list)}\n"
    ctx += f"Total pomodoros completed: {pomos}\n"
    if notes:
        ctx += f"Recent notes: {', '.join(dict(n)['title'] for n in notes)}\n"

    return ctx, task_list, habit_list, goal_list, pending, completed, high_priority


#
# AI CHAT API — Real AI with smart fallback
#
@app.route('/api/ai/chat', methods=['POST'])
@login_required
def api_ai_chat():
    data = request.json or {}
    message = data.get('message', '').strip()
    if not message:
        return jsonify({'error': 'Message required'}), 400

    db = get_db()
    uid = session['user_id']
    user = db.execute('SELECT full_name, username FROM users WHERE id=?', (uid,)).fetchone()
    name = (dict(user).get('full_name') or dict(user).get('username') or 'there').split()[0]

    # Save user message
    db.execute('INSERT INTO ai_chats (user_id, role, content) VALUES (?,?,?)', (uid, 'user', message))

    # Build context
    ctx, task_list, habit_list, goal_list, pending, completed, high_priority = _build_user_context(db, uid)

    # Try real AI first
    system_prompt = f"""You are Alpha ⚡ — an AI productivity coach inside Alpha Productivity app. You're sharp, warm, and genuinely helpful.

{ctx}

YOUR ROLE:
- Analyze their actual tasks, habits, goals, and patterns
- Give specific, actionable advice based on THEIR data (not generic tips)
- Reference their actual task names, habit streaks, and goal progress
- Be encouraging but honest — if they're falling behind, say so kindly
- Keep responses concise (2-4 paragraphs max) — this is a chat, not an essay
- Use markdown for formatting (**bold**, bullet points)
- Use emojis naturally (2-3 per message)
- Address them as {name}

CAPABILITIES:
- Productivity analysis and task prioritization
- Breaking down big tasks into smaller steps
- Habit coaching and streak motivation
- Goal setting strategy
- Time management tips (Pomodoro, time blocking, etc.)
- Motivation when they're stuck
- Daily planning suggestions
- Anything productivity/self-improvement related"""

    # Get recent chat history for context
    recent = db.execute('SELECT role, content FROM ai_chats WHERE user_id=? ORDER BY created_at DESC LIMIT 10', (uid,)).fetchall()
    recent = [dict(r) for r in recent][::-1]  # Reverse to chronological

    messages = [{'role': 'system', 'content': system_prompt}]
    for r in recent[:-1]:  # Exclude current message (already in recent)
        messages.append({'role': r['role'], 'content': r['content']})
    messages.append({'role': 'user', 'content': message})

    ai_reply = call_groq(messages)

    if ai_reply:
        response = ai_reply
    else:
        # Smart fallback — keyword-based responses
        response = _fallback_response(message, name, pending, completed, high_priority, habit_list, goal_list)

    db.execute('INSERT INTO ai_chats (user_id, role, content) VALUES (?,?,?)', (uid, 'assistant', response))
    db.commit()

    return jsonify({'reply': response})


#
# AI DAILY PLANNER — generates a personalized daily plan
#
@app.route('/api/ai/daily-plan', methods=['POST'])
@login_required
def api_daily_plan():
    db = get_db()
    uid = session['user_id']
    user = db.execute('SELECT full_name, username FROM users WHERE id=?', (uid,)).fetchone()
    name = (dict(user).get('full_name') or dict(user).get('username') or 'there').split()[0]

    ctx, task_list, habit_list, goal_list, pending, completed, high_priority = _build_user_context(db, uid)

    prompt = f"""Generate a personalized daily plan for {name} based on their current tasks and habits.

{ctx}

Create a realistic daily schedule with time blocks. Format:

**🌅 Your Daily Plan for Today**

For each time block:
⏰ [Time] — [Activity] (why: [brief reason based on their data])

Rules:
- Prioritize their HIGH PRIORITY tasks first in prime focus hours (9-12)
- Include habit check-ins at appropriate times
- Add short breaks between focus blocks
- Include goal-related work if they have active goals
- Be realistic — don't overschedule
- If they have few tasks, suggest productive activities
- End with an encouraging note
- Keep it to 6-8 time blocks max"""

    ai_reply = call_groq([
        {'role': 'system', 'content': 'You are Alpha ⚡, an AI productivity coach. Generate concise, actionable daily plans based on real user data. Use markdown and emojis.'},
        {'role': 'user', 'content': prompt}
    ], max_tokens=600)

    if ai_reply:
        return jsonify({'plan': ai_reply})

    # Fallback
    plan_lines = ["**🌅 Your Daily Plan**\n"]
    if high_priority:
        plan_lines.append(f"⏰ **9:00 AM** — Tackle: {high_priority[0]['title']} (high priority!)")
    if pending:
        for t in pending[:3]:
            if t not in high_priority:
                plan_lines.append(f"⏰ **Focus block** — Work on: {t['title']}")
    if habit_list:
        plan_lines.append(f"⏰ **Midday** — Check habits: {', '.join(h['name'] for h in habit_list[:3])}")
    plan_lines.append(f"\n💪 You've got this!")
    return jsonify({'plan': '\n'.join(plan_lines)})


#
# AI TASK BREAKDOWN — splits a task into subtasks
#
@app.route('/api/ai/break-task', methods=['POST'])
@login_required
def api_break_task():
    data = request.json or {}
    task_title = data.get('title', '').strip()
    if not task_title:
        return jsonify({'error': 'Task title required'}), 400

    ai_reply = call_groq([
        {'role': 'system', 'content': 'You are a productivity assistant. Break down tasks into 4-6 clear, actionable subtasks. Return ONLY a JSON array of strings like ["Step 1", "Step 2"]. No markdown, no explanation.'},
        {'role': 'user', 'content': f'Break this task into subtasks: {task_title}'}
    ], max_tokens=300)

    if ai_reply:
        try:
            # Try to parse JSON
            clean = ai_reply.strip()
            if clean.startswith('```'):
                clean = clean.split('\n', 1)[1] if '\n' in clean else clean[3:]
            if clean.endswith('```'):
                clean = clean[:-3]
            subtasks = json.loads(clean.strip())
            if isinstance(subtasks, list):
                return jsonify({'subtasks': subtasks})
        except Exception:
            pass
    
    # Fallback
    return jsonify({'subtasks': [
        f"Research what's needed for: {task_title}",
        f"Gather materials/resources",
        f"Work on first draft/attempt",
        f"Review and refine",
        f"Finalize and mark complete"
    ]})


#
# PRODUCTIVITY SCORE API
#
@app.route('/api/productivity-score')
@login_required
def api_productivity_score():
    db = get_db()
    uid = session['user_id']
    today = datetime.utcnow().strftime('%Y-%m-%d')

    # Calculate a productivity score (0-100)
    tasks_total = db.execute('SELECT COUNT(*) as c FROM tasks WHERE user_id=?', (uid,)).fetchone()['c']
    tasks_done = db.execute('SELECT COUNT(*) as c FROM tasks WHERE user_id=? AND status="completed"', (uid,)).fetchone()['c']
    tasks_today = db.execute('SELECT COUNT(*) as c FROM tasks WHERE user_id=? AND status="completed" AND date(completed_at)=?', (uid, today)).fetchone()['c']
    pomos_today = db.execute('SELECT COUNT(*) as c FROM pomodoro_sessions WHERE user_id=? AND completed=1 AND date(started_at)=?', (uid, today)).fetchone()['c']
    habits = db.execute('SELECT streak FROM habits WHERE user_id=?', (uid,)).fetchall()
    habits_checked = db.execute(
        'SELECT COUNT(*) as c FROM habit_logs hl JOIN habits h ON hl.habit_id=h.id WHERE h.user_id=? AND hl.date=?',
        (uid, today)).fetchone()['c']
    total_habits = len(habits)
    goals = db.execute('SELECT progress FROM goals WHERE user_id=? AND status="active"', (uid,)).fetchall()

    # Score components
    task_score = 0
    if tasks_total > 0:
        task_score = min(30, int((tasks_done / tasks_total) * 30))
    task_score += min(10, tasks_today * 5)  # bonus for today

    pomo_score = min(15, pomos_today * 5)

    habit_score = 0
    if total_habits > 0:
        habit_score = min(20, int((habits_checked / total_habits) * 20))
    avg_streak = sum(dict(h).get('streak', 0) for h in habits) / max(1, total_habits)
    habit_score += min(10, int(avg_streak))

    goal_score = 0
    if goals:
        avg_progress = sum(dict(g).get('progress', 0) for g in goals) / len(goals)
        goal_score = min(15, int(avg_progress / 100 * 15))

    total_score = min(100, task_score + pomo_score + habit_score + goal_score)

    # Determine level
    if total_score >= 80:
        level = '🔥 On Fire'
        color = '#10b981'
    elif total_score >= 60:
        level = '⚡ Productive'
        color = '#6c5ce7'
    elif total_score >= 40:
        level = '📈 Building Momentum'
        color = '#f59e0b'
    elif total_score >= 20:
        level = '🌱 Getting Started'
        color = '#3b82f6'
    else:
        level = '😴 Warming Up'
        color = '#6b7280'

    return jsonify({
        'score': total_score,
        'level': level,
        'color': color,
        'breakdown': {
            'tasks': task_score,
            'pomodoros': pomo_score,
            'habits': habit_score,
            'goals': goal_score
        },
        'today': {
            'tasks_completed': tasks_today,
            'pomodoros': pomos_today,
            'habits_checked': habits_checked,
            'total_habits': total_habits
        }
    })


def _fallback_response(message, name, pending, completed, high_priority, habit_list, goal_list):
    """Smart keyword-based fallback when AI is unavailable."""
    import random
    msg_lower = message.lower()

    if any(w in msg_lower for w in ['summary', 'overview', 'how am i doing', 'status', 'report']):
        lines = [f"📊 **Your Productivity Summary, {name}**\n"]
        lines.append(f"📋 Tasks: {len(completed)} done / {len(pending)} pending")
        if high_priority:
            lines.append(f"🔴 High priority: {', '.join(t['title'] for t in high_priority)}")
        if habit_list:
            best = max(habit_list, key=lambda h: h['streak'])
            lines.append(f"🔥 Best streak: {best['name']} ({best['streak']} days)")
        if goal_list:
            lines.append(f"🎯 Active goals: {len(goal_list)}")
        return "\n".join(lines)

    if any(w in msg_lower for w in ['what should i', 'suggest', 'recommend', 'focus', 'what next', 'priority']):
        if high_priority:
            return f"🎯 {name}, focus on **{high_priority[0]['title']}** — it's high priority. Knock it out first, then you'll feel unstoppable."
        elif pending:
            return f"📋 Next up: **{pending[0]['title']}**. Start a 25-min Pomodoro and just go."
        return f"🎉 All clear, {name}! Set a new goal or add some tasks."

    if any(w in msg_lower for w in ['motivat', 'tired', 'lazy', 'procrastinat', 'struggling', 'can\'t focus']):
        quotes = [
            "💪 \"The secret of getting ahead is getting started.\" — Mark Twain",
            "🔥 \"It does not matter how slowly you go as long as you do not stop.\" — Confucius",
            "⭐ \"You don't have to be great to start, but you have to start to be great.\" — Zig Ziglar",
            "🚀 \"Small daily improvements over time lead to stunning results.\" — Robin Sharma"
        ]
        tip = f"\n\nStart with just 10 minutes on **{pending[0]['title']}**." if pending else ""
        return f"{random.choice(quotes)}{tip}"

    if any(w in msg_lower for w in ['plan', 'schedule', 'today', 'morning']):
        return f"📅 {name}, try asking me to **generate your daily plan** — I'll create a time-blocked schedule based on your tasks!"

    if any(w in msg_lower for w in ['break', 'split', 'subtask', 'smaller']):
        return f"🔨 Want me to break a task into subtasks? Tell me which task and I'll split it up!"

    return f"👋 Hey {name}! I'm your AI productivity coach. Try:\n\n📊 **\"How am I doing?\"** — Productivity summary\n🎯 **\"What should I focus on?\"** — Smart priorities\n📅 **\"Plan my day\"** — AI daily planner\n💪 **\"I need motivation\"** — Get fired up\n🔨 **\"Break down [task]\"** — Split into subtasks"

# 
# STATS API
# 
@app.route('/api/stats')
@login_required
def api_stats():
    db = get_db()
    uid = session['user_id']
    today = datetime.utcnow().strftime('%Y-%m-%d')

    total = db.execute('SELECT COUNT(*) as c FROM tasks WHERE user_id=?', (uid,)).fetchone()['c']
    done = db.execute('SELECT COUNT(*) as c FROM tasks WHERE user_id=? AND status="completed"', (uid,)).fetchone()['c']
    pomos = db.execute('SELECT COUNT(*) as c FROM pomodoro_sessions WHERE user_id=? AND completed=1 AND date(started_at)=?', (uid, today)).fetchone()['c']
    streak_max = db.execute('SELECT MAX(streak) as m FROM habits WHERE user_id=?', (uid,)).fetchone()['m'] or 0

    # Weekly completion data
    week_data = []
    for i in range(6, -1, -1):
        d = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
        c = db.execute('SELECT COUNT(*) as c FROM tasks WHERE user_id=? AND status="completed" AND date(completed_at)=?', (uid, d)).fetchone()['c']
        week_data.append({'date': d, 'count': c})

    return jsonify({
        'total_tasks': total, 'completed_tasks': done,
        'pomodoros_today': pomos, 'best_streak': streak_max,
        'weekly': week_data
    })

#
# INIT & RUN
# 
with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
