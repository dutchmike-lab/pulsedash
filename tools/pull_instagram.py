"""
Pull Instagram organic metrics via the Instagram Graph API.

Returns followers, engagement rate, reach, impressions, and profile visits
with daily sparkline arrays for the requested date range.
"""

import os
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_number(n: float) -> str:
    """Format a number with commas and K/M suffix for large values."""
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:,.1f}M"
    if abs(n) >= 100_000:
        return f"{n / 1_000:,.0f}K"
    if isinstance(n, float) and n != int(n):
        return f"{n:,.2f}"
    return f"{int(n):,}"


def _fmt_pct(n: float) -> str:
    """Format a percentage value."""
    return f"{n:.1f}%"


def _graph_get(endpoint: str, params: dict) -> dict:
    """Make a GET request to the Graph API and return JSON."""
    url = f"{GRAPH_API_BASE}/{endpoint}"
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _extract_daily_values(insights_data: list, metric_name: str) -> list[int]:
    """Extract a daily value list from an insights response for a given metric."""
    for item in insights_data:
        if item.get("name") == metric_name:
            return [v["value"] for v in item.get("values", [])]
    return []


# ---------------------------------------------------------------------------
# Main pull function
# ---------------------------------------------------------------------------

def pull(
    ig_account_id: str,
    access_token: str,
    start_date: str,
    end_date: str,
) -> dict:
    """
    Pull Instagram organic metrics for a given account and date range.

    Parameters
    ----------
    ig_account_id : str
        The Instagram Business/Creator account ID.
    access_token : str
        A valid Facebook/Instagram Graph API access token.
    start_date : str
        Start date in ``YYYY-MM-DD`` format.
    end_date : str
        End date in ``YYYY-MM-DD`` format.

    Returns
    -------
    dict
        A ``metrics`` dict with formatted values and daily spark arrays,
        or an ``error`` dict on failure.
    """
    if not ig_account_id or not access_token:
        return {"error": "Missing ig_account_id or access_token."}

    try:
        # Convert dates to Unix timestamps for the insights endpoint
        since_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        until_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp())

        # ------------------------------------------------------------------
        # 1. Account info (followers, media count)
        # ------------------------------------------------------------------
        account_info = _graph_get(ig_account_id, {
            "fields": "followers_count,media_count",
            "access_token": access_token,
        })
        followers_count = account_info.get("followers_count", 0)

        # ------------------------------------------------------------------
        # 2. Account insights (impressions, reach, profile_views) — daily
        # ------------------------------------------------------------------
        insights_resp = _graph_get(f"{ig_account_id}/insights", {
            "metric": "impressions,reach,profile_views",
            "period": "day",
            "since": since_ts,
            "until": until_ts,
            "access_token": access_token,
        })
        insights_data = insights_resp.get("data", [])

        daily_impressions = _extract_daily_values(insights_data, "impressions")
        daily_reach = _extract_daily_values(insights_data, "reach")
        daily_profile_views = _extract_daily_values(insights_data, "profile_views")

        total_impressions = sum(daily_impressions)
        total_reach = sum(daily_reach)
        total_profile_views = sum(daily_profile_views)

        # ------------------------------------------------------------------
        # 3. Recent media — engagement (likes + comments) and reach per post
        # ------------------------------------------------------------------
        media_resp = _graph_get(f"{ig_account_id}/media", {
            "fields": "like_count,comments_count,timestamp",
            "limit": 50,
            "access_token": access_token,
        })
        media_items = media_resp.get("data", [])

        # Filter media within the date range
        total_engagement = 0
        daily_engagement: dict[str, int] = {}

        for item in media_items:
            ts = item.get("timestamp", "")
            post_date = ts[:10] if ts else ""
            if post_date and start_date <= post_date <= end_date:
                likes = item.get("like_count", 0)
                comments = item.get("comments_count", 0)
                eng = likes + comments
                total_engagement += eng
                daily_engagement[post_date] = daily_engagement.get(post_date, 0) + eng

        # Build a daily engagement spark (aligned to reach days if available)
        engagement_spark = list(daily_engagement.values()) if daily_engagement else []

        # Engagement rate = total engagement / followers * 100
        engagement_rate = (total_engagement / followers_count * 100) if followers_count else 0.0

        # For followers spark we don't have historical daily data from this
        # endpoint, so we return a single-point list.  A production version
        # could use the ``follower_count`` insight (available on some accounts).
        followers_spark = [followers_count]

        return {
            "metrics": {
                "followers": {
                    "value": _fmt_number(followers_count),
                    "spark": followers_spark,
                },
                "engagement_rate": {
                    "value": _fmt_pct(engagement_rate),
                    "spark": engagement_spark,
                },
                "reach": {
                    "value": _fmt_number(total_reach),
                    "spark": daily_reach,
                },
                "impressions": {
                    "value": _fmt_number(total_impressions),
                    "spark": daily_impressions,
                },
                "profile_visits": {
                    "value": _fmt_number(total_profile_views),
                    "spark": daily_profile_views,
                },
            }
        }

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = ""
        try:
            body = exc.response.json().get("error", {}).get("message", "")
        except Exception:
            pass
        return {"error": f"Instagram API HTTP {status}: {body or exc}"}
    except Exception as exc:
        return {"error": f"Instagram API error: {exc}"}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    result = pull(
        ig_account_id=os.getenv("INSTAGRAM_ACCOUNT_ID", ""),
        access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN", ""),
        start_date=os.getenv("IG_START_DATE", "2026-03-01"),
        end_date=os.getenv("IG_END_DATE", "2026-03-31"),
    )
    print(json.dumps(result, indent=2))
