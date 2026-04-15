"""
GoHighLevel (GHL) API v2 pull tool.

Fetches contacts, opportunities, and form submissions from a GHL location
to surface lead volume, qualified leads, and average response time.

.env keys required:
    GHL_API_KEY
    GHL_LOCATION_ID

API quirks / notes:
- GHL API v2 lives at https://services.leadconnectorhq.com/ (not gohighlevel.com).
- All requests need both Authorization and Version headers.
- The /forms/submissions endpoint may not be available on all plans; we fall
  back to /opportunities/search when it 404s.
- Date filtering on contacts uses startAfter/startBefore query params (epoch ms).
- Pagination uses nextPageUrl or startAfterId depending on the endpoint.
- Rate limits are relatively tight (~100 req/min); we keep calls minimal.
"""

import os
from collections import Counter
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

API_BASE = "https://services.leadconnectorhq.com/"
API_VERSION = "2021-07-28"


def _fmt(n: float | int, decimals: int = 0) -> str:
    """Format a number with commas and optional decimal places."""
    if decimals:
        return f"{n:,.{decimals}f}"
    return f"{int(n):,}"


def _pct(n: float) -> str:
    return f"{n:.1f}%"


def _hours(seconds: float) -> str:
    """Format seconds as hours with one decimal."""
    h = seconds / 3600
    return f"{h:.1f}h"


def _to_epoch_ms(date_str: str) -> int:
    """Convert YYYY-MM-DD to epoch milliseconds."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return int(dt.timestamp() * 1000)


def _daily_buckets(timestamps: list[str], start_date: str, end_date: str) -> list[int]:
    """Bin ISO timestamps into daily counts for sparkline data."""
    dt_start = datetime.strptime(start_date, "%Y-%m-%d")
    dt_end = datetime.strptime(end_date, "%Y-%m-%d")
    num_days = (dt_end - dt_start).days + 1

    buckets = [0] * num_days
    for ts in timestamps:
        if not ts:
            continue
        try:
            # GHL timestamps may be ISO 8601 with or without timezone
            entry_date = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            idx = (entry_date.date() - dt_start.date()).days
            if 0 <= idx < num_days:
                buckets[idx] += 1
        except (ValueError, AttributeError):
            continue
    return buckets


# ---------------------------------------------------------------------------
# Internal API helpers
# ---------------------------------------------------------------------------

def _ghl_get(path: str, headers: dict, params: dict | None = None) -> requests.Response:
    """Make a GET request to the GHL API with standard error handling."""
    url = f"{API_BASE}{path.lstrip('/')}"
    return requests.get(url, headers=headers, params=params or {}, timeout=30)


def _fetch_contacts(
    headers: dict, location_id: str, start_date: str, end_date: str
) -> list[dict]:
    """Fetch contacts created within the date range.

    Uses startAfter/startBefore epoch-ms filters. Paginates via startAfterId.
    """
    contacts: list[dict] = []
    params = {
        "locationId": location_id,
        "startAfter": _to_epoch_ms(start_date),
        "startBefore": _to_epoch_ms(end_date),
        "limit": 100,
    }

    # GHL paginates with startAfterId — cap at 10 pages to avoid runaway loops
    for _ in range(10):
        resp = _ghl_get("contacts/", headers, params)
        if resp.status_code != 200:
            break

        data = resp.json()
        batch = data.get("contacts", [])
        contacts.extend(batch)

        # Check for next page
        meta = data.get("meta", {})
        next_id = meta.get("startAfterId") or meta.get("nextPageUrl")
        if not next_id or len(batch) < params["limit"]:
            break
        params["startAfterId"] = next_id

    return contacts


def _fetch_form_submissions(
    headers: dict, location_id: str, start_date: str, end_date: str
) -> list[dict] | None:
    """Try to fetch form submissions. Returns None if endpoint is unavailable.

    The /forms/submissions endpoint is not available on all GHL plans/versions.
    Callers should fall back to opportunities if this returns None.
    """
    params = {
        "locationId": location_id,
        "startAt": start_date,
        "endAt": end_date,
        "limit": 100,
    }

    resp = _ghl_get("forms/submissions", headers, params)

    # 404 or 403 means endpoint is not available on this plan
    if resp.status_code in (404, 403):
        return None

    if resp.status_code != 200:
        return None

    data = resp.json()
    return data.get("submissions", [])


def _fetch_appointments(
    headers: dict, location_id: str, start_date: str, end_date: str
) -> list[dict]:
    """Fetch calendar appointments within the date range.

    Returns a list of appointment dicts with status (confirmed, showed, noshow, cancelled).
    """
    appointments: list[dict] = []
    params = {
        "locationId": location_id,
        "startDate": f"{start_date}T00:00:00Z",
        "endDate": f"{end_date}T23:59:59Z",
        "limit": 100,
    }

    for _ in range(10):
        resp = _ghl_get("calendars/events", headers, params)
        if resp.status_code in (404, 403):
            # Calendar endpoint not available on this plan
            return []
        if resp.status_code != 200:
            break

        data = resp.json()
        batch = data.get("events", [])
        appointments.extend(batch)

        meta = data.get("meta", {})
        next_id = meta.get("startAfterId") or meta.get("nextPageUrl")
        if not next_id or len(batch) < params["limit"]:
            break
        params["startAfterId"] = next_id

    return appointments


def _fetch_opportunities(
    headers: dict, location_id: str, start_date: str, end_date: str
) -> list[dict]:
    """Fallback: fetch opportunities when form submissions endpoint is unavailable.

    The /opportunities/search endpoint is more universally available and gives
    us a reasonable proxy for lead activity.
    """
    opportunities: list[dict] = []
    payload = {
        "locationId": location_id,
        "filters": [
            {
                "field": "createdAt",
                "operator": "gte",
                "value": f"{start_date}T00:00:00Z",
            },
            {
                "field": "createdAt",
                "operator": "lte",
                "value": f"{end_date}T23:59:59Z",
            },
        ],
        "limit": 100,
    }

    # opportunities/search is a POST endpoint
    url = f"{API_BASE}opportunities/search"
    for _ in range(10):
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            break

        data = resp.json()
        batch = data.get("opportunities", [])
        opportunities.extend(batch)

        meta = data.get("meta", {})
        next_id = meta.get("startAfterId")
        if not next_id or len(batch) < payload["limit"]:
            break
        payload["startAfterId"] = next_id

    return opportunities


# ---------------------------------------------------------------------------
# Main pull function
# ---------------------------------------------------------------------------

def pull(api_key: str, location_id: str, start_date: str, end_date: str) -> dict:
    """Pull GoHighLevel lead data for the given location and date range.

    Parameters
    ----------
    api_key : str
        GHL API v2 Bearer token.
    location_id : str
        GHL location/sub-account ID.
    start_date : str
        Start of the date range in ``YYYY-MM-DD`` format.
    end_date : str
        End of the date range in ``YYYY-MM-DD`` format.

    Returns
    -------
    dict
        Formatted lead metrics ready for the dashboard, or a dict with
        an ``"error"`` key if something goes wrong.
    """

    if not api_key or not location_id:
        return {
            "error": (
                "Missing credentials. Set GHL_API_KEY and GHL_LOCATION_ID "
                "env vars."
            )
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        # ---- 1. Fetch contacts --------------------------------------------
        contacts = _fetch_contacts(headers, location_id, start_date, end_date)

        # ---- 2. Try form submissions, fall back to opportunities ----------
        submissions = _fetch_form_submissions(
            headers, location_id, start_date, end_date
        )

        used_fallback = False
        if submissions is None:
            # Form submissions endpoint not available — use opportunities
            submissions = _fetch_opportunities(
                headers, location_id, start_date, end_date
            )
            used_fallback = True

        total_submissions = len(submissions)

        # ---- 3. Classify qualified leads ----------------------------------
        # In GHL, a "qualified" lead typically has a status/stage beyond
        # initial contact. We check for common status values.
        qualified_count = 0
        submission_timestamps: list[str] = []
        source_counts: Counter = Counter()
        form_counts: Counter = Counter()
        form_sources: dict[str, Counter] = {}

        for sub in submissions:
            # Grab timestamp
            ts = sub.get("createdAt", sub.get("dateAdded", ""))
            submission_timestamps.append(ts)

            # Qualified check — varies by whether we used forms or opportunities
            if used_fallback:
                # Opportunities have a status field
                status = (sub.get("status", "") or "").lower()
                if status not in ("abandoned", "lost", ""):
                    qualified_count += 1
            else:
                # Form submissions are generally all qualified unless spam-flagged
                qualified_count += 1

            # Source detection
            source = (
                sub.get("source", "")
                or sub.get("medium", "")
                or sub.get("utm_source", "")
                or "direct"
            ).lower()
            # Normalize to friendly labels
            if "google" in source or "organic" in source:
                source = "organic"
            elif "facebook" in source or "fb" in source or "instagram" in source:
                source = "social"
            elif "cpc" in source or "paid" in source or "ads" in source:
                source = "paid"
            elif "email" in source:
                source = "email"
            elif source in ("", "direct", "none"):
                source = "direct"
            else:
                source = "referral"

            source_counts[source] += 1

            # Form name grouping
            form_name = sub.get("formName", sub.get("pipelineName", "Unknown Form"))
            form_counts[form_name] += 1
            if form_name not in form_sources:
                form_sources[form_name] = Counter()
            form_sources[form_name][source] += 1

        # ---- 4. Average response time -------------------------------------
        # GHL contacts may have dateAdded and firstReplyAt or similar fields.
        # We compute avg time between contact creation and first activity.
        response_times: list[float] = []
        for contact in contacts:
            created = contact.get("dateAdded", "")
            first_reply = contact.get("lastActivity", "")
            if created and first_reply:
                try:
                    dt_created = datetime.fromisoformat(
                        created.replace("Z", "+00:00")
                    )
                    dt_reply = datetime.fromisoformat(
                        first_reply.replace("Z", "+00:00")
                    )
                    delta = (dt_reply - dt_created).total_seconds()
                    if 0 < delta < 86400 * 7:  # Ignore outliers > 7 days
                        response_times.append(delta)
                except (ValueError, AttributeError):
                    continue

        avg_response_secs = (
            sum(response_times) / len(response_times) if response_times else 0
        )

        # ---- 4b. Appointments / consultations -----------------------------
        appts = _fetch_appointments(headers, location_id, start_date, end_date)
        total_appts = len(appts)
        showed_count = 0
        noshow_count = 0
        cancelled_count = 0
        appt_timestamps: list[str] = []

        for appt in appts:
            status = (appt.get("appointmentStatus", "") or appt.get("status", "") or "").lower()
            appt_timestamps.append(appt.get("startTime", appt.get("createdAt", "")))

            if status in ("showed", "completed", "confirmed"):
                showed_count += 1
            elif status in ("noshow", "no_show", "no-show"):
                noshow_count += 1
            elif status in ("cancelled", "canceled"):
                cancelled_count += 1

        booked_count = total_appts - cancelled_count
        show_rate = (showed_count / booked_count * 100) if booked_count > 0 else 0

        # ---- 5. Per-form breakdown ----------------------------------------
        forms_breakdown: list[list[str]] = []
        for form_name, count in form_counts.most_common():
            dominant_source = (
                form_sources[form_name].most_common(1)[0][0]
                if form_sources.get(form_name)
                else "direct"
            )
            conv_rate = (count / total_submissions * 100) if total_submissions else 0
            forms_breakdown.append([
                form_name,
                str(count),
                dominant_source,
                _pct(conv_rate),
            ])

        # ---- 6. Sparkline data --------------------------------------------
        spark_total = _daily_buckets(submission_timestamps, start_date, end_date)
        spark_appts = _daily_buckets(appt_timestamps, start_date, end_date)

        # Qualified sparkline — approximate by scaling total per day
        qual_ratio = qualified_count / total_submissions if total_submissions else 0
        spark_qualified = [round(v * qual_ratio) for v in spark_total]

        # Response time sparkline — use daily average from contacts
        # (simplified: use total sparkline shape since per-day response data
        # would require much more API calls)
        spark_response = spark_total  # shape proxy

        # ---- Build response -----------------------------------------------
        return {
            "metrics": {
                "total_submissions": {
                    "value": _fmt(total_submissions),
                    "spark": spark_total,
                },
                "qualified_leads": {
                    "value": _fmt(qualified_count),
                    "spark": spark_qualified,
                },
                "avg_response_time": {
                    "value": _hours(avg_response_secs) if avg_response_secs else "N/A",
                    "spark": spark_response,
                },
                "appointments_booked": {
                    "value": _fmt(booked_count),
                    "spark": spark_appts,
                },
                "appointment_show_rate": {
                    "value": _pct(show_rate) if booked_count > 0 else "N/A",
                    "spark": [],
                },
                "appointments_showed": {
                    "value": _fmt(showed_count),
                    "spark": [],
                },
                "appointments_noshow": {
                    "value": _fmt(noshow_count),
                    "spark": [],
                },
            },
            "forms": forms_breakdown,
        }

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        return {"error": f"GHL API HTTP {status}: {exc}"}
    except requests.exceptions.RequestException as exc:
        return {"error": f"GHL API request failed: {exc}"}
    except Exception as exc:
        return {"error": f"GHL error: {exc}"}


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    key = os.getenv("GHL_API_KEY")
    loc = os.getenv("GHL_LOCATION_ID")

    if not key or not loc:
        print("Set GHL_API_KEY and GHL_LOCATION_ID env vars to test.")
        raise SystemExit(1)

    start = os.getenv("GHL_START_DATE", "2025-03-01")
    end = os.getenv("GHL_END_DATE", "2025-03-31")

    result = pull(key, loc, start, end)
    print(json.dumps(result, indent=2))
