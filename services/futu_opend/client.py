from __future__ import annotations

import socket
from dataclasses import dataclass

from .config import OpenDConfig


@dataclass
class OpenDProbeResult:
    reachable: bool
    host: str
    port: int
    message: str


class OpenDClient:
    def __init__(self, config: OpenDConfig | None = None):
        self.config = config or OpenDConfig()

    def probe(self, timeout: float = 2.0) -> OpenDProbeResult:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((self.config.host, self.config.port))
            return OpenDProbeResult(True, self.config.host, self.config.port, "connected")
        except Exception as e:
            return OpenDProbeResult(False, self.config.host, self.config.port, str(e))
        finally:
            try:
                sock.close()
            except Exception:
                pass
