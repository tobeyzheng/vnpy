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

    def get_snapshot(self, codes: List[str]) -> list[dict[str, Any]]:
        if self._futu is None:
            raise RuntimeError(self._import_error or "futu sdk unavailable")
        futu = self._futu
        ctx = futu.OpenQuoteContext(host=self.config.host, port=self.config.port)
        try:
            ret, data = ctx.get_market_snapshot(codes)
            if ret != futu.RET_OK:
                raise RuntimeError(str(data))
            return data.to_dict(orient="records") if hasattr(data, "to_dict") else []
        finally:
            ctx.close()
