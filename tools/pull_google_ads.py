"""
Pull Google Ads data for the marketing dashboard.

Required .env keys:
  GOOGLE_ADS_CUSTOMER_ID_RC / GOOGLE_ADS_CUSTOMER_ID_RNR
  GOOGLE_ADS_DEVELOPER_TOKEN
  GOOGLE_APPLICATION_CREDENTIALS (or credentials.json)
"""

import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def fmt_number(n):
    """Format number with commas or K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 100_000:
        return f"{n // 1000}K"
    return f"{n:,.0f}"


def fmt_dollars(n):
    """Format as dollar amount."""
    return f"${n:,.2f}"


def fmt_pct(n):
    """Format as percentage."""
    return f"{n:.2f}%"


def pull(customer_id: str, developer_token: str, start_date: str, end_date: str) -> dict:
    """
    Pull Google Ads campaign performance data.

    Args:
        customer_id: Google Ads customer ID (format: 1234567890, no dashes)
        developer_token: Google Ads API developer token
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD

    Returns:
        Dict with metrics and sparkline data
    """
    if not customer_id or not developer_token:
        return {"error": "Missing Google Ads credentials (customer_id or developer_token)"}

    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError:
        return {"error": "google-ads package not installed. Run: pip install google-ads"}

    try:
        # Strip dashes from customer ID
        customer_id = customer_id.replace("-", "")

        # Build client config
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        config = {
            "developer_token": developer_token,
            "use_proto_plus": True,
            "json_key_file_path": credentials_path,
            "impersonated_email": os.getenv("GOOGLE_ADS_LOGIN_EMAIL", ""),
        }

        # If OAuth refresh token is available, use that instead
        refresh_token = os.getenv("GOOGLE_ADS_REFRESH_TOKEN")
        client_id = os.getenv("GOOGLE_ADS_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_ADS_CLIENT_SECRET")
        if refresh_token and client_id and client_secret:
            config = {
                "developer_token": developer_token,
                "use_proto_plus": True,
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }

        client = GoogleAdsClient.load_from_dict(config)
        ga_service = client.get_service("GoogleAdsService")

        # Query: aggregate metrics for the date range
        query_agg = f"""
            SELECT
                metrics.cost_micros,
                metrics.clicks,
                metrics.impressions,
                metrics.ctr,
                metrics.conversions,
                metrics.cost_per_conversion
            FROM campaign
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
                AND campaign.status = 'ENABLED'
        """

        response_agg = ga_service.search(customer_id=customer_id, query=query_agg)

        total_cost = 0
        total_clicks = 0
        total_impressions = 0
        total_conversions = 0

        for row in response_agg:
            total_cost += row.metrics.cost_micros / 1_000_000
            total_clicks += row.metrics.clicks
            total_impressions += row.metrics.impressions
            total_conversions += row.metrics.conversions

        ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0
        cost_per_conv = (total_cost / total_conversions) if total_conversions > 0 else 0

        # Query: daily breakdown for sparklines
        query_daily = f"""
            SELECT
                segments.date,
                metrics.cost_micros,
                metrics.clicks,
                metrics.impressions,
                metrics.conversions
            FROM campaign
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
                AND campaign.status = 'ENABLED'
            ORDER BY segments.date
        """

        response_daily = ga_service.search(customer_id=customer_id, query=query_daily)

        daily = {}
        for row in response_daily:
            d = row.segments.date
            if d not in daily:
                daily[d] = {"cost": 0, "clicks": 0, "impressions": 0, "conversions": 0}
            daily[d]["cost"] += row.metrics.cost_micros / 1_000_000
            daily[d]["clicks"] += row.metrics.clicks
            daily[d]["impressions"] += row.metrics.impressions
            daily[d]["conversions"] += row.metrics.conversions

        dates = sorted(daily.keys())
        spark_cost = [round(daily[d]["cost"], 2) for d in dates]
        spark_clicks = [daily[d]["clicks"] for d in dates]
        spark_impressions = [daily[d]["impressions"] for d in dates]
        spark_conversions = [int(daily[d]["conversions"]) for d in dates]

        # CTR sparkline
        spark_ctr = []
        for d in dates:
            imp = daily[d]["impressions"]
            cl = daily[d]["clicks"]
            spark_ctr.append(round(cl / imp * 100, 2) if imp > 0 else 0)

        # Cost per conversion sparkline
        spark_cpc = []
        for d in dates:
            c = daily[d]["cost"]
            conv = daily[d]["conversions"]
            spark_cpc.append(round(c / conv, 2) if conv > 0 else 0)

        result = {
            "metrics": {
                "spend": {"value": fmt_dollars(total_cost), "spark": spark_cost},
                "clicks": {"value": fmt_number(total_clicks), "spark": spark_clicks},
                "impressions": {"value": fmt_number(total_impressions), "spark": spark_impressions},
                "ctr": {"value": fmt_pct(ctr), "spark": spark_ctr},
                "conversions": {"value": fmt_number(total_conversions), "spark": spark_conversions},
                "cost_per_conversion": {"value": fmt_dollars(cost_per_conv), "spark": spark_cpc},
            }
        }

        # Phone call metrics
        try:
            query_phone = f"""
                SELECT
                    metrics.phone_calls,
                    metrics.phone_impressions
                FROM campaign
                WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
            """
            response_phone = ga_service.search(customer_id=customer_id, query=query_phone)
            total_phone_calls = 0
            total_phone_impressions = 0
            for row in response_phone:
                total_phone_calls += row.metrics.phone_calls
                total_phone_impressions += row.metrics.phone_impressions
            result["metrics"]["phone_calls"] = {"value": fmt_number(total_phone_calls), "spark": []}
            result["metrics"]["phone_impressions"] = {"value": fmt_number(total_phone_impressions), "spark": []}
        except Exception:
            pass

        return result

    except Exception as e:
        return {"error": f"Google Ads API error: {str(e)}"}


if __name__ == "__main__":
    import json

    customer_id = os.getenv("GOOGLE_ADS_CUSTOMER_ID_RC", "")
    dev_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    result = pull(customer_id, dev_token, "2026-03-08", "2026-04-07")
    print(json.dumps(result, indent=2))
