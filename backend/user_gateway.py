#!/usr/bin/env python3
import copy
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests
from engine_runtime import atomic_json

HOST = os.getenv("NEO_USER_GATEWAY_HOST", "127.0.0.1")
PORT = int(os.getenv("NEO_USER_GATEWAY_PORT", "8789"))
CENTRAL_UPSTREAM = os.getenv("NEO_MARKET_UPSTREAM", "http://127.0.0.1:8788").rstrip("/")
ROOT = Path(os.getenv("NEO_MARKET_ROOT", "/root/neo-meme-trade"))
ENGINE_SCRIPT = ROOT / "backend" / "market_monitor.py"
STORE_PATH = Path(os.getenv("NEO_USER_STATE_PATH", "/var/lib/neo-market/user_accounts.json"))
USER_ENGINE_ROOT = Path(os.getenv("NEO_USER_ENGINE_ROOT", "/var/lib/neo-market/users"))
LIVE_TAPE_PATH = os.getenv("NEO_LIVE_TAPE_PATH", "/var/lib/neo-market/live_tape.json")
STRATEGY_LAB_PATH = os.getenv("NEO_STRATEGY_LAB_PATH", "/var/lib/neo-market/strategy_lab.json")
BASE_USER_PORT = int(os.getenv("NEO_USER_ENGINE_PORT_START", "18800"))
MAX_USER_PORT = int(os.getenv("NEO_USER_ENGINE_PORT_END", "19800"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://qziuovwcauaklgqscqys.supabase.co").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
STARTING_BALANCE = 1000.0

LOCK = threading.RLock()
ENGINE_PROCESSES = {}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "NEO-Meme-User-Gateway/2.0", "Accept": "application/json"})


def now_ms():
    return int(time.time() * 1000)


def number(value, default=0.0):
    try:
        out = float(value)
        return out
    except Exception:
        return default


def parse_created_at(value):
    if not value:
        return now_ms()
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return now_ms()


def safe_user_id(value):
    return "".join(ch for ch in str(value) if ch.isalnum() or ch in ("-", "_"))[:128]


def load_store():
    try:
        if not STORE_PATH.exists():
            return {"accounts": {}}
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get('accounts', {}), dict):
            raise ValueError('invalid account store schema')
        data.setdefault("accounts", {})
        return data
    except Exception as exc:
        raise RuntimeError('Account registry unreadable; refusing silent account reset') from exc


STORE = load_store()


def save_store():
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(STORE_PATH, STORE)


def verify_user(headers):
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    if not token or not SUPABASE_PUBLISHABLE_KEY:
        return None
    try:
        response = SESSION.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": SUPABASE_PUBLISHABLE_KEY,
            },
            timeout=8,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        return data if data.get("id") else None
    except Exception:
        return None


def central_state():
    response = SESSION.get(f"{CENTRAL_UPSTREAM}/state", timeout=12)
    response.raise_for_status()
    return response.json()


def engine_dir(user_id):
    return USER_ENGINE_ROOT / safe_user_id(user_id)


def engine_state_path(user_id):
    return engine_dir(user_id) / "state.json"


def engine_audit_path(user_id):
    return engine_dir(user_id) / "audit.jsonl"


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def engine_health(port):
    try:
        response = SESSION.get(f"http://127.0.0.1:{int(port)}/health", timeout=1.5)
        return response.status_code == 200 and bool(response.json().get("ok"))
    except Exception:
        return False


def allocate_port(user_id):
    accounts = STORE.setdefault("accounts", {})
    reserved = {
        int(a.get("engine_port"))
        for uid, a in accounts.items()
        if uid != user_id and a.get("engine_port")
    }
    for port in range(BASE_USER_PORT, MAX_USER_PORT + 1):
        if port in reserved:
            continue
        if not port_open(port):
            return port
    raise RuntimeError("No free per-user engine ports available.")


def ensure_account_record(user):
    user_id = str(user["id"])
    accounts = STORE.setdefault("accounts", {})
    account = accounts.get(user_id)
    if account is None:
        account = {
            "user_id": user_id,
            "registered_at": parse_created_at(user.get("created_at")),
            "created_at": now_ms(),
            "updated_at": now_ms(),
        }
        accounts[user_id] = account
        save_store()
    return account


def build_bootstrap_state(user, account, raw):
    registered_at = int(account.get("registered_at") or parse_created_at(user.get("created_at")))
    central_started = int((raw.get("stats") or {}).get("demo_started_at") or 0)
    legacy_started = int(account.get("started_at") or 0)
    started_at = max(registered_at, central_started, legacy_started)

    legacy_history = account.get("history")
    if isinstance(legacy_history, list) and legacy_history:
        history = copy.deepcopy(legacy_history)
    else:
        history = []

    # A fresh personal account owns no central positions or central profits.
    # Explicit legacy personal state may be migrated without borrowing another book.
    positions = copy.deepcopy(account.get('positions') or [])
    events = [
        copy.deepcopy(event)
        for event in (raw.get("events") or [])
        if int(event.get("ts") or 0) >= started_at
    ]

    balance = account.get("balance")
    if balance is None:
        balance = STARTING_BALANCE + sum(number(t.get("pnl_usd")) for t in history)
    balance = number(balance, STARTING_BALANCE)

    trade_numbers = []
    for item in history + positions:
        try:
            trade_numbers.append(int(item.get("trade_no") or 0))
        except Exception:
            pass
    trade_seq = max(trade_numbers or [int(account.get("trade_seq") or 0), len(history) + len(positions)])

    sid = f"USER-{str(user['id'])[:8]}-{str(started_at)[-6:]}"
    return {
        "positions": positions[-20:],
        "history": history,
        "events": events[:100],
        "demo_starting_balance_usd": STARTING_BALANCE,
        "demo_balance_usd": round(balance, 8),
        "demo_started_at": started_at,
        "demo_session_id": sid,
        "trade_seq": trade_seq,
        "price_history": {},
    }


def bootstrap_if_needed(user, account):
    state_path = engine_state_path(user["id"])
    if state_path.exists():
        return
    raw = central_state()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    bootstrap = build_bootstrap_state(user, account, raw)
    atomic_json(state_path, bootstrap)
    account["migrated_to_independent_engine_at"] = now_ms()
    account["started_at"] = bootstrap["demo_started_at"]
    account["balance"] = bootstrap["demo_balance_usd"]
    account["updated_at"] = now_ms()
    save_store()


def start_engine(user, account):
    user_id = str(user["id"])
    existing = ENGINE_PROCESSES.get(user_id)
    if existing is not None and existing.poll() is None:
        port = int(account.get("engine_port") or 0)
        if port and engine_health(port):
            return port

    port = int(account.get("engine_port") or 0)
    if port and engine_health(port):
        return port
    if port <= 0 or port_open(port):
        port = allocate_port(user_id)
        account["engine_port"] = port
        account["updated_at"] = now_ms()
        save_store()

    bootstrap_if_needed(user, account)

    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "NEO_MONITOR_HOST": "127.0.0.1",
        "NEO_MONITOR_PORT": str(port),
        "NEO_MARKET_STATE_PATH": str(engine_state_path(user_id)),
        "NEO_MARKET_AUDIT_PATH": str(engine_audit_path(user_id)),
        "NEO_LIVE_TAPE_PATH": LIVE_TAPE_PATH,
        "NEO_STRATEGY_LAB_PATH": STRATEGY_LAB_PATH,
        "NEO_EXECUTION_MODE": "PAPER",
        "NEO_ENGINE_MODE": "PAPER",
        "NEO_TRAINING_ROOT": str(engine_dir(user_id) / 'training'),
    })

    engine_dir(user_id).mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [sys.executable, str(ENGINE_SCRIPT)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ENGINE_PROCESSES[user_id] = process

    deadline = time.time() + 10
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"User engine exited with code {process.returncode}.")
        if engine_health(port):
            return port
        time.sleep(0.2)
    raise RuntimeError("User engine did not become ready in time.")


def ensure_engine(user):
    with LOCK:
        account = ensure_account_record(user)
        return start_engine(user, account)


def proxy_user_engine(user, method, path):
    port = ensure_engine(user)
    url = f"http://127.0.0.1:{port}{path}"
    if method == "POST":
        response = SESSION.post(url, timeout=15)
    else:
        response = SESSION.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        data["account_scope"] = {
            "user_id": str(user["id"]),
            "isolated": True,
            "independent_engine": True,
            "strategy": (data.get('config') or {}).get('signal_strategy', 'UNKNOWN'),
        }
    return data


def revive_known_engines():
    # Engines are restarted automatically after a gateway/system restart so
    # previously activated accounts keep paper-trading even while logged out.
    with LOCK:
        accounts = list((STORE.get("accounts") or {}).values())
    for account in accounts:
        user_id = account.get("user_id")
        if not user_id or not engine_state_path(user_id).exists():
            continue
        fake_user = {
            "id": user_id,
            "created_at": None,
        }
        try:
            with LOCK:
                start_engine(fake_user, account)
        except Exception:
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = "NEOUserGateway/2.0"

    def log_message(self, fmt, *args):
        return

    def cors(self):
        origin = self.headers.get("Origin", "")
        allowed = {
            "https://angelmalevbiz-debug.github.io",
            "https://angelmalev9-creator.github.io",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        }
        self.send_header(
            "Access-Control-Allow-Origin",
            origin if origin in allowed else "https://angelmalevbiz-debug.github.io",
        )
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")

    def json_response(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def authenticated(self):
        user = verify_user(self.headers)
        if not user:
            self.json_response({"error": "unauthorized"}, 401)
            return None
        return user

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/user/health":
            self.json_response({
                "ok": True,
                "isolated_accounts": True,
                "independent_engines": True,
                "engine_scope": "INDEPENDENT_PAPER",
            })
            return

        user = self.authenticated()
        if not user:
            return

        try:
            if parsed.path == "/user/state":
                self.json_response(proxy_user_engine(user, "GET", "/state"))
                return
            if parsed.path == "/user/token":
                suffix = f"?{parsed.query}" if parsed.query else ""
                self.json_response(proxy_user_engine(user, "GET", f"/token{suffix}"))
                return
            self.json_response({"error": "not_found"}, 404)
        except Exception as exc:
            self.json_response({"error": "gateway_error", "message": str(exc)}, 502)

    def do_POST(self):
        parsed = urlparse(self.path)
        user = self.authenticated()
        if not user:
            return
        try:
            if parsed.path == "/user/reset":
                self.json_response(proxy_user_engine(user, "POST", "/control/reset"))
                return
            if parsed.path == "/user/rescan":
                self.json_response(proxy_user_engine(user, "POST", "/control/rescan"))
                return
            self.json_response({"error": "not_found"}, 404)
        except Exception as exc:
            self.json_response({"error": "gateway_error", "message": str(exc)}, 502)


def main():
    USER_ENGINE_ROOT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=revive_known_engines, name="neo-user-engine-revive", daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"NEO user gateway listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
