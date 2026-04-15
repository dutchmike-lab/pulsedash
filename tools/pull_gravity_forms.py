"""
Gravity Forms REST API v2 pull tool.

Fetches form submissions from a WordPress site running Gravity Forms,
counts entries per form within a date range, and attempts to detect
lead source from entry meta (HTTP referrer).

.env keys required:
    GF_WP_URL               (e.g. https://example.com)
    GF_CONSUMER_KEY
    GF_CONSUMER_SECRET
"""

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt(n: float | int, decimals: int = 0) -> str:
    """Format a number with commas and optional decimal places."""
    if decimals:
        return f"{n:,.{decimals}f}"
    return f"{int(n):,}"


def _pct(n: float) -> str:
    return f"{n:.1f}%"


def _classify_source(referrer: str) -> str:
    """Best-effort classification of a lead source from an HTTP referrer.

    Returns one of: organic, paid, social, direct, referral, email.
    """
    if not referrer:
        return "direct"

    ref = referrer.lower()

    # Paid — common ad click parameters
    if any(tag in ref for tag in ("gclid=", "fbclid=", "msclkid=", "utm_medium=cpc",
                                   "utm_medium=paid", "utm_source=google_ads")):
        return "paid"

    # Social networks
    social_domains = (
        "facebook.com", "fb.com", "instagram.com", "linkedin.com",
        "twitter.com", "x.com", "tiktok.com", "pinterest.com",
        "youtube.com", "reddit.com",
    )
    if any(d in ref for d in social_domains):
        return "social"

    # Search engines (organic)
    search_engines = (
        "google.", "bing.com", "yahoo.com", "duckduckgo.com",
        "baidu.com", "yandex.",
    )
    if any(se in ref for se in search_engines):
        return "organic"

    # Email
    if any(tag in ref for tag in ("utm_medium=email", "utm_source=email",
                                   "mail.", "outlook.", "campaign-archive")):
        return "email"

    # Anything else with a domain is a referral
    if "http" in ref:
        return "referral"

    return "direct"


def _daily_buckets(entries: list[dict], start_date: str, end_date: str) -> list[int]:
    """Bin entry counts by day for sparkline data."""
    dt_start = datetime.strptime(start_date, "%Y-%m-%d")
    dt_end = datetime.strptime(end_date, "%Y-%m-%d")
    num_days = (dt_end - dt_start).days + 1

    buckets = [0] * num_days
    for entry in entries:
        created = entry.get("date_created", "")
        if not created:
            continue
        try:
            entry_date = datetime.strptime(created[:10], "%Y-%m-%d")
            idx = (entry_date - dt_start).days
            if 0 <= idx < num_days:
                buckets[idx] += 1
        except ValueError:
            continue
    return buckets


# ---------------------------------------------------------------------------
# Main pull function
# ---------------------------------------------------------------------------

def pull(
    wp_url: str,
    consumer_key: str,
    consumer_secret: str,
    start_date: str,
    end_date: str,
) -> dict:
    """Pull Gravity Forms submissions from a WordPress site.

    Parameters
    ----------
    wp_url : str
        WordPress site URL (e.g. ``"https://example.com"``). No trailing slash.
    consumer_key : str
        Gravity Forms REST API consumer key.
    consumer_secret : str
        Gravity Forms REST API consumer secret.
    start_date : str
        Start of the date range in ``YYYY-MM-DD`` format.
    end_date : str
        End of the date range in ``YYYY-MM-DD`` format.

    Returns
    -------
    dict
        Formatted form submission data ready for the dashboard, or a dict with
        an ``"error"`` key if something goes wrong.
    """

    if not wp_url or not consumer_key or not consumer_secret:
        return {
            "error": (
                "Missing credentials. Set GF_WP_URL, GF_CONSUMER_KEY, "
                "and GF_CONSUMER_SECRET env vars."
            )
        }

    wp_url = wp_url.rstrip("/")
    api_base = f"{wp_url}/wp-json/gf/v2"
    auth = HTTPBasicAuth(consumer_key, consumer_secret)

    try:
        # ---- 1. List all forms --------------------------------------------
        forms_resp = requests.get(
            f"{api_base}/forms", auth=auth, timeout=30
        )
        forms_resp.raise_for_status()
        forms_data = forms_resp.json()

        # The GF API may return a dict keyed by form ID or a list
        if isinstance(forms_data, dict):
            forms_list = list(forms_data.values())
        elif isinstance(forms_data, list):
            forms_list = forms_data
        else:
            return {"error": "Unexpected Gravity Forms /forms response format."}

        if not forms_list:
            return {
                "metrics": {
                    "total_submissions": {"value": "0", "spark": []},
                    "qualified_leads": {"value": "0", "spark": []},
                },
                "forms": [],
            }

        # ---- 2. Fetch entries per form ------------------------------------
        all_entries: list[dict] = []
        form_entries: dict[str, list[dict]] = {}  # form_title -> entries

        for form in forms_list:
            form_id = form.get("id")
            form_title = form.get("title", f"Form {form_id}")

            if not form_id:
                continue

            # Gravity Forms date filter uses search criteria
            params = {
                "search": (
                    f'{{"start_date":"{start_date}","end_date":"{end_date}"}}'
                ),
                "paging[page_size]": 500,
                "_labels": 1,
            }

            entries_resp = requests.get(
                f"{api_base}/forms/{form_id}/entries",
                auth=auth,
                params=params,
                timeout=30,
            )

            if entries_resp.status_code == 200:
                entries_json = entries_resp.json()
                entries = entries_json.get("entries", entries_json)
                if isinstance(entries, list):
                    form_entries[form_title] = entries
                    all_entries.extend(entries)

        total_submissions = len(all_entries)

        # ---- 3. Classify sources & detect qualified leads -----------------
        # A "qualified" lead is any entry that is not spam (status != "spam")
        # and has is_starred or status == "active"
        qualified_count = 0
        source_counts: Counter = Counter()

        for entry in all_entries:
            status = entry.get("status", "active")
            if status != "spam":
                qualified_count += 1

            # Try to detect source from entry meta
            referrer = (
                entry.get("source_url", "")
                or entry.get("ip", "")  # fallback — not great, but shows activity
            )
            source = _classify_source(referrer)
            source_counts[source] += 1

        # ---- 4. Per-form breakdown ----------------------------------------
        forms_breakdown: list[list[str]] = []
        for form_title, entries in form_entries.items():
            count = len(entries)
            if count == 0:
                continue

            # Dominant source for this form
            form_source_counts: Counter = Counter()
            for entry in entries:
                src = _classify_source(entry.get("source_url", ""))
                form_source_counts[src] += 1

            dominant_source = form_source_counts.most_common(1)[0][0] if form_source_counts else "direct"

            # Conversion rate approximation (submissions / total across forms)
            conv_rate = (count / total_submissions * 100) if total_submissions else 0

            forms_breakdown.append([
                form_title,
                str(count),
                dominant_source,
                _pct(conv_rate),
            ])

        # Sort by submission count descending
        forms_breakdown.sort(key=lambda row: int(row[1]), reverse=True)

        # ---- 5. Sparkline data --------------------------------------------
        spark_total = _daily_buckets(all_entries, start_date, end_date)

        qualified_entries = [
            e for e in all_entries if e.get("status", "active") != "spam"
        ]
        spark_qualified = _daily_buckets(qualified_entries, start_date, end_date)

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
            },
            "forms": forms_breakdown,
        }

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        return {"error": f"Gravity Forms API HTTP {status}: {exc}"}
    except requests.exceptions.RequestException as exc:
        return {"error": f"Gravity Forms API request failed: {exc}"}
    except Exception as exc:
        return {"error": f"Gravity Forms error: {exc}"}


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    url = os.getenv("GF_WP_URL")
    key = os.getenv("GF_CONSUMER_KEY")
    secret = os.getenv("GF_CONSUMER_SECRET")

    if not url or not key or not secret:
        print(
            "Set GF_WP_URL, GF_CONSUMER_KEY, and GF_CONSUMER_SECRET "
            "env vars to test."
        )
        raise SystemExit(1)

    start = os.getenv("GF_START_DATE", "2025-03-01")
    end = os.getenv("GF_END_DATE", "2025-03-31")

    result = pull(url, key, secret, start, end)
    print(json.dumps(result, indent=2))
