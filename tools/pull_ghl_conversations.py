"""
Pull GHL conversation/message data for sales activity metrics.

Uses the message export endpoint to compute:
- Average response time (first inbound -> first outbound per conversation)
- Outbound activity counts (SMS, email, call)
- Activity volume by day (sparkline)

.env keys: GHL_API_KEY_{brand}, GHL_LOCATION_ID_{brand}
"""

import os
from collections import defaultdict
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://services.leadconnectorhq.com/"
API_VERSION = "2021-07-28"


def _ghl_get(path, headers, params=None):
    url = f"{API_BASE}{path.lstrip('/')}"
    return requests.get(url, headers=headers, params=params or {}, timeout=30)


def _daily_buckets(timestamps, start_date, end_date):
    dt_start = datetime.strptime(start_date, "%Y-%m-%d")
    dt_end = datetime.strptime(end_date, "%Y-%m-%d")
    num_days = (dt_end - dt_start).days + 1
    buckets = [0] * num_days
    for ts in timestamps:
        if not ts:
            continue
        try:
            entry_date = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            idx = (entry_date.date() - dt_start.date()).days
            if 0 <= idx < num_days:
                buckets[idx] += 1
        except (ValueError, AttributeError):
            continue
    return buckets


def pull(api_key: str, location_id: str, start_date: str = None, end_date: str = None) -> dict:
    if not api_key or not location_id:
        return {"error": "Missing GHL_API_KEY or GHL_LOCATION_ID"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        params = {"locationId": location_id, "limit": 100}
        if start_date:
            params["startDate"] = f"{start_date}T00:00:00Z"
        if end_date:
            params["endDate"] = f"{end_date}T23:59:59Z"

        messages = []
        for _ in range(10):
            resp = _ghl_get("conversations/messages/export", headers, params)
            if resp.status_code in (404, 403):
                return {"error": "Message export endpoint not available"}
            if resp.status_code != 200:
                break
            data = resp.json()
            batch = data.get("messages", [])
            messages.extend(batch)
            cursor = data.get("nextCursor") or data.get("meta", {}).get("nextCursor")
            if not cursor or len(batch) < params["limit"]:
                break
            params["cursor"] = cursor

        # Classify messages
        outbound_sms = 0
        outbound_email = 0
        outbound_call = 0
        inbound_count = 0
        outbound_timestamps = []
        conversations = defaultdict(list)

        for msg in messages:
            msg_type = str(msg.get("type", "") or "").lower()
            direction = str(msg.get("direction", "") or "").lower()
            ts = msg.get("dateAdded", msg.get("createdAt", ""))
            conv_id = msg.get("conversationId", "")

            if conv_id and ts:
                conversations[conv_id].append({"direction": direction, "type": msg_type, "timestamp": ts})

            if direction == "outbound":
                outbound_timestamps.append(ts)
                if "sms" in msg_type or "text" in msg_type:
                    outbound_sms += 1
                elif "email" in msg_type:
                    outbound_email += 1
                elif "call" in msg_type or "voice" in msg_type:
                    outbound_call += 1
            elif direction == "inbound":
                inbound_count += 1

        # Average response time (first inbound -> first outbound per conversation)
        response_times = []
        for conv_id, msgs in conversations.items():
            sorted_msgs = sorted(msgs, key=lambda m: m["timestamp"])
            first_inbound = None
            for m in sorted_msgs:
                if m["direction"] == "inbound" and first_inbound is None:
                    first_inbound = m["timestamp"]
                elif m["direction"] == "outbound" and first_inbound is not None:
                    try:
                        dt_in = datetime.fromisoformat(first_inbound.replace("Z", "+00:00"))
                        dt_out = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
                        delta = (dt_out - dt_in).total_seconds()
                        if 0 < delta < 86400 * 3:
                            response_times.append(delta)
                    except (ValueError, AttributeError):
                        pass
                    break

        avg_secs = sum(response_times) / len(response_times) if response_times else 0
        avg_mins = avg_secs / 60
        if avg_mins >= 60:
            response_str = f"{avg_mins / 60:.1f}h"
        elif avg_mins >= 1:
            response_str = f"{avg_mins:.0f}m"
        else:
            response_str = f"{avg_secs:.0f}s"

        total_outbound = outbound_sms + outbound_email + outbound_call
        spark = _daily_buckets(outbound_timestamps, start_date, end_date) if start_date and end_date else []

        return {
            "metrics": {
                "avg_response_time": {"value": response_str if avg_secs else "N/A", "spark": []},
                "outbound_total": {"value": f"{total_outbound:,}", "spark": spark},
                "outbound_sms": {"value": f"{outbound_sms:,}", "spark": []},
                "outbound_email": {"value": f"{outbound_email:,}", "spark": []},
                "outbound_calls": {"value": f"{outbound_call:,}", "spark": []},
                "inbound_total": {"value": f"{inbound_count:,}", "spark": []},
            },
        }
    except requests.exceptions.RequestException as exc:
        return {"error": f"GHL Conversations API error: {exc}"}
    except Exception as exc:
        return {"error": f"GHL Conversations error: {exc}"}


if __name__ == "__main__":
    import json
    key = os.getenv("GHL_API_KEY_RC", "")
    loc = os.getenv("GHL_LOCATION_ID_RC", "")
    result = pull(key, loc, "2026-03-01", "2026-03-31")
    print(json.dumps(result, indent=2))
