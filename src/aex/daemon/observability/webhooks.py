"""Webhook delivery helpers for tenant budget/execution events."""

from __future__ import annotations

from datetime import datetime, UTC
import hashlib
import hmac
import json
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

from ..db import get_db_connection
from ..utils.logging_config import StructuredLogger

logger = StructuredLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _signature(secret: str, body: str) -> str:
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


def dispatch_budget_webhooks(
    *,
    tenant_id: str,
    event_type: str,
    execution_id: str | None,
    payload: dict,
) -> None:
    """Best-effort webhook fan-out for budget/execution events.

    Delivery attempts are recorded in `webhook_deliveries` for later audit/retry.
    """
    subscriptions = []
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, url, secret, event_types_json
            FROM webhook_subscriptions
            WHERE tenant_id = ? AND enabled = 1
            ORDER BY id ASC
            """,
            (tenant_id,),
        ).fetchall()

        for row in rows:
            allowed = []
            try:
                raw = row["event_types_json"]
                loaded = json.loads(raw) if raw else []
                if isinstance(loaded, list):
                    allowed = [str(v) for v in loaded]
            except Exception:
                allowed = []

            if allowed and event_type not in allowed and "*" not in allowed:
                continue

            payload_text = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            cursor = conn.execute(
                """
                INSERT INTO webhook_deliveries (
                    subscription_id, tenant_id, event_type, execution_id,
                    payload_json, status, attempts, created_at
                ) VALUES (?, ?, ?, ?, ?, 'PENDING', 0, ?)
                RETURNING id
                """,
                (
                    int(row["id"]),
                    tenant_id,
                    event_type,
                    execution_id,
                    payload_text,
                    _utc_now_iso(),
                ),
            )
            inserted = cursor.fetchone()
            subscriptions.append(
                {
                    "delivery_id": int(inserted["id"]),
                    "subscription_id": int(row["id"]),
                    "url": str(row["url"]),
                    "secret": str(row["secret"] or ""),
                }
            )
        conn.commit()

    for sub in subscriptions:
        envelope = {
            "event_id": f"wh_{sub['delivery_id']}",
            "event_type": event_type,
            "tenant_id": tenant_id,
            "execution_id": execution_id,
            "ts": _utc_now_iso(),
            "payload": payload,
        }
        body = json.dumps(envelope, ensure_ascii=True, sort_keys=True)
        headers = {
            "Content-Type": "application/json",
            "X-AEX-Event-Type": event_type,
        }
        if sub["secret"]:
            headers["X-AEX-Signature"] = _signature(sub["secret"], body)

        status = "FAILED"
        http_status = None
        error_text = None
        try:
            req = urlrequest.Request(sub["url"], method="POST", data=body.encode("utf-8"), headers=headers)
            with urlrequest.urlopen(req, timeout=3.0) as resp:
                http_status = int(getattr(resp, "status", 200))
            status = "DELIVERED" if http_status < 400 else "FAILED"
        except HTTPError as err:
            http_status = int(err.code)
            error_text = f"HTTPError {err.code}"
        except URLError as err:
            error_text = f"URLError {err.reason}"
        except Exception as err:
            error_text = str(err)

        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE webhook_deliveries
                SET status = ?,
                    attempts = attempts + 1,
                    http_status = ?,
                    error = ?,
                    delivered_at = CASE WHEN ? = 'DELIVERED' THEN ? ELSE delivered_at END
                WHERE id = ?
                """,
                (
                    status,
                    http_status,
                    error_text,
                    status,
                    _utc_now_iso(),
                    int(sub["delivery_id"]),
                ),
            )
            conn.commit()

        if status != "DELIVERED":
            logger.warning(
                "Webhook delivery failed",
                tenant_id=tenant_id,
                event_type=event_type,
                subscription_id=sub["subscription_id"],
                http_status=http_status,
                error=error_text,
            )
