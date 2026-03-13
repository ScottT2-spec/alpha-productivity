# alpha Productivity

An AI-powered productivity web app that helps you manage tasks, build habits, track goals, and stay focused — with a built-in AI assistant that gives you real-time suggestions based on your data.

## features

- **📋 Smart Tasks** — Create, prioritize (high/medium/low), and track tasks
- **🧠 AI Assistant** — Chat with AI that knows your tasks, habits, and goals to give personalized advice
- **🔥 Habit Tracker** — Build daily habits with streak tracking
- **🎯 Goal Setting** — Set goals and track progress with visual progress bars
- **🍅 Pomodoro Timer** — Built-in 25-minute focus timer with browser notifications
- **📝 Quick Notes** — Fast note-taking with edit/pin/delete

## tech Stack

- **Backend:** Python + Flask
- **Database:** SQLite (zero config)
- **Frontend:** Vanilla JS + CSS (no frameworks, fast loading)
- **AI:** Context-aware response engine using user's productivity data
- **Auth:** Secure password hashing (PBKDF2-SHA256)

## run Locally

```bash
pip install -r requirements.txt
python app.py
```

Opens on `http://localhost:5001`

## run on Replit

1. Import this repo
2. Set run command to `python app.py`
3. Done — it auto-creates the database

## project Structure

```
alpha-productivity/
├── app.py              # Flask app — routes, API, AI engine
├── requirements.txt    # Dependencies
├── templates/
│   ├── base.html       # Base template (dark theme, nav)
│   ├── index.html      # Landing page
│   ├── login.html      # Login page
│   ├── signup.html     # Signup page
│   └── dashboard.html  # Main dashboard (tasks, notes, habits, goals, pomodoro, AI)
└── data/
    └── productivity.db # SQLite database (auto-created)
```

## what I Learned

- Building REST APIs with Flask (GET, POST, PUT, DELETE)
- SQLite database design with foreign keys and indexes
- Session-based authentication with secure password hashing
- Building an AI response engine that uses user context
- CSS Grid/Flexbox for responsive dashboard layouts
- Vanilla JS for SPA-like tab switching without page reloads

## screenshots

*Coming soon*

---

