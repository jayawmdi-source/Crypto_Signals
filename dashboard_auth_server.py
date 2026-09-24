import os
import sys
import uuid
import json
import urllib.parse
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

PORT = 80
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
AUTH_USER = os.environ.get("DASHBOARD_USER", "DMD-DJ")
AUTH_PASS = os.environ.get("DASHBOARD_PASS", "D@m!dMD@0912")
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

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sign In | Binance Institutional SMC 2.0</title>
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
            padding: 12px 14px;
            background: #121418;
            border: 1px solid #2b313a;
            border-radius: 8px;
            color: #ffffff;
            font-size: 14px;
            transition: all 0.2s;
            outline: none;
        }
        .form-control:focus {
            border-color: #f0b90b;
            box-shadow: 0 0 0 2px rgba(240,185,11,0.2);
        }
        .btn-submit {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #f0b90b 0%, #fcd535 100%);
            color: #121418;
            font-size: 15px;
            font-weight: 700;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            transition: transform 0.1s, box-shadow 0.2s;
            margin-top: 10px;
        }
        .btn-submit:hover {
            box-shadow: 0 6px 16px rgba(240,185,11,0.35);
            transform: translateY(-1px);
        }
        .btn-submit:active {
            transform: translateY(0);
        }
        .alert-error {
            background: rgba(246, 70, 93, 0.15);
            border: 1px solid #f6465d;
            color: #f6465d;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 20px;
            text-align: center;
        }
        .footer-note {
            text-align: center;
            font-size: 11px;
            color: #5e6673;
            margin-top: 24px;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="brand">
            <div class="brand-badge">⚡ PRIVATE TERMINAL</div>
            <h1>Binance SMC 2.0</h1>
            <p>Institutional Market Intelligence & Auto-Trading</p>
        </div>
        {ERROR_ALERT}
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" name="username" class="form-control" placeholder="Enter username" required autofocus autocomplete="username" value="{PREFILL_USER}">
            </div>
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" class="form-control" placeholder="Enter password" required autocomplete="current-password">
            </div>
            <button type="submit" class="btn-submit">Sign In to Dashboard</button>
        </form>
        <div class="footer-note">
            Protected by Isolated Hardware Security • 24/7 Cloud Node
        </div>
    </div>
</body>
</html>
"""

LOGOUT_INJECTION = """
<!-- Institutional Logout Button Injection -->
<div id="auth-logout-bar" style="position:fixed;top:14px;right:18px;z-index:999999;display:flex;align-items:center;gap:10px;">
    <div style="background:rgba(18,20,24,0.92);border:1px solid rgba(240,185,11,0.3);padding:6px 14px;border-radius:24px;color:#f0b90b;font-family:sans-serif;font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px;box-shadow:0 4px 12px rgba(0,0,0,0.5);backdrop-filter:blur(10px);">
        <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#0ecb81;box-shadow:0 0 6px #0ecb81;"></span>
        <span>{USER}</span>
    </div>
    <a href="/logout" style="background:rgba(246,70,93,0.18);border:1px solid #f6465d;color:#ff6b7e;padding:6px 14px;border-radius:24px;font-family:sans-serif;font-size:12px;font-weight:600;text-decoration:none;display:flex;align-items:center;gap:6px;transition:all 0.2s;box-shadow:0 4px 12px rgba(0,0,0,0.4);backdrop-filter:blur(10px);" onmouseover="this.style.background='rgba(246,70,93,0.35)';this.style.color='#fff';" onmouseout="this.style.background='rgba(246,70,93,0.18)';this.style.color='#ff6b7e';">
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

    def do_HEAD(self):
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

    def do_GET(self):
        # 1. Handle Logout
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

        # 2. Handle Login Page
        if self.path.startswith("/login"):
            user = self.get_authenticated_user()
            if user:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            rendered = LOGIN_HTML.replace("{ERROR_ALERT}", "").replace("{PREFILL_USER}", "")
            self.wfile.write(rendered.encode("utf-8"))
            return

        # 3. Check Authentication for all other routes
        user = self.get_authenticated_user()
        if not user:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # 4. Inject Logout button into HTML files
        clean_path = self.path.split("?")[0]
        if clean_path in ["/", "/index.html", "/dashboard.html"]:
            target_file = os.path.join(DIRECTORY, "index.html" if clean_path == "/" else clean_path.lstrip("/"))
            if os.path.exists(target_file):
                try:
                    with open(target_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    injected_btn = LOGOUT_INJECTION.replace("{USER}", user)
                    if "</body>" in content:
                        content = content.replace("</body>", f"{injected_btn}</body>")
                    else:
                        content = content + injected_btn

                    content_bytes = content.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content_bytes)))
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.end_headers()
                    self.wfile.write(content_bytes)
                    return
                except Exception as e:
                    print(f"[!] Error serving {clean_path}: {e}")

        # Dynamic JSON data no-cache
        if clean_path.endswith(".json"):
            self.send_response(200) if False else None

        super().do_GET()

    def do_POST(self):
        if self.path == "/login":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8")
            fields = urllib.parse.parse_qs(post_data)

            username = fields.get("username", [""])[0].strip()
            password = fields.get("password", [""])[0].strip()

            if username == AUTH_USER and password == AUTH_PASS:
                token = uuid.uuid4().hex
                SESSIONS[token] = username
                save_sessions(SESSIONS)

                self.send_response(302)
                self.send_header("Location", "/")
                # 30-day session cookie
                self.send_header("Set-Cookie", f"session_token={token}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax")
                self.end_headers()
                return
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                error_msg = '<div class="alert-error">❌ Invalid Username or Password. Please try again.</div>'
                rendered = LOGIN_HTML.replace("{ERROR_ALERT}", error_msg).replace("{PREFILL_USER}", username)
                self.wfile.write(rendered.encode("utf-8"))
                return

        self.send_response(404)
        self.end_headers()

    def end_headers(self):
        if self.path.endswith(".json") or self.path.endswith(".html") or self.path == "/":
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
        super().end_headers()

def run():
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadingHTTPServer(server_address, SecureDashboardHandler)
    print(f"[+] Binance SMC Secure Web Server running on port {PORT} with Sign In / Sign Out...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

if __name__ == "__main__":
    run()
