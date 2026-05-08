from __future__ import annotations

from typing import Any, List

from services.futu_opend import OpenDConfig


class FutuQuoteClient:
    def __init__(self, config: OpenDConfig | None = None):
        self.config = config or OpenDConfig()
        self._futu = None
        self._import_error = None
        try:
            import futu  # type: ignore
            self._futu = futu
        except Exception as e:
            self._import_error = str(e)

    def availability(self) -> tuple[bool, str]:
        if self._futu is None:
            return False, self._import_error or "futu sdk import failed"
        return True, "ok"

    def normalize_code(self, code: str) -> str:
        if not code or '.' not in code:
            return code
        left, right = code.split('.', 1)
        left = left.upper()
        right = right.upper()

        if left in {'US', 'HK', 'SH', 'SZ'}:
            return f"{left}.{right}"
        if right in {'US', 'HK', 'SH', 'SZ'}:
            return f"{right}.{left}"
        return code

    def get_snapshot(self, codes: List[str]) -> list[dict[str, Any]]:
        if self._futu is None:
            raise RuntimeError(self._import_error or "futu sdk unavailable")
        futu = self._futu
        normalized = [self.normalize_code(code) for code in codes]
        ctx = futu.OpenQuoteContext(host=self.config.host, port=self.config.port)
        try:
            ret, data = ctx.get_market_snapshot(normalized)
            if ret != futu.RET_OK:
                raise RuntimeError(str(data))
            rows = data.to_dict(orient="records") if hasattr(data, "to_dict") else []
            return [self._normalize_row(row) for row in rows]
        finally:
            ctx.close()

    @staticmethod
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize Futu snapshot row field aliases for downstream consumers.

        - Adds ``change_pct`` alias when only ``change_rate`` is present.
          Futu's ``change_rate`` is already expressed in percent units
          (e.g. -1.23 means -1.23%), identical to what callers expect from
          ``change_pct``. Original ``change_rate`` is preserved.
        - Falls back to computing ``change_pct`` from ``last_price`` and
          ``prev_close_price`` when neither field is provided.
        """
        if not isinstance(row, dict):
            return row
        if row.get("change_pct") in (None, ""):
            change_rate = row.get("change_rate")
            if change_rate not in (None, ""):
                try:
                    row["change_pct"] = float(change_rate)
                except (TypeError, ValueError):
                    pass
            else:
                last = row.get("last_price")
                prev = row.get("prev_close_price")
                try:
                    if last not in (None, "") and prev not in (None, "", 0):
                        row["change_pct"] = round(
                            (float(last) - float(prev)) / float(prev) * 100, 3
                        )
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        return row
