import json
import re

with open('/home/opc/Crypto_Signals/dashboard.html', 'r', encoding='utf-8') as f:
    html = f.read()

m = re.search(r'EMBEDDED_DATA\s*=\s*(\{.*?\});', html, re.DOTALL)
if m:
    data = json.loads(m.group(1))
    print("EMBEDDED_DATA loaded successfully!")
    print("Keys:", list(data.keys()))
    print("active_signals count:", len(data.get("active_signals", [])))
    print("real_positions count:", len(data.get("real_positions", [])))
    print("history stats:", data.get("history"))
else:
    print("EMBEDDED_DATA regex not found!")
