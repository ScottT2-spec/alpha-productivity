"""
Alpha Productivity — AI-Powered Productivity Web App
Built by Scott Antwi (@ScottT2-spec)

A dynamic web-based assistant that helps users manage tasks, stay organized,
and boost productivity with AI-powered suggestions and summaries.
"""

import os
import json
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, g, send_from_directory)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(days=30)

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'productivity.db')
ADMIN_EMAIL = 'ADMIN EMAIL'

# ============================================
# DATABASE
# ============================================
def get_db():
    if 'db' not in g:
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
        db.close()

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

# ============================================
# AUTH HELPERS
# ============================================
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

# ============================================
# AUTH ROUTES
# ============================================
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

# ============================================
# MAIN PAGES
# ============================================
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

# ============================================
# TASKS API
# ============================================
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

# ============================================
# NOTES API
# ============================================
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

# ============================================
# HABITS API
# ============================================
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

# ============================================
# GOALS API
# ============================================
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

# ============================================
# POMODORO API
# ============================================
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

# ============================================
# AI CHAT API
# ============================================
@app.route('/api/ai/chat', methods=['POST'])
@login_required
def api_ai_chat():
    data = request.json or {}
    message = data.get('message', '').strip()
    if not message:
        return jsonify({'error': 'Message required'}), 400

    db = get_db()
    uid = session['user_id']

    # Save user message
    db.execute('INSERT INTO ai_chats (user_id, role, content) VALUES (?,?,?)', (uid, 'user', message))

    # Get context: user's tasks, habits, goals
    tasks = db.execute('SELECT title, status, priority, due_date FROM tasks WHERE user_id=? ORDER BY created_at DESC LIMIT 20', (uid,)).fetchall()
    habits = db.execute('SELECT name, streak FROM habits WHERE user_id=?', (uid,)).fetchall()
    goals = db.execute('SELECT title, progress, status FROM goals WHERE user_id=? AND status="active"', (uid,)).fetchall()

    # Build AI response based on context
    response = generate_ai_response(message, tasks, habits, goals)

    db.execute('INSERT INTO ai_chats (user_id, role, content) VALUES (?,?,?)', (uid, 'assistant', response))
    db.commit()

    return jsonify({'reply': response})

def generate_ai_response(message, tasks, habits, goals):
    """Generate contextual AI response based on user data and message."""
    msg_lower = message.lower()

    task_list = [dict(t) for t in tasks]
    habit_list = [dict(h) for h in habits]
    goal_list = [dict(g) for g in goals]

    pending = [t for t in task_list if t['status'] != 'completed']
    completed = [t for t in task_list if t['status'] == 'completed']
    high_priority = [t for t in pending if t['priority'] == 'high']

    # Task summary
    if any(w in msg_lower for w in ['summary', 'overview', 'how am i doing', 'status', 'report']):
        lines = [f"📊 **Your Productivity Summary**\n"]
        lines.append(f"📋 Tasks: {len(completed)} done / {len(pending)} pending")
        if high_priority:
            lines.append(f"🔴 High priority: {', '.join(t['title'] for t in high_priority)}")
        if habit_list:
            best_habit = max(habit_list, key=lambda h: h['streak'])
            lines.append(f"🔥 Best streak: {best_habit['name']} ({best_habit['streak']} days)")
        if goal_list:
            lines.append(f"🎯 Active goals: {len(goal_list)}")
            for g in goal_list:
                lines.append(f"  • {g['title']} — {g['progress']}%")
        if not pending:
            lines.append("\n✨ All tasks done! Great work!")
        return "\n".join(lines)

    # Suggest what to work on
    if any(w in msg_lower for w in ['what should i', 'suggest', 'recommend', 'focus', 'what next', 'priority']):
        if high_priority:
            return f"🎯 Focus on your high-priority task: **{high_priority[0]['title']}**\n\nYou have {len(high_priority)} urgent task(s). Tackle those first, then move to medium priority."
        elif pending:
            return f"📋 Next up: **{pending[0]['title']}**\n\nYou have {len(pending)} pending tasks. Try the Pomodoro timer to stay focused — 25 min on, 5 min break."
        else:
            return "🎉 You've completed all your tasks! Time to set new goals or review your habits."

    # Motivation
    if any(w in msg_lower for w in ['motivat', 'tired', 'lazy', 'procrastinat', 'can\'t focus', 'struggling']):
        quotes = [
            "💪 \"The secret of getting ahead is getting started.\" — Mark Twain",
            "🔥 \"It does not matter how slowly you go as long as you do not stop.\" — Confucius",
            "⭐ \"You don't have to be great to start, but you have to start to be great.\" — Zig Ziglar",
            "🎯 \"Focus on being productive instead of busy.\" — Tim Ferriss",
            "🚀 \"Small daily improvements over time lead to stunning results.\" — Robin Sharma"
        ]
        import random
        quote = random.choice(quotes)
        tip = ""
        if pending:
            tip = f"\n\nStart small — try working on **{pending[0]['title']}** for just 10 minutes. Once you start, momentum will carry you."
        return f"{quote}{tip}"

    # Pomodoro help
    if any(w in msg_lower for w in ['pomodoro', 'timer', 'focus time']):
        return "🍅 **Pomodoro Technique:**\n1. Pick a task\n2. Set timer for 25 minutes\n3. Work with zero distractions\n4. Take a 5-min break\n5. After 4 rounds, take a 15-min break\n\nUse the Pomodoro timer on your dashboard!"

    # Habits
    if any(w in msg_lower for w in ['habit', 'streak', 'routine', 'daily']):
        if habit_list:
            lines = ["📅 **Your Habits:**"]
            for h in habit_list:
                emoji = "🔥" if h['streak'] >= 7 else "✅" if h['streak'] >= 3 else "🌱"
                lines.append(f"  {emoji} {h['name']} — {h['streak']} day streak")
            lines.append("\nKeep going! Consistency beats intensity.")
            return "\n".join(lines)
        return "🌱 You haven't set up any habits yet! Go to Habits and add your first one. Start with something small and build up."

    # Goals
    if any(w in msg_lower for w in ['goal', 'target', 'aim', 'objective']):
        if goal_list:
            lines = ["🎯 **Your Active Goals:**"]
            for g in goal_list:
                bar_fill = int(g['progress'] / 10)
                bar = "█" * bar_fill + "░" * (10 - bar_fill)
                lines.append(f"  {g['title']} [{bar}] {g['progress']}%")
            return "\n".join(lines)
        return "🎯 Set your first goal! Having clear goals makes you 42% more likely to achieve them."

    # Default - helpful response
    return f"👋 I'm your AI productivity assistant! I can help with:\n\n📊 **\"Give me a summary\"** — See your productivity overview\n🎯 **\"What should I focus on?\"** — Get task recommendations\n💪 **\"I need motivation\"** — Get inspired\n🍅 **\"Tell me about Pomodoro\"** — Learn focus techniques\n📅 **\"How are my habits?\"** — Check your streaks\n🎯 **\"Show my goals\"** — Track your progress"

# ============================================
# STATS API
# ============================================
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

# ============================================
# INIT & RUN
# ============================================
with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
