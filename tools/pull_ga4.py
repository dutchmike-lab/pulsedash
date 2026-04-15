"""
GA4 Data API pull tool.

Fetches analytics metrics, top pages, traffic sources, and daily sparkline
data from a Google Analytics 4 property using the GA4 Data API (v1beta).

Auth: set GOOGLE_APPLICATION_CREDENTIALS env var to a service-account JSON
path, or place a ``credentials.json`` file in the project root.
"""

import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SOURCE_COLORS = [
    "#4f7cff", "#34d399", "#f59e42", "#ef4444", "#a78bfa",
    "#f472b6", "#38bdf8", "#facc15", "#818cf8", "#fb923c",
]


def _fmt(n: float | int, decimals: int = 0) -> str:
    """Format a number with commas and optional decimal places."""
    if decimals:
        return f"{n:,.{decimals}f}"
    return f"{int(n):,}"


def _pct(n: float) -> str:
    return f"{n:.1f}%"


def _duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


# ---------------------------------------------------------------------------
# Main pull function
# ---------------------------------------------------------------------------

def pull(property_id: str, start_date: str, end_date: str) -> dict:
    """Pull GA4 analytics data for the given property and date range.

    Parameters
    ----------
    property_id : str
        GA4 property ID (numeric, e.g. ``"123456789"``).
    start_date : str
        Start of the date range in ``YYYY-MM-DD`` format.
    end_date : str
        End of the date range in ``YYYY-MM-DD`` format.

    Returns
    -------
    dict
        Formatted analytics data ready for the dashboard, or a dict with
        an ``"error"`` key if something goes wrong.
    """

    # --- Credential check ---------------------------------------------------
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        fallback = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "credentials.json",
        )
        if os.path.exists(fallback):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = fallback
        else:
            return {
                "error": (
                    "Missing credentials. Set GOOGLE_APPLICATION_CREDENTIALS "
                    "env var or place credentials.json in the project root."
                )
            }

    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            Metric,
            RunReportRequest,
        )
    except ImportError:
        return {
            "error": (
                "google-analytics-data package not installed. "
                "Run: pip install google-analytics-data"
            )
        }

    try:
        client = BetaAnalyticsDataClient()
        prop = f"properties/{property_id}"
        date_range = DateRange(start_date=start_date, end_date=end_date)

        # ---- 1. Overall metrics -------------------------------------------
        overview_req = RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            metrics=[
                Metric(name="totalUsers"),
                Metric(name="sessions"),
                Metric(name="bounceRate"),
                Metric(name="averageSessionDuration"),
                Metric(name="newUsers"),
                Metric(name="screenPageViewsPerSession"),
            ],
        )
        overview = client.run_report(overview_req)

        row = overview.rows[0] if overview.rows else None
        if row:
            visitors_val = float(row.metric_values[0].value)
            sessions_val = float(row.metric_values[1].value)
            bounce_val = float(row.metric_values[2].value)
            duration_val = float(row.metric_values[3].value)
            new_users_val = float(row.metric_values[4].value)
            pages_per_session_val = float(row.metric_values[5].value)
        else:
            visitors_val = sessions_val = bounce_val = 0
            duration_val = new_users_val = pages_per_session_val = 0

        # ---- 2. Daily totals (sparkline) ----------------------------------
        daily_req = RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            dimensions=[Dimension(name="date")],
            metrics=[
                Metric(name="totalUsers"),
                Metric(name="sessions"),
                Metric(name="bounceRate"),
                Metric(name="averageSessionDuration"),
                Metric(name="newUsers"),
                Metric(name="screenPageViewsPerSession"),
            ],
            order_bys=[
                {
                    "dimension": {"dimension_name": "date"},
                    "desc": False,
                }
            ],
        )
        daily = client.run_report(daily_req)

        spark_visitors: list[int] = []
        spark_sessions: list[int] = []
        spark_bounce: list[float] = []
        spark_duration: list[float] = []
        spark_new_users: list[int] = []
        spark_pps: list[float] = []

        for r in daily.rows:
            spark_visitors.append(int(float(r.metric_values[0].value)))
            spark_sessions.append(int(float(r.metric_values[1].value)))
            spark_bounce.append(round(float(r.metric_values[2].value), 1))
            spark_duration.append(round(float(r.metric_values[3].value), 1))
            spark_new_users.append(int(float(r.metric_values[4].value)))
            spark_pps.append(round(float(r.metric_values[5].value), 1))

        # ---- 3. Top 5 pages by views -------------------------------------
        pages_req = RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            order_bys=[
                {
                    "metric": {"metric_name": "screenPageViews"},
                    "desc": True,
                }
            ],
            limit=5,
        )
        pages = client.run_report(pages_req)

        total_views = sum(
            int(r.metric_values[0].value) for r in pages.rows
        )
        top_pages: list[list[str]] = []
        for r in pages.rows:
            path = r.dimension_values[0].value
            views = int(r.metric_values[0].value)
            pct = (views / total_views * 100) if total_views else 0
            top_pages.append([path, _fmt(views), _pct(pct)])

        # ---- 3b. Top 5 blog articles by views ------------------------------
        from google.analytics.data_v1beta.types import Filter, FilterExpression
        blog_req = RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
            metrics=[Metric(name="screenPageViews"), Metric(name="averageSessionDuration")],
            dimension_filter=FilterExpression(
                filter=Filter(
                    field_name="pagePath",
                    string_filter=Filter.StringFilter(
                        match_type=Filter.StringFilter.MatchType.CONTAINS,
                        value="/blog",
                    ),
                )
            ),
            order_bys=[{"metric": {"metric_name": "screenPageViews"}, "desc": True}],
            limit=5,
        )
        try:
            blog_resp = client.run_report(blog_req)
            top_blogs = []
            for r in blog_resp.rows:
                path = r.dimension_values[0].value
                title = r.dimension_values[1].value or path
                views = int(r.metric_values[0].value)
                avg_dur = float(r.metric_values[1].value)
                dur_str = f"{int(avg_dur // 60)}m {int(avg_dur % 60)}s"
                top_blogs.append({"path": path, "title": title, "views": _fmt(views), "avg_duration": dur_str})
        except Exception:
            top_blogs = []

        # ---- 4. Traffic sources -------------------------------------------
        sources_req = RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            dimensions=[Dimension(name="sessionDefaultChannelGroup")],
            metrics=[Metric(name="totalUsers")],
            order_bys=[
                {
                    "metric": {"metric_name": "totalUsers"},
                    "desc": True,
                }
            ],
        )
        sources = client.run_report(sources_req)

        total_source_users = sum(
            int(r.metric_values[0].value) for r in sources.rows
        )
        traffic_sources: list[dict] = []
        for i, r in enumerate(sources.rows):
            name = r.dimension_values[0].value
            val = int(r.metric_values[0].value)
            pct = round(val / total_source_users * 100) if total_source_users else 0
            traffic_sources.append(
                {
                    "name": name,
                    "val": _fmt(val),
                    "pct": pct,
                    "color": SOURCE_COLORS[i % len(SOURCE_COLORS)],
                }
            )

        # ---- 5. Landing page conversion rates ------------------------------
        landing_pages = []
        try:
            from google.analytics.data_v1beta.types import OrderBy
            lp_req = RunReportRequest(
                property=prop,
                date_ranges=[date_range],
                dimensions=[Dimension(name="landingPage")],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="conversions"),
                    Metric(name="bounceRate"),
                ],
                order_bys=[
                    {
                        "metric": {"metric_name": "conversions"},
                        "desc": True,
                    }
                ],
                limit=10,
            )
            lp_resp = client.run_report(lp_req)
            for r in lp_resp.rows:
                sess = int(float(r.metric_values[0].value))
                conv = int(float(r.metric_values[1].value))
                br = float(r.metric_values[2].value)
                conv_rate = (conv / sess * 100) if sess > 0 else 0.0
                landing_pages.append({
                    "page": r.dimension_values[0].value,
                    "sessions": sess,
                    "conversions": conv,
                    "conv_rate": f"{conv_rate:.1f}%",
                    "bounce_rate": f"{br:.1f}%",
                })
        except Exception:
            landing_pages = []

        # ---- 6. New vs returning visitors --------------------------------
        new_vs_returning = {"new": 0, "returning": 0}
        try:
            nvr_req = RunReportRequest(
                property=prop,
                date_ranges=[date_range],
                dimensions=[Dimension(name="newVsReturning")],
                metrics=[Metric(name="activeUsers")],
            )
            nvr_resp = client.run_report(nvr_req)
            for r in nvr_resp.rows:
                label = r.dimension_values[0].value.lower()
                users = int(float(r.metric_values[0].value))
                if label == "new":
                    new_vs_returning["new"] = users
                elif label == "returning":
                    new_vs_returning["returning"] = users
        except Exception:
            new_vs_returning = {"new": 0, "returning": 0}

        # ---- Build response -----------------------------------------------
        result = {
            "metrics": {
                "visitors": {"value": _fmt(visitors_val), "spark": spark_visitors},
                "sessions": {"value": _fmt(sessions_val), "spark": spark_sessions},
                "bounce_rate": {"value": _pct(bounce_val), "spark": spark_bounce},
                "avg_session_duration": {
                    "value": _duration(duration_val),
                    "spark": spark_duration,
                },
                "new_users": {"value": _fmt(new_users_val), "spark": spark_new_users},
                "pages_per_session": {
                    "value": _fmt(pages_per_session_val, decimals=1),
                    "spark": spark_pps,
                },
            },
            "top_pages": top_pages,
            "top_blogs": top_blogs,
            "traffic_sources": traffic_sources,
            "landing_pages": landing_pages,
            "new_vs_returning": new_vs_returning,
        }
        return result

    except Exception as exc:
        return {"error": f"GA4 API error: {exc}"}


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    pid = os.getenv("GA4_PROPERTY_ID")
    if not pid:
        print("Set GA4_PROPERTY_ID env var to test.")
        raise SystemExit(1)

    start = os.getenv("GA4_START_DATE", "2025-03-01")
    end = os.getenv("GA4_END_DATE", "2025-03-31")

    result = pull(pid, start, end)
    print(json.dumps(result, indent=2))
