# Mangeli Messenger — real-time messaging starter

This version preserves account registration/login, direct conversations, text messages, image/video uploads, and the existing responsive interface. It adds SQLAlchemy database models (PostgreSQL-ready), real-time message delivery with Flask-SocketIO, and emoji/sticker pickers for posts, replies, quotes, and direct messages.

## 1. Requirements
- Python 3.10 or newer
- VS Code (recommended)
- PostgreSQL 14+ for PostgreSQL-backed testing

## 2. Extract and open
Extract the ZIP archive and open the `mangeli_messenger` folder in VS Code.

## 3. Create a virtual environment (Windows)
In VS Code select **Terminal → New Terminal**, then run:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, use Command Prompt and run `.venv\Scripts\activate.bat` instead.

## 4. Install dependencies
```bash
python -m pip install -r requirements.txt
```

## 5. Configure PostgreSQL
Install PostgreSQL if it is not already installed, and make sure its service is running. Open the PostgreSQL SQL Shell (psql) or another SQL client and create a dedicated database user and database. Example SQL (choose your own strong password):

```sql
CREATE USER messenger_user WITH PASSWORD 'choose-a-strong-password';
CREATE DATABASE mangeli_messenger OWNER messenger_user;
```

Set `DATABASE_URL` in the terminal before launching the app.

**Windows PowerShell:**
```powershell
$env:DATABASE_URL="postgresql://messenger_user:choose-a-strong-password@localhost:5432/mangeli_messenger"
$env:SECRET_KEY="replace-with-a-long-random-secret"
python app.py
```

Replace the example password with the one you chose when creating the database user. Do not publish real passwords or secrets in GitHub. The app normalizes `postgresql://` to the psycopg 3 driver URL.

### Want to test the interface before installing PostgreSQL?
Leave `DATABASE_URL` unset. The project will use a local SQLite file (`messenger.db`) so you can start the app while setting up PostgreSQL. Once you configure `DATABASE_URL`, the app uses PostgreSQL. **SQLite data is not automatically copied into PostgreSQL**; migrate any existing accounts/messages separately if you need to keep them.

## 6. Run locally
```bash
python app.py
```

Open **http://127.0.0.1:5000** in your browser. Keep the terminal open while testing.

## 7. Deploy to Render
The repository includes a `render.yaml` Blueprint configuration. For an existing Render web service, use:

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn --worker-class gthread --threads 100 --bind 0.0.0.0:$PORT app:app`

The final `app:app` means the `app` module (`app.py`) and its Flask application object (`app`). Configure `DATABASE_URL` with your Render PostgreSQL internal URL. Set `SECRET_KEY` to a long, random value; never commit production secrets. After changing the repository, push the changes to GitHub and deploy the latest commit in Render.

## 8. Test real-time chat
1. Register one account in a normal browser window.
2. Open a private/incognito window and register a second account.
3. Log in as the first user, select the second account, and open the same conversation in the private window as the second user.
4. Send a text message, emoji, sticker, or supported image/video. The message should appear in both open chat windows without manually refreshing.

For same-computer testing, both browser windows share the same running server. The status under the other person's name should show `Live · connected` once Socket.IO connects.

## What's included now
- Account registration and login with hashed passwords
- SQLAlchemy models for users and messages
- PostgreSQL support through `DATABASE_URL`; SQLite fallback for easier first-run testing
- Real-time direct messages via Flask-SocketIO
- Text, emoji, stickers, and supported image/video messages
- Emoji and sticker selection for posts, replies, quotes, and direct messages
- Local media uploads under `uploads/`
- Existing landing page and responsive styling

## Important: not yet production-ready
This is still a development starter, not a secure public messaging service. Before inviting real users, add CSRF protection, login rate limits, email verification and password reset, strict private-media authorization, media scanning and storage controls, HTTPS, secure cookie settings, database backups/migrations, and persistent cloud media storage. The current upload endpoint requires login but does not yet check that a user belongs to the specific conversation. Do not expose Flask debug mode to the public internet.

The Socket.IO browser client is loaded from a CDN, so the chat page needs internet access for the client library unless you vendor that file locally.
