"""
Automated GitHub Sync & Update Tool for Crypto_Signals
Allows 1-click synchronization of project files from GitHub main branch
without requiring Git CLI on Windows.
"""

import os
import sys
import io
import zipfile
import requests
import json
import re

sys.stdout.reconfigure(encoding='utf-8')

REPO_OWNER = "jayawmdi-source"
REPO_NAME = "Crypto_Signals"
BRANCH = "main"

# Read token and remote URL from .git/config if available
CONFIG_PATH = os.path.join(os.path.dirname(__file__), ".git", "config")
TOKEN = None
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = f.read()
            m = re.search(r"https://[^:]+:(ghp_[a-zA-Z0-9]+)@github\.com", cfg)
            if m:
                TOKEN = m.group(1)
    except Exception as e:
        pass

# Files/folders to preserve locally (never overwrite)
PRESERVE_FILES = {
    "binance_api_config.json",
    "telegram_config.json",
    ".env",
    "vps_credentials",
    ".git"
}

def get_local_head():
    head_file = os.path.join(os.path.dirname(__file__), ".git", "refs", "heads", BRANCH)
    if os.path.exists(head_file):
        with open(head_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None

def set_local_head(sha):
    git_dir = os.path.join(os.path.dirname(__file__), ".git")
    if os.path.exists(git_dir):
        head_file = os.path.join(git_dir, "refs", "heads", BRANCH)
        os.makedirs(os.path.dirname(head_file), exist_ok=True)
        with open(head_file, "w", encoding="utf-8") as f:
            f.write(sha + "\n")
        
        # update HEAD reflog if exists
        log_file = os.path.join(git_dir, "logs", "HEAD")
        if os.path.exists(log_file):
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"0000000000000000000000000000000000000000 {sha} updater <updater@local> 0 +0000\tpull: sync from github\n")

def sync():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    print("=" * 65)
    print("🔄 Crypto_Signals GitHub Auto-Updater")
    print("=" * 65)

    headers = {"User-Agent": "Mozilla/5.0"}
    if TOKEN:
        headers["Authorization"] = f"token {TOKEN}"

    # 1. Check latest commit on GitHub
    api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits/{BRANCH}"
    print(f"[*] Checking GitHub repository ({REPO_OWNER}/{REPO_NAME}:{BRANCH})...")
    
    try:
        resp = requests.get(api_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"[❌] Error checking GitHub: HTTP {resp.status_code}")
            print(resp.text[:300])
            return
        
        data = resp.json()
        remote_sha = data["sha"]
        commit_msg = data["commit"]["message"].splitlines()[0]
        commit_date = data["commit"]["author"]["date"]
        
        print(f"[+] Remote Latest Commit: {remote_sha[:8]} ({commit_date})")
        print(f"    Message: \"{commit_msg}\"")
        
        local_sha = get_local_head()
        if local_sha:
            print(f"[+] Local Current Commit : {local_sha[:8]}")
            if local_sha.lower() == remote_sha.lower():
                print("\n" + "=" * 65)
                print("✅ EXCELLENT: Your files are ALREADY 100% UP TO DATE!")
                print("   No update required.")
                print("=" * 65)
                return
        
        print("\n[!] New update available! Downloading files from GitHub...")
        zip_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/zipball/{BRANCH}"
        zip_resp = requests.get(zip_url, headers=headers, timeout=60)
        
        if zip_resp.status_code != 200:
            print(f"[❌] Failed to download zipball: HTTP {zip_resp.status_code}")
            return
        
        print("[+] Extracting and updating project files...")
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
            root_prefix = z.namelist()[0].split('/')[0] + '/'
            updated_count = 0
            
            for member in z.namelist():
                if not member.startswith(root_prefix) or member == root_prefix:
                    continue
                
                rel_path = member[len(root_prefix):]
                if not rel_path:
                    continue
                
                # Check preserve list
                first_part = rel_path.split('/')[0]
                if first_part in PRESERVE_FILES or rel_path in PRESERVE_FILES:
                    continue
                
                target_path = os.path.join(base_dir, rel_path.replace('/', os.sep))
                
                if member.endswith('/'):
                    os.makedirs(target_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    content = z.read(member)
                    with open(target_path, 'wb') as out_f:
                        out_f.write(content)
                    updated_count += 1
        
        set_local_head(remote_sha)
        print("\n" + "=" * 65)
        print(f"🎉 SUCCESS! Updated {updated_count} files successfully.")
        print(f"📌 Local system is now synchronized to commit: {remote_sha[:8]}")
        print("=" * 65)

    except Exception as e:
        print(f"[❌] Error during sync: {e}")

if __name__ == "__main__":
    sync()
