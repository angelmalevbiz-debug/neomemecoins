#!/usr/bin/env python3
import json
import os
import re
import threading
import time
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

os.environ.setdefault('DISPLAY', ':99')

from playwright.sync_api import sync_playwright

BASE_URL = os.getenv('FOMO_URL', 'https://fomo.family/')
HOST = os.getenv('NEO_MONITOR_HOST', '127.0.0.1')
PORT = int(os.getenv('NEO_MONITOR_PORT', '8788'))
PROFILE_DIR = Path(os.getenv('FOMO_PROFILE_DIR', '/var/lib/neo-fomo/profile'))
STATE_PATH = Path(os.getenv('FOMO_STATE_PATH', '/var/lib/neo-fomo/state.json'))
SCAN_SECONDS = int(os.getenv('FOMO_SCAN_SECONDS', '20'))
POSITION_SECONDS = int(os.getenv('FOMO_POSITION_SECONDS', '15'))
STOP_LOSS_PCT = float(os.getenv('FOMO_STOP_LOSS_PCT', '8'))
TAKE_PROFIT_PCT = float(os.getenv('FOMO_TAKE_PROFIT_PCT', '16'))
TRAILING_PCT = float(os.getenv('FOMO_TRAILING_PCT', '6'))
MAX_HOLD_MINUTES = int(os.getenv('FOMO_MAX_HOLD_MINUTES', '45'))
MAX_POSITIONS = int(os.getenv('FOMO_MAX_POSITIONS', '2'))
ENTRY_SCORE = float(os.getenv('FOMO_ENTRY_SCORE', '66'))
def find_chromium() -> str:
    candidates = [
        os.getenv('CHROMIUM_PATH', ''),
        '/usr/bin/google-chrome',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
    ]
    cache = Path('/root/.cache/puppeteer/chrome')
    if cache.exists():
        candidates += [str(p) for p in sorted(cache.glob('*/chrome-linux64/chrome'), reverse=True)]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError('Chromium executable not found')


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_float(value: str) -> float | None:
    try:
        return float(value.replace(',', ''))
    except Exception:
        return None


def compact_event(text: str) -> dict[str, Any]:
    return {'ts': now_ms(), 'text': text[:500]}
@dataclass
class Position:
    id: str
    token_url: str
    symbol: str
    entry_price: float
    current_price: float
    peak_price: float
    score: float
    opened_at: int
    updated_at: int
    source_text: str


class MonitorState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.running = True
        self.status = 'starting'
        self.message = 'Initializing Fomo browser monitor.'
        self.last_scan_at = 0
        self.login_required = False
        self.candidates: list[dict[str, Any]] = []
        self.positions: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.load()
    def load(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            data = json.loads(STATE_PATH.read_text())
            self.positions = data.get('positions', [])[-20:]
            self.history = data.get('history', [])[-200:]
            self.events = data.get('events', [])[-100:]
        except Exception:
            pass

    def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'positions': self.positions,
            'history': self.history[-200:],
            'events': self.events[-100:],
        }
        STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    def event(self, text: str) -> None:
        self.events.insert(0, compact_event(text))
        self.events = self.events[:100]
        self.message = text[:500]
        self.save()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                'running': self.running,
                'status': self.status,
                'message': self.message,
                'login_required': self.login_required,
                'last_scan_at': self.last_scan_at,
                'candidates': self.candidates[:30],
                'positions': self.positions,
                'history': self.history[:100],
                'events': self.events[:50],
                'config': {
                    'scan_seconds': SCAN_SECONDS,
                    'stop_loss_pct': STOP_LOSS_PCT,
                    'take_profit_pct': TAKE_PROFIT_PCT,
                    'trailing_pct': TRAILING_PCT,
                    'max_hold_minutes': MAX_HOLD_MINUTES,
                    'max_positions': MAX_POSITIONS,
                    'entry_score': ENTRY_SCORE,
                },
            }
STATE = MonitorState()


def extract_price(text: str) -> float | None:
    patterns = [
        r'(?i)price\s*[:\n]?\s*\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)',
        r'\$\s*(0\.0+[0-9]+)',
        r'\$\s*([0-9]+\.[0-9]{4,})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = safe_float(match.group(1))
        if value and value > 0:
            return value
    return None


def extract_symbol(text: str, fallback: str = 'TOKEN') -> str:
    match = re.search(r'\$([A-Za-z][A-Za-z0-9]{1,12})\b', text)
    if match:
        return match.group(1).upper()
    words = re.findall(r'\b[A-Z][A-Z0-9]{1,9}\b', text)
    return words[0] if words else fallback[:12].upper()
def score_candidate(text: str) -> float:
    clean = ' '.join(text.split())
    low = clean.lower()
    score = 35.0
    for raw in re.findall(r'([+-]?\d+(?:\.\d+)?)\s*%', clean):
        value = safe_float(raw)
        if value is None:
            continue
        if 3 <= value <= 40:
            score += min(20, value * 0.7)
        elif value > 80:
            score -= 8
        elif value < -8:
            score -= 12
    positives = ['large buy', 'new listing', 'price spike', 'trending', 'holders', 'volume']
    negatives = ['large sell', 'closed', 'rug', 'scam', 'warning']
    score += sum(5 for word in positives if word in low)
    score -= sum(8 for word in negatives if word in low)
    if len(clean) >= 50:
        score += 5
    return max(0.0, min(100.0, score))


def normalize_href(href: str) -> str:
    if href.startswith('http://') or href.startswith('https://'):
        return href
    return BASE_URL.rstrip('/') + '/' + href.lstrip('/')
class FomoMonitor:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.playwright = None
        self.context = None
        self.page = None

    def launch(self) -> None:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        chromium = find_chromium()
        self.playwright = sync_playwright().start()
        self.context = self.playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            executable_path=chromium,
            headless=False,
            viewport={'width': 1440, 'height': 1000},
            args=['--no-sandbox', '--disable-dev-shm-usage', '--window-size=1440,1000'],
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.goto(BASE_URL, wait_until='domcontentloaded', timeout=60_000)
        STATE.event('Fomo browser monitor started.')

    def close(self) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self.playwright:
                self.playwright.stop()
    def detect_login_required(self) -> bool:
        try:
            texts = self.page.locator('button').all_inner_texts()
            login_visible = any(text.strip().lower() == 'login' for text in texts)
            token_links = self.page.locator('a[href*="/tokens/"], a[href*="/coin?"]').count()
            return login_visible and token_links == 0
        except Exception:
            return True

    def scrape_candidates(self) -> list[dict[str, Any]]:
        rows = self.page.eval_on_selector_all(
            'a[href]',
            """els => els.map(a => ({
              href: a.getAttribute('href') || '',
              text: (a.innerText || a.textContent || '').trim()
            })).filter(x => x.href.includes('/tokens/') || x.href.includes('/coin?'))""",
        )
        merged: dict[str, dict[str, Any]] = {}
        for row in rows:
            href = normalize_href(row.get('href', ''))
            text = ' '.join((row.get('text') or '').split())[:1200]
            if not href or not text:
                continue
            current = merged.get(href)
            candidate = {
                'url': href,
                'text': text,
                'symbol': extract_symbol(text),
                'score': round(score_candidate(text), 1),
                'seen_at': now_ms(),
            }
            if not current or candidate['score'] > current['score']:
                merged[href] = candidate
        return sorted(merged.values(), key=lambda x: x['score'], reverse=True)[:50]
    def inspect_token(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        page = self.context.new_page()
        try:
            page.goto(candidate['url'], wait_until='domcontentloaded', timeout=45_000)
            page.wait_for_timeout(1400)
            text = page.locator('body').inner_text(timeout=10_000)
            price = extract_price(text)
            symbol = extract_symbol(text, candidate.get('symbol', 'TOKEN'))
            return {
                **candidate,
                'symbol': symbol,
                'price': price,
                'page_text': ' '.join(text.split())[:3000],
                'inspected_at': now_ms(),
            }
        except Exception as exc:
            STATE.event(f"Inspect failed: {candidate.get('symbol', 'TOKEN')} — {exc}")
            return None
        finally:
            page.close()

    def maybe_open_paper(self, detail: dict[str, Any]) -> None:
        price = detail.get('price')
        if not price or detail.get('score', 0) < ENTRY_SCORE:
            return
        if len(STATE.positions) >= MAX_POSITIONS:
            return
        if any(p.get('token_url') == detail['url'] for p in STATE.positions):
            return
        opened_at = now_ms()
        position = Position(
            id=f"{detail['symbol']}:{opened_at}",
            token_url=detail['url'],
            symbol=detail['symbol'],
            entry_price=price,
            current_price=price,
            peak_price=price,
            score=detail['score'],
            opened_at=opened_at,
            updated_at=opened_at,
            source_text=detail.get('text', '')[:1000],
        )
        STATE.positions.append(asdict(position))
        STATE.event(
            f"PAPER ENTRY ${position.symbol} @ ${price:.10g} · Fomo monitor score {position.score:.0f}/100"
        )

    def refresh_position(self, position: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        detail = self.inspect_token({
            'url': position['token_url'],
            'text': position.get('source_text', ''),
            'symbol': position.get('symbol', 'TOKEN'),
            'score': position.get('score', 0),
        })
        if not detail or not detail.get('price'):
            return position, None
        price = float(detail['price'])
        entry = float(position['entry_price'])
        peak = max(float(position.get('peak_price', entry)), price)
        pnl_pct = ((price - entry) / entry) * 100 if entry > 0 else 0.0
        hold_minutes = (now_ms() - int(position['opened_at'])) / 60_000
        trailing_armed = peak >= entry * (1 + TRAILING_PCT / 100)
        trailing_floor = peak * (1 - TRAILING_PCT / 100)
        exit_reason = None
        if pnl_pct <= -STOP_LOSS_PCT:
            exit_reason = 'STOP_LOSS'
        elif pnl_pct >= TAKE_PROFIT_PCT:
            exit_reason = 'TAKE_PROFIT'
        elif trailing_armed and price <= trailing_floor:
            exit_reason = 'TRAILING_STOP'
        elif hold_minutes >= MAX_HOLD_MINUTES:
            exit_reason = 'MAX_HOLD'

        updated = {
            **position,
            'current_price': price,
            'peak_price': peak,
            'pnl_pct': round(pnl_pct, 3),
            'updated_at': now_ms(),
        }
        if not exit_reason:
            return updated, None
        closed = {
            **updated,
            'closed_at': now_ms(),
            'exit_price': price,
            'exit_reason': exit_reason,
        }
        return updated, closed
    def refresh_positions(self) -> None:
        if not STATE.positions:
            return
        next_positions: list[dict[str, Any]] = []
        for position in list(STATE.positions):
            updated, closed = self.refresh_position(position)
            if closed:
                STATE.history.insert(0, closed)
                STATE.history = STATE.history[:200]
                STATE.event(
                    f"PAPER EXIT ${closed['symbol']} {closed['exit_reason']} · {closed.get('pnl_pct', 0):+.2f}%"
                )
            else:
                next_positions.append(updated)
        STATE.positions = next_positions
        STATE.save()

    def scan_once(self) -> None:
        if not STATE.running:
            return
        try:
            if self.page.is_closed():
                self.page = self.context.new_page()
            if not self.page.url.startswith('https://fomo.family'):
                self.page.goto(BASE_URL, wait_until='domcontentloaded', timeout=45_000)
            self.page.wait_for_timeout(600)
            login_required = self.detect_login_required()
            STATE.login_required = login_required
            STATE.last_scan_at = now_ms()
            if login_required:
                STATE.status = 'login_required'
                STATE.message = 'Fomo login is required once in the persistent browser session.'
                return
            STATE.status = 'monitoring'
            candidates = self.scrape_candidates()
            STATE.candidates = candidates
            if candidates:
                top = candidates[0]
                detail = self.inspect_token(top)
                if detail:
                    self.maybe_open_paper(detail)
            self.refresh_positions()
            STATE.message = f"Monitoring Fomo · {len(candidates)} coin links · {len(STATE.positions)} simulated positions."
        except Exception as exc:
            STATE.status = 'error'
            STATE.event(f'Fomo monitor error: {exc}')
    def run(self) -> None:
        try:
            self.launch()
            while not self.stop_event.is_set():
                self.scan_once()
                self.stop_event.wait(SCAN_SECONDS)
        except Exception as exc:
            STATE.status = 'error'
            STATE.event(f'Browser launch failed: {exc}')
        finally:
            self.close()

    def stop(self) -> None:
        self.stop_event.set()


MONITOR = FomoMonitor()
class ApiHandler(BaseHTTPRequestHandler):
    server_version = 'NEOFomoMonitor/1.0'

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == '/health':
            self.send_json({
                'ok': True,
                'status': STATE.status,
                'login_required': STATE.login_required,
                'running': STATE.running,
            })
            return
        if path == '/state':
            self.send_json(STATE.snapshot())
            return
        self.send_json({'error': 'not_found'}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == '/control/start':
            with STATE.lock:
                STATE.running = True
                STATE.event('Monitoring enabled.')
            self.send_json(STATE.snapshot())
            return
        if path == '/control/stop':
            with STATE.lock:
                STATE.running = False
                STATE.status = 'paused'
                STATE.event('Monitoring paused.')
            self.send_json(STATE.snapshot())
            return
        if path == '/control/reset':
            with STATE.lock:
                STATE.positions = []
                STATE.history = []
                STATE.events = []
                STATE.candidates = []
                STATE.event('Simulation history reset.')
            self.send_json(STATE.snapshot())
            return
        if path == '/control/rescan':
            threading.Thread(target=MONITOR.scan_once, daemon=True).start()
            self.send_json({'ok': True})
            return
        self.send_json({'error': 'not_found'}, 404)


def main() -> None:
    browser_thread = threading.Thread(target=MONITOR.run, name='fomo-monitor', daemon=True)
    browser_thread.start()
    server = ThreadingHTTPServer((HOST, PORT), ApiHandler)
    print(f'NEO Fomo monitor API listening on http://{HOST}:{PORT}', flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        MONITOR.stop()
        server.shutdown()


if __name__ == '__main__':
    main()
