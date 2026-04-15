"""
Master data pull script.
Runs all individual tools and combines output into a single data.json
that the dashboard HTML reads from.

Usage:
    python tools/pull_all.py
    python tools/pull_all.py --days 7
    python tools/pull_all.py --start 2026-03-01 --end 2026-03-31
"""

import os
import re
import sys
import json
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()


# ---------------------------------------------------------------------------
# Period-over-period helpers
# ---------------------------------------------------------------------------

def get_previous_range(start_date: str, end_date: str) -> tuple:
    """Calculate the previous period of equal length.

    If current is 30 days (Mar 10 - Apr 8), previous is Feb 8 - Mar 9.
    Returns (prev_start, prev_end) as ISO date strings.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    duration = (end - start).days + 1  # inclusive day count
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=duration - 1)
    return prev_start.strftime("%Y-%m-%d"), prev_end.strftime("%Y-%m-%d")


def compute_trend(current_val, previous_val, is_rate: bool = False) -> tuple:
    """Compute trend string and direction between two numeric values.

    For regular metrics: percentage change like "+12.3%".
    For rates (is_rate=True): absolute point change like "+3.1pp".
    Returns ("--", "neutral") when previous is None or 0.
    """
    if previous_val is None or previous_val == 0:
        return ("--", "neutral")
    if current_val is None:
        return ("--", "neutral")

    if is_rate:
        # Absolute point change for rates
        diff = current_val - previous_val
        sign = "+" if diff >= 0 else ""
        direction = "up" if diff > 0 else ("down" if diff < 0 else "neutral")
        return (f"{sign}{diff:.1f}pp", direction)
    else:
        # Percentage change for regular metrics
        change = ((current_val - previous_val) / abs(previous_val)) * 100
        sign = "+" if change >= 0 else ""
        direction = "up" if change > 0 else ("down" if change < 0 else "neutral")
        return (f"{sign}{change:.1f}%", direction)


def extract_raw_number(source: dict, metric_key: str) -> float | None:
    """Extract numeric value from formatted strings.

    Handles formats like "$1,234", "45.2%", "1.2K", "2m 14s", "3h 5m", "1,234".
    Returns float or None if extraction fails.
    """
    try:
        val = source.get("metrics", {}).get(metric_key, {}).get("value")
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        val = str(val).strip()
        if not val or val in ("N/A", "--", ""):
            return None

        # Duration format: "2m 14s", "3h 5m 2s", "1h", "45s"
        duration_match = re.match(r'^(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?$', val)
        if duration_match and any(duration_match.groups()):
            hours = int(duration_match.group(1) or 0)
            minutes = int(duration_match.group(2) or 0)
            seconds = int(duration_match.group(3) or 0)
            return float(hours * 3600 + minutes * 60 + seconds)

        # Strip currency symbol and commas
        cleaned = val.replace("$", "").replace(",", "").strip()

        # Percentage
        if cleaned.endswith("%"):
            return float(cleaned[:-1])

        # K/M suffixes
        if cleaned.upper().endswith("K"):
            return float(cleaned[:-1]) * 1_000
        if cleaned.upper().endswith("M"):
            return float(cleaned[:-1]) * 1_000_000

        return float(cleaned)
    except (ValueError, AttributeError, TypeError):
        return None


def add_trends_to_metrics(current_source: dict, previous_source: dict,
                          rate_keys: set | None = None) -> None:
    """Iterate over current_source metrics, compute trend vs previous, mutate in place.

    Adds "trend" and "dir" fields to each metric dict in current_source["metrics"].
    rate_keys: set of metric keys that represent rates (use point change instead of %).
    """
    if rate_keys is None:
        rate_keys = set()

    current_metrics = current_source.get("metrics", {})
    if not current_metrics:
        return

    for key in current_metrics:
        if not isinstance(current_metrics[key], dict):
            continue
        cur_val = extract_raw_number(current_source, key)
        prev_val = extract_raw_number(previous_source, key) if previous_source else None
        is_rate = key in rate_keys
        trend_str, direction = compute_trend(cur_val, prev_val, is_rate=is_rate)
        current_metrics[key]["trend"] = trend_str
        current_metrics[key]["dir"] = direction


def get_date_range(args):
    """Calculate start and end dates from args."""
    if args.start and args.end:
        return args.start, args.end
    end = datetime.now()
    start = end - timedelta(days=args.days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def safe_pull(name, pull_fn, *args, **kwargs):
    """Run a pull function safely, catching all errors."""
    print(f"  Pulling {name}...", end=" ", flush=True)
    try:
        result = pull_fn(*args, **kwargs)
        if "error" in result:
            print(f"WARN: {result['error']}")
        else:
            print("OK")
        return result
    except Exception as e:
        print(f"FAIL: {e}")
        return {"error": str(e)}


def pull_brand(brand_code: str, start_date: str, end_date: str) -> dict:
    """Pull all data for a single brand (current + previous period)."""
    prev_start, prev_end = get_previous_range(start_date, end_date)

    print(f"\n{'='*50}")
    print(f"Pulling data for: {brand_code.upper()}")
    print(f"Current period : {start_date} to {end_date}")
    print(f"Previous period: {prev_start} to {prev_end}")
    print(f"{'='*50}")

    suffix = brand_code.upper()
    data = {}
    prev = {}

    # Rate-type metric keys (use point change, not percentage)
    rate_keys = {
        "open_rate", "click_rate", "bounce_rate", "engagement_rate",
        "ctr", "conversion_rate", "win_rate", "appointment_show_rate",
    }

    def _pull_pair(label, pull_fn, *args_before_dates):
        """Pull current and previous period for a single tool."""
        cur = safe_pull(label, pull_fn, *args_before_dates, start_date, end_date)
        prv = safe_pull(f"{label} (prev)", pull_fn, *args_before_dates, prev_start, prev_end)
        if "error" not in cur:
            add_trends_to_metrics(cur, prv if "error" not in prv else {}, rate_keys)
        return cur, prv

    # -- GA4 --
    from tools.pull_ga4 import pull as pull_ga4
    ga4_property = os.getenv(f"GA4_PROPERTY_ID_{suffix}", "")
    data["web"], prev["web"] = _pull_pair("GA4", pull_ga4, ga4_property)

    # -- YouTube --
    from tools.pull_youtube import pull as pull_youtube
    yt_channel = os.getenv(f"YOUTUBE_CHANNEL_ID_{suffix}", "")
    yt_api_key = os.getenv("YOUTUBE_API_KEY", "")
    data["youtube"], prev["youtube"] = _pull_pair("YouTube", pull_youtube, yt_channel, yt_api_key)

    # -- Google Ads --
    from tools.pull_google_ads import pull as pull_gads
    gads_id = os.getenv(f"GOOGLE_ADS_CUSTOMER_ID_{suffix}", "")
    gads_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    data["google_ads"], prev["google_ads"] = _pull_pair("Google Ads", pull_gads, gads_id, gads_token)

    # -- Google Ads Search Terms (new — safe import) --
    try:
        from tools.pull_google_ads_search_terms import pull as pull_gads_st
        data["search_terms"], prev["search_terms"] = _pull_pair(
            "Google Ads Search Terms", pull_gads_st, gads_id, gads_token)
    except ImportError:
        data["search_terms"] = {"error": "pull_google_ads_search_terms module not found"}

    # -- Google Ads Geo (new — safe import) --
    try:
        from tools.pull_google_ads_geo import pull as pull_gads_geo
        data["geo_performance"], prev["geo_performance"] = _pull_pair(
            "Google Ads Geo", pull_gads_geo, gads_id, gads_token)
    except ImportError:
        data["geo_performance"] = {"error": "pull_google_ads_geo module not found"}

    # -- Meta Ads --
    from tools.pull_meta_ads import pull as pull_meta
    meta_token = os.getenv("META_ACCESS_TOKEN", "")
    meta_ad_account = os.getenv(f"META_AD_ACCOUNT_ID_{suffix}", "")
    data["meta_ads"], prev["meta_ads"] = _pull_pair("Meta Ads", pull_meta, meta_ad_account, meta_token)

    # -- Instagram --
    from tools.pull_instagram import pull as pull_ig
    ig_account = os.getenv(f"INSTAGRAM_BUSINESS_ACCOUNT_ID_{suffix}", "")
    data["instagram"], prev["instagram"] = _pull_pair("Instagram", pull_ig, ig_account, meta_token)

    # -- Constant Contact --
    from tools.pull_constant_contact import pull as pull_cc
    cc_key = os.getenv("CONSTANT_CONTACT_API_KEY", "")
    cc_token = os.getenv("CONSTANT_CONTACT_ACCESS_TOKEN", "")
    data["email"], prev["email"] = _pull_pair("Constant Contact", pull_cc, cc_key, cc_token)

    # -- Gravity Forms --
    from tools.pull_gravity_forms import pull as pull_gf
    wp_url = os.getenv(f"WP_{suffix}_URL", "")
    gf_key = os.getenv(f"WP_{suffix}_GF_KEY", "")
    gf_secret = os.getenv(f"WP_{suffix}_GF_SECRET", "")
    data["gravity_forms"], prev["gravity_forms"] = _pull_pair(
        "Gravity Forms", pull_gf, wp_url, gf_key, gf_secret)

    # -- GoHighLevel --
    from tools.pull_ghl import pull as pull_ghl
    ghl_key = os.getenv(f"GHL_API_KEY_{suffix}", "")
    ghl_location = os.getenv(f"GHL_LOCATION_ID_{suffix}", "")
    data["ghl"], prev["ghl"] = _pull_pair("GoHighLevel", pull_ghl, ghl_key, ghl_location)

    # -- GHL Conversations (new — safe import) --
    try:
        from tools.pull_ghl_conversations import pull as pull_ghl_conv
        data["ghl_conversations"], prev["ghl_conversations"] = _pull_pair(
            "GHL Conversations", pull_ghl_conv, ghl_key, ghl_location)
    except ImportError:
        data["ghl_conversations"] = {"error": "pull_ghl_conversations module not found"}

    # -- GHL Tasks (new — safe import) --
    try:
        from tools.pull_ghl_tasks import pull as pull_ghl_tasks
        data["ghl_tasks"], prev["ghl_tasks"] = _pull_pair(
            "GHL Tasks", pull_ghl_tasks, ghl_key, ghl_location)
    except ImportError:
        data["ghl_tasks"] = {"error": "pull_ghl_tasks module not found"}

    # -- JobTread --
    from tools.pull_jobtread import pull as pull_jt
    jt_key = os.getenv("JOBTREAD_GRANT_KEY", "")
    jt_org = os.getenv(f"JOBTREAD_ORG_ID_{suffix}", "")
    data["jobtread"], prev["jobtread"] = _pull_pair("JobTread", pull_jt, jt_key, jt_org)

    # -- Campaign Tracker (Google Sheets CSV or local Excel file) --
    from tools.pull_campaigns import pull as pull_campaigns
    campaign_source = (
        os.getenv(f"CAMPAIGN_TRACKER_URL_{suffix}", "")
        or os.getenv(f"CAMPAIGN_TRACKER_PATH_{suffix}", "")
    )
    if campaign_source:
        data["campaigns"] = safe_pull("Campaign Tracker", pull_campaigns, campaign_source)
    else:
        data["campaigns"] = {"error": f"CAMPAIGN_TRACKER_URL_{suffix} or CAMPAIGN_TRACKER_PATH_{suffix} not set in .env"}

    # Stash previous-period data for downstream consumers
    data["_prev"] = prev

    return data


def build_kpi_summary(data: dict) -> list:
    """Build top-level KPI cards from all source data."""
    kpis = []

    # Total Leads = form fills (gravity + ghl) + ad conversions (google + meta)
    total_leads = 0
    ad_conversions = 0
    form_fills = 0

    # Extract values safely
    def extract_num(source, metric_key):
        try:
            val = source.get("metrics", {}).get(metric_key, {}).get("value", "0")
            return int(val.replace(",", "").replace("$", "").replace("%", "").replace("K", "000").replace("M", "000000").split(".")[0].split("h")[0].split("m")[0])
        except (ValueError, AttributeError):
            return 0

    if "error" not in data.get("google_ads", {}):
        ad_conversions += extract_num(data["google_ads"], "conversions")
    if "error" not in data.get("meta_ads", {}):
        ad_conversions += extract_num(data["meta_ads"], "conversions")
    if "error" not in data.get("gravity_forms", {}):
        form_fills += extract_num(data["gravity_forms"], "total_submissions")
    if "error" not in data.get("ghl", {}):
        form_fills += extract_num(data["ghl"], "total_submissions")

    total_leads = ad_conversions + form_fills

    # Ad spend
    ad_spend = 0
    if "error" not in data.get("google_ads", {}):
        try:
            val = data["google_ads"]["metrics"]["spend"]["value"]
            ad_spend += float(val.replace("$", "").replace(",", ""))
        except (KeyError, ValueError):
            pass
    if "error" not in data.get("meta_ads", {}):
        try:
            val = data["meta_ads"]["metrics"]["spend"]["value"]
            ad_spend += float(val.replace("$", "").replace(",", ""))
        except (KeyError, ValueError):
            pass

    cpl = ad_spend / total_leads if total_leads > 0 else 0

    kpis = [
        {"label": "Total Leads", "value": f"{total_leads:,}", "trend": "", "dir": "neutral"},
        {"label": "Ad Spend", "value": f"${ad_spend:,.0f}", "trend": "", "dir": "neutral"},
        {"label": "Website Visitors", "value": data.get("web", {}).get("metrics", {}).get("visitors", {}).get("value", "0"), "trend": "", "dir": "neutral"},
        {"label": "Cost Per Lead", "value": f"${cpl:.2f}", "trend": "", "dir": "neutral"},
    ]

    # Add platform-specific KPIs
    if "error" not in data.get("email", {}):
        kpis.append({"label": "Email Open Rate", "value": data["email"]["metrics"].get("open_rate", {}).get("value", "N/A"), "trend": "", "dir": "neutral"})
    if "error" not in data.get("instagram", {}):
        kpis.append({"label": "IG Engagement", "value": data["instagram"]["metrics"].get("engagement_rate", {}).get("value", "N/A"), "trend": "", "dir": "neutral"})
    if "error" not in data.get("youtube", {}):
        kpis.append({"label": "YT Watch Time", "value": data["youtube"]["metrics"].get("watch_time", {}).get("value", "N/A"), "trend": "", "dir": "neutral"})

    return kpis


def build_leads_summary(data: dict) -> dict:
    """Build the leads/conversions summary row."""
    def extract_num(source, metric_key):
        try:
            val = source.get("metrics", {}).get(metric_key, {}).get("value", "0")
            return int(val.replace(",", "").replace("$", "").replace("%", "").split(".")[0])
        except (ValueError, AttributeError):
            return 0

    form_fills = 0
    ad_conversions = 0
    ad_spend = 0

    if "error" not in data.get("gravity_forms", {}):
        form_fills += extract_num(data["gravity_forms"], "total_submissions")
    if "error" not in data.get("ghl", {}):
        form_fills += extract_num(data["ghl"], "total_submissions")
    if "error" not in data.get("google_ads", {}):
        ad_conversions += extract_num(data["google_ads"], "conversions")
    if "error" not in data.get("meta_ads", {}):
        ad_conversions += extract_num(data["meta_ads"], "conversions")

    for source_key in ["google_ads", "meta_ads"]:
        if "error" not in data.get(source_key, {}):
            try:
                val = data[source_key]["metrics"]["spend"]["value"]
                ad_spend += float(val.replace("$", "").replace(",", ""))
            except (KeyError, ValueError):
                pass

    total = form_fills + ad_conversions
    cpl = ad_spend / total if total > 0 else 0

    return {
        "formFills": form_fills,
        "adConversions": ad_conversions,
        "totalLeads": total,
        "cpl": f"${cpl:.2f}",
        "adSpend": f"${ad_spend:,.0f}",
    }


def build_executive_summary(current: dict, previous: dict) -> dict:
    """Compute the 6 hero KPIs for the executive summary row.

    Returns a list of dicts with: label, value, trend, dir, subtitle (optional).
    """
    prev = previous or {}

    def _safe_num(source, key):
        return extract_raw_number(source, key) if source and "error" not in source else None

    def _fmt_currency(val):
        if val is None:
            return "$0"
        if val >= 1_000_000:
            return f"${val/1_000_000:.1f}M"
        if val >= 1_000:
            return f"${val:,.0f}"
        return f"${val:,.2f}"

    # 1. Revenue Closed (JobTread)
    rev_cur = _safe_num(current.get("jobtread", {}), "revenue_closed")
    if rev_cur is None:
        rev_cur = _safe_num(current.get("jobtread", {}), "invoiceTotal")
    rev_prev = _safe_num(prev.get("jobtread", {}), "revenue_closed")
    if rev_prev is None:
        rev_prev = _safe_num(prev.get("jobtread", {}), "invoiceTotal")
    rev_trend, rev_dir = compute_trend(rev_cur, rev_prev)

    # 2. Pipeline Value (JobTread)
    pipe_cur = _safe_num(current.get("jobtread", {}), "pipeline_value")
    pipe_prev = _safe_num(prev.get("jobtread", {}), "pipeline_value")
    pipe_trend, pipe_dir = compute_trend(pipe_cur, pipe_prev)

    # 3. Total Leads (GHL + Gravity Forms + ad conversions)
    def _total_leads(data):
        total = 0
        for src_key, metric_key in [
            ("ghl", "total_submissions"),
            ("gravity_forms", "total_submissions"),
            ("google_ads", "conversions"),
            ("meta_ads", "conversions"),
        ]:
            val = _safe_num(data.get(src_key, {}), metric_key)
            if val is not None:
                total += val
        return total

    leads_cur = _total_leads(current)
    leads_prev = _total_leads(prev)
    leads_trend, leads_dir = compute_trend(leads_cur, leads_prev)

    # 4. Cost Per Lead (ad spend / total leads) — INVERTED: lower is better
    def _total_ad_spend(data):
        total = 0
        for src_key in ("google_ads", "meta_ads"):
            val = _safe_num(data.get(src_key, {}), "spend")
            if val is not None:
                total += val
        return total

    spend_cur = _total_ad_spend(current)
    spend_prev = _total_ad_spend(prev)
    cpl_cur = spend_cur / leads_cur if leads_cur > 0 else None
    cpl_prev = spend_prev / leads_prev if leads_prev > 0 else None
    cpl_trend, cpl_dir = compute_trend(cpl_cur, cpl_prev)
    # Invert direction — lower CPL is better
    if cpl_dir == "up":
        cpl_dir = "down"
    elif cpl_dir == "down":
        cpl_dir = "up"

    # 5. Win Rate (JobTread) — point change
    wr_cur = _safe_num(current.get("jobtread", {}), "win_rate")
    wr_prev = _safe_num(prev.get("jobtread", {}), "win_rate")
    wr_trend, wr_dir = compute_trend(wr_cur, wr_prev, is_rate=True)

    # 6. Appointments (GHL)
    appt_cur = _safe_num(current.get("ghl", {}), "appointments_booked")
    appt_prev = _safe_num(prev.get("ghl", {}), "appointments_booked")
    appt_trend, appt_dir = compute_trend(appt_cur, appt_prev)
    show_rate = None
    if current.get("ghl") and "error" not in current["ghl"]:
        show_rate = current["ghl"].get("metrics", {}).get(
            "appointment_show_rate", {}).get("value")

    rev_spark = (current.get("jobtread", {}).get("metrics", {})
                 .get("revenue_closed", {}).get("spark", []))
    appt_spark = (current.get("ghl", {}).get("metrics", {})
                  .get("appointments_booked", {}).get("spark", []))

    return {
        "revenue_closed": {
            "value": _fmt_currency(rev_cur),
            "trend": rev_trend,
            "dir": rev_dir,
            "spark": rev_spark,
        },
        "pipeline_value": {
            "value": _fmt_currency(pipe_cur),
            "trend": pipe_trend,
            "dir": pipe_dir,
        },
        "total_leads": {
            "value": f"{int(leads_cur):,}" if leads_cur else "0",
            "trend": leads_trend,
            "dir": leads_dir,
        },
        "cost_per_lead": {
            "value": _fmt_currency(cpl_cur) if cpl_cur is not None else "$0.00",
            "trend": cpl_trend,
            "dir": cpl_dir,
        },
        "win_rate": {
            "value": f"{wr_cur:.1f}%" if wr_cur is not None else "N/A",
            "trend": wr_trend,
            "dir": wr_dir,
        },
        "appointments": {
            "value": f"{int(appt_cur):,}" if appt_cur else "0",
            "trend": appt_trend,
            "dir": appt_dir,
            "spark": appt_spark,
            **({"subtitle": f"{show_rate} show rate"} if show_rate else {}),
        },
    }


def transform_for_dashboard(raw_data: dict) -> dict:
    """Transform raw API data into the format the dashboard HTML expects."""
    result = {}

    prev = raw_data.get("_prev", {})

    # Executive summary (6 hero KPIs)
    result["executive"] = build_executive_summary(raw_data, prev)

    # Legacy KPI summary (kept for backward compat)
    result["kpi"] = build_kpi_summary(raw_data)
    result["leads"] = build_leads_summary(raw_data)

    # Pass through section data (already in correct format from individual tools)
    section_map = {
        "web": "web",
        "google_ads": "gads",
        "meta_ads": "meta",
        "youtube": "yt",
        "instagram": "ig",
        "email": "email",
    }

    for source_key, dash_key in section_map.items():
        source = raw_data.get(source_key, {})
        if "error" not in source:
            result[dash_key] = source.get("metrics", {})
        else:
            result[dash_key] = {"_error": source["error"]}

    # Email top campaigns
    email_source = raw_data.get("email", {})
    if "error" not in email_source:
        result["topEmailsByOpens"] = email_source.get("top_by_opens", [])
        result["topEmailsByClicks"] = email_source.get("top_by_clicks", [])

    # Combine form data
    forms_rows = []
    forms_total = 0
    if "error" not in raw_data.get("gravity_forms", {}):
        gf = raw_data["gravity_forms"]
        forms_rows.extend(gf.get("forms", []))
        try:
            forms_total += int(gf["metrics"]["total_submissions"]["value"].replace(",", ""))
        except (KeyError, ValueError):
            pass
    if "error" not in raw_data.get("ghl", {}):
        ghl = raw_data["ghl"]
        forms_rows.extend(ghl.get("forms", []))
        try:
            forms_total += int(ghl["metrics"]["total_submissions"]["value"].replace(",", ""))
        except (KeyError, ValueError):
            pass

    result["forms"] = {
        "total": forms_total,
        "rows": forms_rows,
    }

    # Top pages and traffic sources from GA4
    if "error" not in raw_data.get("web", {}):
        result["topPages"] = raw_data["web"].get("top_pages", [])
        result["topBlogs"] = raw_data["web"].get("top_blogs", [])
        result["traffic"] = raw_data["web"].get("traffic_sources", [])
        result["landingPages"] = raw_data["web"].get("landing_pages", [])
        result["newVsReturning"] = raw_data["web"].get("new_vs_returning", {})

    # Top videos from YouTube
    if "error" not in raw_data.get("youtube", {}):
        result["ytVideos"] = raw_data["youtube"].get("top_videos", [])

    # JobTread pipeline + financials
    jt = raw_data.get("jobtread", {})
    if "error" not in jt:
        result["jobtread"] = jt
    else:
        result["jobtread"] = {"_error": jt.get("error", "Not configured")}

    # GHL appointment data (for sales tab)
    ghl = raw_data.get("ghl", {})
    if "error" not in ghl:
        ghl_metrics = ghl.get("metrics", {})
        result["appointments"] = {
            "booked": ghl_metrics.get("appointments_booked", {}).get("value", "0"),
            "show_rate": ghl_metrics.get("appointment_show_rate", {}).get("value", "N/A"),
            "showed": ghl_metrics.get("appointments_showed", {}).get("value", "0"),
            "noshow": ghl_metrics.get("appointments_noshow", {}).get("value", "0"),
            "spark": ghl_metrics.get("appointments_booked", {}).get("spark", []),
            "response_time": ghl_metrics.get("avg_response_time", {}).get("value", "N/A"),
        }
    else:
        result["appointments"] = {"_error": ghl.get("error", "Not configured")}

    # New data pass-through keys (from new tools)
    search_terms = raw_data.get("search_terms", {})
    if "error" not in search_terms:
        result["searchTerms"] = search_terms.get("terms", search_terms.get("metrics", {}))

    geo = raw_data.get("geo_performance", {})
    if "error" not in geo:
        result["geoPerformance"] = geo.get("regions", geo.get("metrics", {}))

    # GHL conversations -> salesActivity
    convos = raw_data.get("ghl_conversations", {})
    if "error" not in convos:
        result["salesActivity"] = convos.get("activity", convos.get("metrics", {}))

    # GHL tasks
    tasks = raw_data.get("ghl_tasks", {})
    if "error" not in tasks:
        result["tasks"] = tasks.get("tasks", tasks.get("metrics", {}))

    # Campaign tracker (Excel spreadsheet)
    campaigns = raw_data.get("campaigns", {})
    if "error" not in campaigns:
        result["campaigns"] = campaigns
    else:
        result["campaigns"] = {"_error": campaigns.get("error", "Not configured")}

    return result


def main():
    parser = argparse.ArgumentParser(description="Pull all marketing data")
    parser.add_argument("--days", type=int, default=30, help="Number of days to pull (default: 30)")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--brands", type=str, default=None,
                        help="Comma-separated brands to pull, e.g. rc or rc,rnr (default: ACTIVE_BRANDS env var or rc,rnr,wl)")
    parser.add_argument("--output", type=str, default=".tmp/data.json", help="Output file path")
    args = parser.parse_args()

    start_date, end_date = get_date_range(args)

    prev_start, prev_end = get_previous_range(start_date, end_date)

    output = {
        "generated_at": datetime.now().isoformat(),
        "date_range": {"start": start_date, "end": end_date},
        "prev_date_range": {"start": prev_start, "end": prev_end},
    }

    # Priority: --brands flag > ACTIVE_BRANDS env var > default all
    if args.brands:
        brands_to_pull = [b.strip() for b in args.brands.split(",") if b.strip()]
    else:
        env_brands = os.getenv("ACTIVE_BRANDS", "")
        brands_to_pull = [b.strip() for b in env_brands.split(",") if b.strip()] or ["rc", "rnr", "wl"]

    for brand in brands_to_pull:
        raw = pull_brand(brand, start_date, end_date)
        output[brand] = transform_for_dashboard(raw)

    # Write output
    output_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), args.output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nData written to {output_path}")
    print(f"Generated at: {output['generated_at']}")

    # Count successes/failures
    for brand in brands_to_pull:
        errors = sum(1 for v in output[brand].values() if isinstance(v, dict) and "_error" in v)
        sources = sum(1 for v in output[brand].values() if isinstance(v, dict))
        print(f"  {brand.upper()}: {sources - errors}/{sources} sources OK")


if __name__ == "__main__":
    main()
