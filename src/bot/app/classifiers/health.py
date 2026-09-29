"""The Laya health prober (§5): deployed and healthy, off one GET /health.

Laya counts as *deployed* once its /health has answered at least once since
the bot started — a state that never un-happens — and *healthy* while the
last probe succeeded. `main` starts `probe_forever` as one asyncio task at
`health_interval_s`; the router and the Menu only read the two flags.
"""

import asyncio
from typing import Final

import httpx

#: The path the Compose healthcheck also uses (§5, §16).
HEALTH_PATH: Final = "/health"

#: §11: the probe loop paces itself at this many seconds by default.
DEFAULT_INTERVAL_S: Final = 60.0


class LayaHealth:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 3.0,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s, transport=transport)
        self._ever_answered = False
        self._last_ok = False

    @property
    def deployed(self) -> bool:
        """True once /health answered at least once since the bot started (§5)."""
        return self._ever_answered

    @property
    def healthy(self) -> bool:
        """True while the last probe succeeded (§5)."""
        return self._last_ok

    async def probe_once(self) -> None:
        """One GET /health; a failure is a fact to record, never a crash (§5)."""
        try:
            response = await self._client.get(HEALTH_PATH)
            self._last_ok = response.status_code == 200
        except httpx.HTTPError:
            self._last_ok = False
        if self._last_ok:
            self._ever_answered = True

    async def probe_forever(self, interval_s: float = DEFAULT_INTERVAL_S) -> None:
        """Probe every `health_interval_s` until the task is cancelled (§5)."""
        while True:
            await self.probe_once()
            await asyncio.sleep(interval_s)

    async def aclose(self) -> None:
        await self._client.aclose()
