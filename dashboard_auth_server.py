import os
import sys
import base64
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 80
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
AUTH_USER = os.environ.get("DASHBOARD_USER", "DMD-DJ")
AUTH_PASS = os.environ.get("DASHBOARD_PASS", "D@m!dMD@0912")

class AuthHTTPRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_HEAD(self):
        if not self.authenticate():
            return
        super().do_HEAD()

    def do_GET(self):
        if not self.authenticate():
            return
        super().do_GET()

    def end_headers(self):
        # Prevent browser caching of dynamic signal data
        if self.path.endswith(".json") or self.path.endswith(".html") or self.path == "/":
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
        super().end_headers()

    def authenticate(self):
        auth_header = self.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Basic "):
            self.send_auth_required()
            return False

        try:
            encoded_creds = auth_header.split(" ", 1)[1].strip()
            decoded_creds = base64.b64decode(encoded_creds).decode("utf-8")
            username, password = decoded_creds.split(":", 1)
            if username == AUTH_USER and password == AUTH_PASS:
                return True
        except Exception:
            pass

        self.send_auth_required()
        return False

    def send_auth_required(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Binance Institutional SMC Dashboard"')
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = (
            "<!DOCTYPE html><html><head><title>401 Unauthorized</title>"
            "<style>body{background:#0b0e14;color:#f0b90b;font-family:sans-serif;text-align:center;padding-top:100px;}</style>"
            "</head><body>"
            "<h1>🔒 401 Unauthorized</h1>"
            "<p>Access Denied. Valid Institutional Credentials Required.</p>"
            "</body></html>"
        )
        self.wfile.write(html.encode("utf-8"))

def run():
    server_address = ("0.0.0.0", PORT)
    httpd = HTTPServer(server_address, AuthHTTPRequestHandler)
    print(f"[+] Secure Dashboard Server running on port {PORT} (Protected with Auth)...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

if __name__ == "__main__":
    run()
