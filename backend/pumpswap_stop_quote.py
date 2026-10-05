#!/usr/bin/env python3
"""Fast read-only PumpSwap liquidation marks for PAPER stop execution.

The production strategy is not implemented here. This module only reads current
PumpSwap pool/vault accounts from Solana RPC and computes the executable sell
mark with the official constant-product formula. It never signs/submits trades.
"""
import base64
import hashlib
import math
import os
import threading
import time
from typing import Any

import requests
import engine_execution as jupiter

RPC_URL = os.getenv("NEO_STOP_RPC_URL", "https://api.mainnet-beta.solana.com")
RPC_FALLBACK_URL = os.getenv("NEO_STOP_RPC_FALLBACK_URL", "https://rpc.solanatracker.io/public")
PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMP_FEE_CONFIG_PDA = "5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx"
PUMP_FEE_PROGRAM_ID = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
GLOBAL_CONFIG = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WSOL = "So11111111111111111111111111111111111111112"
POOL_DISCRIMINATOR = bytes([241, 154, 109, 4, 17, 177, 109, 188])
CONVERSION_BUFFER_BPS = int(os.getenv("NEO_STOP_SOL_USD_BUFFER_BPS", "10"))
RPC_TIMEOUT = float(os.getenv("NEO_STOP_RPC_TIMEOUT_SECONDS", "0.95"))
SNAPSHOT_TTL_MS = int(os.getenv("NEO_STOP_SNAPSHOT_TTL_MS", "900"))

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_SESSION = requests.Session()
_SESSION.headers.update({"content-type": "application/json", "user-agent": "NEO-Paper-Stop-RPC/2.0"})
_LAYOUT_CACHE: dict[str, dict[str, Any]] = {}
_SNAPSHOT_CACHE: dict[str, dict[str, Any]] = {}
_FEE_CACHE: tuple[int, dict[str, Any]] | None = None
_LOCK = threading.Lock()

# Current on-chain Pump fee config fallback. Dynamic config is refreshed from
# its PDA every five minutes; these values are used only if that read fails.
_KNOWN_FLAT = {"lp": 25, "protocol": 5, "creator": 0}
_KNOWN_TIERS = [
    (0, {"lp": 2, "protocol": 93, "creator": 30}),
    (420_000_000_000, {"lp": 20, "protocol": 5, "creator": 95}),
    (1_470_000_000_000, {"lp": 20, "protocol": 5, "creator": 90}),
    (2_460_000_000_000, {"lp": 20, "protocol": 5, "creator": 85}),
    (3_440_000_000_000, {"lp": 20, "protocol": 5, "creator": 80}),
    (4_420_000_000_000, {"lp": 20, "protocol": 5, "creator": 75}),
    (9_820_000_000_000, {"lp": 20, "protocol": 5, "creator": 70}),
    (14_740_000_000_000, {"lp": 20, "protocol": 5, "creator": 65}),
    (19_650_000_000_000, {"lp": 20, "protocol": 5, "creator": 60}),
    (24_560_000_000_000, {"lp": 20, "protocol": 5, "creator": 55}),
    (29_470_000_000_000, {"lp": 20, "protocol": 5, "creator": 50}),
    (34_380_000_000_000, {"lp": 20, "protocol": 5, "creator": 45}),
    (39_300_000_000_000, {"lp": 20, "protocol": 5, "creator": 40}),
    (44_210_000_000_000, {"lp": 20, "protocol": 5, "creator": 35}),
    (49_120_000_000_000, {"lp": 20, "protocol": 5, "creator": 30}),
    (54_030_000_000_000, {"lp": 20, "protocol": 5, "creator": 28}),
    (58_940_000_000_000, {"lp": 20, "protocol": 5, "creator": 25}),
    (63_860_000_000_000, {"lp": 20, "protocol": 5, "creator": 23}),
    (68_770_000_000_000, {"lp": 20, "protocol": 5, "creator": 20}),
    (73_681_000_000_000, {"lp": 20, "protocol": 5, "creator": 18}),
    (78_590_000_000_000, {"lp": 20, "protocol": 5, "creator": 15}),
    (83_500_000_000_000, {"lp": 20, "protocol": 5, "creator": 13}),
    (88_400_000_000_000, {"lp": 20, "protocol": 5, "creator": 10}),
    (93_330_000_000_000, {"lp": 20, "protocol": 5, "creator": 8}),
    (98_240_000_000_000, {"lp": 20, "protocol": 5, "creator": 5}),
]


def now_ms() -> int:
    return int(time.time() * 1000)


def _b58_decode(value: str) -> bytes:
    number = 0
    for char in value:
        number = number * 58 + _B58.index(char)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + body


def _b58_encode(value: bytes) -> str:
    number = int.from_bytes(value, "big")
    text = ""
    while number:
        number, rem = divmod(number, 58)
        text = _B58[rem] + text
    return "1" * (len(value) - len(value.lstrip(b"\0"))) + (text or "")


_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _is_on_curve(encoded: bytes) -> bool:
    if len(encoded) != 32:
        return False
    y = int.from_bytes(encoded, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    y2 = y * y % _P
    denominator = (_D * y2 + 1) % _P
    if denominator == 0:
        return False
    x2 = (y2 - 1) * pow(denominator, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if x * x % _P != x2:
        x = x * _I % _P
    return x * x % _P == x2


def _find_pda(seeds: list[bytes], program_id: str) -> str:
    program = _b58_decode(program_id)
    for bump in range(255, -1, -1):
        digest = hashlib.sha256(
            b"".join(seeds + [bytes([bump])]) + program + b"ProgramDerivedAddress"
        ).digest()
        if not _is_on_curve(digest):
            return _b58_encode(digest)
    raise ValueError("unable to derive PDA")


def _rpc(method: str, params: list[Any]) -> tuple[Any, int, int, str]:
    last_error: Exception | None = None
    urls = [RPC_URL]
    if RPC_FALLBACK_URL and RPC_FALLBACK_URL != RPC_URL:
        urls.append(RPC_FALLBACK_URL)
    for url in urls:
        started = now_ms()
        try:
            response = _SESSION.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=(0.35, RPC_TIMEOUT),
            )
            received = now_ms()
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(str(payload["error"])[:180])
            return payload.get("result"), received - started, received, url
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "RPC unavailable"))


def _account_bytes(value: dict[str, Any] | None) -> bytes:
    if not value or not value.get("data"):
        raise ValueError("account missing")
    return base64.b64decode(value["data"][0])


def _decode_pool(data: bytes) -> dict[str, Any]:
    if len(data) < 269 or data[:8] != POOL_DISCRIMINATOR:
        raise ValueError("not a supported PumpSwap pool")
    return {
        "creator": _b58_encode(data[11:43]),
        "base_mint": _b58_encode(data[43:75]),
        "quote_mint": _b58_encode(data[75:107]),
        "base_vault": _b58_encode(data[139:171]),
        "quote_vault": _b58_encode(data[171:203]),
        "coin_creator_present": any(data[211:243]),
        "is_mayhem_mode": bool(data[243]),
        "is_cashback_coin": bool(data[244]),
        "virtual_quote_reserves": int.from_bytes(data[245:261], "little", signed=True),
        "creator_fee_bps": int.from_bytes(data[261:269], "little"),
        "is_holder_reward": bool(data[270]) if len(data)>270 else False,
    }


def _token_account_amount(data: bytes) -> int:
    if len(data) < 72:
        raise ValueError("token account too short")
    return int.from_bytes(data[64:72], "little")


def _validate_token_account(value, mint, authority):
    if (value or {}).get('owner') not in (TOKEN_PROGRAM,TOKEN_2022_PROGRAM):
        raise ValueError('vault owner is not a supported token program')
    data = _account_bytes(value)
    if len(data)<165 or _b58_encode(data[:32])!=mint or _b58_encode(data[32:64])!=authority or data[108]!=1:
        raise ValueError('vault mint, authority or initialization mismatch')
    # Extensions can change executable quantities (transfer fees/hooks). Use
    # the aggregate quote adapter until a specific extension is modeled.
    if len(data)>165 or value.get('owner')==TOKEN_2022_PROGRAM:
        raise ValueError('token extensions require validated aggregate route')
    return _token_account_amount(data)


def _decode_global(value):
    if (value or {}).get('owner')!=PUMP_AMM_PROGRAM_ID:
        raise ValueError('global config owner mismatch')
    data = _account_bytes(value)
    if len(data)<907 or data[:8]!=hashlib.sha256(b'account:GlobalConfig').digest()[:8]:
        raise ValueError('unsupported global config schema')
    return {'disable_flags':data[56],'cashback_enabled':bool(data[642]),
            'buyback_bps':int.from_bytes(data[899:907],'little')}


def _mint_supply(data: bytes) -> int:
    if len(data) < 45:
        raise ValueError("mint account too short")
    return int.from_bytes(data[36:44], "little")


def _mint_decimals(data: bytes) -> int:
    if len(data) < 45:
        raise ValueError("mint account too short")
    return int(data[44])


def _validate_mint(value,expected_decimals=None):
    data=_account_bytes(value)
    if (value or {}).get('owner')!=TOKEN_PROGRAM or len(data)!=82 or data[45]!=1:
        raise ValueError('unsupported or uninitialized mint account')
    decimals=_mint_decimals(data)
    if not 0<=decimals<=18 or (expected_decimals is not None and decimals!=expected_decimals):
        raise ValueError('mint decimals mismatch')
    return _mint_supply(data),decimals


def _fees(data: bytes, offset: int) -> tuple[dict[str, int], int]:
    return {
        "lp": int.from_bytes(data[offset : offset + 8], "little"),
        "protocol": int.from_bytes(data[offset + 8 : offset + 16], "little"),
        "creator": int.from_bytes(data[offset + 16 : offset + 24], "little"),
    }, offset + 24


def _fee_config() -> dict[str, Any]:
    global _FEE_CACHE
    current = now_ms()
    with _LOCK:
        cached = _FEE_CACHE
    if cached and 0 <= current - cached[0] < 5000 and not cached[1].get('fallback'):
        return cached[1]
    parsed = {"flat": dict(_KNOWN_FLAT), "tiers": [(t, dict(f)) for t, f in _KNOWN_TIERS], "fallback": True}
    try:
        result, _, _, _ = _rpc(
            "getAccountInfo",
            [PUMP_FEE_CONFIG_PDA, {"encoding": "base64", "commitment": "confirmed"}],
        )
        value=(result or {}).get('value') or {}
        if value.get('owner')!=PUMP_FEE_PROGRAM_ID:
            raise ValueError('fee config owner mismatch')
        data = _account_bytes(value)
        if data[:8]!=bytes([143,52,146,187,219,123,76,155]):
            raise ValueError('fee config discriminator mismatch')
        if len(data) >= 69:
            flat, _ = _fees(data, 41)
            offset = 65
            tier_count = int.from_bytes(data[offset : offset + 4], "little")
            offset += 4
            tiers = []
            for _ in range(tier_count):
                if offset + 40 > len(data):
                    raise ValueError("fee tier truncated")
                threshold = int.from_bytes(data[offset : offset + 16], "little")
                schedule, _ = _fees(data, offset + 16)
                tiers.append((threshold, schedule))
                offset += 40
            if tiers and all(0<=f<=10000 for f in flat.values()) and all(0<=f<=10000 for _,fees in tiers for f in fees.values()):
                parsed = {"flat": flat, "tiers": tiers, "fallback": False,
                          'slot':int(((result or {}).get('context') or {}).get('slot') or 0)}
    except (requests.RequestException, ValueError, TypeError, KeyError, RuntimeError):
        pass
    with _LOCK:
        _FEE_CACHE = (current, parsed)
    return parsed


def _get_multiple(pubkeys: list[str]) -> tuple[dict[str, dict[str, Any]], int, int, int, str]:
    if not pubkeys:
        return {}, 0, now_ms(), 0, RPC_URL
    unique = list(dict.fromkeys(pubkeys))
    result, http_ms, received, source = _rpc(
        "getMultipleAccounts",
        [unique, {"encoding": "base64", "commitment": "confirmed"}],
    )
    values = (result or {}).get("value") or []
    if len(values) != len(unique):
        raise ValueError("incomplete multiple-account response")
    slot = int(((result or {}).get("context") or {}).get("slot") or 0)
    if slot<=0:
        raise ValueError('RPC context slot missing')
    return dict(zip(unique, values)), http_ms, received, slot, source


def prime_positions(positions: list[dict[str, Any]]) -> bool:
    """Refresh all open PumpSwap positions with at most two RPC batches.

    Static pool layout is cached, so normal guard iterations use one batch for
    every open position together instead of one request per position.
    """
    targets = []
    for position in positions:
        coin = position.get("coin_snapshot") or {}
        if str(coin.get("dexId") or "").lower() != "pumpswap":
            continue
        pair = str(position.get("pairAddress") or "")
        mint = str(position.get("address") or "")
        if pair and mint:
            targets.append((pair, mint))
    if not targets:
        return False
    try:
        missing = []
        with _LOCK:
            for pair, _ in targets:
                if pair not in _LAYOUT_CACHE:
                    missing.append(pair)
        if missing:
            account_map, _, _, _, _ = _get_multiple(missing)
            with _LOCK:
                for pair in missing:
                    value = account_map.get(pair)
                    if value and value.get("owner") == PUMP_AMM_PROGRAM_ID:
                        _LAYOUT_CACHE[pair] = _decode_pool(_account_bytes(value))

        addresses = []
        valid = []
        with _LOCK:
            layouts = {pair: dict(_LAYOUT_CACHE.get(pair) or {}) for pair, _ in targets}
        for pair, mint in targets:
            layout = layouts.get(pair) or {}
            if layout.get("base_mint") != mint or layout.get("quote_mint") != WSOL:
                continue
            valid.append((pair, mint, layout))
            addresses.extend([pair, layout["base_vault"], layout["quote_vault"], mint])
        if not valid:
            return False
        addresses.extend([WSOL,GLOBAL_CONFIG])
        account_map, http_ms, received, slot, source = _get_multiple(addresses)
        global_config = _decode_global(account_map.get(GLOBAL_CONFIG))
        quote_mint_value=account_map.get(WSOL) or {}
        _validate_mint(quote_mint_value,9)
        fresh: dict[str, dict[str, Any]] = {}
        for pair, mint, layout in valid:
            pool_value = account_map.get(pair)
            base_value = account_map.get(layout["base_vault"])
            quote_value = account_map.get(layout["quote_vault"])
            mint_value = account_map.get(mint)
            if not all((pool_value, base_value, quote_value, mint_value)):
                continue
            if pool_value.get("owner") != PUMP_AMM_PROGRAM_ID:
                continue
            pool = _decode_pool(_account_bytes(pool_value))
            if (
                pool["base_mint"] != mint
                or pool["quote_mint"] != WSOL
                or pool["base_vault"] != layout["base_vault"]
                or pool["quote_vault"] != layout["quote_vault"]
            ):
                continue
            base_supply,base_decimals=_validate_mint(mint_value)
            base_reserve = _validate_token_account(base_value,mint,pair)
            quote_reserve = _validate_token_account(quote_value,WSOL,pair)
            fresh[pair] = {
                "pool": pool,
                "base_reserve": base_reserve,
                "quote_reserve": quote_reserve,
                "base_supply": base_supply,
                "base_decimals": base_decimals,
                "quoted_at": received,
                "slot": slot,
                "http_ms": http_ms,
                "rpc_source": source,
                "global_config":global_config,
                "commitment":"confirmed",
                "validated_accounts":True,
            }
        with _LOCK:
            _SNAPSHOT_CACHE.update(fresh)
        _fee_config()
        return bool(fresh)
    except (requests.RequestException, ValueError, TypeError, KeyError, RuntimeError, OverflowError):
        return False


def _fee_schedule(
    pool: dict[str, Any], base_reserve: int, effective_quote_reserve: int, base_supply: int
) -> tuple[dict[str, int], bool, float | None, bool]:
    config = _fee_config()
    canonical_creator = _find_pda(
        [b"pool-authority", _b58_decode(pool["base_mint"])], PUMP_PROGRAM_ID
    )
    canonical = pool["creator"] == canonical_creator
    market_cap_sol = None
    if canonical:
        circulating = 1_000_000_000_000_000 if pool["is_mayhem_mode"] else base_supply
        market_cap_lamports = effective_quote_reserve * circulating // max(base_reserve, 1)
        market_cap_sol = market_cap_lamports / 1_000_000_000
        tiers = config["tiers"]
        schedule = dict(tiers[0][1]) if tiers else dict(config["flat"])
        for threshold, fees in reversed(tiers):
            if market_cap_lamports >= threshold:
                schedule = dict(fees)
                break
    else:
        schedule = dict(config["flat"])
    if not pool["coin_creator_present"]:
        schedule["creator"] = 0
    if pool["creator_fee_bps"] > 0:
        schedule["creator"] = pool["creator_fee_bps"]
    return schedule, canonical, market_cap_sol, bool(config.get("fallback"))


def quote_from_reserves(
    base_reserve: int,
    quote_reserve: int,
    virtual_quote_reserve: int,
    base_amount: int,
    fee_schedule: dict[str, int],
) -> dict[str, Any]:
    if any(type(v) is not int for v in (base_reserve,quote_reserve,virtual_quote_reserve,base_amount)):
        raise ValueError('integer raw amounts required')
    if min(base_reserve, quote_reserve, base_amount) <= 0:
        raise ValueError("invalid reserves")
    effective_quote = quote_reserve + int(virtual_quote_reserve)
    if effective_quote <= 0:
        raise ValueError("effective quote reserve is non-positive")
    no_impact = effective_quote * base_amount // base_reserve
    raw_out = effective_quote * base_amount // (base_reserve + base_amount)
    if any(type(v) is not int or not 0<=v<=10000 for v in fee_schedule.values()):
        raise ValueError('invalid fee basis points')
    lp_fee = (raw_out * fee_schedule.get("lp", 0)+9999)//10_000
    protocol_fee = (raw_out * fee_schedule.get("protocol", 0)+9999)//10_000
    creator_fee = (raw_out * fee_schedule.get("creator", 0)+9999)//10_000
    if quote_reserve < raw_out - lp_fee:
        raise ValueError("insufficient real quote reserve")
    final_out = raw_out - lp_fee - protocol_fee - creator_fee
    if final_out <= 0:
        raise ValueError("fees exceed output")
    impact_pct = max(0.0, (1.0 - raw_out / max(no_impact, 1)) * 100.0)
    return {
        "effective_quote_reserve": effective_quote,
        "no_impact_quote_raw": no_impact,
        "raw_quote_out": raw_out,
        "final_quote_out": final_out,
        "lp_fee_raw": lp_fee,
        "protocol_fee_raw": protocol_fee,
        "creator_fee_raw": creator_fee,
        "impact_pct": impact_pct,
    }


def buy_quote_from_reserves(
    base_reserve: int,
    quote_reserve: int,
    virtual_quote_reserve: int,
    quote_amount: int,
    fee_schedule: dict[str, int],
) -> dict[str, Any]:
    """Exact-quote-in PumpSwap buy math mirrored from the official SDK."""
    if any(type(v) is not int for v in (base_reserve,quote_reserve,virtual_quote_reserve,quote_amount)):
        raise ValueError('integer raw amounts required')
    if min(base_reserve, quote_reserve, quote_amount) <= 0:
        raise ValueError("invalid reserves")
    effective_quote_reserve = quote_reserve + int(virtual_quote_reserve)
    if effective_quote_reserve <= 0:
        raise ValueError("effective quote reserve is non-positive")

    total_fee_bps = (
        int(fee_schedule.get("lp", 0))
        + int(fee_schedule.get("protocol", 0))
        + int(fee_schedule.get("creator", 0))
    )
    effective_quote = quote_amount * 10_000 // (10_000 + total_fee_bps)
    if any(type(v) is not int or not 0<=v<=10000 for v in fee_schedule.values()):
        raise ValueError('invalid fee basis points')
    lp_fee = (effective_quote * fee_schedule.get("lp", 0)+9999)//10_000
    protocol_fee = (effective_quote * fee_schedule.get("protocol", 0)+9999)//10_000
    creator_fee = (effective_quote * fee_schedule.get("creator", 0)+9999)//10_000
    total_with_fees = effective_quote + lp_fee + protocol_fee + creator_fee
    if total_with_fees > quote_amount:
        effective_quote -= total_with_fees - quote_amount
    if effective_quote <= 1:
        raise ValueError("quote too small after fees")

    input_amount = effective_quote - 1
    base_out = base_reserve * input_amount // (effective_quote_reserve + input_amount)
    if base_out <= 0 or base_out >= base_reserve:
        raise ValueError("invalid base output")

    no_impact_base = base_reserve * input_amount // effective_quote_reserve
    impact_pct = max(0.0, (1.0 - base_out / max(no_impact_base, 1)) * 100.0)
    return {
        "effective_quote_reserve": effective_quote_reserve,
        "quote_input_raw": quote_amount,
        "effective_quote_raw": effective_quote,
        "base_out_raw": base_out,
        "lp_fee_raw": lp_fee,
        "protocol_fee_raw": protocol_fee,
        "creator_fee_raw": creator_fee,
        "impact_pct": impact_pct,
    }


def cached_snapshot(pair: str, mint: str, *, max_age_ms: int = SNAPSHOT_TTL_MS):
    """Return a validated read-only reserve observation without any API calls.

    The training recorder can share this observation. A reserve estimate in
    SOL is not a sale for USDC; callers must retain the settlement asset.
    """
    with _LOCK:
        snapshot = dict(_SNAPSHOT_CACHE.get(pair) or {})
        fee_cache = _FEE_CACHE
    if not snapshot or not snapshot.get('validated_accounts'):
        return None
    age = now_ms()-int(snapshot.get('quoted_at') or 0)
    pool = snapshot.get('pool') or {}
    if not 0<=age<=max_age_ms or pool.get('base_mint')!=mint or pool.get('quote_mint')!=WSOL:
        return None
    snapshot['pool'] = dict(pool)
    snapshot['available_at'] = snapshot['quoted_at']
    snapshot['settlement_asset'] = WSOL
    snapshot['fee_config_known'] = bool(fee_cache and not fee_cache[1].get('fallback')
                                       and 0<=now_ms()-fee_cache[0]<=5000)
    return snapshot


def reserve_inventory_mark(position: dict[str, Any]):
    """SOL inventory estimate only, never booked as realized USD proceeds."""
    pair,mint = str(position.get('pairAddress') or ''),str(position.get('address') or '')
    snapshot = cached_snapshot(pair,mint)
    if not snapshot:
        return None
    pool,global_config = snapshot['pool'],snapshot.get('global_config') or {}
    # Current official IDL contains buyback, cashback and holder reward modes.
    # Until every component is modeled, reject rather than reuse old fee tiers.
    if pool.get('is_cashback_coin') or pool.get('is_holder_reward') or global_config.get('buyback_bps'):
        return None
    if global_config.get('disable_flags',0)&16 or not snapshot['fee_config_known']:
        return None
    base_reserve,quote_reserve = int(snapshot['base_reserve']),int(snapshot['quote_reserve'])
    schedule,canonical,market_cap_sol,fallback = _fee_schedule(
        pool,base_reserve,quote_reserve+int(pool['virtual_quote_reserves']),int(snapshot['base_supply']))
    if fallback:
        return None
    with _LOCK:
        config = _FEE_CACHE[1] if _FEE_CACHE else {}
    config_slot = int(config.get('slot') or 0)
    if not config_slot or abs(config_slot-int(snapshot['slot']))>25:
        return None
    calculated = quote_from_reserves(base_reserve,quote_reserve,int(pool['virtual_quote_reserves']),
                                     int(position.get('jupiter_token_raw_amount') or 0),schedule)
    return {**calculated,'settlement_asset':WSOL,'settlement_decimals':9,
            'expected_sol_raw':calculated['final_quote_out'],'realized_usdc':None,
            'quoted_at':snapshot['quoted_at'],'available_at':snapshot['available_at'],
            'slot':snapshot['slot'],'pairAddress':pair,'address':mint,
            'fee_bps':schedule,'canonical_pool':canonical,'market_cap_sol':market_cap_sol,
            'source':'VALIDATED_RESERVE_SOL_INVENTORY_ESTIMATE','is_simulated_fill':False}


def prepare_entry(coin: dict[str, Any],notional_usd: float,sol_usd: float,
                  buffer_bps: int=10,slippage_bps: int=100):
    """Use a validated full USDC route for USD-denominated PAPER accounts.

    Previously this divided USD by a scanner SOL price and pretended the SOL
    conversion executed. Reserve math remains available as native inventory
    evidence; only the aggregate quote can model both actual conversion legs.
    """
    pair,mint = str(coin.get('pairAddress') or ''),str(coin.get('address') or '')
    if not pair or not mint:
        return None
    return jupiter.prepare_entry(mint,pair,notional_usd)


def position_mark(position: dict[str, Any],coin: dict[str, Any],network_fee_usd: float,sol_usd: float):
    """Liquidation uses a full token-to-USDC route, with no fictitious SOL FX.

    A missing conversion keeps the position unavailable and in risk. Validated
    native reserve estimates are attached only as diagnostics, not USDC fills.
    """
    result = jupiter.position_mark(position,coin,network_fee_usd,force=False)
    if not result:
        return None
    try:
        native = reserve_inventory_mark(position)
    except (ValueError,TypeError,KeyError,RuntimeError,OverflowError):
        native = None
    if native:
        result['native_inventory_estimate'] = native
    result['settlement_asset'] = jupiter.USDC
    result['conversion_route_validated'] = True
    return result
