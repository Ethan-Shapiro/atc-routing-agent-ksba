"""OAuth2 client-credentials auth + /states/all polling against the OpenSky Network API.

NOTE: OpenSky's identity backend has moved before — re-verify OPENSKY_TOKEN_URL against
current OpenSky docs before relying on this in production.
"""

import math
import time

import httpx

OPENSKY_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network"
    "/protocol/openid-connect/token"
)
OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"

MILES_TO_KM = 1.609344
# Rough degrees-per-mile at LAX's latitude, used only to size the polling bounding box —
# not used anywhere near the accuracy-sensitive separation math (that's all PostGIS `geography`).
KM_PER_DEG_LAT = 111.32


def compute_bbox(lat: float, lon: float, radius_miles: float) -> dict[str, float]:
    radius_km = radius_miles * MILES_TO_KM
    dlat = radius_km / KM_PER_DEG_LAT
    dlon = radius_km / (KM_PER_DEG_LAT * math.cos(math.radians(lat)))
    return {
        "lamin": lat - dlat,
        "lamax": lat + dlat,
        "lomin": lon - dlon,
        "lomax": lon + dlon,
    }


class OpenSkyClient:
    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(timeout=15.0)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _ensure_token(self) -> str:
        # Refresh 60s before actual expiry so an in-flight poll never starts with a stale token.
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token

        resp = await self._http.post(
            OPENSKY_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 1800))
        return self._token

    async def fetch_states(self, bbox: dict[str, float]) -> httpx.Response:
        token = await self._ensure_token()
        return await self._http.get(
            OPENSKY_STATES_URL,
            params=bbox,
            headers={"Authorization": f"Bearer {token}"},
        )
