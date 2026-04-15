"""
Live Google Ads query tools for the Ask AI agent.

Routes to the brand-specific customer ID from .env based on the `brand` argument.
Reuses the existing pull logic in tools/pull_google_ads.py where possible.

Required .env:
  GOOGLE_ADS_DEVELOPER_TOKEN
  GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET / GOOGLE_ADS_REFRESH_TOKEN  (OAuth)
  GOOGLE_ADS_LOGIN_CUSTOMER_ID  (manager account, optional)
  GOOGLE_ADS_CUSTOMER_ID_RC / _RNR / _WL
"""

import os
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()


def _customer_id(brand: str) -> str:
    key = f"GOOGLE_ADS_CUSTOMER_ID_{brand.upper()}"
    return (os.environ.get(key) or "").replace("-", "").strip()


def _client():
    """Build a GoogleAdsClient from env. Returns (client, error_dict). Only one will be non-None."""
    dev_token = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
    client_id = os.environ.get("GOOGLE_ADS_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_ADS_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_ADS_REFRESH_TOKEN")
    login_cid = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").replace("-", "").strip()

    missing = [k for k, v in [
        ("GOOGLE_ADS_DEVELOPER_TOKEN", dev_token),
        ("GOOGLE_ADS_CLIENT_ID", client_id),
        ("GOOGLE_ADS_CLIENT_SECRET", client_secret),
        ("GOOGLE_ADS_REFRESH_TOKEN", refresh_token),
    ] if not v]
    if missing:
        return None, {
            "error": "Google Ads auth not configured.",
            "missing_env_vars": missing,
            "how_to_fix": (
                "1) Get a developer token from the Google Ads API Center. "
                "2) Create an OAuth client (Desktop app) in Google Cloud Console. "
                "3) Run the OAuth flow to get a refresh token (google-ads SDK has a helper). "
                "4) Add all four values to .env. "
                "5) If your account is under a manager (MCC), also set GOOGLE_ADS_LOGIN_CUSTOMER_ID."
            ),
        }

    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError:
        return None, {"error": "google-ads package not installed. Run: pip3 install --break-system-packages google-ads"}

    config = {
        "developer_token": dev_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "use_proto_plus": True,
    }
    if login_cid:
        config["login_customer_id"] = login_cid

    try:
        return GoogleAdsClient.load_from_dict(config), None
    except Exception as e:
        return None, {"error": f"Failed to build GoogleAdsClient: {e}"}


def _run_gaql(brand: str, query: str) -> dict:
    cid = _customer_id(brand)
    if not cid:
        return {"error": f"No GOOGLE_ADS_CUSTOMER_ID set for brand '{brand}'."}
    client, err = _client()
    if err:
        return err
    service = client.get_service("GoogleAdsService")
    try:
        rows = []
        stream = service.search_stream(customer_id=cid, query=query)
        for batch in stream:
            for row in batch.results:
                rows.append(row)
        return {"rows": rows}
    except Exception as e:
        return {"error": f"Google Ads API error: {e}"}


def _default_range(start_date: str, end_date: str) -> tuple[str, str]:
    end = end_date or datetime.now().strftime("%Y-%m-%d")
    start = start_date or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    return start, end


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def account_summary(brand: str = "rc", start_date: str = "", end_date: str = "") -> dict:
    """High-level Google Ads account metrics for a brand over a date range."""
    start, end = _default_range(start_date, end_date)
    query = f"""
        SELECT metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions, metrics.conversions_value,
               metrics.ctr, metrics.average_cpc
        FROM customer
        WHERE segments.date BETWEEN '{start}' AND '{end}'
    """
    result = _run_gaql(brand, query)
    if "error" in result:
        return result
    imp = clicks = cost = conv = conv_val = 0.0
    for r in result["rows"]:
        imp += r.metrics.impressions
        clicks += r.metrics.clicks
        cost += r.metrics.cost_micros / 1_000_000
        conv += r.metrics.conversions
        conv_val += r.metrics.conversions_value
    return {
        "brand": brand,
        "date_range": {"start": start, "end": end},
        "impressions": int(imp),
        "clicks": int(clicks),
        "cost": round(cost, 2),
        "conversions": round(conv, 1),
        "conversions_value": round(conv_val, 2),
        "ctr_pct": round((clicks / imp * 100) if imp else 0, 2),
        "avg_cpc": round((cost / clicks) if clicks else 0, 2),
        "cost_per_conversion": round((cost / conv) if conv else 0, 2),
        "roas": round((conv_val / cost) if cost else 0, 2),
    }


def list_campaigns(brand: str = "rc", start_date: str = "", end_date: str = "", limit: int = 25) -> dict:
    """Per-campaign performance: impressions, clicks, cost, conversions."""
    start, end = _default_range(start_date, end_date)
    query = f"""
        SELECT campaign.id, campaign.name, campaign.status,
               metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions, metrics.conversions_value
        FROM campaign
        WHERE segments.date BETWEEN '{start}' AND '{end}'
        ORDER BY metrics.cost_micros DESC
        LIMIT {max(1, min(int(limit or 25), 100))}
    """
    result = _run_gaql(brand, query)
    if "error" in result:
        return result
    rows = []
    for r in result["rows"]:
        cost = r.metrics.cost_micros / 1_000_000
        rows.append({
            "id": str(r.campaign.id),
            "name": r.campaign.name,
            "status": r.campaign.status.name if hasattr(r.campaign.status, "name") else str(r.campaign.status),
            "impressions": int(r.metrics.impressions),
            "clicks": int(r.metrics.clicks),
            "cost": round(cost, 2),
            "conversions": round(r.metrics.conversions, 1),
            "conversions_value": round(r.metrics.conversions_value, 2),
            "cost_per_conversion": round(cost / r.metrics.conversions, 2) if r.metrics.conversions else None,
        })
    return {
        "brand": brand,
        "date_range": {"start": start, "end": end},
        "campaigns": rows,
    }


def top_search_terms(brand: str = "rc", start_date: str = "", end_date: str = "", limit: int = 25) -> dict:
    """Top search queries that triggered ads, ranked by clicks."""
    start, end = _default_range(start_date, end_date)
    query = f"""
        SELECT search_term_view.search_term, campaign.name,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
        FROM search_term_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
        ORDER BY metrics.clicks DESC
        LIMIT {max(1, min(int(limit or 25), 100))}
    """
    result = _run_gaql(brand, query)
    if "error" in result:
        return result
    rows = [{
        "term": r.search_term_view.search_term,
        "campaign": r.campaign.name,
        "impressions": int(r.metrics.impressions),
        "clicks": int(r.metrics.clicks),
        "cost": round(r.metrics.cost_micros / 1_000_000, 2),
        "conversions": round(r.metrics.conversions, 1),
    } for r in result["rows"]]
    return {"brand": brand, "date_range": {"start": start, "end": end}, "search_terms": rows}


def geo_performance(brand: str = "rc", start_date: str = "", end_date: str = "", limit: int = 25) -> dict:
    """Performance by geographic location (city/region)."""
    start, end = _default_range(start_date, end_date)
    query = f"""
        SELECT geographic_view.country_criterion_id, campaign.name,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
        FROM geographic_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
        ORDER BY metrics.clicks DESC
        LIMIT {max(1, min(int(limit or 25), 100))}
    """
    result = _run_gaql(brand, query)
    if "error" in result:
        return result
    rows = [{
        "country_criterion_id": str(r.geographic_view.country_criterion_id),
        "campaign": r.campaign.name,
        "impressions": int(r.metrics.impressions),
        "clicks": int(r.metrics.clicks),
        "cost": round(r.metrics.cost_micros / 1_000_000, 2),
        "conversions": round(r.metrics.conversions, 1),
    } for r in result["rows"]]
    return {"brand": brand, "date_range": {"start": start, "end": end}, "geo": rows}


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS_SCHEMA = [
    {
        "name": "google_ads_account_summary",
        "description": "High-level Google Ads account metrics (impressions, clicks, cost, conversions, CTR, CPC, ROAS) for the specified brand and date range. If brand is omitted, uses current chat brand.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "description": "'rc', 'rnr', or 'wl'. Default: current brand."},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
            },
        },
    },
    {
        "name": "google_ads_list_campaigns",
        "description": "Per-campaign performance sorted by cost desc. Use for 'top ad campaigns', 'what's our best-performing campaign', spend breakdowns.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "google_ads_top_search_terms",
        "description": "Top search queries that triggered ads, ranked by clicks. Use for 'what are people searching' or 'what keywords drive the most traffic'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "google_ads_geo_performance",
        "description": "Google Ads performance broken down by geographic location. Use for 'where are our leads coming from geographically', 'which regions convert best'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
]


TOOL_IMPLS = {
    "google_ads_account_summary": account_summary,
    "google_ads_list_campaigns": list_campaigns,
    "google_ads_top_search_terms": top_search_terms,
    "google_ads_geo_performance": geo_performance,
}


def run_tool(name: str, args: dict, default_brand: str = "rc") -> dict:
    fn = TOOL_IMPLS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    args = dict(args or {})
    if "brand" not in args:
        args["brand"] = default_brand
    try:
        return fn(**args)
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} failed: {e}"}
