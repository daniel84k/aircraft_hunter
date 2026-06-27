from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

from alerts import format_alert
from config import load_settings
from db import connect, run_migrations
from logging_config import configure_logging
from main import (
    candidate_notification_phase,
    is_better_notification,
    notification_event_seen,
    process_due_transit_validations,
    remaining_cycle_delay,
)
from storage import Storage
from telegram import TelegramNotifier


LOG = logging.getLogger(__name__)


def process_pending_alerts(storage: Storage, notifier: TelegramNotifier, settings) -> int:
    now = datetime.now(timezone.utc)
    candidates = storage.pending_notification_candidates(
        now=now,
        event_window_seconds=settings.locked_alert_window_seconds,
        lookback_seconds=settings.alert_service_candidate_lookback_seconds,
        limit=50,
    )
    notified_events = []
    sent_count = 0
    early_sent_count = 0

    for candidate_id, candidate in candidates:
        if notification_event_seen(candidate, notified_events, settings.locked_alert_window_seconds):
            continue

        convergence_enabled = settings.notification_require_convergence
        if convergence_enabled:
            convergence_count, convergence_reason = storage.stored_candidate_convergence_count(
                candidate,
                event_window_seconds=settings.locked_alert_window_seconds,
                max_gap_seconds=max(30, settings.poll_interval_seconds * 3),
                max_time_shift_seconds=settings.notification_max_time_shift_seconds,
                max_observer_shift_km=settings.notification_max_observer_shift_km,
                max_offset_worsening_diameters=settings.notification_max_offset_worsening_diameters,
            )
        else:
            convergence_count = max(
                settings.early_notification_consecutive_cycles,
                settings.notification_consecutive_cycles,
            )
            convergence_reason = "DISABLED"

        notification_phase = candidate_notification_phase(
            candidate,
            convergence_count,
            settings,
            convergence_enabled=convergence_enabled,
        )
        if notification_phase is None:
            LOG.info(
                "ALERT_SERVICE_DEFERRED | candidate_id=%s aircraft=%s body=%s status=%s "
                "consecutive_cycles=%s reason=%s",
                candidate_id,
                candidate.aircraft.icao,
                candidate.body,
                candidate.status,
                convergence_count,
                convergence_reason,
            )
            continue

        alert_type = "EARLY" if notification_phase == "EARLY" else "CONFIRMED"
        is_better_alert = False
        if notification_phase == "EARLY":
            duplicate_event = storage.alert_event_exists(
                icao=candidate.aircraft.icao,
                body=candidate.body,
                transit_time_utc=candidate.transit_time_utc,
                event_window_seconds=settings.locked_alert_window_seconds,
                alert_type="EARLY",
            )
        else:
            previous_event = storage.alert_event_summary(
                icao=candidate.aircraft.icao,
                body=candidate.body,
                transit_time_utc=candidate.transit_time_utc,
                event_window_seconds=settings.locked_alert_window_seconds,
                confirmed_only=True,
            )
            duplicate_event = previous_event is not None
            is_better_alert = is_better_notification(
                candidate,
                previous_event,
                min_distance_improvement_km=settings.telegram_update_min_distance_improvement_km,
                min_offset_improvement_ratio=settings.telegram_update_min_offset_improvement_ratio,
            )
            if duplicate_event and is_better_alert:
                alert_type = "BETTER"

        if duplicate_event and not is_better_alert:
            storage.mark_candidate_rejected(candidate_id, "DUPLICATE_ALERT")
            continue
        if notification_phase == "EARLY" and early_sent_count >= settings.telegram_max_candidates_per_cycle:
            continue

        message = format_alert(candidate, better=is_better_alert, phase=notification_phase)
        sent = notifier.send_candidate(
            candidate,
            message,
            settings.telegram_candidate_cooldown_seconds,
            settings.telegram_update_cooldown_seconds,
            settings.telegram_update_min_distance_improvement_km,
            settings.telegram_update_min_offset_improvement_ratio,
            settings.locked_alert_window_seconds,
            notification_phase,
        )
        if not sent:
            continue

        print(message, flush=True)
        storage.insert_alert(
            candidate_id,
            message,
            f"{notification_phase.lower()}:{candidate.dedupe_key}",
            datetime.now(timezone.utc),
            alert_type,
        )
        sent_count += 1
        if notification_phase == "EARLY":
            early_sent_count += 1
        notified_events.append(candidate)
        LOG.info(
            "ALERT_SERVICE_SENT | candidate_id=%s aircraft=%s body=%s phase=%s score=%.2f",
            candidate_id,
            candidate.aircraft.icao,
            candidate.body,
            notification_phase,
            candidate.score,
        )

    return sent_count


def main() -> None:
    settings = load_settings()
    configure_logging(
        settings.log_level,
        settings.log_to_file,
        settings.log_dir,
        retain_full_days=settings.log_retain_full_days,
        retain_compressed_days=settings.log_retain_compressed_days,
        emergency_free_mb=settings.log_emergency_free_mb,
    )
    conn = connect(settings.database_url)
    run_migrations(conn)
    storage = Storage(conn)
    notifier = TelegramNotifier()

    LOG.info("Alert service started")
    if notifier.enabled:
        notifier.send_message("Aircraft Transit Hunter alert-service started")
        LOG.info("Telegram notifications enabled")
    else:
        LOG.warning("Telegram notifications disabled: missing token or chat id")

    while True:
        cycle_started_monotonic = time.monotonic()
        try:
            sent_count = process_pending_alerts(storage, notifier, settings)
            process_due_transit_validations(settings, storage, notifier, datetime.now(timezone.utc))
            LOG.info("ALERT_SERVICE_COMPLETE | sent=%s", sent_count)
        except Exception as exc:
            LOG.exception("Alert service cycle failed error=%s", exc)
        delay = remaining_cycle_delay(
            cycle_started_monotonic,
            settings.alert_service_poll_interval_seconds,
            time.monotonic(),
        )
        if delay > 0:
            time.sleep(delay)


if __name__ == "__main__":
    main()
