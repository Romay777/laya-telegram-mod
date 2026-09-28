"""The SystemOneClient: transport for the `/v1/systemone` protocol (§5, ADR-0002).

One client serves both Laya and Jev: a base URL, an optional Bearer key,
and a pinned model. The client owns the transport facts and normalises
answers into a label → probability map; a response that is missing any of
the four labels is treated as a backend error (§5).
"""

from typing import Any

import httpx

from app.classifiers.spec import LABELS

#: A Verdict's raw material: label → probability, every label of the spec present.
Probabilities = dict[str, float]


class SystemOneError(Exception):
    """A backend failure: transport, HTTP status, or a malformed answer."""


class SystemOneTimeoutError(SystemOneError):
    """The backend did not answer within `timeout_s`."""


class SystemOneClient:
    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 3.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout_s, transport=transport
        )

    async def classify(self, state: dict[str, Any], spec: dict[str, Any]) -> Probabilities:
        """One check: `state` plus the question spec, back as probabilities (§5)."""
        try:
            response = await self._client.post(
                "/systemone", json={"model": self._model, "state": state, "questions": spec}
            )
        except httpx.TimeoutException as error:
            raise SystemOneTimeoutError(str(error) or "backend timed out") from error
        except httpx.HTTPError as error:
            raise SystemOneError(f"backend transport failed: {error}") from error

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise SystemOneError(f"backend answered {response.status_code}") from error

        return self._extract(response)

    def _extract(self, response: httpx.Response) -> Probabilities:
        try:
            probabilities = response.json()["answers"]["category"]["probabilities"]
            missing = [label for label in LABELS if label not in probabilities]
            if missing:
                raise KeyError(f"missing labels: {', '.join(missing)}")
            return {label: float(probabilities[label]) for label in probabilities}
        except (KeyError, TypeError, ValueError) as error:
            raise SystemOneError(f"malformed backend answer: {error}") from error

    async def aclose(self) -> None:
        await self._client.aclose()
