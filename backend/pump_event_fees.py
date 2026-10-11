"""Pure, fail-closed fee evidence from complete known PumpSwap events.

Schemas checked 2026-10-07:
https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/idl/pump_amm.json
https://registry.npmjs.org/@pump-fun/pump-swap-sdk/-/pump-swap-sdk-2.0.0.tgz

This decodes bytes, not their provenance. A caller must authenticate the Pump
instruction/event, exact pool/mints, successful transaction, complete coverage
and freshness before using the result in a prospective PAPER model. It is
never an execution quote or entry permission. Unsupported evidence returns
None, so callers can retain their existing conservative fallback.
"""

VERSION = 'OBSERVED_PUMP_EVENT_FEES_V1'
BUY_EVENT = bytes([103, 244, 82, 31, 44, 245, 119, 119])
SELL_EVENT = bytes([62, 47, 55, 10, 165, 3, 220, 42])
PUBLIC_IDL_URL = 'https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/idl/pump_amm.json'
SDK_SCHEMA_URL = 'https://registry.npmjs.org/@pump-fun/pump-swap-sdk/-/pump-swap-sdk-2.0.0.tgz'
_BUY_NAMES = ('buy', 'buy_exact_quote_in', 'buy_v2', 'buy_exact_quote_in_v2')
_INSTRUCTION_SIDES = {
    'BUY': 'BUY', 'SELL': 'SELL', 'buy': 'BUY', 'buy_exact_quote_in': 'BUY',
    'buy_v2': 'BUY', 'buy_exact_quote_in_v2': 'BUY', 'sell': 'SELL', 'sell_v2': 'SELL',
}
_BPS_DENOMINATOR = 10_000


class _InvalidEvidence(ValueError):
    pass


class _Reader:
    def __init__(self, payload):
        self.payload = payload
        self.offset = 0

    def take(self, size):
        end = self.offset + size
        if end > len(self.payload):
            raise _InvalidEvidence('truncated field')
        result = self.payload[self.offset:end]
        self.offset = end
        return result

    def integer(self, size=8, *, signed=False):
        return int.from_bytes(self.take(size), 'little', signed=signed)

    def boolean(self):
        value = self.integer(1)
        if value not in (0, 1):
            raise _InvalidEvidence('invalid Borsh bool')
        return bool(value)

    def instruction_name(self):
        length = self.integer(4)
        if not 1 <= length <= max(map(len, _BUY_NAMES)):
            raise _InvalidEvidence('unsupported instruction name')
        value = self.take(length).decode('utf-8', errors='strict')
        if value not in _BUY_NAMES:
            raise _InvalidEvidence('unsupported instruction name')
        return value


def _ceil_fee(gross, bps):
    return (gross * bps + _BPS_DENOMINATOR - 1) // _BPS_DENOMINATOR


def _fee_component(reader):
    bps, amount = reader.integer(), reader.integer()
    if bps > _BPS_DENOMINATOR:
        raise _InvalidEvidence('fee rate exceeds 100 percent')
    return {'bps': bps, 'amount': amount}


def _check_component(component, gross):
    if component['amount'] != _ceil_fee(gross, component['bps']):
        raise _InvalidEvidence('fee amount/rate mismatch')


def decode_fee_evidence(payload, instruction_side):
    """Return fully reconciled known-schema fees, or None.

    ``payload`` starts at the Anchor BuyEvent/SellEvent discriminator (no
    emit_cpi wrapper). ``instruction_side`` is BUY/SELL, or an exact supported
    instruction name. Exact buy names additionally constrain the event name.
    All raw quantities are decimal strings to preserve u64 precision in JSON.

    Only a separate gross-quote-basis buyback charge is supported. Nonzero
    cashback, unrecognized fee routing, partial legacy schemas and unknown
    tails remain unsupported. Holder rewards are the already charged creator
    fee, as documented; they must match it and are never counted twice.
    """
    if not isinstance(payload, (bytes, bytearray)) or not 433 <= len(payload) <= 512:
        return None
    if not isinstance(instruction_side, str):
        return None
    expected = _INSTRUCTION_SIDES.get(instruction_side)
    if expected is None:
        return None
    try:
        reader = _Reader(payload)
        side = {BUY_EVENT: 'BUY', SELL_EVENT: 'SELL'}.get(bytes(reader.take(8)))
        if side != expected:
            return None
        timestamp = reader.integer(signed=True)
        base_amount = reader.integer()
        quote_limit = reader.integer()
        # User and pool reserves; retained schema fields cannot be skipped
        # by a shorter payload or shifted variable-length prefix.
        for _ in range(4):
            reader.integer()
        gross = reader.integer()
        lp = _fee_component(reader)
        protocol = _fee_component(reader)
        quote_after_lp = reader.integer()
        user_quote = reader.integer()
        reader.take(32 * 6)  # pool, user, user ATAs, protocol recipient and ATA
        coin_creator = bytes(reader.take(32))
        creator = _fee_component(reader)
        instruction_name = 'sell'
        if side == 'BUY':
            reader.boolean()  # track_volume
            for _ in range(3):
                reader.integer()
            reader.integer(signed=True)  # last_update_timestamp
            minimum_base = reader.integer()
            instruction_name = reader.instruction_name()
            if instruction_side in _BUY_NAMES and instruction_side != instruction_name:
                return None
            if minimum_base > base_amount:
                return None
        cashback = _fee_component(reader)
        buyback = _fee_component(reader)
        reader.integer(16, signed=True)  # virtual_quote_reserves may be negative
        reader.boolean()  # can_boost
        reader.integer()  # base_supply
        holder = _fee_component(reader)
        remaining = len(payload) - reader.offset
        creator_fee_unclaimed = None
        if remaining == 0:
            schema = 'PUMP_AMM_PUBLIC_IDL_FULL_2026_10_07'
            schema_url = PUBLIC_IDL_URL
        elif remaining == 8:
            creator_fee_unclaimed = reader.integer()
            schema = 'PUMP_AMM_SDK_2_0_0_FULL_CREATOR_UNCLAIMED'
            schema_url = SDK_SCHEMA_URL
        else:
            return None
        if reader.offset != len(payload) or min(timestamp, base_amount, gross, user_quote) <= 0:
            return None
        if cashback['bps'] or cashback['amount']:
            # A cashback accrual is not cash proceeds. Its precise routing
            # cannot be assumed from these fields alone.
            return None
        _check_component(lp, gross)
        _check_component(protocol, gross)
        # Old pools may still report the schedule's creator rate while a
        # default creator key waives the actual fee (official buy/sell SDK).
        creator_waived = not any(coin_creator)
        creator_charged_bps = creator['bps']
        if creator_waived:
            if creator['amount'] or holder['bps'] or holder['amount']:
                return None
            creator_charged_bps = 0
        else:
            _check_component(creator, gross)
        if holder['bps'] or holder['amount']:
            if holder != creator:
                return None
        _check_component(buyback, gross)
        charged = lp['amount'] + protocol['amount'] + creator['amount'] + buyback['amount']
        if charged >= gross:
            return None
        if side == 'BUY':
            if quote_after_lp != gross + lp['amount'] or user_quote != gross + charged:
                return None
            if user_quote > quote_limit:
                return None
        elif (quote_after_lp != gross - lp['amount'] or user_quote != gross - charged
              or user_quote < quote_limit):
            return None
        rate_sum = lp['bps'] + protocol['bps'] + creator_charged_bps + buyback['bps']
        # Integer fee rounding can exceed emitted nominal bps, especially for
        # tiny swaps. Do not understate the actual observed gross/net loss.
        observed_bps_ceiling = (charged * _BPS_DENOMINATOR + gross - 1) // gross
        fee_bps = max(rate_sum, observed_bps_ceiling)
        if fee_bps >= _BPS_DENOMINATOR:
            return None
        components = {name: {'emitted_bps': value['bps'], 'raw_amount': str(value['amount'])}
                      for name, value in (('lp', lp), ('protocol', protocol),
                                          ('creator', creator), ('cashback', cashback),
                                          ('buyback', buyback), ('holder_rewards', holder))}
        return {
            'version': VERSION,
            'source': 'PUMP_AMM_COMPLETE_EVENT_RAW_FEE_RECONCILIATION',
            'schema': schema, 'schema_url': schema_url,
            'instruction_side': side, 'instruction_name': instruction_name,
            'fee_bps': fee_bps, 'nominal_charged_fee_bps': rate_sum,
            'observed_fee_bps_ceiling': observed_bps_ceiling,
            'gross_quote_raw': str(gross), 'user_quote_raw': str(user_quote),
            'quote_after_lp_raw': str(quote_after_lp), 'total_charged_fee_raw': str(charged),
            'components': components, 'creator_fee_waived': creator_waived,
            'holder_rewards_included_in_creator': bool(holder['bps'] or holder['amount']),
            'buyback_charge_basis': 'SEPARATE_GROSS_QUOTE_FEE' if buyback['amount'] else 'ZERO',
            'creator_fee_unclaimed_raw': (str(creator_fee_unclaimed)
                                          if creator_fee_unclaimed is not None else None),
            'raw_reconciliation_complete': True,
            'provenance_authenticated': False, 'is_execution_quote': False,
            'entry_permission': False,
        }
    except (_InvalidEvidence, UnicodeError, OverflowError, TypeError, ValueError):
        return None
