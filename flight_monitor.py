from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

DEFAULT_HEALTH_FAILURE_THRESHOLD = 3
DEFAULT_HEALTH_REMINDER_INTERVAL = 6
DEFAULT_HEALTH_FAILURE_ERROR_RATIO = 0.9


@dataclass
class FlightCandidate:
    source: str
    watch_id: str
    watch_label: str
    origin: str
    destination: str
    outbound_date: str
    return_date: str
    price_cny: int
    airlines: list[str]
    detail: str
    url: str | None = None

    @property
    def key(self) -> str:
        return "|".join(
            [
                self.source,
                self.watch_id,
                self.origin,
                self.destination,
                self.outbound_date,
                self.return_date,
            ]
        )


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def resolve_last_run_path(config_path: Path) -> Path:
    config = load_json(config_path, {})
    return config_path.parent / config.get("last_run_file", "last_run.json")


def resolve_state_path(config_path: Path) -> Path:
    config = load_json(config_path, {})
    return config_path.parent / config.get("state_file", "state.json")


def format_notification_text(payload: dict[str, Any]) -> str:
    lines = [
        f"checked_at={payload['checked_at']}",
        f"query_count={payload['query_count']}",
        f"candidate_count={payload['candidate_count']}",
        f"alert_count={payload['alert_count']}",
    ]
    if payload.get("alerts"):
        lines.append("alerts:")
        for alert in payload["alerts"]:
            lines.append(
                "- "
                f"{alert['watch_id']} {alert['origin']}-{alert['destination']} "
                f"{alert['outbound_date']}/{alert['return_date']} "
                f"CNY {alert['price_cny']} < {alert['threshold_cny']}"
            )
    elif payload.get("health_alerts"):
        lines.append("health_alerts:")
        for alert in payload["health_alerts"]:
            lines.append(
                "- "
                f"{alert['type']} consecutive_failures={alert['consecutive_failures']} "
                f"query_count={alert['query_count']} error_count={alert['error_count']}"
            )
    elif payload.get("best_by_watch"):
        lines.append("best_by_watch:")
        for watch_id, best in payload["best_by_watch"].items():
            lines.append(
                "- "
                f"{watch_id} {best['origin']}-{best['destination']} "
                f"{best['outbound_date']}/{best['return_date']} "
                f"CNY {best['price_cny']}"
            )
    if payload.get("errors"):
        lines.append(f"errors={len(payload['errors'])}")
    return "\n".join(lines)


def send_webhook_notification(payload: dict[str, Any], *, force: bool = False) -> list[dict[str, Any]]:
    if not payload.get("alerts") and not payload.get("health_alerts") and not force:
        return []

    url = os.environ.get("FLIGHT_MONITOR_WEBHOOK_URL", "").strip()
    if not url:
        return [
            {
                "channel": "generic_webhook",
                "status": "skipped",
                "reason": "FLIGHT_MONITOR_WEBHOOK_URL is not set",
            }
        ]

    if payload.get("alerts"):
        title = "Flight price monitor alert"
    elif payload.get("health_alerts"):
        title = "Flight monitor data source alert"
    else:
        title = "Flight price monitor summary"

    body = {
        "title": title,
        "text": format_notification_text(payload),
        "payload": payload,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
            return [
                {
                    "channel": "generic_webhook",
                    "status": "sent",
                    "http_status": response.status,
                }
            ]
    except urllib.error.HTTPError as exc:
        return [
            {
                "channel": "generic_webhook",
                "status": "failed",
                "http_status": exc.code,
                "error": str(exc),
            }
        ]
    except Exception as exc:
        return [
            {
                "channel": "generic_webhook",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        ]


def format_pushplus_content(payload: dict[str, Any]) -> str:
    lines = [
        "# Flight price monitor",
        "",
        f"- checked_at: `{payload['checked_at']}`",
        f"- query_count: `{payload['query_count']}`",
        f"- candidate_count: `{payload['candidate_count']}`",
        f"- alert_count: `{payload['alert_count']}`",
    ]
    if payload.get("alerts"):
        lines.extend(["", "## Alerts"])
        for alert in payload["alerts"]:
            lines.extend(
                [
                    "",
                    f"- watch: `{alert['watch_id']}`",
                    f"- route: `{alert['origin']}-{alert['destination']}`",
                    f"- dates: `{alert['outbound_date']}/{alert['return_date']}`",
                    f"- price: `CNY {alert['price_cny']}`",
                    f"- threshold: `CNY {alert['threshold_cny']}`",
                    f"- airlines: `{', '.join(alert.get('airlines') or [])}`",
                    f"- detail: `{alert.get('detail') or ''}`",
                ]
            )
            if alert.get("url"):
                lines.append(f"- url: {alert['url']}")
    elif payload.get("health_alerts"):
        lines.extend(["", "## Data source health alert"])
        for alert in payload["health_alerts"]:
            lines.extend(
                [
                    "",
                    f"- type: `{alert['type']}`",
                    f"- source: `{alert['source']}`",
                    f"- status: `{alert['status']}`",
                    f"- consecutive_failures: `{alert['consecutive_failures']}`",
                    f"- query_count: `{alert['query_count']}`",
                    f"- candidate_count: `{alert['candidate_count']}`",
                    f"- error_count: `{alert['error_count']}`",
                    f"- first_failed_at: `{alert.get('streak_started_at') or ''}`",
                ]
            )
            for item in alert.get("error_summary", [])[:3]:
                lines.append(f"- error x{item['count']}: `{item['error']}`")
    elif payload.get("best_by_watch"):
        lines.extend(["", "## Best by watch"])
        for watch_id, best in payload["best_by_watch"].items():
            lines.extend(
                [
                    "",
                    f"- watch: `{watch_id}`",
                    f"- route: `{best['origin']}-{best['destination']}`",
                    f"- dates: `{best['outbound_date']}/{best['return_date']}`",
                    f"- price: `CNY {best['price_cny']}`",
                    f"- airlines: `{', '.join(best.get('airlines') or [])}`",
                ]
            )
    if payload.get("errors"):
        lines.extend(["", f"## Errors: `{len(payload['errors'])}`"])
    return "\n".join(lines)


def send_pushplus_notification(payload: dict[str, Any], *, force: bool = False) -> list[dict[str, Any]]:
    if not payload.get("alerts") and not payload.get("health_alerts") and not force:
        return []

    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        return [
            {
                "channel": "pushplus",
                "status": "skipped",
                "reason": "PUSHPLUS_TOKEN is not set",
            }
        ]

    endpoint = os.environ.get("PUSHPLUS_ENDPOINT", "https://www.pushplus.plus/send").strip()
    if payload.get("alerts"):
        title = "Flight price alert"
    elif payload.get("health_alerts"):
        title = "Flight monitor data source alert"
    else:
        title = "Flight price summary"

    body = {
        "token": token,
        "title": title,
        "content": format_pushplus_content(payload),
        "template": os.environ.get("PUSHPLUS_TEMPLATE", "markdown").strip() or "markdown",
        "channel": os.environ.get("PUSHPLUS_CHANNEL", "wechat").strip() or "wechat",
    }
    topic = os.environ.get("PUSHPLUS_TOPIC", "").strip()
    if topic:
        body["topic"] = topic

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
        parsed: dict[str, Any]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        code = parsed.get("code")
        if code == 200:
            return [
                {
                    "channel": "pushplus",
                    "status": "sent",
                    "code": code,
                    "data": parsed.get("data"),
                }
            ]
        return [
            {
                "channel": "pushplus",
                "status": "failed",
                "code": code,
                "message": parsed.get("msg") or parsed.get("message") or raw[:500],
            }
        ]
    except urllib.error.HTTPError as exc:
        return [
            {
                "channel": "pushplus",
                "status": "failed",
                "http_status": exc.code,
                "error": str(exc),
            }
        ]
    except Exception as exc:
        return [
            {
                "channel": "pushplus",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        ]


def send_notifications(payload: dict[str, Any], *, force: bool = False) -> list[dict[str, Any]]:
    results = send_pushplus_notification(payload, force=force)
    if os.environ.get("FLIGHT_MONITOR_WEBHOOK_URL", "").strip():
        results.extend(send_webhook_notification(payload, force=force))
    return results


def notification_was_sent(results: list[dict[str, Any]]) -> bool:
    return any(result.get("status") == "sent" for result in results)


def payload_candidate_key(candidate: dict[str, Any]) -> str:
    return "|".join(
        [
            candidate["source"],
            candidate["watch_id"],
            candidate["origin"],
            candidate["destination"],
            candidate["outbound_date"],
            candidate["return_date"],
        ]
    )


def rollback_alert_state(config_path: Path, alerts: list[dict[str, Any]]) -> None:
    if not alerts:
        return
    state_path = resolve_state_path(config_path)
    state = load_json(state_path, {"routes": {}})
    routes = state.setdefault("routes", {})
    for alert in alerts:
        entry = routes.get(payload_candidate_key(alert))
        if not entry:
            continue
        entry["below_threshold"] = False
        entry.pop("last_alert_price_cny", None)
        entry.pop("last_alert_at", None)
    write_json(state_path, state)


def mark_health_alert_sent(config_path: Path, health_alerts: list[dict[str, Any]]) -> None:
    if not health_alerts:
        return
    state_path = resolve_state_path(config_path)
    state = load_json(state_path, {"routes": {}})
    source_health = state.setdefault("source_health", {})
    max_failure_count = max(int(alert.get("consecutive_failures", 0)) for alert in health_alerts)
    source_health["last_alert_failure_count"] = max_failure_count
    source_health["last_alert_at"] = datetime.now(timezone.utc).isoformat()
    write_json(state_path, state)


def google_fast_flights_query(
    *,
    watch: dict[str, Any],
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str,
    currency: str,
    language: str,
    max_results: int,
) -> tuple[list[FlightCandidate], str | None]:
    try:
        from fast_flights import FlightQuery, Passengers, create_query, get_flights
    except Exception as exc:  # pragma: no cover - runtime dependency check
        return [], f"google_fast_flights import failed: {type(exc).__name__}: {exc}"

    query_url = None
    try:
        query = create_query(
            flights=[
                FlightQuery(
                    date=outbound_date,
                    from_airport=origin,
                    to_airport=destination,
                ),
                FlightQuery(
                    date=return_date,
                    from_airport=destination,
                    to_airport=origin,
                ),
            ],
            seat="economy",
            trip="round-trip",
            passengers=Passengers(adults=1),
            language=language,
            currency=currency,
        )
        query_url = query.url()
        results = get_flights(query)
    except Exception as exc:
        return [], f"google_fast_flights query failed: {type(exc).__name__}: {exc}"

    candidates: list[FlightCandidate] = []
    for result in list(results)[:max_results]:
        price = getattr(result, "price", None)
        if not isinstance(price, int | float):
            continue
        airlines = [str(x) for x in (getattr(result, "airlines", None) or [])]
        legs = []
        for leg in getattr(result, "flights", []) or []:
            from_airport = getattr(getattr(leg, "from_airport", None), "code", "?")
            to_airport = getattr(getattr(leg, "to_airport", None), "code", "?")
            legs.append(f"{from_airport}-{to_airport}")
        candidates.append(
            FlightCandidate(
                source="google_fast_flights",
                watch_id=watch["id"],
                watch_label=watch["label"],
                origin=origin,
                destination=destination,
                outbound_date=outbound_date,
                return_date=return_date,
                price_cny=int(round(price)),
                airlines=airlines,
                detail="; ".join(legs) if legs else "",
                url=query_url,
            )
        )
    return candidates, None


def pick_alerts(
    *,
    candidates: list[FlightCandidate],
    thresholds: dict[str, int],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    prior = state.setdefault("routes", {})
    for candidate in candidates:
        threshold = thresholds[candidate.watch_id]
        entry = prior.setdefault(candidate.key, {})
        was_below = bool(entry.get("below_threshold"))
        last_alert_price = entry.get("last_alert_price_cny")
        is_below = candidate.price_cny < threshold

        should_alert = False
        if is_below and not was_below:
            should_alert = True
        elif is_below and isinstance(last_alert_price, int) and candidate.price_cny < last_alert_price:
            should_alert = True

        entry["last_seen_price_cny"] = candidate.price_cny
        entry["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        entry["below_threshold"] = is_below
        if should_alert:
            entry["last_alert_price_cny"] = candidate.price_cny
            entry["last_alert_at"] = entry["last_seen_at"]
            alerts.append(asdict(candidate) | {"threshold_cny": threshold})
        elif not is_below:
            entry.pop("last_alert_price_cny", None)
            entry.pop("last_alert_at", None)
    return alerts


def count_config_queries(config: dict[str, Any]) -> int:
    total = 0
    for watch in config["watches"]:
        total += (
            len(watch["origins"])
            * len(watch["destinations"])
            * len(watch["outbound_dates"])
            * len(watch["return_dates"])
        )
    return total


def summarize_errors(errors: list[dict[str, Any]], *, max_items: int = 3) -> list[dict[str, Any]]:
    counts = Counter(str(error.get("error", "")) for error in errors)
    return [
        {"count": count, "error": message[:500]}
        for message, count in counts.most_common(max_items)
    ]


def update_source_health(
    *,
    config: dict[str, Any],
    state: dict[str, Any],
    query_count: int,
    candidate_count: int,
    errors: list[dict[str, Any]],
    checked_at: str,
    limit_queries: int | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    health_config = config.get("health_alerts", {})
    failure_threshold = int(
        health_config.get("consecutive_failure_threshold", DEFAULT_HEALTH_FAILURE_THRESHOLD)
    )
    reminder_interval = int(
        health_config.get("reminder_failure_interval", DEFAULT_HEALTH_REMINDER_INTERVAL)
    )
    failure_error_ratio = float(
        health_config.get("full_failure_error_ratio", DEFAULT_HEALTH_FAILURE_ERROR_RATIO)
    )

    expected_query_count = count_config_queries(config)
    error_count = len(errors)
    full_run = limit_queries is None and query_count == expected_query_count
    error_ratio = (error_count / query_count) if query_count else 0.0
    full_failure = full_run and candidate_count == 0 and error_ratio >= failure_error_ratio

    source_health = state.setdefault("source_health", {})
    if not full_run:
        health = {
            "status": "skipped_limited_run",
            "full_run": False,
            "expected_query_count": expected_query_count,
            "query_count": query_count,
            "candidate_count": candidate_count,
            "error_count": error_count,
            "error_ratio": round(error_ratio, 4),
        }
        source_health["last_checked_at"] = checked_at
        source_health["last_status"] = health["status"]
        return health, []

    if full_failure:
        consecutive_failures = int(source_health.get("consecutive_failures", 0)) + 1
        if consecutive_failures == 1:
            source_health["streak_started_at"] = checked_at
        source_health["consecutive_failures"] = consecutive_failures
        source_health["last_failure_at"] = checked_at
        status = "failed"
    else:
        consecutive_failures = 0
        source_health["consecutive_failures"] = 0
        source_health["last_success_at"] = checked_at
        source_health.pop("streak_started_at", None)
        source_health.pop("last_alert_failure_count", None)
        status = "degraded" if error_ratio >= failure_error_ratio else "healthy"

    source_health["last_checked_at"] = checked_at
    source_health["last_status"] = status
    source_health["last_query_count"] = query_count
    source_health["last_candidate_count"] = candidate_count
    source_health["last_error_count"] = error_count
    source_health["last_error_ratio"] = round(error_ratio, 4)

    health = {
        "status": status,
        "full_run": True,
        "expected_query_count": expected_query_count,
        "query_count": query_count,
        "candidate_count": candidate_count,
        "error_count": error_count,
        "error_ratio": round(error_ratio, 4),
        "consecutive_failures": consecutive_failures,
        "failure_threshold": failure_threshold,
        "reminder_interval": reminder_interval,
    }

    health_alerts: list[dict[str, Any]] = []
    last_alert_failure_count = source_health.get("last_alert_failure_count")
    should_alert = False
    if full_failure and consecutive_failures >= failure_threshold:
        if not isinstance(last_alert_failure_count, int):
            should_alert = True
        elif consecutive_failures - last_alert_failure_count >= reminder_interval:
            should_alert = True

    if should_alert:
        health_alerts.append(
            {
                "type": "source_full_failure",
                "source": "google_fast_flights",
                "status": status,
                "checked_at": checked_at,
                "streak_started_at": source_health.get("streak_started_at"),
                "consecutive_failures": consecutive_failures,
                "failure_threshold": failure_threshold,
                "reminder_interval": reminder_interval,
                "query_count": query_count,
                "candidate_count": candidate_count,
                "error_count": error_count,
                "error_ratio": round(error_ratio, 4),
                "error_summary": summarize_errors(errors),
            }
        )

    return health, health_alerts


def run(config_path: Path, *, limit_queries: int | None = None, no_state_update: bool = False) -> dict[str, Any]:
    config = load_json(config_path, {})
    state_path = config_path.parent / config.get("state_file", "state.json")
    last_run_path = config_path.parent / config.get("last_run_file", "last_run.json")
    state = load_json(state_path, {"routes": {}})
    currency = config.get("currency", "CNY")
    language = config.get("language", "zh-CN")
    max_results = int(config.get("max_results_per_query", 5))
    sleep_seconds = float(config.get("sleep_seconds_between_queries", 1.0))

    candidates: list[FlightCandidate] = []
    errors: list[dict[str, Any]] = []
    query_count = 0
    thresholds = {watch["id"]: int(watch["threshold_cny"]) for watch in config["watches"]}

    for watch in config["watches"]:
        for origin in watch["origins"]:
            for destination in watch["destinations"]:
                for outbound_date in watch["outbound_dates"]:
                    for return_date in watch["return_dates"]:
                        if limit_queries is not None and query_count >= limit_queries:
                            break
                        query_count += 1
                        new_candidates, error = google_fast_flights_query(
                            watch=watch,
                            origin=origin,
                            destination=destination,
                            outbound_date=outbound_date,
                            return_date=return_date,
                            currency=currency,
                            language=language,
                            max_results=max_results,
                        )
                        candidates.extend(new_candidates)
                        if error:
                            errors.append(
                                {
                                    "source": "google_fast_flights",
                                    "watch_id": watch["id"],
                                    "origin": origin,
                                    "destination": destination,
                                    "outbound_date": outbound_date,
                                    "return_date": return_date,
                                    "error": error,
                                }
                            )
                        if sleep_seconds > 0:
                            time.sleep(sleep_seconds)
                    if limit_queries is not None and query_count >= limit_queries:
                        break
                if limit_queries is not None and query_count >= limit_queries:
                    break
            if limit_queries is not None and query_count >= limit_queries:
                break

    best_by_watch: dict[str, dict[str, Any]] = {}
    for candidate in sorted(candidates, key=lambda x: x.price_cny):
        best_by_watch.setdefault(candidate.watch_id, asdict(candidate))

    checked_at = datetime.now(timezone.utc).isoformat()
    alerts = pick_alerts(candidates=candidates, thresholds=thresholds, state=state)
    source_health, health_alerts = update_source_health(
        config=config,
        state=state,
        query_count=query_count,
        candidate_count=len(candidates),
        errors=errors,
        checked_at=checked_at,
        limit_queries=limit_queries,
    )
    payload = {
        "checked_at": checked_at,
        "query_count": query_count,
        "candidate_count": len(candidates),
        "alert_count": len(alerts),
        "alerts": alerts,
        "health_alert_count": len(health_alerts),
        "health_alerts": health_alerts,
        "source_health": source_health,
        "best_by_watch": best_by_watch,
        "candidates": [asdict(x) for x in sorted(candidates, key=lambda x: x.price_cny)],
        "errors": errors,
        "source_notes": config.get("source_notes", {}),
    }

    write_json(last_run_path, payload)
    if not no_state_update:
        write_json(state_path, state)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--limit-queries", type=int, default=None)
    parser.add_argument("--no-state-update", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--notify-every-run", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    payload = run(
        config_path,
        limit_queries=args.limit_queries,
        no_state_update=args.no_state_update,
    )
    if args.notify:
        payload["notification_results"] = send_notifications(
            payload,
            force=args.notify_every_run,
        )
        if payload["alerts"] and not notification_was_sent(payload["notification_results"]):
            rollback_alert_state(config_path, payload["alerts"])
            payload["state_update"] = "alert markers rolled back because notification was not sent"
        if payload.get("health_alerts") and notification_was_sent(payload["notification_results"]):
            mark_health_alert_sent(config_path, payload["health_alerts"])
            payload["health_state_update"] = "health alert marker saved after notification was sent"
        write_json(resolve_last_run_path(config_path), payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"checked_at={payload['checked_at']}")
        print(f"query_count={payload['query_count']}")
        print(f"candidate_count={payload['candidate_count']}")
        print(f"alert_count={payload['alert_count']}")
        print(f"health_alert_count={payload['health_alert_count']}")
        print(f"source_health_status={payload['source_health']['status']}")
        for watch_id, best in payload["best_by_watch"].items():
            print(
                "best "
                f"{watch_id} {best['origin']}-{best['destination']} "
                f"{best['outbound_date']}/{best['return_date']} "
                f"CNY {best['price_cny']}"
            )
        if payload["alerts"]:
            print("alerts:")
            for alert in payload["alerts"]:
                print(
                    f"- {alert['watch_id']} {alert['origin']}-{alert['destination']} "
                    f"{alert['outbound_date']}/{alert['return_date']} "
                    f"CNY {alert['price_cny']} < {alert['threshold_cny']}"
                )
        if payload["errors"]:
            print(f"errors={len(payload['errors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
