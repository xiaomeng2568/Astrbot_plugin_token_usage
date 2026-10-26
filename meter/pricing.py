"""Split token costs and immutable, time-matched price snapshots."""
import time
from decimal import Decimal, InvalidOperation
from .schedule import deepseek_period, deepseek_prices, local_datetime, matches


def rate(value):
    if value is None or isinstance(value, bool) or str(value).strip() == '':
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and 0 <= result <= 10**6 else None
    except (InvalidOperation, ValueError):
        return None


def estimate(usage, billing, provider, model, prices, timestamp=None, local_zone='local', options=None):
    timestamp = time.time() if timestamp is None else timestamp
    options = options or {}
    result = {'billing': billing, 'currency': '', 'cost': None, 'saving': None,
              'period': 'unmatched', 'local_time': '', 'price_snapshot': {},
              'input_cost': None, 'cached_cost': None, 'write_cost': None, 'output_cost': None}
    try:
        result['local_time'] = local_datetime(timestamp, local_zone).isoformat()
    except (ValueError, KeyError):
        result['period'] = 'invalid_timezone'
        return result
    candidates = [p for p in prices if isinstance(p, dict) and p.get('model') == model
                  and p.get('provider', '') in ('', provider)]
    # Explicit model rules override the official preset, including uncovered hours.
    if not candidates and options.get('deepseek_official', False):
        allowed = options.get('deepseek_provider_ids', [])
        if not allowed or provider in allowed:
            candidates = [p for p in deepseek_prices() if p['model'] == model]
    active = []
    for price in candidates:
        try:
            if matches(price, timestamp, local_zone, options):
                rank = (bool(price.get('provider')), price.get('period', 'all') != 'all')
                active.append((rank, price))
        except (ValueError, KeyError, TypeError):
            result['period'] = 'invalid_schedule'
            return result
    if not active:
        if candidates and any(p.get('period', '').startswith('deepseek_') for p in candidates):
            result['period'] = deepseek_period(timestamp, options)
        return result
    best = max(rank for rank, _ in active)
    selected = [price for rank, price in active if rank == best]
    if len(selected) != 1:
        result['period'] = 'ambiguous'
        return result
    price = selected[0]
    period = price.get('period', 'all')
    keys = ('name', 'model', 'provider', 'billing', 'currency', 'input', 'cached', 'write',
            'output', 'period', 'start', 'end', 'weekdays', 'timezone', 'source', 'verified_on')
    result.update(period=period, price_snapshot={k: price[k] for k in keys if k in price})
    matched_zone = 'Asia/Shanghai' if period.startswith('deepseek_') else price.get('timezone') or local_zone
    try:
        result['price_snapshot']['matched_at'] = local_datetime(timestamp, matched_zone).isoformat()
    except (ValueError, KeyError):
        result['period'] = 'invalid_timezone'
        return result
    result['price_snapshot']['matched_timezone'] = matched_zone
    # An explicit external subscription classification can never be priced as API.
    kind = billing if billing in {'subscription', 'local'} else price.get('billing', billing)
    if kind not in {'api', 'subscription', 'local'}:
        kind = 'unknown'
    result['billing'] = kind
    if kind != 'api' or usage.input is None or usage.output is None:
        return result
    currency = price.get('currency', '')
    if currency not in {'CNY', 'USD', 'EUR'}:
        return result
    ordinary, cached, output = (rate(price.get(k)) for k in ('input', 'cached', 'output'))
    if ordinary is None or output is None:
        return result
    if usage.cached is None:
        if cached != ordinary:
            return result
        count = 0  # equal rates allow a price without pretending cache usage is known
    else:
        count = usage.cached
        if count and cached is None:
            return result
    writes = usage.written or 0
    write_rate = rate(price.get('write')) if writes else ordinary
    if write_rate is None:
        return result
    components = dict(input_cost=Decimal(usage.input - count - writes) * ordinary / Decimal(10**6),
                      cached_cost=Decimal(count) * (cached or 0) / Decimal(10**6),
                      write_cost=Decimal(writes) * write_rate / Decimal(10**6),
                      output_cost=Decimal(usage.output) * output / Decimal(10**6))
    cost = sum(components.values())
    saving = Decimal(count) * (ordinary - (cached or 0)) / Decimal(10**6) if usage.cached is not None else None
    return dict(result, currency=currency, cost=str(cost), saving=str(saving) if saving is not None else None,
                **{k: str(v) for k, v in components.items()})
