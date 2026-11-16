"""The only module that talks to HockeyTech (or stands in for it).

`Feed` is the interface the rest of the app depends on; `HockeyTechFeed` is the live
implementation and `FixtureFeed` replays recorded responses for tests and season replay.
The API key is config (`HOCKEYTECH_KEY`), never in code, and never stored in a snapshot URL.
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx

from undertow.config import Settings, get_settings

BASE_URL = "https://lscluster.hockeytech.com/feed/index.php"


@dataclass(frozen=True, slots=True)
class RawResponse:
    """A fetched response body plus provenance. `source_url` never contains the API key."""

    source_url: str
    body: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class Feed(Protocol):
    def seasons(self) -> RawResponse: ...
    def teams(self, season_id: int) -> RawResponse: ...
    def schedule(self, season_id: int) -> RawResponse: ...
    def roster(self, team_id: int, season_id: int) -> RawResponse: ...
    def game_summary(self, game_id: int) -> RawResponse: ...
    def play_by_play(self, game_id: int) -> RawResponse: ...
    def player_profile(self, player_id: int, season_id: int) -> RawResponse: ...


class FeedError(RuntimeError):
    pass


class HockeyTechFeed:
    """Live feed. Throttled (default one request per 2 s) with an identifying User-Agent."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        min_interval: float = 2.0,
    ) -> None:
        self._s = settings or get_settings()
        if not self._s.hockeytech_key:
            raise FeedError("HOCKEYTECH_KEY is not set")
        self._client = client or httpx.Client(
            timeout=20, headers={"User-Agent": self._s.hockeytech_user_agent}
        )
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def _get(self, **params: str | int) -> RawResponse:
        public = {"client_code": self._s.hockeytech_client_code, "fmt": "json", **params}
        with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self._client.get(BASE_URL, params={**public, "key": self._s.hockeytech_key})
            finally:
                self._last = time.monotonic()
        if resp.status_code != 200:
            raise FeedError(f"HockeyTech returned HTTP {resp.status_code} for {public}")
        query = "&".join(f"{k}={v}" for k, v in sorted(public.items()))
        return RawResponse(source_url=f"{BASE_URL}?{query}", body=resp.text)

    def seasons(self) -> RawResponse:
        return self._get(feed="modulekit", view="seasons")

    def teams(self, season_id: int) -> RawResponse:
        return self._get(feed="modulekit", view="teamsbyseason", season_id=season_id)

    def schedule(self, season_id: int) -> RawResponse:
        return self._get(feed="modulekit", view="schedule", season_id=season_id)

    def roster(self, team_id: int, season_id: int) -> RawResponse:
        return self._get(feed="modulekit", view="roster", team_id=team_id, season_id=season_id)

    def game_summary(self, game_id: int) -> RawResponse:
        return self._get(feed="statviewfeed", view="gameSummary", game_id=game_id)

    def play_by_play(self, game_id: int) -> RawResponse:
        return self._get(feed="gc", tab="pxpverbose", game_id=game_id)

    def player_profile(self, player_id: int, season_id: int) -> RawResponse:
        return self._get(
            feed="statviewfeed",
            view="player",
            player_id=player_id,
            season_id=season_id,
            site_id=0,
            lang="en",
        )


class FixtureFeed:
    """Serves recorded responses from a directory. Mapping is by (kind, ids) -> file name."""

    def __init__(self, directory: Path, files: dict[str, str]) -> None:
        self._dir = directory
        self._files = files

    def _read(self, key: str) -> RawResponse:
        try:
            name = self._files[key]
        except KeyError:
            raise FeedError(f"no fixture registered for {key}") from None
        return RawResponse(source_url=f"fixture://{key}", body=(self._dir / name).read_text())

    def seasons(self) -> RawResponse:
        return self._read("seasons")

    def teams(self, season_id: int) -> RawResponse:
        return self._read(f"teams:{season_id}")

    def schedule(self, season_id: int) -> RawResponse:
        return self._read(f"schedule:{season_id}")

    def roster(self, team_id: int, season_id: int) -> RawResponse:
        return self._read(f"roster:{team_id}:{season_id}")

    def game_summary(self, game_id: int) -> RawResponse:
        return self._read(f"game:{game_id}")

    def play_by_play(self, game_id: int) -> RawResponse:
        return self._read(f"pxp:{game_id}")

    def player_profile(self, player_id: int, season_id: int) -> RawResponse:
        return self._read(f"player:{player_id}")
