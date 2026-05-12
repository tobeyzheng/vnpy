from __future__ import annotations


def normalize_symbol(symbol: str, market: str | None = None) -> str:
    s = (symbol or '').strip().upper()
    if not s:
        return s
    if '.' not in s:
        return s
    code, suffix = s.split('.', 1)
    suffix = suffix.upper()
    if market == 'hong_kong' or suffix == 'HK':
        return f"{code.zfill(5)}.HK"
    if market == 'us' or suffix == 'US':
        return f"{code}.US"
    if market == 'china' or suffix in {'SH', 'SZ'}:
        return f"{code.zfill(6)}.{suffix}"
    return s
