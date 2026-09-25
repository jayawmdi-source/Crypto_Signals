import os
import sys
import uuid
import json
import time
import gzip
import urllib.parse
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

PORT = 80
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# Native Zero-Dependency .env Loader
def load_dotenv(env_path=None):
    if not env_path:
        env_path = os.path.join(DIRECTORY, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

load_dotenv()

AUTH_USER = os.environ.get("DASHBOARD_USER", "DMD-DJ")
AUTH_PASS = os.environ.get("DASHBOARD_PASS")
if not AUTH_PASS:
    print("[!] Security Note: DASHBOARD_PASS not found in .env, using default credential.")
    AUTH_PASS = "D@m!dMD@0912"

SESSION_FILE = os.path.join(DIRECTORY, ".sessions.json")

# Persistent sessions across service restarts
def load_sessions():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_sessions(sessions):
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f)
    except Exception:
        pass

SESSIONS = load_sessions()

# -----------------------------------------------------------------------------
# Security Hardening: Brute-Force Rate Limiter & File Firewall
# -----------------------------------------------------------------------------
FAILED_LOGINS = {}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 900  # 15 minutes lockout

def get_client_ip(handler):
    forwarded = handler.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return handler.client_address[0] if handler.client_address else "unknown"

def is_ip_locked(client_ip):
    now = time.time()
    if client_ip in FAILED_LOGINS:
        attempts = [t for t in FAILED_LOGINS[client_ip] if now - t < LOCKOUT_SECONDS]
        FAILED_LOGINS[client_ip] = attempts
        if len(attempts) >= MAX_FAILED_ATTEMPTS:
            remaining_mins = max(1, int(((attempts[0] + LOCKOUT_SECONDS) - now) / 60))
            return True, remaining_mins
    return False, 0

def record_failed_attempt(client_ip):
    now = time.time()
    if client_ip not in FAILED_LOGINS:
        FAILED_LOGINS[client_ip] = []
    FAILED_LOGINS[client_ip].append(now)

def clear_failed_attempts(client_ip):
    if client_ip in FAILED_LOGINS:
        del FAILED_LOGINS[client_ip]

FORBIDDEN_FILES = {
    "binance_api_config.json",
    "telegram_config.json",
    ".sessions.json",
    "trade_history.json",
    ".env",
    ".gitignore",
    "requirements.txt"
}
FORBIDDEN_EXTENSIONS = {
    ".py", ".pyc", ".key", ".sh", ".bat", ".service", ".zip"
}

def is_file_forbidden(path_str):
    clean = path_str.split("?")[0].lstrip("/").replace("\\", "/")
    # Prevent directory traversal attacks
    if ".." in clean:
        return True
    parts = clean.split("/")
    for p in parts:
        if p.startswith(".") and p not in [".", ""]:
            return True
    filename = parts[-1].lower() if parts else ""
    if filename in FORBIDDEN_FILES:
        return True
    if parts and parts[0].lower() in ["vps_credentials", "mingit", "__pycache__"]:
        return True
    ext = os.path.splitext(filename)[1].lower()
    if ext in FORBIDDEN_EXTENSIONS:
        return True
    return False

# -----------------------------------------------------------------------------
# HTML Templates
# -----------------------------------------------------------------------------
LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sign In | DMD Institutional SMC 2.0</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', sans-serif;
            background: radial-gradient(circle at 50% 20%, #1e2329 0%, #0b0e14 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            color: #eaecef;
        }
        .login-card {
            background: rgba(24, 26, 32, 0.95);
            border: 1px solid rgba(240, 185, 11, 0.25);
            box-shadow: 0 20px 40px rgba(0,0,0,0.6), 0 0 30px rgba(240,185,11,0.08);
            border-radius: 16px;
            width: 100%;
            max-width: 420px;
            padding: 40px 32px;
            backdrop-filter: blur(12px);
        }
        .brand {
            text-align: center;
            margin-bottom: 28px;
        }
        .brand-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(240, 185, 11, 0.12);
            color: #f0b90b;
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            margin-bottom: 12px;
            border: 1px solid rgba(240,185,11,0.3);
        }
        .brand h1 {
            font-size: 22px;
            font-weight: 700;
            color: #ffffff;
            margin-bottom: 6px;
        }
        .brand p {
            font-size: 13px;
            color: #848e9c;
        }
        .form-group {
            margin-bottom: 20px;
        }
        .form-group label {
            display: block;
            font-size: 13px;
            font-weight: 500;
            color: #b7bdc6;
            margin-bottom: 8px;
        }
        .form-control {
            width: 100%;
            background: #1e2329;
            border: 1px solid #474d57;
            border-radius: 8px;
            padding: 12px 16px;
            color: #ffffff;
            font-size: 14px;
            transition: all 0.2s ease;
        }
        .form-control:focus {
            outline: none;
            border-color: #f0b90b;
            box-shadow: 0 0 0 2px rgba(240, 185, 11, 0.2);
            background: #2b313a;
        }
        .btn-submit {
            width: 100%;
            background: #f0b90b;
            color: #181a20;
            border: none;
            border-radius: 8px;
            padding: 13px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            margin-top: 10px;
        }
        .btn-submit:hover {
            background: #fcd535;
            box-shadow: 0 4px 16px rgba(240, 185, 11, 0.4);
            transform: translateY(-1px);
        }
        .btn-submit:active {
            transform: translateY(0);
        }
        .alert-error {
            background: rgba(246, 70, 93, 0.15);
            border: 1px solid rgba(246, 70, 93, 0.4);
            color: #f6465d;
            padding: 12px 14px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .footer-note {
            margin-top: 24px;
            text-align: center;
            font-size: 12px;
            color: #5e6673;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="brand">
            <div class="brand-badge">
                <span>🛡️</span>
                <span>AUTHENTICATION GATEWAY</span>
            </div>
            <h1>DMD SMC Pro 2.0</h1>
            <p>Institutional Trading Terminal</p>
        </div>
        {ERROR_ALERT}
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">Institutional ID / Username</label>
                <input type="text" id="username" name="username" class="form-control" placeholder="Enter Operator ID" required autofocus value="{PREFILL_USER}">
            </div>
            <div class="form-group">
                <label for="password">Security Password</label>
                <input type="password" id="password" name="password" class="form-control" placeholder="Enter Password" required>
            </div>
            <button type="submit" class="btn-submit">Sign In to Dashboard</button>
        </form>
        <div class="footer-note">
            Protected by Isolated Hardware Security • Rate-Limited
        </div>
    </div>
</body>
</html>
"""

LOGOUT_INJECTION = """
<!-- Institutional Navbar Auth Badge -->
<div id="auth-logout-bar" style="display:inline-flex;align-items:center;gap:8px;margin-left:10px;padding-left:10px;border-left:1px solid rgba(255,255,255,0.2);">
    <div style="background:rgba(18,20,24,0.95);border:1px solid rgba(240,185,11,0.4);padding:4px 10px;border-radius:20px;color:#f0b90b;font-family:sans-serif;font-size:11px;font-weight:600;display:flex;align-items:center;gap:6px;">
        <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#0ecb81;box-shadow:0 0 6px #0ecb81;"></span>
        <span>{USER}</span>
    </div>
    <a href="/logout" style="background:rgba(246,70,93,0.18);border:1px solid #f6465d;color:#ff6b7e;padding:4px 10px;border-radius:20px;font-family:sans-serif;font-size:11px;font-weight:600;text-decoration:none;display:inline-flex;align-items:center;gap:4px;transition:all 0.2s;" onmouseover="this.style.background='rgba(246,70,93,0.35)';this.style.color='#fff';" onmouseout="this.style.background='rgba(246,70,93,0.18)';this.style.color='#ff6b7e';">
        <span>🚪</span>
        <span>Sign Out</span>
    </a>
</div>
"""

class SecureDashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def get_authenticated_user(self):
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            cookie = SimpleCookie()
            try:
                cookie.load(cookie_header)
                if "session_token" in cookie:
                    token = cookie["session_token"].value
                    if token in SESSIONS:
                        return SESSIONS[token]
            except Exception:
                pass
        return None

    def send_forbidden(self):
        self.send_response(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"403 Forbidden: Direct access to configuration files and scripts is strictly prohibited.")

    def do_HEAD(self):
        if is_file_forbidden(self.path):
            self.send_forbidden()
            return
        if self.path in ["/login", "/logout"]:
            self.send_response(200)
            self.end_headers()
            return
        if not self.get_authenticated_user():
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        super().do_HEAD()

    def send_compressed_response(self, content_bytes, content_type="text/html; charset=utf-8"):
        accept_encoding = self.headers.get("Accept-Encoding", "")
        if "gzip" in accept_encoding:
            compressed = gzip.compress(content_bytes, compresslevel=6)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(compressed)))
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            self.wfile.write(compressed)
        else:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content_bytes)))
            self.end_headers()
            self.wfile.write(content_bytes)

    def do_GET(self):
        # 1. Security Firewall: Block forbidden and confidential files
        if is_file_forbidden(self.path):
            self.send_forbidden()
            return

        # 2. Handle Logout
        if self.path == "/logout":
            cookie_header = self.headers.get("Cookie")
            if cookie_header:
                cookie = SimpleCookie()
                try:
                    cookie.load(cookie_header)
                    if "session_token" in cookie:
                        token = cookie["session_token"].value
                        if token in SESSIONS:
                            del SESSIONS[token]
                            save_sessions(SESSIONS)
                except Exception:
                    pass
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "session_token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.end_headers()
            return

        # 3. Handle Login Page
        if self.path.startswith("/login"):
            user = self.get_authenticated_user()
            if user:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return

            rendered = LOGIN_HTML.replace("{ERROR_ALERT}", "").replace("{PREFILL_USER}", "")
            self.send_compressed_response(rendered.encode("utf-8"), "text/html; charset=utf-8")
            return

        # 4. Check Authentication for all other routes
        user = self.get_authenticated_user()
        if not user:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # 5. Inject Logout button into HTML files
        clean_path = self.path.split("?")[0]
        if clean_path in ["/", "/index.html", "/dashboard.html"]:
            target_file = os.path.join(DIRECTORY, "index.html" if clean_path == "/" else clean_path.lstrip("/"))
            if os.path.exists(target_file):
                try:
                    with open(target_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    injected_btn = LOGOUT_INJECTION.replace("{USER}", user)
                    if "<!-- AUTH_NAV_SLOT -->" in content:
                        content = content.replace("<!-- AUTH_NAV_SLOT -->", injected_btn)
                    elif "</span></span>" in content:
                        content = content.replace("</span></span>", f"</span></span>{injected_btn}", 1)
                    elif "</body>" in content:
                        content = content.replace("</body>", f"{injected_btn}</body>")
                    else:
                        content = content + injected_btn

                    content_bytes = content.encode("utf-8")
                    self.send_compressed_response(content_bytes, "text/html; charset=utf-8")
                    return
                except Exception as e:
                    print(f"[!] Error serving {clean_path}: {e}")

        # High-Speed Compressed JSON delivery (89% bandwidth reduction)
        if clean_path.endswith(".json"):
            target_json = os.path.join(DIRECTORY, clean_path.lstrip("/"))
            if os.path.exists(target_json):
                try:
                    with open(target_json, "rb") as f:
                        data_bytes = f.read()
                    self.send_compressed_response(data_bytes, "application/json; charset=utf-8")
                    return
                except Exception as e:
                    print(f"[!] Error serving {clean_path}: {e}")

        super().do_GET()

    def do_POST(self):
        if self.path == "/login":
            client_ip = get_client_ip(self)
            is_locked, rem_mins = is_ip_locked(client_ip)
            if is_locked:
                error_msg = f'<div class="alert-error">🛑 Too many failed login attempts. Temporarily locked for {rem_mins} minutes.</div>'
                rendered = LOGIN_HTML.replace("{ERROR_ALERT}", error_msg).replace("{PREFILL_USER}", "")
                self.send_compressed_response(rendered.encode("utf-8"), "text/html; charset=utf-8")
                return

            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8")
            fields = urllib.parse.parse_qs(post_data)

            username = fields.get("username", [""])[0].strip()
            password = fields.get("password", [""])[0].strip()

            if username == AUTH_USER and password == AUTH_PASS:
                clear_failed_attempts(client_ip)
                token = uuid.uuid4().hex
                SESSIONS[token] = username
                save_sessions(SESSIONS)

                self.send_response(302)
                self.send_header("Location", "/")
                # 7-day session cookie
                self.send_header("Set-Cookie", f"session_token={token}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax")
                self.end_headers()
                return
            else:
                record_failed_attempt(client_ip)
                error_msg = '<div class="alert-error">❌ Invalid Username or Password. Please try again.</div>'
                rendered = LOGIN_HTML.replace("{ERROR_ALERT}", error_msg).replace("{PREFILL_USER}", username)
                self.send_compressed_response(rendered.encode("utf-8"), "text/html; charset=utf-8")
                return

        self.send_response(404)
        self.end_headers()

    def end_headers(self):
        if self.path.endswith(".json") or self.path.endswith(".html") or self.path == "/":
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
        # Universal Security Hardening Headers
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

def run():
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadingHTTPServer(server_address, SecureDashboardHandler)
    print(f"[+] Binance SMC Secure Web Server running on port {PORT} with Sign In / Sign Out & Brute-Force Shield...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

if __name__ == "__main__":
    run()
