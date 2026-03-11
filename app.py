#!/usr/bin/env python3
"""
TrainTrack - Training & Certification Management System
A web-based LMS for technical and HSE courses, tests, and certification tracking
"""

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_from_directory, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, date
import json
import functools
import uuid

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'traintrack-secret-change-in-prod-' + str(uuid.uuid4()))

# ─── Configuration ────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
DATABASE      = os.path.join(BASE_DIR, 'training.db')
ALLOWED_EXT   = {'png','jpg','jpeg','gif','pdf','pptx','ppt','docx','doc','mp4','webm','mov','xlsx','xls'}
NOTIFY_DAYS   = [30, 21, 14, 7, 3, 1]   # Days before expiry to send notifications

app.config['UPLOAD_FOLDER']      = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─── Database ─────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                email        TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role         TEXT DEFAULT 'employee',
                department   TEXT DEFAULT '',
                position     TEXT DEFAULT '',
                employee_id  TEXT DEFAULT '',
                phone        TEXT DEFAULT '',
                is_active    INTEGER DEFAULT 1,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS courses (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT NOT NULL,
                description   TEXT DEFAULT '',
                category      TEXT DEFAULT 'General',
                passing_score INTEGER DEFAULT 70,
                is_active     INTEGER DEFAULT 1,
                created_by    INTEGER REFERENCES users(id),
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS lessons (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id   INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                title       TEXT NOT NULL,
                content     TEXT DEFAULT '',
                video_url   TEXT DEFAULT '',
                order_index INTEGER DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS lesson_files (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                lesson_id     INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                filename      TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_type     TEXT DEFAULT '',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS questions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id     INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                question_text TEXT NOT NULL,
                question_type TEXT DEFAULT 'multiple_choice',
                order_index   INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS answer_options (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                answer_text TEXT NOT NULL,
                is_correct  INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS course_assignments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id   INTEGER NOT NULL REFERENCES courses(id),
                user_id     INTEGER NOT NULL REFERENCES users(id),
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                due_date    DATE,
                UNIQUE(course_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS course_completions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id      INTEGER NOT NULL REFERENCES courses(id),
                user_id        INTEGER NOT NULL REFERENCES users(id),
                score          REAL DEFAULT 0,
                passed         INTEGER DEFAULT 0,
                attempt_number INTEGER DEFAULT 1,
                answers_json   TEXT DEFAULT '{}',
                completed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS lesson_progress (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                lesson_id    INTEGER NOT NULL REFERENCES lessons(id),
                user_id      INTEGER NOT NULL REFERENCES users(id),
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(lesson_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS certification_types (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                name             TEXT NOT NULL UNIQUE,
                description      TEXT DEFAULT '',
                validity_months  INTEGER DEFAULT 12,
                category         TEXT DEFAULT 'Medical',
                is_active        INTEGER DEFAULT 1,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS employee_certifications (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id            INTEGER NOT NULL REFERENCES users(id),
                cert_type_id       INTEGER NOT NULL REFERENCES certification_types(id),
                issue_date         DATE NOT NULL,
                expiry_date        DATE NOT NULL,
                issuer             TEXT DEFAULT '',
                certificate_number TEXT DEFAULT '',
                notes              TEXT DEFAULT '',
                file_path          TEXT DEFAULT '',
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER REFERENCES users(id),
                title      TEXT NOT NULL,
                message    TEXT NOT NULL,
                type       TEXT DEFAULT 'info',
                link       TEXT DEFAULT '',
                is_read    INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS notification_sent_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                cert_id           INTEGER REFERENCES employee_certifications(id),
                notification_type TEXT NOT NULL,
                sent_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            );
        ''')
        # Default admin
        if not conn.execute('SELECT id FROM users WHERE role="admin"').fetchone():
            conn.execute(
                'INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)',
                ('Administrator','admin@company.com', generate_password_hash('Admin@123'),'admin')
            )
        # Default settings
        for k, v in [
            ('company_name','My Company'),
            ('smtp_server','smtp.gmail.com'), ('smtp_port','587'),
            ('smtp_email',''),('smtp_password',''),
            ('smtp_from_name','TrainTrack'),
            ('notifications_enabled','0'),
            ('admin_notification_email',''),
        ]:
            conn.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(k,v))

def get_setting(key, default=''):
    with get_db() as conn:
        row = conn.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row['value'] if row else default

def set_setting(key, value):
    with get_db() as conn:
        conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(key,value))

# ─── Auth helpers ──────────────────────────────────────────────────────────────
def login_required(f):
    @functools.wraps(f)
    def dec(*a,**kw):
        if 'user_id' not in session: return redirect(url_for('login'))
        return f(*a,**kw)
    return dec

def admin_required(f):
    @functools.wraps(f)
    def dec(*a,**kw):
        if 'user_id' not in session: return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Access denied.','danger')
            return redirect(url_for('employee_dashboard'))
        return f(*a,**kw)
    return dec

def allowed_file(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXT

def unread_count(uid):
    with get_db() as conn:
        if session.get('role') == 'admin':
            return conn.execute(
                'SELECT COUNT(*) FROM notifications WHERE (user_id IS NULL OR user_id=?) AND is_read=0',(uid,)
            ).fetchone()[0]
        return conn.execute(
            'SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0',(uid,)
        ).fetchone()[0]

def get_video_embed(url):
    """Convert YouTube/Vimeo URL to embed URL."""
    if not url: return None
    import re
    yt = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([^&\s]+)', url)
    if yt: return f"https://www.youtube.com/embed/{yt.group(1)}"
    vm = re.search(r'vimeo\.com/(\d+)', url)
    if vm: return f"https://player.vimeo.com/video/{vm.group(1)}"
    return url

app.jinja_env.globals['get_video_embed'] = get_video_embed
app.jinja_env.globals['enumerate'] = enumerate

# ─── Notifications / Email ─────────────────────────────────────────────────────
def send_email(to_email, subject, body_html):
    if get_setting('notifications_enabled') != '1': return False
    srv = get_setting('smtp_server','smtp.gmail.com')
    port = int(get_setting('smtp_port','587'))
    em = get_setting('smtp_email')
    pw = get_setting('smtp_password')
    fn = get_setting('smtp_from_name','TrainTrack')
    if not em or not pw: return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{fn} <{em}>"
        msg['To'] = to_email
        msg.attach(MIMEText(body_html,'html'))
        with smtplib.SMTP(srv, port) as s:
            s.starttls(); s.login(em, pw)
            s.sendmail(em, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}"); return False

def notify(user_id, title, message, ntype='info', link=''):
    with get_db() as conn:
        conn.execute(
            'INSERT INTO notifications(user_id,title,message,type,link) VALUES(?,?,?,?,?)',
            (user_id, title, message, ntype, link)
        )

def check_expirations():
    """Run daily – check certs, send emails + in-app notifications."""
    today = date.today()
    admin_email = get_setting('admin_notification_email')
    with get_db() as conn:
        certs = conn.execute('''
            SELECT ec.*, u.name as uname, u.email as uemail, u.id as uid,
                   ct.name as cname
            FROM employee_certifications ec
            JOIN users u ON ec.user_id=u.id
            JOIN certification_types ct ON ec.cert_type_id=ct.id
            WHERE ec.expiry_date >= ? AND u.is_active=1
        ''', (today.isoformat(),)).fetchall()

        admins = [r['id'] for r in conn.execute('SELECT id FROM users WHERE role="admin"').fetchall()]

        for cert in certs:
            expiry = date.fromisoformat(cert['expiry_date'])
            days_left = (expiry - today).days
            if days_left not in NOTIFY_DAYS: continue

            notif_key = f'd{days_left}'
            if conn.execute(
                'SELECT id FROM notification_sent_log WHERE cert_id=? AND notification_type=?',
                (cert['id'], notif_key)
            ).fetchone(): continue

            if days_left == 30:
                label = "1 month"
            elif days_left == 1:
                label = "1 day"
            else:
                label = f"{days_left} days"

            subject = f"⚠️ Certification expiring in {label}: {cert['cname']}"
            body = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
<h2 style="color:#e67e22">Certification Expiry Reminder</h2>
<table style="border-collapse:collapse;width:100%">
<tr><td style="padding:8px;border:1px solid #ddd;font-weight:bold">Employee</td>
    <td style="padding:8px;border:1px solid #ddd">{cert['uname']}</td></tr>
<tr><td style="padding:8px;border:1px solid #ddd;font-weight:bold">Certification</td>
    <td style="padding:8px;border:1px solid #ddd">{cert['cname']}</td></tr>
<tr><td style="padding:8px;border:1px solid #ddd;font-weight:bold">Expires</td>
    <td style="padding:8px;border:1px solid #ddd">{cert['expiry_date']} &mdash; <strong>{days_left} days remaining</strong></td></tr>
</table>
<p style="margin-top:16px">Please arrange renewal as soon as possible.</p>
</div>"""
            send_email(cert['uemail'], subject, body)
            if admin_email: send_email(admin_email, f"[Admin] {subject}", body)

            for aid in admins:
                notify(aid, subject,
                       f"{cert['uname']}'s {cert['cname']} expires in {label}",
                       'warning', '/admin/certifications')

            notify(cert['uid'], f"Your {cert['cname']} expires in {label}",
                   f"Please arrange renewal. Expiry: {cert['expiry_date']}",
                   'warning', '/my-certifications')

            conn.execute(
                'INSERT INTO notification_sent_log(cert_id,notification_type) VALUES(?,?)',
                (cert['id'], notif_key)
            )
        print(f"[Scheduler] Expiration check done: {today}")

# ─── Background scheduler ─────────────────────────────────────────────────────
def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = BackgroundScheduler()
    sched.add_job(check_expirations, 'interval', hours=24,
                  next_run_time=datetime.now() + timedelta(seconds=5))
    sched.start()
    return sched

# ══════════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/')
def index():
    if 'user_id' not in session: return redirect(url_for('login'))
    return redirect(url_for('admin_dashboard' if session.get('role')=='admin' else 'employee_dashboard'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        pw    = request.form.get('password','')
        with get_db() as conn:
            u = conn.execute('SELECT * FROM users WHERE email=? AND is_active=1',(email,)).fetchone()
        if u and check_password_hash(u['password_hash'], pw):
            session.update(user_id=u['id'], user_name=u['name'],
                           role=u['role'], email=u['email'])
            return redirect(url_for('admin_dashboard' if u['role']=='admin' else 'employee_dashboard'))
        flash('Invalid email or password.','danger')
    return render_template('login.html', company=get_setting('company_name','My Company'))

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

@app.route('/change-password', methods=['GET','POST'])
@login_required
def change_password():
    if request.method == 'POST':
        cur  = request.form.get('current_password','')
        new  = request.form.get('new_password','')
        conf = request.form.get('confirm_password','')
        if new != conf:           flash('New passwords do not match.','danger')
        elif len(new) < 6:        flash('Password must be at least 6 characters.','danger')
        else:
            with get_db() as conn:
                u = conn.execute('SELECT * FROM users WHERE id=?',(session['user_id'],)).fetchone()
                if check_password_hash(u['password_hash'], cur):
                    conn.execute('UPDATE users SET password_hash=? WHERE id=?',
                                 (generate_password_hash(new), session['user_id']))
                    flash('Password updated.','success')
                    return redirect(url_for('index'))
                flash('Current password is incorrect.','danger')
    uc = unread_count(session['user_id'])
    return render_template('change_password.html', unread_count=uc,
                           company=get_setting('company_name'))

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN – DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/admin')
@admin_required
def admin_dashboard():
    today      = date.today()
    thirty     = today + timedelta(days=30)
    uid        = session['user_id']
    with get_db() as conn:
        emp_count   = conn.execute('SELECT COUNT(*) FROM users WHERE role="employee" AND is_active=1').fetchone()[0]
        course_count= conn.execute('SELECT COUNT(*) FROM courses WHERE is_active=1').fetchone()[0]
        exp_soon    = conn.execute('''
            SELECT ec.*,u.name as uname,ct.name as cname FROM employee_certifications ec
            JOIN users u ON ec.user_id=u.id JOIN certification_types ct ON ec.cert_type_id=ct.id
            WHERE ec.expiry_date BETWEEN ? AND ? ORDER BY ec.expiry_date
        ''',(today.isoformat(),thirty.isoformat())).fetchall()
        expired     = conn.execute('''
            SELECT ec.*,u.name as uname,ct.name as cname FROM employee_certifications ec
            JOIN users u ON ec.user_id=u.id JOIN certification_types ct ON ec.cert_type_id=ct.id
            WHERE ec.expiry_date < ? ORDER BY ec.expiry_date DESC LIMIT 10
        ''',(today.isoformat(),)).fetchall()
        recent_comp = conn.execute('''
            SELECT cc.*,u.name as uname,c.title as ctitle FROM course_completions cc
            JOIN users u ON cc.user_id=u.id JOIN courses c ON cc.course_id=c.id
            ORDER BY cc.completed_at DESC LIMIT 10
        ''').fetchall()
        notifs      = conn.execute('''
            SELECT * FROM notifications WHERE (user_id IS NULL OR user_id=?)
            ORDER BY created_at DESC LIMIT 15
        ''',(uid,)).fetchall()
        uc = unread_count(uid)
        chart_data  = conn.execute('''
            SELECT c.title,COUNT(cc.id) as total,SUM(cc.passed) as passed
            FROM courses c LEFT JOIN course_completions cc ON c.id=cc.course_id
            WHERE c.is_active=1 GROUP BY c.id ORDER BY total DESC LIMIT 8
        ''').fetchall()
    return render_template('admin/dashboard.html',
        emp_count=emp_count, course_count=course_count,
        exp_soon=exp_soon, expired=expired, recent_comp=recent_comp,
        notifs=notifs, unread_count=uc, chart_data=chart_data,
        today=today.isoformat(), company=get_setting('company_name'))

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN – EMPLOYEES
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/admin/employees')
@admin_required
def admin_employees():
    with get_db() as conn:
        emps = conn.execute('''
            SELECT u.*,
              (SELECT COUNT(*) FROM course_completions cc WHERE cc.user_id=u.id AND cc.passed=1) as passed_courses,
              (SELECT COUNT(*) FROM employee_certifications ec WHERE ec.user_id=u.id) as cert_count
            FROM users u WHERE u.role="employee" ORDER BY u.name
        ''').fetchall()
    uc = unread_count(session['user_id'])
    return render_template('admin/employees.html', employees=emps,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/employees/new', methods=['GET','POST'])
@admin_required
def admin_employee_new():
    if request.method == 'POST':
        name  = request.form.get('name','').strip()
        email = request.form.get('email','').strip().lower()
        pw    = request.form.get('password','')
        dept  = request.form.get('department','')
        pos   = request.form.get('position','')
        eid   = request.form.get('employee_id','')
        phone = request.form.get('phone','')
        if not name or not email or not pw:
            flash('Name, email and password are required.','danger')
        else:
            try:
                with get_db() as conn:
                    conn.execute('''INSERT INTO users(name,email,password_hash,role,department,position,employee_id,phone)
                        VALUES(?,?,?,"employee",?,?,?,?)''',
                        (name,email,generate_password_hash(pw),dept,pos,eid,phone))
                flash(f'Employee {name} added.','success')
                return redirect(url_for('admin_employees'))
            except sqlite3.IntegrityError:
                flash('Email already exists.','danger')
    uc = unread_count(session['user_id'])
    return render_template('admin/employee_form.html', employee=None,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/employees/<int:eid>/edit', methods=['GET','POST'])
@admin_required
def admin_employee_edit(eid):
    with get_db() as conn:
        emp = conn.execute('SELECT * FROM users WHERE id=?',(eid,)).fetchone()
        if not emp: abort(404)
        if request.method == 'POST':
            name  = request.form.get('name','').strip()
            email = request.form.get('email','').strip().lower()
            dept  = request.form.get('department','')
            pos   = request.form.get('position','')
            eid_f = request.form.get('employee_id','')
            phone = request.form.get('phone','')
            act   = 1 if request.form.get('is_active') else 0
            newpw = request.form.get('new_password','')
            try:
                if newpw:
                    conn.execute('''UPDATE users SET name=?,email=?,department=?,position=?,
                        employee_id=?,phone=?,is_active=?,password_hash=? WHERE id=?''',
                        (name,email,dept,pos,eid_f,phone,act,generate_password_hash(newpw),eid))
                else:
                    conn.execute('''UPDATE users SET name=?,email=?,department=?,position=?,
                        employee_id=?,phone=?,is_active=? WHERE id=?''',
                        (name,email,dept,pos,eid_f,phone,act,eid))
                flash('Employee updated.','success')
                return redirect(url_for('admin_employees'))
            except sqlite3.IntegrityError:
                flash('Email already in use.','danger')
    uc = unread_count(session['user_id'])
    return render_template('admin/employee_form.html', employee=emp,
                           unread_count=uc, company=get_setting('company_name'))

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN – COURSES
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/admin/courses')
@admin_required
def admin_courses():
    with get_db() as conn:
        courses = conn.execute('''
            SELECT c.*,
              COUNT(DISTINCT l.id) as lcount,
              COUNT(DISTINCT q.id) as qcount,
              COUNT(DISTINCT ca.user_id) as assigned,
              SUM(CASE WHEN cc.passed=1 THEN 1 ELSE 0 END) as passed
            FROM courses c
            LEFT JOIN lessons l ON c.id=l.course_id
            LEFT JOIN questions q ON c.id=q.course_id
            LEFT JOIN course_assignments ca ON c.id=ca.course_id
            LEFT JOIN course_completions cc ON c.id=cc.course_id
            WHERE c.is_active=1 GROUP BY c.id ORDER BY c.created_at DESC
        ''').fetchall()
    uc = unread_count(session['user_id'])
    return render_template('admin/courses.html', courses=courses,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/courses/new', methods=['GET','POST'])
@admin_required
def admin_course_new():
    if request.method == 'POST':
        title = request.form.get('title','').strip()
        desc  = request.form.get('description','')
        cat   = request.form.get('category','General')
        ps    = int(request.form.get('passing_score',70))
        if not title: flash('Title required.','danger')
        else:
            with get_db() as conn:
                cur = conn.execute(
                    'INSERT INTO courses(title,description,category,passing_score,created_by) VALUES(?,?,?,?,?)',
                    (title,desc,cat,ps,session['user_id']))
                cid = cur.lastrowid
            flash('Course created. Add lessons below.','success')
            return redirect(url_for('admin_course_builder', cid=cid))
    uc = unread_count(session['user_id'])
    return render_template('admin/course_form.html', course=None,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/courses/<int:cid>/edit', methods=['GET','POST'])
@admin_required
def admin_course_edit(cid):
    with get_db() as conn:
        course = conn.execute('SELECT * FROM courses WHERE id=?',(cid,)).fetchone()
        if not course: abort(404)
        if request.method == 'POST':
            conn.execute('UPDATE courses SET title=?,description=?,category=?,passing_score=? WHERE id=?',
                (request.form.get('title'),request.form.get('description'),
                 request.form.get('category'),request.form.get('passing_score',70),cid))
            flash('Course updated.','success')
            return redirect(url_for('admin_course_builder', cid=cid))
    uc = unread_count(session['user_id'])
    return render_template('admin/course_form.html', course=course,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/courses/<int:cid>/delete', methods=['POST'])
@admin_required
def admin_course_delete(cid):
    with get_db() as conn:
        conn.execute('UPDATE courses SET is_active=0 WHERE id=?',(cid,))
    flash('Course archived.','success')
    return redirect(url_for('admin_courses'))

@app.route('/admin/courses/<int:cid>/builder')
@admin_required
def admin_course_builder(cid):
    with get_db() as conn:
        course  = conn.execute('SELECT * FROM courses WHERE id=?',(cid,)).fetchone()
        if not course: abort(404)
        lessons = conn.execute('''
            SELECT l.*,COUNT(lf.id) as fcount FROM lessons l
            LEFT JOIN lesson_files lf ON l.id=lf.lesson_id
            WHERE l.course_id=? GROUP BY l.id ORDER BY l.order_index,l.id
        ''',(cid,)).fetchall()
        questions = conn.execute('''
            SELECT q.*,COUNT(ao.id) as ocount FROM questions q
            LEFT JOIN answer_options ao ON q.id=ao.question_id
            WHERE q.course_id=? GROUP BY q.id ORDER BY q.order_index,q.id
        ''',(cid,)).fetchall()
        employees = conn.execute('''
            SELECT u.*,
              EXISTS(SELECT 1 FROM course_assignments ca WHERE ca.course_id=? AND ca.user_id=u.id) as assigned
            FROM users u WHERE u.role="employee" AND u.is_active=1 ORDER BY u.name
        ''',(cid,)).fetchall()
        uc = unread_count(session['user_id'])
    return render_template('admin/course_builder.html',
        course=course, lessons=lessons, questions=questions, employees=employees,
        unread_count=uc, company=get_setting('company_name'))

# ─── Lessons ──────────────────────────────────────────────────────────────────
@app.route('/admin/courses/<int:cid>/lessons/new', methods=['POST'])
@admin_required
def admin_lesson_new(cid):
    title = request.form.get('title','New Lesson').strip()
    with get_db() as conn:
        n = conn.execute('SELECT COUNT(*) FROM lessons WHERE course_id=?',(cid,)).fetchone()[0]
        conn.execute('INSERT INTO lessons(course_id,title,order_index) VALUES(?,?,?)',(cid,title,n))
    return redirect(url_for('admin_course_builder', cid=cid))

@app.route('/admin/lessons/<int:lid>/edit', methods=['GET','POST'])
@admin_required
def admin_lesson_edit(lid):
    with get_db() as conn:
        lesson = conn.execute('SELECT * FROM lessons WHERE id=?',(lid,)).fetchone()
        if not lesson: abort(404)
        files  = conn.execute('SELECT * FROM lesson_files WHERE lesson_id=?',(lid,)).fetchall()
        if request.method == 'POST':
            conn.execute('UPDATE lessons SET title=?,content=?,video_url=? WHERE id=?',
                (request.form.get('title'),request.form.get('content',''),
                 request.form.get('video_url','').strip(), lid))
            if 'file' in request.files:
                f = request.files['file']
                if f and f.filename and allowed_file(f.filename):
                    ext  = f.filename.rsplit('.',1)[1].lower()
                    fname= f"{uuid.uuid4().hex}.{ext}"
                    f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                    conn.execute(
                        'INSERT INTO lesson_files(lesson_id,filename,original_name,file_type) VALUES(?,?,?,?)',
                        (lid, fname, f.filename, ext))
            flash('Lesson saved.','success')
            return redirect(url_for('admin_course_builder', cid=lesson['course_id']))
    uc = unread_count(session['user_id'])
    return render_template('admin/lesson_editor.html', lesson=lesson, files=files,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/lessons/<int:lid>/delete', methods=['POST'])
@admin_required
def admin_lesson_delete(lid):
    with get_db() as conn:
        lesson = conn.execute('SELECT * FROM lessons WHERE id=?',(lid,)).fetchone()
        cid = lesson['course_id']
        for f in conn.execute('SELECT * FROM lesson_files WHERE lesson_id=?',(lid,)).fetchall():
            p = os.path.join(app.config['UPLOAD_FOLDER'], f['filename'])
            if os.path.exists(p): os.remove(p)
        conn.execute('DELETE FROM lessons WHERE id=?',(lid,))
    return redirect(url_for('admin_course_builder', cid=cid))

@app.route('/admin/lesson-files/<int:fid>/delete', methods=['POST'])
@admin_required
def admin_lesson_file_delete(fid):
    with get_db() as conn:
        row = conn.execute(
            'SELECT lf.*,l.course_id FROM lesson_files lf JOIN lessons l ON lf.lesson_id=l.id WHERE lf.id=?',(fid,)
        ).fetchone()
        if row:
            p = os.path.join(app.config['UPLOAD_FOLDER'], row['filename'])
            if os.path.exists(p): os.remove(p)
            conn.execute('DELETE FROM lesson_files WHERE id=?',(fid,))
            return redirect(url_for('admin_lesson_edit', lid=row['lesson_id']))
    abort(404)

# ─── Questions ────────────────────────────────────────────────────────────────
@app.route('/admin/courses/<int:cid>/questions/new', methods=['POST'])
@admin_required
def admin_question_new(cid):
    qtext = request.form.get('question_text','').strip()
    qtype = request.form.get('question_type','multiple_choice')
    if not qtext:
        flash('Question text required.','danger')
        return redirect(url_for('admin_course_builder', cid=cid))
    with get_db() as conn:
        n   = conn.execute('SELECT COUNT(*) FROM questions WHERE course_id=?',(cid,)).fetchone()[0]
        cur = conn.execute(
            'INSERT INTO questions(course_id,question_text,question_type,order_index) VALUES(?,?,?,?)',
            (cid,qtext,qtype,n))
        qid = cur.lastrowid
        if qtype == 'true_false':
            conn.execute('INSERT INTO answer_options(question_id,answer_text,is_correct) VALUES(?,?,?)',(qid,'True',1))
            conn.execute('INSERT INTO answer_options(question_id,answer_text,is_correct) VALUES(?,?,?)',(qid,'False',0))
        else:
            opts    = request.form.getlist('option_text')
            correct = request.form.get('correct_option','0')
            for i,opt in enumerate(opts):
                if opt.strip():
                    conn.execute(
                        'INSERT INTO answer_options(question_id,answer_text,is_correct) VALUES(?,?,?)',
                        (qid, opt.strip(), 1 if str(i)==correct else 0))
    flash('Question added.','success')
    return redirect(url_for('admin_course_builder', cid=cid))

@app.route('/admin/questions/<int:qid>/delete', methods=['POST'])
@admin_required
def admin_question_delete(qid):
    with get_db() as conn:
        q = conn.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone()
        cid = q['course_id']
        conn.execute('DELETE FROM questions WHERE id=?',(qid,))
    return redirect(url_for('admin_course_builder', cid=cid))

# ─── Assignments ──────────────────────────────────────────────────────────────
@app.route('/admin/courses/<int:cid>/assign', methods=['POST'])
@admin_required
def admin_course_assign(cid):
    uids     = request.form.getlist('user_ids')
    due_date = request.form.get('due_date') or None
    with get_db() as conn:
        conn.execute('DELETE FROM course_assignments WHERE course_id=?',(cid,))
        for uid in uids:
            conn.execute(
                'INSERT OR IGNORE INTO course_assignments(course_id,user_id,due_date) VALUES(?,?,?)',
                (cid,int(uid),due_date))
    flash('Assignments updated.','success')
    return redirect(url_for('admin_course_builder', cid=cid))

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN – CERTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/admin/cert-types', methods=['GET','POST'])
@admin_required
def admin_cert_types():
    if request.method == 'POST':
        name  = request.form.get('name','').strip()
        desc  = request.form.get('description','')
        vm    = int(request.form.get('validity_months',12))
        cat   = request.form.get('category','Medical')
        if name:
            try:
                with get_db() as conn:
                    conn.execute(
                        'INSERT INTO certification_types(name,description,validity_months,category) VALUES(?,?,?,?)',
                        (name,desc,vm,cat))
                flash(f'Certification type "{name}" added.','success')
            except sqlite3.IntegrityError:
                flash('Name already exists.','danger')
    with get_db() as conn:
        types = conn.execute('''
            SELECT ct.*,COUNT(ec.id) as usage FROM certification_types ct
            LEFT JOIN employee_certifications ec ON ct.id=ec.cert_type_id
            WHERE ct.is_active=1 GROUP BY ct.id ORDER BY ct.name
        ''').fetchall()
    uc = unread_count(session['user_id'])
    return render_template('admin/cert_types.html', cert_types=types,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/certifications')
@admin_required
def admin_certifications():
    today  = date.today().isoformat()
    thirty = (date.today()+timedelta(days=30)).isoformat()
    with get_db() as conn:
        certs = conn.execute('''
            SELECT ec.*,u.name as uname,u.department,ct.name as cname,ct.category
            FROM employee_certifications ec
            JOIN users u ON ec.user_id=u.id
            JOIN certification_types ct ON ec.cert_type_id=ct.id
            WHERE u.is_active=1 ORDER BY ec.expiry_date,u.name
        ''').fetchall()
        emps  = conn.execute('SELECT * FROM users WHERE role="employee" AND is_active=1 ORDER BY name').fetchall()
        types = conn.execute('SELECT * FROM certification_types WHERE is_active=1 ORDER BY name').fetchall()
    uc = unread_count(session['user_id'])
    return render_template('admin/certifications.html',
        certs=certs, employees=emps, cert_types=types,
        today=today, thirty=thirty, unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/certifications/new', methods=['POST'])
@admin_required
def admin_cert_new():
    uid    = request.form.get('user_id')
    ctid   = request.form.get('cert_type_id')
    issue  = request.form.get('issue_date')
    expiry = request.form.get('expiry_date')
    issuer = request.form.get('issuer','')
    certno = request.form.get('certificate_number','')
    notes  = request.form.get('notes','')
    fpath  = ''
    if 'cert_file' in request.files:
        f = request.files['cert_file']
        if f and f.filename and allowed_file(f.filename):
            ext   = f.filename.rsplit('.',1)[1].lower()
            fname = f"cert_{uuid.uuid4().hex}.{ext}"
            f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
            fpath = fname
    with get_db() as conn:
        conn.execute('''INSERT INTO employee_certifications
            (user_id,cert_type_id,issue_date,expiry_date,issuer,certificate_number,notes,file_path)
            VALUES(?,?,?,?,?,?,?,?)''',(uid,ctid,issue,expiry,issuer,certno,notes,fpath))
    flash('Certification added.','success')
    return redirect(url_for('admin_certifications'))

@app.route('/admin/certifications/<int:cid>/edit', methods=['GET','POST'])
@admin_required
def admin_cert_edit(cid):
    with get_db() as conn:
        cert  = conn.execute('''
            SELECT ec.*,u.name as uname,ct.name as cname FROM employee_certifications ec
            JOIN users u ON ec.user_id=u.id JOIN certification_types ct ON ec.cert_type_id=ct.id
            WHERE ec.id=?''',(cid,)).fetchone()
        if not cert: abort(404)
        if request.method == 'POST':
            fpath = cert['file_path']
            if 'cert_file' in request.files:
                f = request.files['cert_file']
                if f and f.filename and allowed_file(f.filename):
                    if fpath:
                        p = os.path.join(app.config['UPLOAD_FOLDER'], fpath)
                        if os.path.exists(p): os.remove(p)
                    ext = f.filename.rsplit('.',1)[1].lower()
                    fname = f"cert_{uuid.uuid4().hex}.{ext}"
                    f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                    fpath = fname
            conn.execute('''UPDATE employee_certifications SET
                issue_date=?,expiry_date=?,issuer=?,certificate_number=?,notes=?,file_path=?
                WHERE id=?''',
                (request.form.get('issue_date'),request.form.get('expiry_date'),
                 request.form.get('issuer'),request.form.get('certificate_number'),
                 request.form.get('notes'), fpath, cid))
            flash('Certification updated.','success')
            return redirect(url_for('admin_certifications'))
        emps  = conn.execute('SELECT * FROM users WHERE role="employee" AND is_active=1 ORDER BY name').fetchall()
        types = conn.execute('SELECT * FROM certification_types WHERE is_active=1 ORDER BY name').fetchall()
    uc = unread_count(session['user_id'])
    return render_template('admin/cert_edit.html', cert=cert, employees=emps, cert_types=types,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/certifications/<int:cid>/delete', methods=['POST'])
@admin_required
def admin_cert_delete(cid):
    with get_db() as conn:
        cert = conn.execute('SELECT * FROM employee_certifications WHERE id=?',(cid,)).fetchone()
        if cert and cert['file_path']:
            p = os.path.join(app.config['UPLOAD_FOLDER'], cert['file_path'])
            if os.path.exists(p): os.remove(p)
        conn.execute('DELETE FROM employee_certifications WHERE id=?',(cid,))
    flash('Certification deleted.','success')
    return redirect(url_for('admin_certifications'))

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN – REPORTS
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/admin/reports')
@admin_required
def admin_reports():
    with get_db() as conn:
        emps  = conn.execute('SELECT * FROM users WHERE role="employee" AND is_active=1 ORDER BY name').fetchall()
        types = conn.execute('SELECT * FROM certification_types WHERE is_active=1 ORDER BY name').fetchall()
        crs   = conn.execute('SELECT * FROM courses WHERE is_active=1 ORDER BY title').fetchall()
    uc = unread_count(session['user_id'])
    return render_template('admin/reports.html', employees=emps, cert_types=types, courses=crs,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/reports/person/<int:uid>')
@admin_required
def admin_report_person(uid):
    today  = date.today().isoformat()
    thirty = (date.today()+timedelta(days=30)).isoformat()
    with get_db() as conn:
        emp   = conn.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not emp: abort(404)
        certs = conn.execute('''
            SELECT ec.*,ct.name as cname,ct.category,ct.validity_months
            FROM employee_certifications ec
            JOIN certification_types ct ON ec.cert_type_id=ct.id
            WHERE ec.user_id=? ORDER BY ct.name
        ''',(uid,)).fetchall()
        assigned = conn.execute('''
            SELECT c.*,ca.due_date,
              (SELECT MAX(score) FROM course_completions cc WHERE cc.course_id=c.id AND cc.user_id=?) as best_score,
              (SELECT MAX(passed) FROM course_completions cc WHERE cc.course_id=c.id AND cc.user_id=?) as passed,
              (SELECT COUNT(*) FROM course_completions cc WHERE cc.course_id=c.id AND cc.user_id=?) as attempts
            FROM course_assignments ca JOIN courses c ON ca.course_id=c.id
            WHERE ca.user_id=? AND c.is_active=1 ORDER BY ca.due_date NULLS LAST,c.title
        ''',(uid,uid,uid,uid)).fetchall()
    uc = unread_count(session['user_id'])
    return render_template('admin/report_person.html', emp=emp,
        certs=certs, assigned=assigned, today=today, thirty=thirty,
        unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/reports/certification/<int:ctid>')
@admin_required
def admin_report_certification(ctid):
    today  = date.today().isoformat()
    thirty = (date.today()+timedelta(days=30)).isoformat()
    with get_db() as conn:
        ctype = conn.execute('SELECT * FROM certification_types WHERE id=?',(ctid,)).fetchone()
        if not ctype: abort(404)
        with_cert    = conn.execute('''
            SELECT u.name,u.department,u.employee_id,ec.issue_date,ec.expiry_date,ec.issuer,ec.certificate_number,ec.id as cid
            FROM users u JOIN employee_certifications ec ON u.id=ec.user_id
            WHERE ec.cert_type_id=? AND u.is_active=1 ORDER BY ec.expiry_date,u.name
        ''',(ctid,)).fetchall()
        without_cert = conn.execute('''
            SELECT u.name,u.department,u.employee_id FROM users u
            WHERE u.role="employee" AND u.is_active=1
            AND u.id NOT IN (SELECT user_id FROM employee_certifications WHERE cert_type_id=?)
            ORDER BY u.name
        ''',(ctid,)).fetchall()
    uc = unread_count(session['user_id'])
    return render_template('admin/report_certification.html', ctype=ctype,
        with_cert=with_cert, without_cert=without_cert,
        today=today, thirty=thirty, unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/reports/matrix')
@admin_required
def admin_report_matrix():
    with get_db() as conn:
        emps    = conn.execute('SELECT * FROM users WHERE role="employee" AND is_active=1 ORDER BY name').fetchall()
        courses = conn.execute('SELECT * FROM courses WHERE is_active=1 ORDER BY title').fetchall()
        rows    = conn.execute('''
            SELECT user_id,course_id,MAX(passed) as passed,MAX(score) as best_score
            FROM course_completions GROUP BY user_id,course_id
        ''').fetchall()
        matrix  = {(r['user_id'],r['course_id']):{'passed':r['passed'],'score':r['best_score']} for r in rows}
    uc = unread_count(session['user_id'])
    return render_template('admin/report_matrix.html', employees=emps, courses=courses, matrix=matrix,
                           unread_count=uc, company=get_setting('company_name'))

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN – NOTIFICATIONS & SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/admin/notifications')
@admin_required
def admin_notifications():
    uid = session['user_id']
    with get_db() as conn:
        notifs = conn.execute('''
            SELECT n.*,u.name as uname FROM notifications n LEFT JOIN users u ON n.user_id=u.id
            ORDER BY n.created_at DESC LIMIT 150
        ''').fetchall()
        conn.execute('UPDATE notifications SET is_read=1 WHERE user_id=? OR user_id IS NULL',(uid,))
    return render_template('admin/notifications.html', notifications=notifs,
                           unread_count=0, company=get_setting('company_name'))

@app.route('/admin/settings', methods=['GET','POST'])
@admin_required
def admin_settings():
    if request.method == 'POST':
        for k in ['company_name','smtp_server','smtp_port','smtp_email',
                  'smtp_password','smtp_from_name','notifications_enabled','admin_notification_email']:
            set_setting(k, request.form.get(k,''))
        flash('Settings saved.','success')
        return redirect(url_for('admin_settings'))
    s  = {k: get_setting(k) for k in ['company_name','smtp_server','smtp_port','smtp_email',
                                       'smtp_password','smtp_from_name','notifications_enabled',
                                       'admin_notification_email']}
    uc = unread_count(session['user_id'])
    return render_template('admin/settings.html', settings=s,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/admin/settings/test-email', methods=['POST'])
@admin_required
def admin_test_email():
    to = request.form.get('test_email','').strip()
    if not to: flash('Enter a test email address.','danger')
    else:
        ok = send_email(to,'Test from TrainTrack',
                        '<h2>✅ Email working!</h2><p>Your SMTP configuration is correct.</p>')
        flash(f'Email sent to {to}!' if ok else 'Failed – check SMTP settings.','success' if ok else 'danger')
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/run-notifications', methods=['POST'])
@admin_required
def admin_run_notifications():
    check_expirations()
    flash('Notification check completed.','success')
    return redirect(url_for('admin_settings'))

# ══════════════════════════════════════════════════════════════════════════════
# EMPLOYEE ROUTES
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/dashboard')
@login_required
def employee_dashboard():
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    uid    = session['user_id']
    today  = date.today().isoformat()
    thirty = (date.today()+timedelta(days=30)).isoformat()
    with get_db() as conn:
        assigned = conn.execute('''
            SELECT c.*,ca.due_date,
              (SELECT MAX(passed) FROM course_completions cc WHERE cc.course_id=c.id AND cc.user_id=?) as passed,
              (SELECT MAX(score)  FROM course_completions cc WHERE cc.course_id=c.id AND cc.user_id=?) as best_score
            FROM course_assignments ca JOIN courses c ON ca.course_id=c.id
            WHERE ca.user_id=? AND c.is_active=1 ORDER BY ca.due_date NULLS LAST,c.title
        ''',(uid,uid,uid)).fetchall()
        exp_soon = conn.execute('''
            SELECT ec.*,ct.name as cname FROM employee_certifications ec
            JOIN certification_types ct ON ec.cert_type_id=ct.id
            WHERE ec.user_id=? AND ec.expiry_date BETWEEN ? AND ? ORDER BY ec.expiry_date
        ''',(uid,today,thirty)).fetchall()
        expired  = conn.execute('''
            SELECT ec.*,ct.name as cname FROM employee_certifications ec
            JOIN certification_types ct ON ec.cert_type_id=ct.id
            WHERE ec.user_id=? AND ec.expiry_date < ? ORDER BY ec.expiry_date DESC
        ''',(uid,today)).fetchall()
        notifs   = conn.execute(
            'SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 10',(uid,)
        ).fetchall()
        uc = unread_count(uid)
    return render_template('employee/dashboard.html',
        assigned=assigned, exp_soon=exp_soon, expired=expired,
        notifs=notifs, unread_count=uc, company=get_setting('company_name'))

@app.route('/courses/<int:cid>')
@login_required
def employee_course_view(cid):
    uid = session['user_id']
    with get_db() as conn:
        course  = conn.execute('SELECT * FROM courses WHERE id=? AND is_active=1',(cid,)).fetchone()
        if not course: abort(404)
        lessons = conn.execute('''
            SELECT l.*,
              EXISTS(SELECT 1 FROM lesson_progress lp WHERE lp.lesson_id=l.id AND lp.user_id=?) as done,
              (SELECT COUNT(*) FROM lesson_files lf WHERE lf.lesson_id=l.id) as fcount
            FROM lessons l WHERE l.course_id=? ORDER BY l.order_index,l.id
        ''',(uid,cid)).fetchall()
        has_test  = conn.execute('SELECT COUNT(*) FROM questions WHERE course_id=?',(cid,)).fetchone()[0] > 0
        best_comp = conn.execute(
            'SELECT * FROM course_completions WHERE course_id=? AND user_id=? ORDER BY score DESC LIMIT 1',(cid,uid)
        ).fetchone()
        attempts  = conn.execute(
            'SELECT COUNT(*) FROM course_completions WHERE course_id=? AND user_id=?',(cid,uid)
        ).fetchone()[0]
        uc = unread_count(uid)
    return render_template('employee/course_view.html', course=course, lessons=lessons,
        has_test=has_test, best_comp=best_comp, attempts=attempts,
        unread_count=uc, company=get_setting('company_name'))

@app.route('/courses/<int:cid>/lesson/<int:lid>')
@login_required
def employee_lesson_view(cid, lid):
    uid = session['user_id']
    with get_db() as conn:
        course = conn.execute('SELECT * FROM courses WHERE id=? AND is_active=1',(cid,)).fetchone()
        lesson = conn.execute('SELECT * FROM lessons WHERE id=? AND course_id=?',(lid,cid)).fetchone()
        if not course or not lesson: abort(404)
        files  = conn.execute('SELECT * FROM lesson_files WHERE lesson_id=?',(lid,)).fetchall()
        ids    = [r['id'] for r in conn.execute(
            'SELECT id FROM lessons WHERE course_id=? ORDER BY order_index,id',(cid,)).fetchall()]
        idx    = ids.index(lid) if lid in ids else 0
        prev_id= ids[idx-1] if idx > 0 else None
        next_id= ids[idx+1] if idx < len(ids)-1 else None
        conn.execute('INSERT OR IGNORE INTO lesson_progress(lesson_id,user_id) VALUES(?,?)',(lid,uid))
        uc = unread_count(uid)
    return render_template('employee/lesson_view.html', course=course, lesson=lesson,
        files=files, prev_id=prev_id, next_id=next_id,
        unread_count=uc, company=get_setting('company_name'))

@app.route('/courses/<int:cid>/test', methods=['GET','POST'])
@login_required
def employee_course_test(cid):
    uid = session['user_id']
    with get_db() as conn:
        course = conn.execute('SELECT * FROM courses WHERE id=?',(cid,)).fetchone()
        qs     = conn.execute('''
            SELECT q.*,
              json_group_array(json_object('id',ao.id,'text',ao.answer_text,'correct',ao.is_correct)) as opts
            FROM questions q JOIN answer_options ao ON q.id=ao.question_id
            WHERE q.course_id=? GROUP BY q.id ORDER BY q.order_index,q.id
        ''',(cid,)).fetchall()
        if not qs:
            flash('No questions available yet.','warning')
            return redirect(url_for('employee_course_view', cid=cid))

        if request.method == 'POST':
            correct = 0
            total   = len(qs)
            answers = {}
            for q in qs:
                aid = request.form.get(f'q_{q["id"]}')
                answers[str(q['id'])] = aid
                if aid:
                    row = conn.execute('SELECT is_correct FROM answer_options WHERE id=?',(aid,)).fetchone()
                    if row and row['is_correct']: correct += 1
            score   = (correct/total*100) if total else 0
            passed  = 1 if score >= course['passing_score'] else 0
            attempt = conn.execute(
                'SELECT COUNT(*) FROM course_completions WHERE course_id=? AND user_id=?',(cid,uid)
            ).fetchone()[0] + 1
            conn.execute('''INSERT INTO course_completions(course_id,user_id,score,passed,attempt_number,answers_json)
                VALUES(?,?,?,?,?,?)''',(cid,uid,score,passed,attempt,json.dumps(answers)))
            for aid in [r['id'] for r in conn.execute('SELECT id FROM users WHERE role="admin"').fetchall()]:
                notify(aid, f"{session['user_name']} {'✅ passed' if passed else '❌ failed'} {course['title']}",
                       f"Score: {score:.0f}% | Attempt #{attempt}",
                       'success' if passed else 'warning',
                       url_for('admin_report_person', uid=uid))
            return redirect(url_for('employee_test_result', cid=cid,
                                    score=int(score), passed=passed, attempt=attempt))

    qdata = [{'id':q['id'],'text':q['question_text'],'type':q['question_type'],
              'opts':json.loads(q['opts'])} for q in qs]
    uc = unread_count(uid)
    return render_template('employee/test.html', course=course, questions=qdata,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/courses/<int:cid>/result')
@login_required
def employee_test_result(cid):
    score   = request.args.get('score',0,type=int)
    passed  = request.args.get('passed',0,type=int)
    attempt = request.args.get('attempt',1,type=int)
    with get_db() as conn:
        course = conn.execute('SELECT * FROM courses WHERE id=?',(cid,)).fetchone()
    uc = unread_count(session['user_id'])
    return render_template('employee/test_result.html', course=course, score=score,
        passed=passed, attempt=attempt, unread_count=uc, company=get_setting('company_name'))

@app.route('/my-certifications')
@login_required
def employee_my_certs():
    uid    = session['user_id']
    today  = date.today().isoformat()
    thirty = (date.today()+timedelta(days=30)).isoformat()
    with get_db() as conn:
        certs = conn.execute('''
            SELECT ec.*,ct.name as cname,ct.category,ct.description FROM employee_certifications ec
            JOIN certification_types ct ON ec.cert_type_id=ct.id
            WHERE ec.user_id=? ORDER BY ec.expiry_date,ct.name
        ''',(uid,)).fetchall()
        uc = unread_count(uid)
    return render_template('employee/my_certifications.html', certs=certs,
        today=today, thirty=thirty, unread_count=uc, company=get_setting('company_name'))

@app.route('/my-history')
@login_required
def employee_history():
    uid = session['user_id']
    with get_db() as conn:
        history = conn.execute('''
            SELECT cc.*,c.title as ctitle,c.category,c.passing_score FROM course_completions cc
            JOIN courses c ON cc.course_id=c.id WHERE cc.user_id=? ORDER BY cc.completed_at DESC
        ''',(uid,)).fetchall()
        uc = unread_count(uid)
    return render_template('employee/history.html', history=history,
                           unread_count=uc, company=get_setting('company_name'))

@app.route('/my-notifications')
@login_required
def employee_notifications():
    uid = session['user_id']
    with get_db() as conn:
        notifs = conn.execute(
            'SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC',(uid,)
        ).fetchall()
        conn.execute('UPDATE notifications SET is_read=1 WHERE user_id=?',(uid,))
    return render_template('employee/notifications.html', notifications=notifs,
                           unread_count=0, company=get_setting('company_name'))

# ══════════════════════════════════════════════════════════════════════════════
# API / STATIC
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/api/notifications/count')
@login_required
def api_notif_count():
    return jsonify({'count': unread_count(session['user_id'])})

@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
# Initialize DB when imported by gunicorn
init_db()
if not os.environ.get('WERKZEUG_RUN_MAIN') and os.environ.get('PORT'):
    sched = start_scheduler()
    import atexit
    atexit.register(lambda: sched.shutdown())

if __name__ == '__main__':
    init_db()
    import atexit
    if not os.environ.get('WERKZEUG_RUN_MAIN'):
        sched = start_scheduler()
        atexit.register(lambda: sched.shutdown())
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG','false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
