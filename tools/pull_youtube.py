"""
YouTube Data API v3 pull tool.

Fetches channel statistics and recent video performance from the YouTube
Data API v3 (public data, API-key auth).  Time-range metrics like watch
time and impressions CTR require the YouTube Analytics API + OAuth and are
stubbed with placeholders until OAuth credentials are configured.

Auth: set ``YOUTUBE_API_KEY`` env var.
"""

import os
import re
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE = "https://www.googleapis.com/youtube/v3"
ANALYTICS_BASE = "https://youtubeanalytics.googleapis.com/v2/reports"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(n: float | int, decimals: int = 0) -> str:
    """Format a number with commas and optional decimal places."""
    if decimals:
        return f"{n:,.{decimals}f}"
    return f"{int(n):,}"


def _duration_iso(iso: str) -> str:
    """Convert ISO 8601 duration (PT#H#M#S) to human-readable string."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not match:
        return "0:00"
    h, m, s = (int(v) if v else 0 for v in match.groups())
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _duration_seconds(seconds: float) -> str:
    """Format seconds into Xm YYs string."""
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def _api_get(endpoint: str, params: dict) -> dict:
    """Make a GET request to the YouTube Data API v3."""
    resp = requests.get(f"{BASE}/{endpoint}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Main pull function
# ---------------------------------------------------------------------------

def pull(channel_id: str, api_key: str, start_date: str, end_date: str) -> dict:
    """Pull YouTube channel data for the dashboard.

    Parameters
    ----------
    channel_id : str
        YouTube channel ID (starts with ``UC``).
    api_key : str
        YouTube Data API v3 key.
    start_date : str
        Start of the date range in ``YYYY-MM-DD`` format.
        Used for YouTube Analytics queries (when OAuth is available).
    end_date : str
        End of the date range in ``YYYY-MM-DD`` format.

    Returns
    -------
    dict
        Formatted YouTube data ready for the dashboard, or a dict with
        an ``"error"`` key if something goes wrong.
    """

    if not api_key:
        return {
            "error": (
                "Missing YouTube API key. "
                "Set YOUTUBE_API_KEY env var."
            )
        }

    if not channel_id:
        return {"error": "Missing channel_id."}

    try:
        # ---- 1. Channel statistics ----------------------------------------
        channel_data = _api_get("channels", {
            "part": "statistics,snippet",
            "id": channel_id,
            "key": api_key,
        })

        items = channel_data.get("items", [])
        if not items:
            return {"error": f"Channel not found: {channel_id}"}

        stats = items[0]["statistics"]
        subscriber_count = int(stats.get("subscriberCount", 0))
        total_views = int(stats.get("viewCount", 0))
        video_count = int(stats.get("videoCount", 0))

        # ---- 2. Recent videos (search + details) --------------------------
        # First try videos from the date range
        search_data = _api_get("search", {
            "part": "id,snippet",
            "channelId": channel_id,
            "order": "date",
            "maxResults": 10,
            "type": "video",
            "publishedAfter": f"{start_date}T00:00:00Z",
            "publishedBefore": f"{end_date}T23:59:59Z",
            "key": api_key,
        })

        # If no videos in date range, get top videos by view count (all time)
        if not search_data.get("items"):
            search_data = _api_get("search", {
                "part": "id,snippet",
                "channelId": channel_id,
                "order": "viewCount",
                "maxResults": 10,
                "type": "video",
                "key": api_key,
            })

        video_ids = [
            item["id"]["videoId"]
            for item in search_data.get("items", [])
            if item["id"].get("videoId")
        ]

        top_videos: list[list[str]] = []
        video_view_counts: list[int] = []

        if video_ids:
            details_data = _api_get("videos", {
                "part": "statistics,contentDetails,snippet",
                "id": ",".join(video_ids),
                "key": api_key,
            })

            # Sort by views descending
            vids = sorted(
                details_data.get("items", []),
                key=lambda v: int(v["statistics"].get("viewCount", 0)),
                reverse=True,
            )

            for v in vids[:5]:
                title = v["snippet"]["title"]
                views = int(v["statistics"].get("viewCount", 0))
                duration = _duration_iso(
                    v["contentDetails"].get("duration", "")
                )
                top_videos.append([title, f"{_fmt(views)} views", duration])
                video_view_counts.append(views)

        # ---- 3. Sparkline data --------------------------------------------
        # The public Data API v3 does not provide daily breakdowns.
        # We generate a simple placeholder sparkline from recent video view
        # counts.  Replace with YouTube Analytics API data when OAuth is
        # available.
        spark_views = video_view_counts[:14] if video_view_counts else []
        spark_subs = []  # requires Analytics API

        # ---- 4. YouTube Analytics (OAuth required) ------------------------
        # The following metrics require the YouTube Analytics API with OAuth
        # 2.0 credentials (not just an API key):
        #   - estimatedMinutesWatched  (watch time)
        #   - averageViewDuration
        #   - impressions / impressionClickThroughRate
        #
        # When OAuth is set up, uncomment the block below and pass the
        # authorized session.  Until then, return placeholder values.

        watch_time_val = None
        avg_view_duration_val = None
        impressions_ctr_val = None
        spark_watch_time: list[int] = []
        spark_avg_duration: list[float] = []
        spark_ctr: list[float] = []

        oauth_token = os.getenv("YOUTUBE_OAUTH_TOKEN")
        if oauth_token:
            try:
                analytics_params = {
                    "ids": f"channel=={channel_id}",
                    "startDate": start_date,
                    "endDate": end_date,
                    "metrics": (
                        "estimatedMinutesWatched,"
                        "averageViewDuration,"
                        "views,"
                        "subscribersGained,"
                        "annotationClickThroughRate"
                    ),
                    "dimensions": "day",
                    "sort": "day",
                }
                headers = {"Authorization": f"Bearer {oauth_token}"}
                analytics_resp = requests.get(
                    ANALYTICS_BASE,
                    params=analytics_params,
                    headers=headers,
                    timeout=30,
                )
                analytics_resp.raise_for_status()
                analytics_data = analytics_resp.json()

                total_watch_mins = 0
                total_avg_dur = 0
                total_ctr = 0
                rows = analytics_data.get("rows", [])

                for row in rows:
                    # row: [day, estimatedMinutesWatched, avgViewDuration,
                    #        views, subsGained, ctr]
                    spark_watch_time.append(int(row[1]))
                    spark_avg_duration.append(round(float(row[2]), 1))
                    spark_ctr.append(round(float(row[5]) * 100, 1))
                    total_watch_mins += float(row[1])
                    total_avg_dur += float(row[2])
                    total_ctr += float(row[5])

                day_count = len(rows) or 1
                watch_time_val = total_watch_mins / 60  # hours
                avg_view_duration_val = total_avg_dur / day_count
                impressions_ctr_val = (total_ctr / day_count) * 100

                # Override sparklines with real daily data
                spark_views_daily = [int(row[3]) for row in rows]
                if spark_views_daily:
                    spark_views = spark_views_daily
                spark_subs = [int(row[4]) for row in rows]

            except Exception:
                # Fall through to placeholder values
                pass

        # ---- Build response -----------------------------------------------
        metrics = {
            "subscribers": {
                "value": _fmt(subscriber_count),
                "spark": spark_subs or [],
            },
            "views": {
                "value": _fmt(total_views),
                "spark": spark_views,
            },
        }

        # Only include OAuth-dependent metrics if we have real data
        if watch_time_val is not None:
            metrics["watch_time"] = {
                "value": f"{_fmt(watch_time_val, decimals=0)}h",
                "spark": spark_watch_time,
            }
        if avg_view_duration_val is not None:
            metrics["avg_view_duration"] = {
                "value": _duration_seconds(avg_view_duration_val),
                "spark": spark_avg_duration,
            }
        if impressions_ctr_val is not None:
            metrics["impressions_ctr"] = {
                "value": f"{impressions_ctr_val:.1f}%",
                "spark": spark_ctr,
            }

        return {
            "metrics": metrics,
            "top_videos": top_videos,
        }

    except requests.HTTPError as exc:
        return {"error": f"YouTube API HTTP error: {exc.response.status_code} - {exc.response.text}"}
    except Exception as exc:
        return {"error": f"YouTube API error: {exc}"}


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    api_key = os.getenv("YOUTUBE_API_KEY")
    channel = os.getenv("YOUTUBE_CHANNEL_ID")

    if not api_key or not channel:
        print("Set YOUTUBE_API_KEY and YOUTUBE_CHANNEL_ID env vars to test.")
        raise SystemExit(1)

    start = os.getenv("YT_START_DATE", "2025-03-01")
    end = os.getenv("YT_END_DATE", "2025-03-31")

    result = pull(channel, api_key, start, end)
    print(json.dumps(result, indent=2))
