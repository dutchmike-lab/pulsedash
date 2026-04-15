"""
Pull Meta Ads data via the facebook-business SDK.

Returns spend, clicks, impressions, CTR, conversions, and cost-per-conversion
with daily sparkline arrays for the requested date range.
"""

import os
from dotenv import load_dotenv

load_dotenv()

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


def _fmt_dollar(n: float) -> str:
    """Format a dollar amount."""
    if abs(n) >= 100_000:
        return f"${n / 1_000:,.0f}K"
    return f"${n:,.2f}"


def _fmt_pct(n: float) -> str:
    """Format a percentage value."""
    return f"{n:.2f}%"


# ---------------------------------------------------------------------------
# Main pull function
# ---------------------------------------------------------------------------

def pull(
    ad_account_id: str,
    access_token: str,
    start_date: str,
    end_date: str,
) -> dict:
    """
    Pull Meta Ads insights for a given ad account and date range.

    Parameters
    ----------
    ad_account_id : str
        The Meta ad account ID (e.g. ``act_123456789``).
    access_token : str
        A valid Meta Marketing API access token.
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
    if not ad_account_id or not access_token:
        return {"error": "Missing ad_account_id or access_token."}

    try:
        from facebook_business.api import FacebookAdsApi
        from facebook_business.adobjects.adaccount import AdAccount
    except ImportError:
        return {"error": "facebook-business SDK is not installed. Run: pip install facebook-business"}

    try:
        FacebookAdsApi.init(access_token=access_token)
        account = AdAccount(ad_account_id)

        params = {
            "time_range": {"since": start_date, "until": end_date},
            "time_increment": 1,  # daily breakdown
        }
        fields = [
            "spend",
            "clicks",
            "impressions",
            "ctr",
            "actions",
            "cost_per_action_type",
            "frequency",
            "reach",
            "cpm",
        ]

        insights = account.get_insights(params=params, fields=fields)

        # Accumulate daily values
        daily_spend = []
        daily_clicks = []
        daily_impressions = []
        daily_ctr = []
        daily_conversions = []
        daily_cpc = []  # cost per conversion
        daily_reach = []

        for row in insights:
            daily_spend.append(float(row.get("spend", 0)))
            daily_clicks.append(int(row.get("clicks", 0)))
            daily_impressions.append(int(row.get("impressions", 0)))
            daily_ctr.append(float(row.get("ctr", 0)))

            # Extract conversions from actions list
            conversions = 0
            actions = row.get("actions", [])
            for action in actions:
                if action.get("action_type") in (
                    "offsite_conversion.fb_pixel_purchase",
                    "offsite_conversion.fb_pixel_lead",
                    "lead",
                    "purchase",
                    "complete_registration",
                ):
                    conversions += int(action.get("value", 0))
            daily_conversions.append(conversions)

            # Extract cost per conversion
            cost_per = 0.0
            cost_actions = row.get("cost_per_action_type", [])
            for action in cost_actions:
                if action.get("action_type") in (
                    "offsite_conversion.fb_pixel_purchase",
                    "offsite_conversion.fb_pixel_lead",
                    "lead",
                    "purchase",
                    "complete_registration",
                ):
                    cost_per = float(action.get("value", 0))
                    break
            daily_cpc.append(cost_per)
            daily_reach.append(int(row.get("reach", 0)))

        total_spend = sum(daily_spend)
        total_clicks = sum(daily_clicks)
        total_impressions = sum(daily_impressions)
        avg_ctr = (total_clicks / total_impressions * 100) if total_impressions else 0.0
        total_conversions = sum(daily_conversions)
        avg_cost_per_conversion = (total_spend / total_conversions) if total_conversions else 0.0

        # Compute frequency, reach, CPM from the daily rows
        total_reach = sum(daily_reach)
        avg_frequency = (total_impressions / total_reach) if total_reach > 0 else 0.0
        avg_cpm = (total_spend / total_impressions * 1000) if total_impressions > 0 else 0.0

        result = {
            "metrics": {
                "spend": {
                    "value": _fmt_dollar(total_spend),
                    "spark": daily_spend,
                },
                "clicks": {
                    "value": _fmt_number(total_clicks),
                    "spark": daily_clicks,
                },
                "impressions": {
                    "value": _fmt_number(total_impressions),
                    "spark": daily_impressions,
                },
                "ctr": {
                    "value": _fmt_pct(avg_ctr),
                    "spark": daily_ctr,
                },
                "conversions": {
                    "value": _fmt_number(total_conversions),
                    "spark": daily_conversions,
                },
                "cost_per_conversion": {
                    "value": _fmt_dollar(avg_cost_per_conversion),
                    "spark": daily_cpc,
                },
                "frequency": {
                    "value": f"{avg_frequency:.1f}",
                    "spark": [],
                },
                "reach": {
                    "value": _fmt_number(total_reach),
                    "spark": [],
                },
                "cpm": {
                    "value": f"${avg_cpm:.2f}",
                    "spark": [],
                },
            }
        }

        # Placement breakdown query
        try:
            placement_params = {
                "time_range": {"since": start_date, "until": end_date},
                "breakdowns": ["publisher_platform", "platform_position"],
            }
            placement_fields = ["impressions", "clicks", "spend"]
            placement_insights = account.get_insights(
                params=placement_params, fields=placement_fields
            )
            placements = []
            for row in placement_insights:
                placements.append({
                    "platform": row.get("publisher_platform", ""),
                    "position": row.get("platform_position", ""),
                    "impressions": int(row.get("impressions", 0)),
                    "clicks": int(row.get("clicks", 0)),
                    "spend": float(row.get("spend", 0)),
                })
                if len(placements) >= 20:
                    break
            result["placements"] = placements
        except Exception:
            result["placements"] = []

        return result

    except Exception as exc:
        return {"error": f"Meta Ads API error: {exc}"}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    result = pull(
        ad_account_id=os.getenv("META_AD_ACCOUNT_ID", ""),
        access_token=os.getenv("META_ACCESS_TOKEN", ""),
        start_date=os.getenv("META_START_DATE", "2026-03-01"),
        end_date=os.getenv("META_END_DATE", "2026-03-31"),
    )
    print(json.dumps(result, indent=2))
