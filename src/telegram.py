from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import requests

from models import TransitCandidate


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SentCandidate:
    sent_at: float
    status: str
    observer_distance_km: float
    offset_body_diameters: float


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = token or os.getenv("TELEGRAM_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._sent_candidates: dict[tuple[str, str, str], SentCandidate] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_message(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data={
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=5,
            )
            if response.status_code >= 400:
                LOG.warning("Telegram sendMessage failed status=%s body=%s", response.status_code, response.text[:200])
        except Exception as exc:
            LOG.warning("Telegram sendMessage failed error=%s", exc)

    def send_location(self, lat: float, lon: float) -> None:
        if not self.enabled:
            return
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendLocation",
                data={"chat_id": self.chat_id, "latitude": lat, "longitude": lon},
                timeout=5,
            )
            if response.status_code >= 400:
                LOG.warning("Telegram sendLocation failed status=%s body=%s", response.status_code, response.text[:200])
        except Exception as exc:
            LOG.warning("Telegram sendLocation failed error=%s", exc)

    def send_candidate(
        self,
        candidate: TransitCandidate,
        text: str,
        cooldown_seconds: int,
        update_cooldown_seconds: int,
        min_distance_improvement_km: float,
        min_offset_improvement_ratio: float,
        event_window_seconds: int = 600,
    ) -> bool:
        event_slot = int(candidate.transit_time_utc.timestamp()) // max(60, event_window_seconds)
        key = (candidate.aircraft.icao.lower(), candidate.body.lower(), str(event_slot))
        now = time.monotonic()
        previous = self._sent_candidates.get(key)
        if previous and not self._should_update(
            previous,
            candidate,
            now,
            cooldown_seconds,
            update_cooldown_seconds,
            min_distance_improvement_km,
            min_offset_improvement_ratio,
        ):
            return False
        self.send_message(text)
        self.send_location(candidate.observer_lat, candidate.observer_lon)
        self._sent_candidates[key] = SentCandidate(
            sent_at=now,
            status=candidate.status,
            observer_distance_km=candidate.observer_distance_km,
            offset_body_diameters=candidate.offset_body_diameters,
        )
        return True

    def _should_update(
        self,
        previous: SentCandidate,
        candidate: TransitCandidate,
        now: float,
        cooldown_seconds: int,
        update_cooldown_seconds: int,
        min_distance_improvement_km: float,
        min_offset_improvement_ratio: float,
    ) -> bool:
        if now < previous.sent_at + max(30, update_cooldown_seconds):
            return False
        if now >= previous.sent_at + max(60, cooldown_seconds):
            return True
        if previous.status != "ALERT_READY" and candidate.status == "ALERT_READY":
            return True
        distance_improvement = previous.observer_distance_km - candidate.observer_distance_km
        if distance_improvement >= min_distance_improvement_km:
            return True
        if previous.offset_body_diameters <= 0:
            return False
        offset_improvement = previous.offset_body_diameters - candidate.offset_body_diameters
        return offset_improvement / previous.offset_body_diameters >= min_offset_improvement_ratio
