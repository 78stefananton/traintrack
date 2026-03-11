# TrainTrack – Training & Certification Management System

A self-hosted web app for managing employee training courses, tests, and yearly medical/HSE certifications — built as a replacement for EdApp/SC Training.

---

## Features

- **Custom Course Builder** — Create technical and HSE courses with lessons containing rich text (Markdown), images, videos (YouTube/Vimeo), PDFs, PPTX, and other file attachments
- **Quiz/Test System** — Multiple choice and True/False questions; configurable passing score; unlimited retakes
- **Course Assignment** — Assign specific courses to employees with optional due dates
- **Certification Tracking** — Track all personnel medical and safety certifications with expiry dates
- **Automatic Notifications** — Email + in-app alerts at 30, 21, 14, 7, 3, and 1 day(s) before expiry
- **Reports** — Per-employee full report, per-certification status report, full training matrix
- **Mobile Friendly** — Works on any phone browser; installable as PWA (Add to Home Screen)
- **Employee Portal** — Each employee logs in to see their assigned courses, take tests, and check their certifications

---

## Default Login

```
URL: http://your-server/
Email: admin@company.com
Password: Admin@123
```

**⚠️ Change this password immediately after first login.**

---

## Quick Start (Local or Server)

### 1. Install Python 3.10+

Windows: https://python.org/downloads
Linux/Mac: usually pre-installed

### 2. Install dependencies

```bash
pip install flask werkzeug apscheduler
```

### 3. Run the app

```bash
python app.py
```

Open http://localhost:5000 in your browser.

---

## Deploy to Render.com (Free – Recommended for field access)

This gives your field guys access from their phones anywhere.

### Step 1: Create a GitHub account and upload the files

1. Go to https://github.com and create a free account
2. Click **New Repository** → name it `traintrack` → Create
3. Upload all files from this folder to the repository

### Step 2: Deploy on Render.com

1. Go to https://render.com and sign up (free)
2. Click **New → Web Service**
3. Connect your GitHub account and select the `traintrack` repository
4. Fill in:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python app.py`
   - **Environment:** Python 3
5. Click **Create Web Service**
6. After ~2 minutes, your app is live at `https://traintrack-xxxx.onrender.com`

### Step 3 (Important): Add a persistent disk on Render

By default, files reset on each deploy. To keep your database and uploads:

1. In your Render service → **Disks** → **Add Disk**
2. Mount Path: `/opt/render/project/src/data`
3. Size: 1 GB (free tier allows this)
4. Set the environment variable `DATABASE` = `/opt/render/project/src/data/training.db`
5. Set `UPLOAD_FOLDER` = `/opt/render/project/src/data/uploads`

### Step 4: Set environment variables on Render

In your Render service → **Environment**:

| Key | Value |
|-----|-------|
| `SECRET_KEY` | (generate a random 32-character string) |
| `PORT` | `5000` |

---

## Email Notifications Setup (Gmail)

1. Go to your Google Account → Security → **2-Step Verification** (enable it)
2. Then go to → **App Passwords** → Create one for "TrainTrack"
3. In TrainTrack → **Settings** → Email:
   - SMTP Server: `smtp.gmail.com`
   - Port: `587`
   - Email: your Gmail address
   - Password: the App Password (NOT your Google password)
   - Enable notifications: ✓

---

## How Employees Access on Mobile

1. Share the URL (e.g. `https://traintrack-xxxx.onrender.com`)
2. Employee opens it in their phone browser
3. On iPhone: tap Share → **Add to Home Screen**
4. On Android: tap menu → **Add to Home Screen** or **Install App**
5. It appears on their home screen like a real app

---

## Folder Structure

```
training-app/
  app.py              # Main application
  requirements.txt    # Python dependencies
  Procfile            # For deployment
  training.db         # Database (auto-created)
  static/
    uploads/          # Uploaded files
    manifest.json     # PWA manifest
  templates/
    base.html
    login.html
    admin/            # Admin pages
    employee/         # Employee pages
```

---

## Backup

To back up your data, copy these two items:
- `training.db` — all your data
- `static/uploads/` — all uploaded files

---

*Built with Flask + SQLite. No subscription fees. You own your data.*
