"""
Constant Contact API v3 pull tool.

Fetches email campaign summary statistics — sends, opens, clicks, bounces,
unsubscribes — and computes open rate, click rate, and deliverability.

.env keys required:
    CONSTANT_CONTACT_API_KEY
    CONSTANT_CONTACT_ACCESS_TOKEN
"""

import os
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

API_BASE = "https://api.cc.email/v3/"


def _fmt(n: float | int, decimals: int = 0) -> str:
    """Format a number with commas and optional decimal places."""
    if decimals:
        return f"{n:,.{decimals}f}"
    return f"{int(n):,}"


def _pct(n: float) -> str:
    return f"{n:.1f}%"


def _safe_rate(numerator: float, denominator: float) -> float:
    """Return percentage rate, guarding against division by zero."""
    if denominator == 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)


# ---------------------------------------------------------------------------
# Main pull function
# ---------------------------------------------------------------------------

def pull(api_key: str, access_token: str, start_date: str, end_date: str) -> dict:
    """Pull Constant Contact email campaign stats for the given date range.

    Parameters
    ----------
    api_key : str
        Constant Contact API key.
    access_token : str
        OAuth2 Bearer token for Constant Contact API v3.
    start_date : str
        Start of the date range in ``YYYY-MM-DD`` format.
    end_date : str
        End of the date range in ``YYYY-MM-DD`` format.

    Returns
    -------
    dict
        Formatted email metrics ready for the dashboard, or a dict with
        an ``"error"`` key if something goes wrong.
    """

    if not api_key or not access_token:
        return {
            "error": (
                "Missing credentials. Set CONSTANT_CONTACT_API_KEY and "
                "CONSTANT_CONTACT_ACCESS_TOKEN env vars."
            )
        }

    # Auto-refresh token if it's expired
    refresh_token = os.getenv("CONSTANT_CONTACT_REFRESH_TOKEN", "")
    client_secret = os.getenv("CONSTANT_CONTACT_CLIENT_SECRET", "")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Quick test to see if token is valid
    test_resp = requests.get(f"{API_BASE}emails?limit=1", headers=headers, timeout=10)
    if test_resp.status_code == 401 and refresh_token and client_secret:
        # Token expired — refresh it
        token_resp = requests.post(
            "https://authz.constantcontact.com/oauth2/default/v1/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(api_key, client_secret),
            timeout=15,
        )
        if token_resp.status_code == 200:
            token_data = token_resp.json()
            access_token = token_data["access_token"]
            headers["Authorization"] = f"Bearer {access_token}"
            # Save new token to .env
            env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    content = f.read()
                old_token = os.getenv("CONSTANT_CONTACT_ACCESS_TOKEN", "")
                if old_token:
                    content = content.replace(old_token, access_token)
                    new_refresh = token_data.get("refresh_token", "")
                    if new_refresh and new_refresh != refresh_token:
                        content = content.replace(refresh_token, new_refresh)
                    with open(env_path, "w") as f:
                        f.write(content)

    # Convert dates to ISO 8601 format required by CC API
    try:
        dt_start = datetime.strptime(start_date, "%Y-%m-%d")
        dt_end = datetime.strptime(end_date, "%Y-%m-%d")
        iso_start = dt_start.strftime("%Y-%m-%dT00:00:00.000Z")
        iso_end = dt_end.strftime("%Y-%m-%dT23:59:59.000Z")
    except ValueError as exc:
        return {"error": f"Invalid date format: {exc}"}

    try:
        # ---- 1. Campaign summaries ----------------------------------------
        # Try the v3 reports endpoint first, fall back to email campaigns list
        url = f"{API_BASE}reports/email_reports/campaign_summaries"
        params = {
            "start": iso_start,
            "end": iso_end,
        }
        resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code == 404:
            # Fallback: list campaigns, get activity IDs, fetch stats per activity
            url = f"{API_BASE}emails"
            params = {"limit": 50}
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            campaign_summaries = []
            campaigns = data.get("campaigns", [])
            for campaign in campaigns:
                cid = campaign.get("campaign_id", "")
                cname = campaign.get("name", "Untitled")
                if not cid:
                    continue
                # Get campaign detail to find activity IDs
                detail_resp = requests.get(f"{API_BASE}emails/{cid}", headers=headers, timeout=30)
                if detail_resp.status_code != 200:
                    continue
                detail = detail_resp.json()
                activities = detail.get("campaign_activities", [])
                for act in activities:
                    if act.get("role") != "primary_email":
                        continue
                    act_id = act.get("campaign_activity_id", "")
                    if not act_id:
                        continue
                    stats_url = f"{API_BASE}reports/stats/email_campaign_activities/{act_id}"
                    stats_resp = requests.get(stats_url, headers=headers, timeout=30)
                    if stats_resp.status_code == 200:
                        stats_data = stats_resp.json()
                        results = stats_data.get("results", [])
                        if results:
                            entry = results[0]
                            entry["_campaign_name"] = cname
                            campaign_summaries.append(entry)
        else:
            resp.raise_for_status()
            data = resp.json()
            campaign_summaries = data.get("campaign_summaries", data.get("bulk_email_campaign_summaries", []))

        if not campaign_summaries:
            return {
                "metrics": {
                    "sends": {"value": "0", "spark": []},
                    "open_rate": {"value": "0.0%", "spark": []},
                    "click_rate": {"value": "0.0%", "spark": []},
                    "unsubscribes": {"value": "0", "spark": []},
                    "unique_opens": {"value": "0", "spark": []},
                    "deliverability": {"value": "0.0%", "spark": []},
                }
            }

        # ---- 2. Aggregate totals and per-campaign sparklines --------------
        total_sends = 0
        total_opens = 0
        total_unique_opens = 0
        total_clicks = 0
        total_bounces = 0
        total_unsubs = 0

        spark_sends: list[int] = []
        spark_open_rate: list[float] = []
        spark_click_rate: list[float] = []
        spark_unsubs: list[int] = []
        spark_unique_opens: list[int] = []
        spark_deliverability: list[float] = []

        for campaign in campaign_summaries:
            # Stats can be nested under "stats" or directly on the object
            stats = campaign.get("stats", campaign)

            sends = stats.get("em_sends", 0)
            opens = stats.get("em_opens", 0)
            unique_opens = stats.get("em_opens", 0)  # CC uses em_opens for unique
            clicks = stats.get("em_clicks", 0)
            bounces = stats.get("em_bounces", 0)
            unsubs = stats.get("em_optouts", stats.get("em_unsubscribes", 0))

            total_sends += sends
            total_opens += opens
            total_unique_opens += unique_opens
            total_clicks += clicks
            total_bounces += bounces
            total_unsubs += unsubs

            # Per-campaign values for sparkline approximation
            spark_sends.append(sends)
            spark_open_rate.append(_safe_rate(unique_opens, sends))
            spark_click_rate.append(_safe_rate(clicks, sends))
            spark_unsubs.append(unsubs)
            spark_unique_opens.append(unique_opens)

            delivered = sends - bounces
            spark_deliverability.append(_safe_rate(delivered, sends))

        # ---- 3. Compute aggregate rates -----------------------------------
        overall_open_rate = _safe_rate(total_unique_opens, total_sends)
        overall_click_rate = _safe_rate(total_clicks, total_sends)
        total_delivered = total_sends - total_bounces
        overall_deliverability = _safe_rate(total_delivered, total_sends)

        # ---- 3. Top 5 emails by open rate and click rate --------------------
        per_campaign = []
        for cs in campaign_summaries:
            stats = cs.get("stats", cs)
            sends = stats.get("em_sends", 0)
            opens = stats.get("em_opens", 0)
            clicks = stats.get("em_clicks", 0)
            name = cs.get("_campaign_name", "Untitled")
            if sends > 0:
                per_campaign.append({
                    "name": name,
                    "sends": sends,
                    "opens": opens,
                    "clicks": clicks,
                    "open_rate": round(opens / sends * 100, 1),
                    "click_rate": round(clicks / sends * 100, 1),
                })

        top_by_opens = sorted(per_campaign, key=lambda x: x["open_rate"], reverse=True)[:5]
        top_by_clicks = sorted(per_campaign, key=lambda x: x["click_rate"], reverse=True)[:5]

        # ---- 4. Per-link click details for top 3 campaigns by clicks ------
        link_clicks = []
        try:
            top_click_campaigns = sorted(
                campaign_summaries,
                key=lambda c: c.get("stats", c).get("em_clicks", 0),
                reverse=True,
            )[:3]
            for cs in top_click_campaigns:
                # Get campaign activity ID — try different possible keys
                camp_activity_id = cs.get("campaign_activity_id", "")
                camp_name = cs.get("_campaign_name", "Untitled")
                if not camp_activity_id:
                    continue
                try:
                    links_url = f"{API_BASE}reports/email_reports/{camp_activity_id}/links"
                    links_resp = requests.get(links_url, headers=headers, timeout=30)
                    if links_resp.status_code != 200:
                        continue
                    links_data = links_resp.json()
                    link_list = links_data.get("link_click_counts", [])
                    # Take top 5 by clicks
                    link_list_sorted = sorted(
                        link_list,
                        key=lambda l: l.get("url_click_count", l.get("click_count", 0)),
                        reverse=True,
                    )[:5]
                    for link in link_list_sorted:
                        link_clicks.append({
                            "campaign": camp_name,
                            "url": link.get("link_url", link.get("url", "")),
                            "clicks": link.get("url_click_count", link.get("click_count", 0)),
                        })
                except Exception:
                    continue
        except Exception:
            link_clicks = []

        # ---- Build response -----------------------------------------------
        return {
            "metrics": {
                "sends": {"value": _fmt(total_sends), "spark": spark_sends},
                "open_rate": {
                    "value": _pct(overall_open_rate),
                    "spark": spark_open_rate,
                },
                "click_rate": {
                    "value": _pct(overall_click_rate),
                    "spark": spark_click_rate,
                },
                "unsubscribes": {
                    "value": _fmt(total_unsubs),
                    "spark": spark_unsubs,
                },
                "unique_opens": {
                    "value": _fmt(total_unique_opens),
                    "spark": spark_unique_opens,
                },
                "deliverability": {
                    "value": _pct(overall_deliverability),
                    "spark": spark_deliverability,
                },
            },
            "top_by_opens": [
                {"name": e["name"], "open_rate": f"{e['open_rate']}%", "sends": _fmt(e["sends"])}
                for e in top_by_opens
            ],
            "top_by_clicks": [
                {"name": e["name"], "click_rate": f"{e['click_rate']}%", "sends": _fmt(e["sends"])}
                for e in top_by_clicks
            ],
            "link_clicks": link_clicks,
        }

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        return {"error": f"Constant Contact API HTTP {status}: {exc}"}
    except requests.exceptions.RequestException as exc:
        return {"error": f"Constant Contact API request failed: {exc}"}
    except Exception as exc:
        return {"error": f"Constant Contact error: {exc}"}


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    key = os.getenv("CONSTANT_CONTACT_API_KEY")
    token = os.getenv("CONSTANT_CONTACT_ACCESS_TOKEN")

    if not key or not token:
        print(
            "Set CONSTANT_CONTACT_API_KEY and CONSTANT_CONTACT_ACCESS_TOKEN "
            "env vars to test."
        )
        raise SystemExit(1)

    start = os.getenv("CC_START_DATE", "2025-03-01")
    end = os.getenv("CC_END_DATE", "2025-03-31")

    result = pull(key, token, start, end)
    print(json.dumps(result, indent=2))
