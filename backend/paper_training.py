"""Persistent, isolated PAPER experimentation on ONE recorded causal stream.

No network, wallet or transaction client is imported. Quote execution requires
exact raw quantities. RECORDED_LIQUIDITY_MODEL is a labelled estimate: a symmetric
constant-product reserve approximation from independently verified recorded
price/liquidity, with recorded route evidence, explicit AMM fee, latency and
adverse slippage. It is neither an observed pool reserve nor a real fill.

Every book owns cash, positions and limits. The learner selects only predefined
parameters, freezes them, then validates on later, embargoed, untouched episodes.
An accepted version affects the isolated LEARNER book's subsequent decisions.
It NEVER edits a primary bot strategy or any real account.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import queue
import re
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from engine_exit_policy import exit_reason as adaptive_exit_reason

VERSION = "PAPER_TRAINING_V1"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
MODEL = "RECORDED_LIQUIDITY_MODEL"
ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
MAX_REJECTED_EPISODE_HISTORY = 2000


def number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError, OverflowError):
        return default


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def raw_int(value):
    """Never truncate decimal quantities or turn booleans into token units."""
    if isinstance(value, bool):
        raise ValueError("boolean raw amount")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError("integer raw quantity required")


# tempfile.mkstemp(prefix="." + name) appends exactly eight characters from
# this alphabet; only such names are atomic_json temporaries of that checkpoint.
TEMP_NAME_SUFFIX = re.compile(r"[a-z0-9_]{8}")
# A live write keeps advancing its temporary's mtime; a hard-stopped one does not.
STALE_TEMP_SECONDS = 600


def _discard_temp(name):
    # Best effort, and never raised: cleanup must not mask the write's own error
    # or fail a completed replace. A scanner can briefly hold the file open on
    # Windows; anything left is reclaimed by remove_stale_temp_files.
    for _ in range(5):
        try:
            os.unlink(name)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(.05)
        except OSError:
            return


def remove_stale_temp_files(path, max_age_seconds=STALE_TEMP_SECONDS, now=None):
    """Delete atomic_json temporaries of this checkpoint abandoned by a hard stop.

    TerminateProcess/taskkill /F/power loss skip atomic_json's finally and leave
    a full-size partial copy beside the checkpoint. Only names atomic_json
    creates for this exact path, in its own directory, are candidates: the
    checkpoint itself, observations.jsonl and every other file are never
    touched. A younger temporary may belong to a write still in progress.
    """
    path = Path(path)
    prefix = "." + path.name
    now = time.time() if now is None else now
    removed = []
    try:
        entries = list(os.scandir(path.parent))
    except FileNotFoundError:
        return removed
    for entry in entries:
        if not (entry.name.startswith(prefix)
                and TEMP_NAME_SUFFIX.fullmatch(entry.name[len(prefix):])):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            # os.stat, not the cached directory entry: Windows can report a
            # stale mtime there for a file another handle is still writing.
            if now - os.stat(entry.path, follow_symlinks=False).st_mtime < max_age_seconds:
                continue
            os.unlink(entry.path)
        except OSError:
            # Still open elsewhere or already gone; a later sweep retries.
            continue
        removed.append(entry.name)
    return removed


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        # Training state grows with its durable observation-ID set. On Windows
        # endpoint scanners/readers may briefly deny replacement of a large
        # checkpoint; retry in this background learner instead of dropping its
        # worker after a transient sharing violation.
        for attempt in range(100):
            try:
                os.replace(name, path)
                break
            except PermissionError:
                if attempt == 99:
                    raise
                # A brief concurrent reader or scanner can deny replacement.
                time.sleep(.03)
    finally:
        # Also runs on KeyboardInterrupt/SystemExit; a hard kill cannot run it.
        _discard_temp(name)


DEFAULT_CONFIG = {
    "initial_cash": 500.0, "notional": 20.0, "max_positions": 4,
    # Full committed capital is at risk; stop distance is not a worst-case loss.
    "max_position_fraction": .05, "max_exposure_fraction": .20,
    "daily_loss_fraction": .05, "max_drawdown_fraction": .10,
    "latency_ms": 500, "quote_ttl_ms": 2000, "feed_ttl_ms": 20000,
    "evidence_ttl_ms": 30000, "flow_ttl_ms": 15000,
    "order_timeout_ms": 30000, "cooldown_ms": 60000,
    "min_liquidity": 10000.0, "max_impact_pct": 2.0,
    "max_roundtrip_cost_pct": 2.75, "adverse_slippage_bps": 20.0,
    "failed_attempt_fee_usd": .02, "min_flow_trades": 3,
    "min_flow_wallets": 2, "min_flow_buy_usd": 25.0,
    "episode_ms": 3600000, "embargo_ms": 60000, "post_exit_window_ms": 3600000,
    "train_every_episodes": 30, "min_train_episodes": 30,
    "min_validation_episodes": 30, "min_validation_trades": 20, "min_validation_days": 5,
    "min_validation_net_usd": 0.0, "min_improvement_usd": 1.0,
    "min_feasibility": .90, "stress_extra_cost_bps": 50.0,
    "require_positive_ci": True, "rollback_min_episodes": 30,
    "rollback_underperformance_usd": 5.0,
    "validation_max_ms": 7 * 86400000,
}

BASELINE = {"min_score": 70.0, "min_flow_ratio": 1.20,
            "stop_pct": 3.0, "take_profit_pct": 10.0,
            "max_hold_ms": 3600000, "trailing_pct": 0.0}
HYPOTHESES = {
    "CONTROL": BASELINE,
    "EARLY": {**BASELINE, "min_score": 60.0, "min_flow_ratio": 1.10},
    "STRICT": {**BASELINE, "min_score": 80.0, "min_flow_ratio": 1.50},
    "FAST_EXIT": {**BASELINE, "take_profit_pct": 5.0, "max_hold_ms": 900000},
    "PROTECT": {**BASELINE, "trailing_pct": 2.0},
    "EARLY_PROTECT": {**BASELINE, "min_score": 60.0, "min_flow_ratio": 1.10,
                      "trailing_pct": 2.0},
    "STRICT_FAST": {**BASELINE, "min_score": 80.0, "min_flow_ratio": 1.50,
                    "take_profit_pct": 5.0, "max_hold_ms": 900000},
    "GOLD_ADAPTIVE": {**BASELINE, "exit_policy": "adaptive"},
}


def _config(supplied):
    result = {**DEFAULT_CONFIG, **(supplied or {})}
    if result.get("live") or result.get("mode", "PAPER") != "PAPER":
        raise ValueError("Training is isolated PAPER only")
    for key in DEFAULT_CONFIG:
        if key == "require_positive_ci":
            if not isinstance(result[key], bool):
                raise ValueError(key + " must be boolean")
        elif isinstance(result[key], bool) or not math.isfinite(number(result[key], math.nan)) or number(result[key]) < 0:
            raise ValueError("invalid configuration: " + key)
    for key in ("initial_cash", "notional", "episode_ms", "max_positions",
                "min_train_episodes", "train_every_episodes", "min_validation_episodes",
                "min_validation_trades", "min_validation_days", "rollback_min_episodes"):
        if result[key] <= 0:
            raise ValueError(key + " must be positive")
    for key in ("max_position_fraction", "max_exposure_fraction", "daily_loss_fraction",
                "max_drawdown_fraction", "min_feasibility"):
        if not 0 < result[key] <= 1:
            raise ValueError(key + " must be in (0,1]")
    if result["notional"] > result["initial_cash"] * result["max_position_fraction"]:
        raise ValueError("notional exceeds full-loss position capital limit")
    return result


def training_candidate_signal(coin, flow, *, now, config=None):
    """Cheap causal gate before spending risk/price-check capacity on a learner.

    Mirrors the least restrictive predefined PAPER hypothesis, while retaining
    fresh exact-pool identity and complete verified on-chain coverage. This is
    only a request for independent evidence; it never authorizes an entry.
    """
    cfg = _config(config)
    if not isinstance(coin, dict) or not isinstance(flow, dict):
        return False
    mint, pair = str(coin.get("address") or ""), str(coin.get("pairAddress") or "")
    updated = number(coin.get("updatedAt"), -1)
    if (not ADDRESS.fullmatch(mint) or not ADDRESS.fullmatch(pair)
            or number(coin.get("priceUsd")) <= 0
            or number(coin.get("liquidityUsd")) < cfg["min_liquidity"]
            or not 0 <= now - updated <= cfg["feed_ttl_ms"]):
        return False
    coverage = flow.get("coverage") or {}
    latest = number(flow.get("latest_at"), -1)
    if (flow.get("fresh") is not True or str(flow.get("quality") or "").upper() != "COMPLETE"
            or coverage.get("status") != "COMPLETE"
            or coverage.get("pairAddress") != pair or coverage.get("address") != mint
            or not 0 <= now - latest <= cfg["flow_ttl_ms"]
            or number(flow.get("trades")) < cfg["min_flow_trades"]
            or number(flow.get("unique_wallets")) < cfg["min_flow_wallets"]
            or number(flow.get("buy_usd")) < cfg["min_flow_buy_usd"]):
        return False
    minimum_score = min(params["min_score"] for params in HYPOTHESES.values())
    minimum_ratio = min(params["min_flow_ratio"] for params in HYPOTHESES.values())
    return (number(coin.get("score")) >= minimum_score
            and number(flow.get("buy_sell_usd_ratio", flow.get("ratio"))) >= minimum_ratio)


def empty_book(name, params, cash):
    return {"id": name, "params": copy.deepcopy(params), "initial_cash": cash,
            "cash": cash, "positions": {}, "pending": {}, "trades": [],
            "rejected": [], "rejected_total": 0, "rejected_market_episodes_total": 0,
            "evaluated_rejected_paths_total": 0, "rejection_reason_counts": {},
            "failed": [], "cooldowns": {}, "peak_equity": cash,
            "max_drawdown": 0.0, "day": None, "day_start_equity": cash,
            "halt_reason": None, "observations": 0}


def normalize_quote(quote, *, side, mint, pair, decimals, network_fee_usd=None,
                    entry_account_reserve_usd=None):
    """Convert saved native Jupiter evidence; never request or manufacture it."""
    raw = quote.get("raw_quote", quote)
    at = int(raw.get("_observed_at") or raw.get("_received_at") or quote.get("quoted_at") or 0)
    return {"side": side, "mint": mint, "pair": pair,
            "input_raw": raw.get("inAmount"), "output_raw": raw.get("outAmount"),
            "floor_raw": raw.get("otherAmountThreshold"), "quoted_at": at,
            "available_at": at, "input_mint": raw.get("inputMint"),
            "output_mint": raw.get("outputMint"), "swap_mode": raw.get("swapMode"),
            "route": copy.deepcopy(raw.get("routePlan")),
            "price_impact_pct": number(raw.get("priceImpactPct"), math.inf) * 100,
            "network_fee_usd": network_fee_usd, "fee_included": True,
            "network_fee_basis": "USER_SUPPLIED_PAPER_FEE_ASSUMPTION" if network_fee_usd is not None else "UNKNOWN",
            "entry_account_reserve_usd": entry_account_reserve_usd if side == "buy" else 0.0,
            "account_reserve_basis": ("USER_SUPPLIED_PAPER_RESERVE_ASSUMPTION" if entry_account_reserve_usd is not None else "UNKNOWN") if side == "buy" else "NOT_APPLICABLE_ON_SELL",
            "decimals": decimals, "provenance": "recorded_read_only_jupiter_quote"}


class PaperTrainingEngine:
    def __init__(self, state_path, config=None):
        self.path = Path(state_path)
        # A service restart hard-stops the worker (TerminateProcess on Windows),
        # often mid-checkpoint. Reclaim those copies now, and again after saves
        # so the copy abandoned just before this start is not kept until the
        # next restart.
        remove_stale_temp_files(self.path)
        self._next_temp_sweep = time.monotonic() + STALE_TEMP_SECONDS
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if self.state.get("version") != VERSION or self.state.get("paper_only") is not True:
                raise ValueError("incompatible/corrupt PAPER training state; explicit reset required")
            self.config = _config(self.state["config"])
            self.state.setdefault("recording_drops_total", 0)
            self.state.setdefault("recording_drops_baseline", 0)
            if config is not None and _config(config) != self.config:
                raise ValueError("configuration changed; use a separate state or explicit reset")
            if self.state["control_hash"] != digest(BASELINE) or self.state["books"]["CONTROL"]["params"] != BASELINE:
                raise ValueError("frozen control parameters changed")
            for name, params in HYPOTHESES.items():
                if self.state["books"][name]["params"] != params:
                    raise ValueError("predefined experiment parameters changed: " + name)
            active = next((v for v in self.state["versions"] if v["id"] == self.state["active_version"]), None)
            if not active or active["params"] != self.state["books"]["LEARNER"]["params"]:
                raise ValueError("active learned parameters do not match saved approved version")
            for book in self.state["books"].values():
                if not math.isfinite(number(book["cash"], math.nan)) or book["cash"] < 0:
                    raise ValueError("invalid paper cash ledger")
            if self._compact_rejections():
                # The append-only observation journal retains individual
                # decisions; state keeps one path per episode plus totals.
                self.save()
        else:
            self.config = _config(config)
            self.state = self._fresh()
            self.save()
        # The journal can contain hundreds of thousands of observations. A
        # list membership check here made every new row scan the entire history
        # and eventually let the asynchronous learner fall behind the market.
        # Keep the persisted list for restart compatibility, and use this
        # in-memory index for constant-time duplicate detection.
        self._seen_ids = set(self.state.get("seen_ids", []))
        self._index_post_exit_assessments()
        self._index_rejected_signals()

    def has_seen(self, row_id):
        return bool(row_id) and row_id in self._seen_ids

    def _index_post_exit_assessments(self):
        """Rebuild once per load/reset; each row then visits active windows only."""
        self._post_exit_pending = {
            name: {index for index, trade in enumerate(book["trades"])
                   if (trade.get("exit_analysis") or {}).get("post_exit")
                   and not trade["exit_analysis"]["post_exit"]["status"].startswith("window_complete")}
            for name, book in self.state["books"].items()}

    def _compact_rejections(self):
        changed = False
        for book in self.state["books"].values():
            records = book.get("rejected") if isinstance(book.get("rejected"), list) else []
            reasons = Counter(book.get("rejection_reason_counts") or {})
            if not reasons:
                for item in records:
                    reasons.update(item.get("reasons") or [])
            grouped = {}
            signal_total = sum(max(1, int(number(item.get("signal_count"), 1))) for item in records)
            for item in records:
                episode = str(item.get("episode") or item.get("id") or "legacy:%s" % item.get("at", 0))
                current = grouped.get(episode)
                if current is None:
                    current = copy.deepcopy(item)
                    current["episode"] = episode
                    current["first_signal_at"] = int(number(item.get("first_signal_at"), item.get("at", 0)))
                    current["last_signal_at"] = int(number(item.get("last_signal_at"), item.get("at", 0)))
                    current["signal_count"] = max(1, int(number(item.get("signal_count"), 1)))
                    grouped[episode] = current
                else:
                    current["signal_count"] += max(1, int(number(item.get("signal_count"), 1)))
                    current["last_signal_at"] = max(current["last_signal_at"], int(number(item.get("last_signal_at"), item.get("at", 0))))
                    current["reasons"] = list(dict.fromkeys((current.get("reasons") or []) + (item.get("reasons") or [])))
                    if item.get("evaluation_at", 0) > current.get("evaluation_at", 0):
                        for key in ("evaluation_at", "subsequent_market_return_pct", "missed_opportunity"):
                            if key in item:
                                current[key] = copy.deepcopy(item[key])
            episodes = list(grouped.values())
            episodes.sort(key=lambda item: (number(item.get("at")), str(item.get("episode"))))
            evaluated = sum(bool(item.get("evaluation_at")) for item in episodes)
            old_total = int(number(book.get("rejected_total"), signal_total))
            old_episode_total = int(number(book.get("rejected_market_episodes_total"), len(grouped)))
            old_evaluated = int(number(book.get("evaluated_rejected_paths_total"), evaluated))
            capped = episodes[-MAX_REJECTED_EPISODE_HISTORY:]
            if (len(records) != len(capped) or old_total != book.get("rejected_total")
                    or old_episode_total != book.get("rejected_market_episodes_total")
                    or old_evaluated != book.get("evaluated_rejected_paths_total")
                    or reasons != Counter(book.get("rejection_reason_counts") or {})):
                changed = True
            book["rejected"] = capped
            book["rejected_total"] = old_total
            book["rejected_market_episodes_total"] = old_episode_total
            book["evaluated_rejected_paths_total"] = old_evaluated
            book["rejection_reason_counts"] = dict(reasons)
        return changed

    def _index_rejected_signals(self):
        now = int(self.state.get("last_available_at", 0))
        self._rejected_episode_index = {}
        self._rejected_market_index = {}
        for name, book in self.state["books"].items():
            episode_index = {}
            market_index = {}
            for item in book.get("rejected", []):
                episode_index[item.get("episode")] = item
                if now and now - int(number(item.get("at"))) > self.config["episode_ms"]:
                    if not item.get("evaluation_at"):
                        item["missed_opportunity"] = "window_complete_no_matching_observation"
                        item["window_complete_at"] = now
                    continue
                market = (item.get("address"), item.get("pair"))
                market_index.setdefault(market, []).append(item)
            self._rejected_episode_index[name] = episode_index
            self._rejected_market_index[name] = market_index

    def _fresh(self):
        cash = self.config["initial_cash"]
        return {"version": VERSION, "paper_only": True, "config": self.config,
                "control_hash": digest(BASELINE), "last_available_at": 0,
                "seen_ids": [], "episodes": {}, "books": {
                    **{key: empty_book(key, params, cash) for key, params in HYPOTHESES.items()},
                    "LEARNER": empty_book("LEARNER", BASELINE, cash)},
                "active_version": "v0-control", "versions": [
                    {"id": "v0-control", "params": copy.deepcopy(BASELINE), "at": 0,
                     "status": "initial", "provenance": "predefined frozen baseline"}],
                "training": None, "last_training": None, "training_history": [], "monitor": None,
                "last_train_episode_count": 0, "simulations": 0,
                "invalid_observations": 0, "duplicate_observations": 0,
                "recording_drops_total": 0, "recording_drops_baseline": 0,
                "status": "WAIT", "updated_at": 0}

    def save(self):
        atomic_json(self.path, self.state)
        if time.monotonic() >= self._next_temp_sweep:
            self._next_temp_sweep = time.monotonic() + STALE_TEMP_SECONDS
            remove_stale_temp_files(self.path)

    def reset(self, initial_cash=None):
        """Explicit PAPER reset; no main/real account is read or written."""
        if initial_cash is not None:
            self.config = _config({**self.config, "initial_cash": initial_cash})
        self.state = self._fresh()
        self._seen_ids = set()
        self._index_post_exit_assessments()
        self._index_rejected_signals()
        self.save()
        return self.snapshot()

    def episode(self, row):
        coin = row["coin"]
        # Identity and event-time bucket cluster correlated repeated decisions.
        return "%s:%s:%d" % (coin["address"], coin["pairAddress"],
                              int(row["observed_at"]) // self.config["episode_ms"])

    def _row_valid(self, row):
        if not isinstance(row, dict):
            return False
        now = number(row.get("available_at"), -1)
        observed = number(row.get("observed_at"), -1)
        coin = row.get("coin") or {}
        return (now > 0 and 0 < observed <= now and now >= self.state["last_available_at"]
                and isinstance(coin, dict) and ADDRESS.fullmatch(str(coin.get("address") or ""))
                and ADDRESS.fullmatch(str(coin.get("pairAddress") or ""))
                and all(isinstance(row.get(key) or {}, dict) for key in ("safety", "flow", "execution", "context"))
                and isinstance(row.get("quotes") or [], list)
                and all(isinstance(q, dict) for q in row.get("quotes") or []))

    def _guards(self, row, *, entry=True):
        now = row["available_at"]
        coin = row["coin"]
        mint, pair = coin["address"], coin["pairAddress"]
        reasons = []
        if not 0 <= now - number(coin.get("updatedAt"), -1) <= self.config["feed_ttl_ms"]:
            reasons.append("stale_or_future_market")
        if number(coin.get("priceUsd")) <= 0 or number(coin.get("liquidityUsd")) < self.config["min_liquidity"]:
            reasons.append("price_or_liquidity")
        if entry:
            safety = row.get("safety") or {}
            if (safety.get("allowed") is not True or safety.get("mint") != mint or safety.get("pair") != pair
                    or not 0 <= now - number(safety.get("checked_at"), -1) <= self.config["evidence_ttl_ms"]):
                reasons.append("safety_unknown_or_rejected")
        return reasons

    def _flow_reasons(self, row, params):
        flow = row.get("flow") or {}
        now = row["available_at"]
        result = []
        if (flow.get("fresh") is not True or not 0 <= now - number(flow.get("latest_at"), -1) <= self.config["flow_ttl_ms"]):
            result.append("stale_or_unknown_flow")
        if (number(flow.get("trades")) < self.config["min_flow_trades"] or
                number(flow.get("unique_wallets")) < self.config["min_flow_wallets"] or
                number(flow.get("buy_usd")) < self.config["min_flow_buy_usd"]):
            result.append("insufficient_flow_evidence")
        ratio = number(flow.get("buy_sell_usd_ratio", flow.get("ratio")))
        if ratio < params["min_flow_ratio"]:
            result.append("flow_ratio")
        if number(row["coin"].get("score")) < params["min_score"]:
            result.append("score")
        if params.get("exit_policy") == "adaptive" and not self._adaptive_context(row):
            result.append("adaptive_context_unknown_or_future")
        return result

    def _adaptive_context(self, row):
        context = row.get("context") or {}
        if not 0 <= number(context.get("conviction"), math.nan) <= 100:
            return None
        if not 0 <= row["available_at"] - number(context.get("available_at", row["observed_at"]), -1) <= self.config["flow_ttl_ms"]:
            return None
        fast = context.get("fast_flow") or row.get("flow") or {}
        if not isinstance(fast, dict):
            return None
        if number(fast.get("latest_at", row["observed_at"]), -1) > row["available_at"]:
            return None
        return {**context, "fast_flow": fast}

    def _model(self, row, side, amount_raw):
        evidence = row.get("execution") or {}
        coin, now = row["coin"], row["available_at"]
        if evidence.get("mode") != MODEL:
            return None
        if (evidence.get("mint") != coin["address"] or evidence.get("pair") != coin["pairAddress"]
                or evidence.get("price_verified") is not True
                or evidence.get(side + "_route") is not True
                or not 0 <= now - number(evidence.get("verified_at"), -1) <= self.config["evidence_ttl_ms"]
                or self._guards(row, entry=False)):
            return None
        decimals = evidence.get("decimals")
        fee_value = evidence.get("entry_dex_fee_bps" if side == "buy" else "exit_dex_fee_bps",
                                 evidence.get("dex_fee_bps"))
        network_value = evidence.get("entry_network_fee_usd" if side == "buy" else "exit_network_fee_usd",
                                     evidence.get("network_fee_usd"))
        reserve_value = evidence.get("entry_account_reserve_usd") if side == "buy" else 0.0
        # Imported model assumptions follow the same fail-closed cost contract
        # as recorded quotes. An omitted reserve or False is not proof of a
        # pre-existing token account, and a boolean is never a fee amount.
        fee_bps, network, reserve = (number(value, math.nan)
                                     for value in (fee_value, network_value, reserve_value))
        if (not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 18
                or any(isinstance(value, bool) for value in (fee_value, network_value, reserve_value))
                or not 0 <= fee_bps <= 500 or not 0 <= network <= 5 or not 0 <= reserve <= 5):
            return None
        price = number(coin["priceUsd"])
        usd_reserve = number(coin["liquidityUsd"]) / 2
        token_reserve = usd_reserve / price
        qty = amount_raw / (1e6 if side == "buy" else 10 ** decimals)
        after_fee = qty * (1 - fee_bps / 10000)
        denominator = usd_reserve if side == "buy" else token_reserve
        reserve_out = token_reserve if side == "buy" else usd_reserve
        gross = reserve_out * after_fee / (denominator + after_fee)
        output = gross * (1 - self.config["adverse_slippage_bps"] / 10000)
        scale_out = 10 ** decimals if side == "buy" else 1e6
        output_raw = math.floor(output * scale_out)
        impact = after_fee / (denominator + after_fee) * 100
        if output_raw <= 0 or impact > self.config["max_impact_pct"]:
            return None
        return {"model": MODEL, "output_raw": output_raw, "decimals": decimals,
                "input_raw": amount_raw, "quoted_at": now, "available_at": now,
                "network_fee_usd": network, "impact_pct": impact,
                "entry_account_reserve_usd": reserve,
                "dex_fee_bps": fee_bps, "dex_fee_input": qty - after_fee,
                "adverse_slippage_bps": self.config["adverse_slippage_bps"],
                "liquidity_usd": usd_reserve * 2, "market_price": price,
                "verified_at": evidence["verified_at"], "fee_included": True,
                "provenance": "estimated symmetric reserves from validated recorded market; not an observed fill"}

    def _execution(self, row, side, amount_raw, earliest=None):
        now = row["available_at"]
        if earliest is not None and now < earliest:
            return None
        evidence = row.get("execution") or {}
        if evidence.get("mode") == MODEL:
            # Merely re-recording an old scanner snapshot at a later wall clock
            # does not create a fresh post-latency execution observation.
            if earliest is not None and number(row["coin"].get("updatedAt"), -1) < earliest:
                return None
            return self._model(row, side, amount_raw)
        # A more recent explicit no-route result overrides an older positive quote.
        if evidence.get(side + "_route") is False:
            return None
        for q in row.get("quotes") or []:
            try:
                at = int(q["quoted_at"])
                available = int(q["available_at"])
                output, floor = raw_int(q["output_raw"]), raw_int(q["floor_raw"])
                decimals = q["decimals"]
                if (q.get("side") != side or q.get("mint") != row["coin"]["address"]
                        or q.get("pair") != row["coin"]["pairAddress"] or raw_int(q["input_raw"]) != amount_raw
                        or not 0 <= now - at <= self.config["quote_ttl_ms"] or not at <= available <= now
                        or earliest is not None and (at < earliest or available < earliest)
                        or not 0 < floor <= output or q.get("route") is not True and not isinstance(q.get("route"), list)
                        or not q.get("route") or q.get("fee_included") is not True
                        or not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 18
                        or not 0 <= number(q.get("price_impact_pct"), math.inf) <= self.config["max_impact_pct"]
                        or isinstance(q.get("network_fee_usd"),bool)
                        or not 0 <= number(q.get("network_fee_usd"), math.inf) <= 5
                        or side == "buy" and (isinstance(q.get("entry_account_reserve_usd"),bool)
                                              or not 0<=number(q.get("entry_account_reserve_usd"), math.inf)<=5)):
                    continue
                # Canonical quotes from normalize_quote additionally carry exact mint evidence.
                if "input_mint" in q and (q["input_mint"] != (USDC if side == "buy" else q["mint"])
                                          or q.get("output_mint") != (q["mint"] if side == "buy" else USDC)
                                          or q.get("swap_mode") != "ExactIn"):
                    continue
                if isinstance(q.get("route"), list):
                    token_legs = [leg["swapInfo"] for leg in q["route"]
                                  if isinstance(leg, dict) and isinstance(leg.get("swapInfo"), dict) and q["mint"] in
                                  (leg["swapInfo"].get("inputMint"), leg["swapInfo"].get("outputMint"))]
                    if not token_legs or side == "buy" and any(leg.get("ammKey") != q["pair"] for leg in token_legs):
                        continue
                return {**copy.deepcopy(q), "model": "RECORDED_QUOTE_FLOOR", "output_raw": floor,
                        "impact_pct": number(q["price_impact_pct"]), "network_fee_usd": number(q["network_fee_usd"]),
                        "entry_account_reserve_usd": number(q["entry_account_reserve_usd"]) if side == "buy" else 0.0,
                        "quoted_at": at, "available_at": available,
                        "provenance": "recorded quote lower output bound; simulated fill"}
            except (TypeError, ValueError, KeyError, OverflowError):
                continue
        return None

    def _liquidation(self, row, position):
        if self._guards(row, entry=False):
            return None
        q = self._execution(row, "sell", position["token_raw"])
        if not q or q["decimals"] != position["decimals"]:
            return None
        return {**q, "net_usd": max(0.0, q["output_raw"] / 1e6 - q["network_fee_usd"])}

    def _equity(self, book):
        return book["cash"] + sum(p.get("mark_usd", 0.0) for p in book["positions"].values())

    def _risk(self, book, now):
        equity = self._equity(book)
        book["peak_equity"] = max(book["peak_equity"], equity)
        drawdown = 1 - equity / max(book["peak_equity"], 1e-9)
        book["max_drawdown"] = max(book["max_drawdown"], drawdown)
        day = int(now) // 86400000
        if book["day"] != day:
            book["day"], book["day_start_equity"] = day, equity
        reason = None
        if equity <= book["day_start_equity"] * (1 - self.config["daily_loss_fraction"]):
            reason = "daily_loss_including_unliquidatable_positions"
        if drawdown >= self.config["max_drawdown_fraction"]:
            reason = "drawdown"
        book["halt_reason"] = reason

    def _reject(self, book, row, episode, reasons):
        # Count every rejected signal, but store one path per market episode.
        # Full individual decisions remain in the append-only observation journal.
        book["rejected_total"] = int(book.get("rejected_total", 0)) + 1
        counts = Counter(book.get("rejection_reason_counts") or {})
        counts.update(reasons)
        book["rejection_reason_counts"] = dict(counts)
        episode_index = self._rejected_episode_index.setdefault(book["id"], {})
        existing = episode_index.get(episode)
        stamp = int(row["available_at"])
        price = number(row["coin"].get("priceUsd"))
        if existing is not None:
            existing["signal_count"] = int(existing.get("signal_count", 1)) + 1
            existing["last_signal_at"] = stamp
            existing["last_signal_price"] = price
            existing["reasons"] = list(dict.fromkeys((existing.get("reasons") or []) + reasons))
            return
        book["rejected_market_episodes_total"] = int(book.get("rejected_market_episodes_total", 0)) + 1
        rejected = {"id": row["id"], "at": stamp, "episode": episode,
                    "first_signal_at": stamp, "last_signal_at": stamp, "signal_count": 1,
                    "address": row["coin"]["address"], "pair": row["coin"]["pairAddress"],
                    "reasons": reasons, "params": copy.deepcopy(book["params"]),
                    "decision_features": copy.deepcopy(row.get("flow") or {}),
                    "execution_evidence": copy.deepcopy(row.get("execution") or {}),
                    "price": price, "score": number(row["coin"].get("score")),
                    "outcome": "not_executed", "missed_opportunity": "unknown_until_future_observation"}
        book["rejected"].append(rejected)
        episode_index[episode] = rejected
        market_index = self._rejected_market_index.setdefault(book["id"], {}).setdefault(
            (rejected["address"], rejected["pair"]), [])
        market_index.append(rejected)
        if len(book["rejected"]) > MAX_REJECTED_EPISODE_HISTORY:
            expired = book["rejected"].pop(0)
            episode_index.pop(expired.get("episode"), None)
            market = (expired.get("address"), expired.get("pair"))
            active = self._rejected_market_index[book["id"]].get(market, [])
            self._rejected_market_index[book["id"]][market] = [item for item in active if item is not expired]

    def _assess_rejected_paths(self, book, row):
        now = int(row["available_at"])
        market = (row["coin"]["address"], row["coin"]["pairAddress"])
        indexed = self._rejected_market_index.get(book["id"], {})
        records = indexed.get(market, [])
        if not records:
            return
        active = []
        price = number(row["coin"].get("priceUsd"))
        for rejected in records:
            if rejected["at"] >= now:
                active.append(rejected)
                continue
            if now - rejected["at"] > self.config["episode_ms"]:
                if not rejected.get("evaluation_at"):
                    rejected["missed_opportunity"] = "window_complete_no_matching_observation"
                    rejected["window_complete_at"] = now
                continue
            if price > 0:
                rejected["subsequent_market_return_pct"] = (price / rejected["price"] - 1) * 100
                rejected["evaluation_at"] = now
                rejected["latest_observed_price"] = price
                rejected["missed_opportunity"] = "market_path_only_not_executable_profit"
                if not rejected.get("path_evaluated"):
                    rejected["path_evaluated"] = True
                    book["evaluated_rejected_paths_total"] = int(book.get("evaluated_rejected_paths_total", 0)) + 1
            active.append(rejected)
        if active:
            indexed[market] = active
        else:
            indexed.pop(market, None)

    def _failure(self, book, pending, now, reason, position=None):
        # Explicit assumed retry overhead, never represented as a known paid fee.
        fee = min(book["cash"], self.config["failed_attempt_fee_usd"])
        book["cash"] -= fee
        event = {"at": now, "episode": pending["episode"], "reason": reason,
                 "side": pending["side"], "estimated_fee_usd": fee,
                 "fee_provenance": "configured hypothetical failed-attempt cost",
                 "decision_at": pending["decision_at"], "address": pending["address"]}
        book["failed"].append(event)
        if position is not None:
            position["failed_exit_fees_usd"] += fee
            position["no_route_attempts"] += 1

    def _open(self, book, row, pending, q):
        committed = self.config["notional"] + q["network_fee_usd"] + number(q.get("entry_account_reserve_usd"))
        gross_exposure = sum(p["committed_usd"] for p in book["positions"].values())
        if (committed > book["cash"] or committed > book["initial_cash"] * self.config["max_position_fraction"]
                or gross_exposure + committed > book["initial_cash"] * self.config["max_exposure_fraction"]):
            self._failure(book, pending, row["available_at"], "capital_changed")
            return
        preview = self._execution(row, "sell", q["output_raw"])
        # Estimate mode can preflight the identical model quantity. Quote mode
        # needs a recorded exact quantity sell quote, never proportional scaling.
        if not preview or preview["decimals"] != q["decimals"]:
            self._failure(book, pending, row["available_at"], "no_sell_preflight")
            return
        liquidation = max(0.0, preview["output_raw"] / 1e6 - preview["network_fee_usd"])
        roundtrip = (liquidation / committed - 1) * 100
        if not -self.config["max_roundtrip_cost_pct"] <= roundtrip <= 0:
            self._failure(book, pending, row["available_at"], "roundtrip_cost_or_quote_inconsistency")
            return
        book["cash"] -= committed
        params = copy.deepcopy(pending["params"])
        position = {"address": pending["address"], "pair": row["coin"]["pairAddress"],
                    "symbol": row["coin"].get("symbol", "?"), "episode": pending["episode"],
                    "decision_at": pending["decision_at"], "opened_at": row["available_at"],
                    "entry_observation_id": row["id"], "token_raw": q["output_raw"],
                    "decimals": q["decimals"], "committed_usd": committed,
                    "entry": q, "params": params, "active_version": pending["active_version"],
                    "decision_features": pending["features"], "decision_reasons": pending["reasons"],
                    "mark_usd": liquidation, "marked_at": row["available_at"], "valuation": "fresh",
                    "mfe_pct": roundtrip, "mae_pct": roundtrip,
                    "failed_exit_fees_usd": 0.0, "no_route_attempts": 0,
                    "peak_net_pct": roundtrip, "planned_stop_pct": params["stop_pct"]}
        book["positions"][pending["address"]] = position
        self.state["simulations"] += 1

    def _close(self, book, row, pending, position, q):
        net = max(0.0, q["output_raw"] / 1e6 - q["network_fee_usd"])
        pnl = net - position["committed_usd"] - position["failed_exit_fees_usd"]
        book["cash"] += net
        trade = {**copy.deepcopy(position), "closed_at": row["available_at"],
                 "exit_decision_at": pending["decision_at"], "exit_observation_id": row["id"],
                 "exit": q, "exit_reason": pending["reason"], "pnl_usd": pnl,
                 "pnl_pct": pnl / position["committed_usd"] * 100,
                 "exit_analysis": {"mfe_minus_realized_pct": position["mfe_pct"] - pnl / position["committed_usd"] * 100,
                                   "late_or_early": "awaiting_retrospective_post_exit_evidence",
                                   "stop_gap_pct": max(0.0, -pnl / position["committed_usd"] * 100 - position["planned_stop_pct"]),
                                   "post_exit": {
                                       "kind": "RETROSPECTIVE_EXECUTABLE_MARK_COMPARISON",
                                       "not_executed": True, "decision_input": False,
                                       "window_ends_at": row["available_at"] + self.config["post_exit_window_ms"],
                                       "status": "awaiting_subsequent_observation",
                                       "valid_observations": 0, "unavailable_observations": 0,
                                       "latest": None, "best": None, "worst": None,
                                       "limitations": "same raw inventory, recorded market and modeled costs only; holding capital, market impact from our own trade and future optimum are unknown"}}}
        book["trades"].append(trade)
        self._post_exit_pending.setdefault(book["id"], set()).add(len(book["trades"]) - 1)
        del book["positions"][position["address"]]
        book["cooldowns"][position["address"]] = row["available_at"] + self.config["cooldown_ms"]

    def _assess_post_exit(self, book, row):
        """Append available retrospective evidence without changing trade returns.

        A later executable mark is a sensitivity comparison for the original
        raw inventory, not a counterfactual executed portfolio. This module's
        decisions, candidate scoring and approval use actual simulated ledgers,
        never these later marks. Unknown liquidation stays unknown, not zero.
        """
        now, coin = row["available_at"], row["coin"]
        pending = self._post_exit_pending.get(book["id"], set())
        for index in list(pending):
            trade = book["trades"][index]
            analysis = trade.get("exit_analysis") or {}
            assessment = analysis.get("post_exit")
            if not assessment:
                # Existing saved trade histories remain readable. Historical
                # analysis is not reconstructed from unobserved later prices.
                pending.discard(index)
                continue
            if now > assessment["window_ends_at"]:
                if not assessment["status"].startswith("window_complete"):
                    assessment["status"] = ("window_complete" if assessment["valid_observations"]
                                             else "window_complete_without_executable_evidence")
                    assessment["completed_at"] = now
                pending.discard(index)
                continue
            if (now <= trade["closed_at"] or row["observed_at"] <= trade["closed_at"]
                    or coin["address"] != trade["address"] or coin["pairAddress"] != trade["pair"]):
                continue
            reasons = self._guards(row, entry=False)
            q = None if reasons else self._execution(row, "sell", trade["token_raw"], trade["closed_at"] + 1)
            if q and q["decimals"] != trade["decimals"]:
                reasons.append("token_decimals_changed")
                q = None
            assessment["last_observed_at"] = now
            if not q:
                assessment["unavailable_observations"] += 1
                assessment["status"] = "subsequent_liquidation_unavailable"
                assessment["latest"] = {"observation_id": row["id"], "available_at": now,
                                         "valuation": "unknown", "delta_to_executed_exit_usd": None,
                                         "reasons": reasons or ["no_fresh_matching_sell_evidence_after_exit"]}
                continue
            proceeds = max(0.0, q["output_raw"] / 1e6 - q["network_fee_usd"])
            alternative_pnl = proceeds - trade["committed_usd"] - trade["failed_exit_fees_usd"]
            sample = {"observation_id": row["id"], "available_at": now,
                      "observed_at": row["observed_at"], "market_updated_at": coin.get("updatedAt"),
                      "valuation": "executable_estimate" if q["model"] == MODEL else "recorded_quote_bound",
                      "hypothetical_net_proceeds_usd": proceeds,
                      "hypothetical_pnl_usd": alternative_pnl,
                      "delta_to_executed_exit_usd": alternative_pnl - trade["pnl_usd"],
                      "execution": copy.deepcopy(q), "not_executed": True}
            assessment["valid_observations"] += 1
            assessment["status"], assessment["latest"] = "observing_retrospective_window", sample
            if assessment["best"] is None or sample["delta_to_executed_exit_usd"] > assessment["best"]["delta_to_executed_exit_usd"]:
                assessment["best"] = copy.deepcopy(sample)
            if assessment["worst"] is None or sample["delta_to_executed_exit_usd"] < assessment["worst"]["delta_to_executed_exit_usd"]:
                assessment["worst"] = copy.deepcopy(sample)
            analysis["late_or_early"] = ("later_better_liquidation_observed_retrospectively"
                                         if assessment["best"]["delta_to_executed_exit_usd"] > 0
                                         else "executed_exit_not_improved_by_observed_later_marks")

    def _step_book(self, book, row, episode):
        now = row["available_at"]
        mint = row["coin"]["address"]
        book["observations"] += 1
        # A token disappearing from the feed must not reserve an entry forever.
        # Advance outstanding order timeouts on the shared stream clock even
        # when the next observation concerns a different token/pool.
        for pending_mint, old_pending in list(book["pending"].items()):
            if pending_mint == mint or now - old_pending["decision_at"] < self.config["order_timeout_ms"]:
                continue
            old_position = book["positions"].get(pending_mint)
            self._failure(book, old_pending, now, "no_matching_market_observation_after_latency", old_position)
            del book["pending"][pending_mint]
            if old_pending["side"] == "buy":
                book["cooldowns"][pending_mint] = now + self.config["cooldown_ms"]
        # Mark every other-token position unknown once its last quote expires.
        for p in book["positions"].values():
            if now - p["marked_at"] > self.config["feed_ttl_ms"]:
                p["mark_usd"], p["valuation"] = 0.0, "stale_unliquidatable_conservative_zero"
                if p["address"] not in book["pending"]:
                    book["pending"][p["address"]] = {
                        "side": "sell", "address": p["address"], "pair": p["pair"],
                        "decision_at": now, "due_at": now+self.config["latency_ms"],
                        "episode": p["episode"], "reason": "STALE_MARKET_LIQUIDATION_UNKNOWN"}
        p = book["positions"].get(mint)
        if p and p["pair"] == row["coin"]["pairAddress"]:
            mark = self._liquidation(row, p)
            if mark:
                p.update(mark_usd=mark["net_usd"], marked_at=now, valuation="fresh")
                pct = (p["mark_usd"] - p["committed_usd"] - p["failed_exit_fees_usd"]) / p["committed_usd"] * 100
                p["mfe_pct"] = max(p["mfe_pct"], pct)
                p["mae_pct"] = min(p["mae_pct"], pct)
                p["peak_net_pct"] = max(p["peak_net_pct"], pct)
            else:
                p.update(mark_usd=0.0, marked_at=now, valuation="no_current_sell_route_conservative_zero")
        self._risk(book, now)
        # Fill only a previously committed decision, after modelled latency.
        pending = book["pending"].get(mint)
        if pending:
            if pending["pair"] != row["coin"]["pairAddress"]:
                return
            amount = int(self.config["notional"] * 1e6) if pending["side"] == "buy" else p["token_raw"] if p else 0
            q = self._execution(row, pending["side"], amount, pending["due_at"])
            if q and now > pending["decision_at"]:
                if pending["side"] == "buy":
                    reasons = self._guards(row) + self._flow_reasons(row, pending["params"])
                    if not reasons and not book["halt_reason"]:
                        self._open(book, row, pending, q)
                    else:
                        self._failure(book, pending, now, "entry_evidence_changed:" + ",".join(reasons + ([book["halt_reason"]] if book["halt_reason"] else [])))
                elif p and q["decimals"] == p["decimals"]:
                    self._close(book, row, pending, p, q)
                del book["pending"][mint]
                self._risk(book, now)
                return
            if now - pending["decision_at"] >= self.config["order_timeout_ms"]:
                self._failure(book, pending, now, "no_executable_quote_after_latency", p)
                del book["pending"][mint]
                if pending["side"] == "buy":
                    book["cooldowns"][mint] = now + self.config["cooldown_ms"]
                self._risk(book, now)
            else:
                return
        p = book["positions"].get(mint)
        if p:
            if p["pair"] != row["coin"]["pairAddress"]:
                return
            params = p["params"]
            pct = (p["mark_usd"] - p["committed_usd"] - p["failed_exit_fees_usd"]) / p["committed_usd"] * 100
            reason = None
            if pct <= -params["stop_pct"]:
                reason = "STOP_NET" if p["valuation"] == "fresh" else "LIQUIDATION_UNKNOWN"
            elif pct >= params["take_profit_pct"]:
                reason = "TAKE_PROFIT_NET"
            elif params["trailing_pct"] and p["peak_net_pct"] >= params["trailing_pct"] and pct <= p["peak_net_pct"] - params["trailing_pct"]:
                reason = "TRAILING_NET"
            elif now - p["opened_at"] >= params["max_hold_ms"]:
                reason = "MAX_HOLD"
            if params.get("exit_policy") == "adaptive":
                context = self._adaptive_context(row)
                if context:
                    reason = adaptive_exit_reason(p, context, net_pct=pct,
                                                  peak_net_pct=p["peak_net_pct"],
                                                  hold_minutes=(now-p["opened_at"])/60000,
                                                  stop_pct=params["stop_pct"],
                                                  take_profit_pct=params["take_profit_pct"], policy="adaptive")
                # Missing context cannot waive net stop or maximum holding time.
            if reason:
                book["pending"][mint] = {"side": "sell", "address": mint, "pair": p["pair"],
                                           "decision_at": now, "due_at": now + self.config["latency_ms"],
                                           "episode": p["episode"], "reason": reason}
            return
        reasons = self._guards(row) + self._flow_reasons(row, book["params"])
        if book["halt_reason"]:
            reasons.append(book["halt_reason"])
        if now < book["cooldowns"].get(mint, 0):
            reasons.append("cooldown")
        if len(book["positions"]) + len(book["pending"]) >= self.config["max_positions"]:
            reasons.append("position_limit")
        committed = sum(p["committed_usd"] for p in book["positions"].values())
        # Bound both an entry's network fee and account reserve; the per-position
        # full-loss cap still limits the total commitment when its quote arrives.
        maximum_commitment = min(self.config["notional"] + 10, book["initial_cash"] * self.config["max_position_fraction"])
        reserved = sum(maximum_commitment for p in book["pending"].values() if p["side"] == "buy")
        if committed + reserved + maximum_commitment > book["initial_cash"] * self.config["max_exposure_fraction"]:
            reasons.append("full_loss_exposure_limit")
        if book["cash"] - reserved < maximum_commitment:
            reasons.append("capital")
        preview = self._execution(row, "buy", int(self.config["notional"] * 1e6))
        if not preview:
            reasons.append("missing_executable_buy_evidence")
        else:
            preview_committed = self.config["notional"]+preview["network_fee_usd"]+number(preview.get("entry_account_reserve_usd"))
            sell_preview = self._execution(row,"sell",preview["output_raw"])
            if not sell_preview or sell_preview["decimals"] != preview["decimals"]:
                reasons.append("missing_executable_sell_preflight")
            else:
                preview_net=max(0,sell_preview["output_raw"]/1e6-sell_preview["network_fee_usd"])
                preview_pct=(preview_net/preview_committed-1)*100
                if not -self.config["max_roundtrip_cost_pct"]<=preview_pct<=0:
                    reasons.append("roundtrip_cost")
            if preview_committed>book["initial_cash"]*self.config["max_position_fraction"]:
                reasons.append("full_loss_position_limit")
        if reasons:
            self._reject(book, row, episode, list(dict.fromkeys(reasons)))
            return
        book["pending"][mint] = {"side": "buy", "address": mint, "pair": row["coin"]["pairAddress"],
                                   "decision_at": now, "due_at": now + self.config["latency_ms"],
                                   "episode": episode, "params": copy.deepcopy(book["params"]),
                                   "features": {"coin": copy.deepcopy(row["coin"]), "flow": copy.deepcopy(row.get("flow"))},
                                   "reasons": ["safe_fresh_flow", "capital_and_execution_evidence"],
                                   "active_version": self.state["active_version"] if book["id"] == "LEARNER" else book["id"]}

    def stats(self, book):
        trades = book["trades"]
        equity = self._equity(book)
        episodes = {t["episode"] for t in trades}
        wins = [t["pnl_usd"] for t in trades if t["pnl_usd"] > 0]
        losses = [t["pnl_usd"] for t in trades if t["pnl_usd"] < 0]
        gross_profit, gross_loss = sum(wins), -sum(losses)
        return {"id": book["id"], "params": copy.deepcopy(book["params"]),
                "starting_balance": book["initial_cash"], "cash": book["cash"],
                "equity": equity, "net_pnl_usd": equity - book["initial_cash"],
                "return_pct": (equity / book["initial_cash"] - 1) * 100,
                "realized_pnl_usd": sum(t["pnl_usd"] for t in trades),
                "unrealized_pnl_usd": sum(p["mark_usd"] - p["committed_usd"] - p["failed_exit_fees_usd"] for p in book["positions"].values()),
                "completed_trades": len(trades), "open_positions": len(book["positions"]),
                "pending_orders": len(book["pending"]), "unique_completed_episodes": len(episodes),
                "wins": sum(t["pnl_usd"] > 0 for t in trades),
                "losses": sum(t["pnl_usd"] < 0 for t in trades),
                "breakeven": sum(t["pnl_usd"] == 0 for t in trades),
                "gross_profit_usd": gross_profit, "gross_loss_usd": gross_loss,
                "profit_factor": gross_profit/gross_loss if gross_loss else None,
                "profit_factor_status": "finite" if gross_loss else "undefined_without_losses",
                "average_win_usd": gross_profit/len(wins) if wins else None,
                "average_loss_usd": -gross_loss/len(losses) if losses else None,
                "mean_pnl_usd": sum(t["pnl_usd"] for t in trades)/len(trades) if trades else None,
                "average_hold_seconds": sum((t["closed_at"]-t["opened_at"])/1000 for t in trades)/len(trades) if trades else None,
                "max_drawdown_pct": book["max_drawdown"] * 100,
                "failed_executions": len(book["failed"]),
                "estimated_failed_fees_usd": sum(t["estimated_fee_usd"] for t in book["failed"]),
                "feasibility": len(trades) / (len(trades) + len(book["failed"])) if trades or book["failed"] else None,
                "rejection_reasons": dict(book.get("rejection_reason_counts") or Counter(
                    r for item in book["rejected"] for r in item["reasons"])),
                "rejected_signals": int(book.get("rejected_total", len(book["rejected"]))),
                "rejected_market_episodes": int(book.get("rejected_market_episodes_total", len(book["rejected"]))),
                "evaluated_rejected_paths": int(book.get("evaluated_rejected_paths_total",
                    sum(bool(r.get("evaluation_at")) for r in book["rejected"]))),
                "recent_rejected_signals": copy.deepcopy(book["rejected"][-10:]),
                "recent_failed_executions": copy.deepcopy(book["failed"][-10:]),
                "valuation_unknown_positions": sum(p["valuation"] != "fresh" for p in book["positions"].values()),
                "risk_halt": book["halt_reason"], "positions": list(copy.deepcopy(book["positions"]).values()),
                "recent_trades": copy.deepcopy(trades[-20:]), "total_history_retained": len(trades)}

    def _completed_episodes(self):
        return {t["episode"] for name, b in self.state["books"].items() if name in HYPOTHESES
                for t in b["trades"]}

    def _train(self, now):
        episodes = self._completed_episodes()
        if (len(episodes) < self.config["min_train_episodes"] or
                len(episodes) - self.state["last_train_episode_count"] < self.config["train_every_episodes"]):
            return
        self.state["last_train_episode_count"] = len(episodes)
        stats = {name: self.stats(b) for name, b in self.state["books"].items() if name in HYPOTHESES}
        # Failed executions and unresolved positions affect equity/risk. Do not
        # optimise just a wins-only journal or drop stranded losers.
        active_params = self.state["books"]["LEARNER"]["params"]
        active_comparison = max([stats["CONTROL"]["net_pnl_usd"]] +
                                [s["net_pnl_usd"] for name, s in stats.items() if HYPOTHESES[name] == active_params])
        eligible = [s for name, s in stats.items() if name != "CONTROL" and HYPOTHESES[name] != active_params
                    and s["unique_completed_episodes"] >= self.config["min_train_episodes"]
                    and s["feasibility"] is not None and s["feasibility"] >= self.config["min_feasibility"]
                    and s["max_drawdown_pct"] <= self.config["max_drawdown_fraction"] * 100
                    and s["net_pnl_usd"] > active_comparison + self.config["min_improvement_usd"]]
        event = {"at": now, "train_cutoff": now, "unique_train_episodes": len(episodes),
                 "candidate_count": len(HYPOTHESES) - 1, "train_comparison": stats,
                 "status": "insufficient_evidence_or_no_better_candidate"}
        if eligible:
            selected = max(eligible, key=lambda s: (s["net_pnl_usd"], -s["max_drawdown_pct"]))
            cash = self.config["initial_cash"]
            candidate = selected["id"]
            self.state["books"]["VALIDATE_CANDIDATE"] = empty_book("VALIDATE_CANDIDATE", HYPOTHESES[candidate], cash)
            self.state["books"]["VALIDATE_CONTROL"] = empty_book("VALIDATE_CONTROL", BASELINE, cash)
            self._post_exit_pending["VALIDATE_CANDIDATE"] = set()
            self._post_exit_pending["VALIDATE_CONTROL"] = set()
            self.state["training"] = {"candidate": candidate, "params": copy.deepcopy(HYPOTHESES[candidate]),
                                       "selected_at": now, "start_after": now + self.config["embargo_ms"],
                                       "train_episode_ids": sorted(self.state["episodes"]),
                                       "validation_episode_ids": [], "status": "awaiting_future_validation"}
            event.update(status="candidate_frozen_awaiting_future_validation", candidate=candidate)
        self.state["last_training"] = event
        self.state["training_history"].append(copy.deepcopy(event))

    def _compare(self, candidate, control):
        cs, bs = self.stats(candidate), self.stats(control)
        pairs = {}
        days = {}
        for sign, book in ((1, candidate), (-1, control)):
            for trade in book["trades"]:
                pairs[trade["episode"]] = pairs.get(trade["episode"], 0.0) + sign * trade["pnl_usd"]
                day = str(trade["closed_at"] // 86400000)
                days[day] = days.get(day, 0.0) + sign * trade["pnl_usd"]
            for failed in book["failed"]:
                # Closed trade pnl already includes position exit failures.
                if failed["side"] == "buy":
                    pairs[failed["episode"]] = pairs.get(failed["episode"], 0.0) - sign * failed["estimated_fee_usd"]
                    day = str(failed["at"] // 86400000)
                    days[day] = days.get(day, 0.0) - sign * failed["estimated_fee_usd"]
        values = list(pairs.values())
        mean = sum(values) / len(values) if values else 0.0
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1) if len(values) > 1 else None
        margin = 1.96 * math.sqrt(variance / len(values)) if variance is not None else None
        ci = [mean - margin, mean + margin] if margin is not None else None
        day_values = list(days.values())
        day_mean = sum(day_values)/len(day_values) if day_values else 0.0
        day_variance = sum((v-day_mean)**2 for v in day_values)/(len(day_values)-1) if len(day_values)>1 else None
        day_margin = 1.96 * math.sqrt(day_variance/len(day_values)) if day_variance is not None else None
        day_ci = [day_mean-day_margin, day_mean+day_margin] if day_margin is not None else None
        stress = sum(t["committed_usd"] * self.config["stress_extra_cost_bps"] / 10000 * 2 for t in candidate["trades"])
        return {"candidate": cs, "control": bs, "net_improvement_usd": cs["net_pnl_usd"] - bs["net_pnl_usd"],
                "paired_episode_count": len(values), "paired_mean_improvement_usd": mean,
                "matched_episode_union_count": len(values), "calendar_day_cluster_count": len(day_values),
                "calendar_day_mean_improvement_usd": day_mean, "approximate_day_block_95_ci": day_ci,
                "approximate_cluster_mean_95_ci": ci,
                "interval_limitations": "normal approximations on token/pool/time clusters and calendar-day blocks; cross-day regimes and candidate selection uncertainty remain",
                "stress_net_pnl_usd": cs["net_pnl_usd"] - stress,
                "stress_assumption": "additional fixed adverse roundtrip cost, not a forecast"}

    def _validate(self, now):
        trial = self.state["training"]
        if not trial:
            return
        candidate = self.state["books"]["VALIDATE_CANDIDATE"]
        control = self.state["books"]["VALIDATE_CONTROL"]
        result = self._compare(candidate, control)
        enough = (result["paired_episode_count"] >= self.config["min_validation_episodes"]
                  and len(candidate["trades"]) >= self.config["min_validation_trades"]
                  and result["calendar_day_cluster_count"] >= self.config["min_validation_days"])
        unresolved = bool(candidate["positions"] or candidate["pending"] or control["positions"] or control["pending"])
        # A stranded holding must neither be called successful nor freeze the
        # periodic learner forever after the closed-evidence threshold is met.
        # Retain its conservative valuation and the full trial in history.
        if now - trial["selected_at"] > self.config["validation_max_ms"]:
            trial["status"] = "expired_unresolved_positions" if unresolved else "expired_validation_window"
            if not enough and not unresolved:
                trial["status"] = "expired_insufficient_evidence"
            self.state["last_training"].update(status=trial["status"], validation=result,
                                               validation_start=trial["start_after"], validation_end=now,
                                               rejected_checks=["validation_window_expired"])
            self.state["training_history"][-1] = copy.deepcopy(self.state["last_training"])
            self.state["training"] = None
            return
        if not enough or unresolved:
            return
        ci = result["approximate_cluster_mean_95_ci"]
        reasons = []
        if self.state["recording_drops_total"]:
            reasons.append("incomplete_recording_coverage")
        if result["candidate"]["net_pnl_usd"] <= self.config["min_validation_net_usd"]:
            reasons.append("nonpositive_net_result")
        if result["net_improvement_usd"] < self.config["min_improvement_usd"]:
            reasons.append("no_independent_improvement")
        if result["candidate"]["max_drawdown_pct"] > self.config["max_drawdown_fraction"] * 100:
            reasons.append("drawdown")
        if (result["candidate"]["feasibility"] or 0) < self.config["min_feasibility"]:
            reasons.append("feasibility")
        if result["stress_net_pnl_usd"] <= 0:
            reasons.append("adverse_cost_stress")
        if self.config["require_positive_ci"] and (not ci or ci[0] <= 0):
            reasons.append("insufficient_cluster_interval")
        day_ci = result["approximate_day_block_95_ci"]
        if self.config["require_positive_ci"] and (not day_ci or day_ci[0] <= 0):
            reasons.append("insufficient_day_block_interval")
        self.state["last_training"].update(validation=result,
                                           validation_start=trial["start_after"], validation_end=now,
                                           rejected_checks=reasons,
                                           status="rejected_future_validation" if reasons else "approved_isolated_paper")
        self.state["training_history"][-1] = copy.deepcopy(self.state["last_training"])
        if not reasons:
            previous = self.state["active_version"]
            version = "v%d-%s" % (len(self.state["versions"]), trial["candidate"].lower())
            self.state["versions"].append({"id": version, "params": trial["params"], "at": now,
                                           "status": "approved_isolated_paper", "previous": previous,
                                           "train_cutoff": trial["selected_at"], "validation": result})
            self.state["active_version"] = version
            self.state["books"]["LEARNER"]["params"] = copy.deepcopy(trial["params"])
            cash = self.config["initial_cash"]
            self.state["books"]["MONITOR_CANDIDATE"] = empty_book("MONITOR_CANDIDATE", trial["params"], cash)
            self.state["books"]["MONITOR_CONTROL"] = empty_book("MONITOR_CONTROL", BASELINE, cash)
            self._post_exit_pending["MONITOR_CANDIDATE"] = set()
            self._post_exit_pending["MONITOR_CONTROL"] = set()
            self.state["monitor"] = {"version": version, "previous": previous, "started_at": now}
        self.state["training"] = None

    def _rollback(self, now):
        monitor = self.state["monitor"]
        if not monitor:
            return
        comparison = self._compare(self.state["books"]["MONITOR_CANDIDATE"], self.state["books"]["MONITOR_CONTROL"])
        if comparison["paired_episode_count"] < self.config["rollback_min_episodes"]:
            return
        if (comparison["net_improvement_usd"] <= -self.config["rollback_underperformance_usd"] or
                comparison["candidate"]["max_drawdown_pct"] >= self.config["max_drawdown_fraction"] * 100):
            previous = next(v for v in self.state["versions"] if v["id"] == monitor["previous"])
            self.state["active_version"] = previous["id"]
            self.state["books"]["LEARNER"]["params"] = copy.deepcopy(previous["params"])
            self.state["versions"].append({"id": "rollback-%d" % len(self.state["versions"]), "at": now,
                                           "status": "automatic_rollback", "from": monitor["version"],
                                           "to": previous["id"], "params": previous["params"], "comparison": comparison})
            self.state["monitor"] = None

    def ingest(self, observation, *, persist=True):
        row = copy.deepcopy(observation)
        # Keep recorder facts and counters deterministic across duplicate replay,
        # including duplicates older than the current time cursor.
        try:
            json.dumps(row, allow_nan=False)
            row_id = str(row.get("id") or digest(row)) if isinstance(row, dict) else None
        except (ValueError, TypeError):
            row_id = None
        if row_id and row_id in self._seen_ids:
            self.state["duplicate_observations"] += 1
            if persist:
                self.save()
            return False
        if not row_id or not self._row_valid(row):
            self.state["invalid_observations"] += 1
            if persist:
                self.save()
            return False
        row["available_at"], row["observed_at"] = int(row["available_at"]), int(row["observed_at"])
        row["id"] = row_id
        now = row["available_at"]
        episode = self.episode(row)
        self.state["recording_drops_total"] = max(self.state["recording_drops_total"],
                                                  max(0, int(number(row.get("recording_drops_total"))) -
                                                      self.state["recording_drops_baseline"]))
        self.state["seen_ids"].append(row["id"])
        self._seen_ids.add(row["id"])
        self.state["episodes"].setdefault(episode, {"first_available_at": now, "observations": 0})["observations"] += 1
        trial_before = self.state["training"]
        monitor_before = self.state["monitor"]
        for name, book in list(self.state["books"].items()):
            if name.startswith("VALIDATE_"):
                if (not trial_before or now <= trial_before["start_after"] or episode in trial_before["train_episode_ids"]):
                    continue
                if episode not in trial_before["validation_episode_ids"]:
                    trial_before["validation_episode_ids"].append(episode)
            if name.startswith("MONITOR_") and (not monitor_before or now <= monitor_before["started_at"]):
                continue
            self._step_book(book, row, episode)
        # Evaluate skipped paths on their own market only. They never become
        # executed trades or enter realized PnL.
        for book in self.state["books"].values():
            self._assess_post_exit(book, row)
            self._assess_rejected_paths(book, row)
        self._validate(now)
        self._rollback(now)
        if not trial_before and not self.state["training"]:
            self._train(now)
        self.state.update(last_available_at=now, updated_at=now,
                          status="ACTIVE" if any(b["positions"] or b["pending"] for b in self.state["books"].values()) else "WAIT")
        if persist:
            self.save()
        return True

    def replay(self, observations):
        """Accelerated deterministic replay; order by availability, not blockTime."""
        ordered = sorted(enumerate(observations), key=lambda pair: (number(pair[1].get("available_at")), pair[0]))
        for _, row in ordered:
            self.ingest(row)
        return self.snapshot()

    def snapshot(self):
        books = [self.stats(b) for b in self.state["books"].values()]
        return {"version": VERSION, "paper_only": True, "status": self.state["status"],
                "updated_at": self.state["updated_at"], "active_version": self.state["active_version"],
                "simulation_count": self.state["simulations"], "unique_market_episodes": len(self.state["episodes"]),
                "unique_observations": len(self.state["seen_ids"]),
                "duplicate_observations": self.state["duplicate_observations"],
                "invalid_observations": self.state["invalid_observations"],
                "recording_drops_total": self.state["recording_drops_total"],
                "episode_definition": "exact token/pool/event-time hour clusters; not a claim of independent markets",
                "capital_note": "Separate accounts; returns and balances must never be summed",
                "execution_note": "Recorded quote lower bound or explicit reserve estimate; neither is an observed fill",
                "books": books, "last_training": copy.deepcopy(self.state["last_training"]),
                "training_history": copy.deepcopy(self.state["training_history"]),
                "training": copy.deepcopy(self.state["training"]), "versions": copy.deepcopy(self.state["versions"]),
                "control_comparison": self._compare(self.state["books"]["LEARNER"], self.state["books"]["CONTROL"]),
                "monitor_comparison": self._compare(self.state["books"]["MONITOR_CANDIDATE"], self.state["books"]["MONITOR_CONTROL"])
                                      if self.state["monitor"] else None}


class AsyncTrainingRecorder:
    """Bounded nonblocking producer; disk recording runs outside main exits.

    Dropped queue entries are counted. The worker/CLI consumes this append-only
    file and performs all experimentation/training in a separate process.
    """
    def __init__(self, path, capacity=512):
        self.path = Path(path)
        self.queue = queue.Queue(maxsize=capacity)
        self.dropped = 0
        self.written = 0
        self.errors = 0
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="paper-observation-recorder", daemon=True)
        self._thread.start()

    def submit(self, observation):
        if self._closed:
            return False
        try:
            # Deep-copy before queueing so later main state mutations cannot
            # change what was actually available at the decision timestamp.
            encoded = json.dumps(observation, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            self.queue.put_nowait(encoded)
            return True
        except queue.Full:
            self.dropped += 1
        except (ValueError, TypeError):
            self.errors += 1
        return False

    def _run(self):
        while True:
            encoded = self.queue.get()
            try:
                if encoded is None:
                    return
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(encoded + "\n")
                    handle.flush()
                self.written += 1
            except OSError:
                self.errors += 1
            finally:
                self.queue.task_done()

    def close(self, timeout=2):
        self._closed = True
        try:
            self.queue.put(None, timeout=timeout)
        except queue.Full:
            return False
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def snapshot(self):
        return {"written": self.written, "dropped": self.dropped, "errors": self.errors,
                "queued": self.queue.qsize(), "closed": self._closed}
