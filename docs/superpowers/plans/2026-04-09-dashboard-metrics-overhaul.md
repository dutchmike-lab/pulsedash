# Dashboard Metrics Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Executive Summary tab with 6 hero KPIs, enrich Marketing + Sales tabs with new API data, and add period-over-period comparisons across all metrics.

**Architecture:** Each Python tool gets a second call for the previous period. `pull_all.py` computes trend deltas. New tools for GHL conversations, GHL tasks, Google Ads search terms, and Google Ads geo. Dashboard HTML gets a new Executive Summary tab as the default landing, plus enriched Marketing and Sales tabs. All data flows through the existing `data.json` pipeline.

**Tech Stack:** Python 3.x, Google Analytics Data API, Google Ads API, Meta Marketing API, GHL API v2, JobTread Pave API, Constant Contact API v3, Chart.js, vanilla HTML/JS.

**Spec:** `docs/superpowers/specs/2026-04-09-dashboard-metrics-overhaul-design.md`

---

## File Map

### New Files
| File | Responsibility |
|------|---------------|
| `tools/pull_ghl_conversations.py` | Pull GHL message export, compute response times + outbound activity |
| `tools/pull_ghl_tasks.py` | Pull GHL tasks, compute overdue count + completion rate |
| `tools/pull_google_ads_search_terms.py` | Pull top search terms from Google Ads |
| `tools/pull_google_ads_geo.py` | Pull geographic performance from Google Ads |

### Modified Files
| File | Changes |
|------|---------|
| `tools/pull_all.py` | Add previous-period calls, trend calculation, wire new tools, new `executive` data key |
| `tools/pull_ga4.py` | Add landing page conversion rates, new vs returning visitors |
| `tools/pull_google_ads.py` | Add phone call metrics |
| `tools/pull_meta_ads.py` | Add frequency, reach, cpm, placement breakdown |
| `tools/pull_jobtread.py` | Add profit margin, payments vs invoiced |
| `tools/pull_constant_contact.py` | Add per-link click details |
| `marketing-dashboard.html` | Add Executive Summary tab, enrich Marketing + Sales tabs, wire trend display |

---

## Task 1: Period-Over-Period Infrastructure in pull_all.py

**Files:**
- Modify: `tools/pull_all.py`

This is the foundation — every subsequent tool enhancement depends on this.

- [ ] **Step 1: Add previous-period date range calculation**

In `pull_all.py`, add a helper function after `get_date_range()`:

```python
def get_previous_range(start_date: str, end_date: str) -> tuple[str, str]:
    """Calculate the previous period of equal length.
    
    If current range is 2026-03-10 to 2026-04-08 (30 days),
    previous range is 2026-02-08 to 2026-03-09 (30 days).
    """
    fmt = "%Y-%m-%d"
    dt_start = datetime.strptime(start_date, fmt)
    dt_end = datetime.strptime(end_date, fmt)
    duration = (dt_end - dt_start)
    prev_end = dt_start - timedelta(days=1)
    prev_start = prev_end - duration
    return prev_start.strftime(fmt), prev_end.strftime(fmt)
```

- [ ] **Step 2: Add trend computation helper**

Add after `get_previous_range()`:

```python
def compute_trend(current_val, previous_val, is_rate=False):
    """Compute trend percentage and direction.
    
    Args:
        current_val: Current period numeric value
        previous_val: Previous period numeric value
        is_rate: If True, show absolute point change instead of percentage
    
    Returns:
        tuple: (trend_str, direction)
        e.g. ("+12.3%", "up") or ("-2.1pp", "down") or ("--", "neutral")
    """
    if previous_val is None or current_val is None:
        return ("--", "neutral")
    
    try:
        current = float(current_val)
        previous = float(previous_val)
    except (ValueError, TypeError):
        return ("--", "neutral")
    
    if previous == 0:
        if current > 0:
            return ("+100%", "up")
        return ("--", "neutral")
    
    if is_rate:
        # Absolute point change for rates
        diff = current - previous
        sign = "+" if diff > 0 else ""
        direction = "up" if diff > 0 else ("down" if diff < 0 else "neutral")
        return (f"{sign}{diff:.1f}pp", direction)
    else:
        pct = ((current - previous) / abs(previous)) * 100
        sign = "+" if pct > 0 else ""
        direction = "up" if pct > 0 else ("down" if pct < 0 else "neutral")
        return (f"{sign}{pct:.1f}%", direction)
```

- [ ] **Step 3: Add extract_raw_number helper**

Add after `compute_trend()`:

```python
def extract_raw_number(source: dict, metric_key: str) -> float | None:
    """Extract a raw numeric value from a source's metrics dict.
    
    Handles formatted strings like "$1,234", "45.2%", "1.2K", "2m 14s".
    Returns None if not found or not parseable.
    """
    try:
        val = source.get("metrics", {}).get(metric_key, {}).get("value", "")
        if not val or val == "N/A":
            return None
        # Strip formatting
        cleaned = str(val).replace(",", "").replace("$", "").replace("%", "")
        cleaned = cleaned.replace("h", "").replace("m", "").replace("s", "")
        if "K" in cleaned:
            cleaned = cleaned.replace("K", "")
            return float(cleaned) * 1000
        if "M" in cleaned:
            cleaned = cleaned.replace("M", "")
            return float(cleaned) * 1000000
        return float(cleaned)
    except (ValueError, TypeError, AttributeError):
        return None
```

- [ ] **Step 4: Add add_trends_to_metrics helper**

```python
def add_trends_to_metrics(current_source: dict, previous_source: dict, rate_keys: set | None = None):
    """Add trend and dir fields to every metric in current_source.
    
    Mutates current_source in place.
    
    Args:
        current_source: Current period data dict with "metrics" key
        previous_source: Previous period data dict with "metrics" key
        rate_keys: Set of metric keys that are rates (use point change, not %)
    """
    if rate_keys is None:
        rate_keys = set()
    
    current_metrics = current_source.get("metrics", {})
    previous_metrics = previous_source.get("metrics", {})
    
    for key, metric in current_metrics.items():
        if not isinstance(metric, dict) or "value" not in metric:
            continue
        curr_raw = extract_raw_number(current_source, key)
        prev_raw = extract_raw_number(previous_source, key)
        is_rate = key in rate_keys
        trend_str, direction = compute_trend(curr_raw, prev_raw, is_rate)
        metric["trend"] = trend_str
        metric["dir"] = direction
```

- [ ] **Step 5: Update pull_brand() to call each tool twice**

Replace the existing `pull_brand()` function. The key change is each tool gets called for both current and previous periods, then trends are computed:

```python
def pull_brand(brand_code: str, start_date: str, end_date: str) -> dict:
    """Pull all data for a single brand, including previous period for trends."""
    print(f"\n{'='*50}")
    print(f"Pulling data for: {brand_code.upper()}")
    print(f"Date range: {start_date} to {end_date}")
    print(f"{'='*50}")

    suffix = brand_code.upper()
    data = {}
    prev_data = {}

    # Calculate previous period
    prev_start, prev_end = get_previous_range(start_date, end_date)
    print(f"Previous period: {prev_start} to {prev_end}")

    # -- GA4 --
    from tools.pull_ga4 import pull as pull_ga4
    ga4_property = os.getenv(f"GA4_PROPERTY_ID_{suffix}", "")
    data["web"] = safe_pull("GA4", pull_ga4, ga4_property, start_date, end_date)
    prev_data["web"] = safe_pull("GA4 (prev)", pull_ga4, ga4_property, prev_start, prev_end)

    # -- YouTube --
    from tools.pull_youtube import pull as pull_youtube
    yt_channel = os.getenv(f"YOUTUBE_CHANNEL_ID_{suffix}", "")
    yt_api_key = os.getenv("YOUTUBE_API_KEY", "")
    data["youtube"] = safe_pull("YouTube", pull_youtube, yt_channel, yt_api_key, start_date, end_date)
    prev_data["youtube"] = safe_pull("YouTube (prev)", pull_youtube, yt_channel, yt_api_key, prev_start, prev_end)

    # -- Google Ads --
    from tools.pull_google_ads import pull as pull_gads
    gads_id = os.getenv(f"GOOGLE_ADS_CUSTOMER_ID_{suffix}", "")
    gads_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    data["google_ads"] = safe_pull("Google Ads", pull_gads, gads_id, gads_token, start_date, end_date)
    prev_data["google_ads"] = safe_pull("Google Ads (prev)", pull_gads, gads_id, gads_token, prev_start, prev_end)

    # -- Google Ads Search Terms (no previous period needed) --
    from tools.pull_google_ads_search_terms import pull as pull_gads_search
    data["google_ads_search_terms"] = safe_pull("Google Ads Search Terms", pull_gads_search, gads_id, gads_token, start_date, end_date)

    # -- Google Ads Geo (no previous period needed) --
    from tools.pull_google_ads_geo import pull as pull_gads_geo
    data["google_ads_geo"] = safe_pull("Google Ads Geo", pull_gads_geo, gads_id, gads_token, start_date, end_date)

    # -- Meta Ads --
    from tools.pull_meta_ads import pull as pull_meta
    meta_token = os.getenv("META_ACCESS_TOKEN", "")
    meta_ad_account = os.getenv(f"META_AD_ACCOUNT_ID_{suffix}", "")
    data["meta_ads"] = safe_pull("Meta Ads", pull_meta, meta_ad_account, meta_token, start_date, end_date)
    prev_data["meta_ads"] = safe_pull("Meta Ads (prev)", pull_meta, meta_ad_account, meta_token, prev_start, prev_end)

    # -- Instagram --
    from tools.pull_instagram import pull as pull_ig
    ig_account = os.getenv(f"INSTAGRAM_BUSINESS_ACCOUNT_ID_{suffix}", "")
    data["instagram"] = safe_pull("Instagram", pull_ig, ig_account, meta_token, start_date, end_date)
    prev_data["instagram"] = safe_pull("Instagram (prev)", pull_ig, ig_account, meta_token, prev_start, prev_end)

    # -- Constant Contact --
    from tools.pull_constant_contact import pull as pull_cc
    cc_key = os.getenv("CONSTANT_CONTACT_API_KEY", "")
    cc_token = os.getenv("CONSTANT_CONTACT_ACCESS_TOKEN", "")
    data["email"] = safe_pull("Constant Contact", pull_cc, cc_key, cc_token, start_date, end_date)
    prev_data["email"] = safe_pull("Constant Contact (prev)", pull_cc, cc_key, cc_token, prev_start, prev_end)

    # -- Gravity Forms --
    from tools.pull_gravity_forms import pull as pull_gf
    wp_url = os.getenv(f"WP_{suffix}_URL", "")
    gf_key = os.getenv(f"WP_{suffix}_GF_KEY", "")
    gf_secret = os.getenv(f"WP_{suffix}_GF_SECRET", "")
    data["gravity_forms"] = safe_pull("Gravity Forms", pull_gf, wp_url, gf_key, gf_secret, start_date, end_date)
    prev_data["gravity_forms"] = safe_pull("Gravity Forms (prev)", pull_gf, wp_url, gf_key, gf_secret, prev_start, prev_end)

    # -- GoHighLevel --
    from tools.pull_ghl import pull as pull_ghl
    ghl_key = os.getenv(f"GHL_API_KEY_{suffix}", "")
    ghl_location = os.getenv(f"GHL_LOCATION_ID_{suffix}", "")
    data["ghl"] = safe_pull("GoHighLevel", pull_ghl, ghl_key, ghl_location, start_date, end_date)
    prev_data["ghl"] = safe_pull("GoHighLevel (prev)", pull_ghl, ghl_key, ghl_location, prev_start, prev_end)

    # -- GHL Conversations --
    from tools.pull_ghl_conversations import pull as pull_ghl_conv
    data["ghl_conversations"] = safe_pull("GHL Conversations", pull_ghl_conv, ghl_key, ghl_location, start_date, end_date)
    prev_data["ghl_conversations"] = safe_pull("GHL Conversations (prev)", pull_ghl_conv, ghl_key, ghl_location, prev_start, prev_end)

    # -- GHL Tasks (snapshot, no previous period) --
    from tools.pull_ghl_tasks import pull as pull_ghl_tasks
    data["ghl_tasks"] = safe_pull("GHL Tasks", pull_ghl_tasks, ghl_key, ghl_location)

    # -- JobTread --
    from tools.pull_jobtread import pull as pull_jt
    jt_key = os.getenv("JOBTREAD_GRANT_KEY", "")
    jt_org = os.getenv(f"JOBTREAD_ORG_ID_{suffix}", "")
    data["jobtread"] = safe_pull("JobTread", pull_jt, jt_key, jt_org, start_date, end_date)
    prev_data["jobtread"] = safe_pull("JobTread (prev)", pull_jt, jt_key, jt_org, prev_start, prev_end)

    # -- Add trends to all sources --
    rate_keys = {"bounce_rate", "avg_session_duration", "ctr", "open_rate", "click_rate",
                 "engagement_rate", "deliverability", "win_rate", "appointment_show_rate",
                 "impressions_ctr", "cost_per_conversion"}
    
    for source_key in data:
        curr = data.get(source_key, {})
        prev = prev_data.get(source_key, {})
        if isinstance(curr, dict) and "error" not in curr and isinstance(prev, dict) and "error" not in prev:
            add_trends_to_metrics(curr, prev, rate_keys)

    data["_prev"] = prev_data  # Keep for cross-source trend calculations
    return data
```

- [ ] **Step 6: Update transform_for_dashboard() to pass through trends and new data**

Add these sections to the end of `transform_for_dashboard()` (before the `return result` line):

```python
    # Google Ads Search Terms (no trend)
    search_terms = raw_data.get("google_ads_search_terms", {})
    if "error" not in search_terms:
        result["searchTerms"] = search_terms.get("terms", [])
    
    # Google Ads Geo (no trend)
    geo_data = raw_data.get("google_ads_geo", {})
    if "error" not in geo_data:
        result["geoPerformance"] = geo_data.get("locations", [])
    
    # GHL Conversations (sales activity)
    conv_data = raw_data.get("ghl_conversations", {})
    if "error" not in conv_data:
        result["salesActivity"] = conv_data.get("metrics", {})
    else:
        result["salesActivity"] = {"_error": conv_data.get("error", "Not configured")}
    
    # GHL Tasks (overdue follow-ups)
    tasks_data = raw_data.get("ghl_tasks", {})
    if "error" not in tasks_data:
        result["tasks"] = tasks_data
    else:
        result["tasks"] = {"_error": tasks_data.get("error", "Not configured")}

    # Build Executive Summary KPIs with trends
    prev_data = raw_data.get("_prev", {})
    result["executive"] = build_executive_summary(raw_data, prev_data)
```

- [ ] **Step 7: Add build_executive_summary() function**

Add this new function before `transform_for_dashboard()`:

```python
def build_executive_summary(current: dict, previous: dict) -> dict:
    """Build the 6 hero KPIs for the Executive Summary tab with trends."""
    
    def _safe_num(source, metric_key):
        return extract_raw_number(source, metric_key)
    
    # Revenue Closed
    jt = current.get("jobtread", {})
    jt_prev = previous.get("jobtread", {})
    revenue = _safe_num(jt, "revenue_closed") or 0
    revenue_prev = _safe_num(jt_prev, "revenue_closed")
    rev_trend, rev_dir = compute_trend(revenue, revenue_prev)
    rev_spark = jt.get("metrics", {}).get("revenue_closed", {}).get("spark", [])
    
    # Pipeline Value
    pipeline_val = _safe_num(jt, "pipeline_value") or 0
    pipeline_prev = _safe_num(jt_prev, "pipeline_value")
    pipe_trend, pipe_dir = compute_trend(pipeline_val, pipeline_prev)
    
    # Total Leads
    total_leads = 0
    total_leads_prev = 0
    for src_key in ["ghl", "gravity_forms"]:
        src = current.get(src_key, {})
        total_leads += (_safe_num(src, "total_submissions") or 0)
        src_prev = previous.get(src_key, {})
        total_leads_prev += (_safe_num(src_prev, "total_submissions") or 0)
    for src_key in ["google_ads", "meta_ads"]:
        src = current.get(src_key, {})
        total_leads += (_safe_num(src, "conversions") or 0)
        src_prev = previous.get(src_key, {})
        total_leads_prev += (_safe_num(src_prev, "conversions") or 0)
    leads_trend, leads_dir = compute_trend(total_leads, total_leads_prev)
    
    # Ad Spend (for CPL)
    ad_spend = 0
    for src_key in ["google_ads", "meta_ads"]:
        src = current.get(src_key, {})
        ad_spend += (_safe_num(src, "spend") or 0)
    cpl = ad_spend / total_leads if total_leads > 0 else 0
    
    ad_spend_prev = 0
    for src_key in ["google_ads", "meta_ads"]:
        src_prev = previous.get(src_key, {})
        ad_spend_prev += (_safe_num(src_prev, "spend") or 0)
    cpl_prev = ad_spend_prev / total_leads_prev if total_leads_prev > 0 else 0
    cpl_trend, cpl_dir = compute_trend(cpl, cpl_prev)
    # Invert CPL direction — lower is better
    if cpl_dir == "up":
        cpl_dir = "down"
    elif cpl_dir == "down":
        cpl_dir = "up"
    
    # Win Rate
    win_rate = _safe_num(jt, "win_rate") or 0
    win_prev = _safe_num(jt_prev, "win_rate")
    win_trend, win_dir = compute_trend(win_rate, win_prev, is_rate=True)
    
    # Appointments
    ghl = current.get("ghl", {})
    ghl_prev = previous.get("ghl", {})
    appts = _safe_num(ghl, "appointments_booked") or 0
    appts_prev = _safe_num(ghl_prev, "appointments_booked")
    appts_trend, appts_dir = compute_trend(appts, appts_prev)
    show_rate = ghl.get("metrics", {}).get("appointment_show_rate", {}).get("value", "N/A")
    appts_spark = ghl.get("metrics", {}).get("appointments_booked", {}).get("spark", [])
    
    def _fmt_dollars(n):
        if n >= 1_000_000:
            return f"${n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"${n / 1_000:.1f}K"
        return f"${n:,.0f}"
    
    return {
        "revenue_closed": {
            "value": _fmt_dollars(revenue), "trend": rev_trend, "dir": rev_dir,
            "spark": rev_spark, "color": "green",
        },
        "pipeline_value": {
            "value": _fmt_dollars(pipeline_val), "trend": pipe_trend, "dir": pipe_dir,
            "color": "accent",
        },
        "total_leads": {
            "value": f"{int(total_leads):,}", "trend": leads_trend, "dir": leads_dir,
        },
        "cost_per_lead": {
            "value": f"${cpl:.2f}", "trend": cpl_trend, "dir": cpl_dir,
        },
        "win_rate": {
            "value": f"{win_rate:.0f}%", "trend": win_trend, "dir": win_dir,
        },
        "appointments": {
            "value": f"{int(appts):,}", "trend": appts_trend, "dir": appts_dir,
            "sub": f"{show_rate} show rate", "spark": appts_spark,
        },
    }
```

- [ ] **Step 8: Verify pull_all.py runs without errors**

Run: `cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard" && python -c "from tools.pull_all import get_previous_range, compute_trend, extract_raw_number; print(get_previous_range('2026-03-10', '2026-04-08')); print(compute_trend(120, 100)); print(compute_trend(45.2, 42.1, is_rate=True))"`

Expected: `('2026-02-08', '2026-03-09')` and `('+20.0%', 'up')` and `('+3.1pp', 'up')`

- [ ] **Step 9: Commit**

```bash
git add tools/pull_all.py
git commit -m "feat: add period-over-period trend infrastructure to pull_all.py"
```

---

## Task 2: Expand pull_ga4.py — Landing Pages + New vs Returning

**Files:**
- Modify: `tools/pull_ga4.py`

- [ ] **Step 1: Read the current pull_ga4.py to find where to add the new queries**

Read `tools/pull_ga4.py` and locate the existing `RunReportRequest` calls. The new queries go after the existing ones, before the return statement.

- [ ] **Step 2: Add landing page conversion rate query**

Add after the existing traffic sources query but before the return statement:

```python
    # Landing page conversion rates (top 10)
    landing_pages = []
    try:
        lp_response = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            dimensions=[Dimension(name="landingPage")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="conversions"),
                Metric(name="bounceRate"),
            ],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="conversions"), desc=True)],
            limit=10,
        ))
        for row in lp_response.rows:
            page = row.dimension_values[0].value
            sessions = int(row.metric_values[0].value)
            conversions = int(row.metric_values[1].value)
            bounce = float(row.metric_values[2].value)
            conv_rate = (conversions / sessions * 100) if sessions > 0 else 0
            landing_pages.append({
                "page": page,
                "sessions": sessions,
                "conversions": conversions,
                "conv_rate": f"{conv_rate:.1f}%",
                "bounce_rate": f"{bounce:.1f}%",
            })
    except Exception:
        pass
```

- [ ] **Step 3: Add new vs returning visitors query**

```python
    # New vs returning visitors
    new_vs_returning = {"new": 0, "returning": 0}
    try:
        nvr_response = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            dimensions=[Dimension(name="newVsReturning")],
            metrics=[Metric(name="activeUsers")],
        ))
        for row in nvr_response.rows:
            segment = row.dimension_values[0].value.lower()
            users = int(row.metric_values[0].value)
            if segment == "new":
                new_vs_returning["new"] = users
            elif segment == "returning":
                new_vs_returning["returning"] = users
    except Exception:
        pass
```

- [ ] **Step 4: Add the new data to the return dict**

Find the existing return statement and add:

```python
    result["landing_pages"] = landing_pages
    result["new_vs_returning"] = new_vs_returning
```

- [ ] **Step 5: Wire into pull_all.py transform**

In `tools/pull_all.py`, in `transform_for_dashboard()`, after the existing `result["traffic"]` line, add:

```python
        result["landingPages"] = raw_data["web"].get("landing_pages", [])
        result["newVsReturning"] = raw_data["web"].get("new_vs_returning", {"new": 0, "returning": 0})
```

- [ ] **Step 6: Commit**

```bash
git add tools/pull_ga4.py tools/pull_all.py
git commit -m "feat: add landing page conversions and new vs returning visitors to GA4 pull"
```

---

## Task 3: Expand pull_google_ads.py — Phone Call Metrics

**Files:**
- Modify: `tools/pull_google_ads.py`

- [ ] **Step 1: Read pull_google_ads.py to understand the existing query structure**

Read the file and find where metrics are queried. The Google Ads API uses GAQL (Google Ads Query Language) for queries.

- [ ] **Step 2: Add phone call metrics to the existing query or add a separate query**

After the existing metrics query, add a call metrics query:

```python
    # Phone call metrics
    phone_calls = 0
    phone_impressions = 0
    try:
        call_query = f"""
            SELECT
                metrics.phone_calls,
                metrics.phone_impressions,
                metrics.phone_through_rate
            FROM campaign
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
        """
        call_response = ga_service.search_stream(customer_id=customer_id, query=call_query)
        for batch in call_response:
            for row in batch.results:
                phone_calls += row.metrics.phone_calls or 0
                phone_impressions += row.metrics.phone_impressions or 0
    except Exception:
        pass
```

- [ ] **Step 3: Add to return metrics**

```python
    result["metrics"]["phone_calls"] = {
        "value": _fmt(phone_calls),
        "spark": [],
    }
```

- [ ] **Step 4: Commit**

```bash
git add tools/pull_google_ads.py
git commit -m "feat: add phone call metrics to Google Ads pull"
```

---

## Task 4: Expand pull_meta_ads.py — Frequency, Reach, Placements

**Files:**
- Modify: `tools/pull_meta_ads.py`

- [ ] **Step 1: Read pull_meta_ads.py to find the existing fields list**

The Meta API uses a `fields` parameter in the insights request. Find it.

- [ ] **Step 2: Add frequency, reach, cpm to the fields list**

Add these strings to the existing `fields` list in the API call:

```python
fields = [
    # ... existing fields ...
    "frequency",
    "reach",
    "cpm",
]
```

- [ ] **Step 3: Add placement breakdown query**

After the main insights query, add:

```python
    # Placement breakdown
    placements = []
    try:
        placement_params = {
            "time_range": {"since": start_date, "until": end_date},
            "fields": ["impressions", "clicks", "spend", "actions"],
            "breakdowns": ["publisher_platform", "platform_position"],
            "limit": 20,
        }
        placement_response = ad_account.get_insights(params=placement_params)
        for row in placement_response:
            placements.append({
                "platform": row.get("publisher_platform", ""),
                "position": row.get("platform_position", ""),
                "impressions": int(row.get("impressions", 0)),
                "clicks": int(row.get("clicks", 0)),
                "spend": float(row.get("spend", 0)),
            })
    except Exception:
        pass
```

- [ ] **Step 4: Add to return dict**

```python
    result["metrics"]["frequency"] = {"value": f"{frequency:.1f}", "spark": []}
    result["metrics"]["reach"] = {"value": _fmt(reach), "spark": []}
    result["metrics"]["cpm"] = {"value": f"${cpm:.2f}", "spark": []}
    result["placements"] = placements
```

- [ ] **Step 5: Wire placements into pull_all.py transform**

In `transform_for_dashboard()`, add after the meta section:

```python
    if "error" not in raw_data.get("meta_ads", {}):
        result["metaPlacements"] = raw_data["meta_ads"].get("placements", [])
```

- [ ] **Step 6: Commit**

```bash
git add tools/pull_meta_ads.py tools/pull_all.py
git commit -m "feat: add frequency, reach, cpm, placement breakdown to Meta Ads pull"
```

---

## Task 5: Expand pull_jobtread.py — Profit Margin + Payments

**Files:**
- Modify: `tools/pull_jobtread.py`

- [ ] **Step 1: Read pull_jobtread.py to find the current financial query**

The file already has `_get_financial_data()` or queries `estimateTotal`, `costTotal`, `invoiceTotal`. Find where these are returned.

- [ ] **Step 2: Add profit margin calculation**

In the `pull()` function, after computing the existing financial metrics, add:

```python
        # Profit margin = (estimateTotal - costTotal) / estimateTotal
        # Use aggregate from sampled jobs
        total_estimate = 0
        total_cost = 0
        total_invoiced = 0
        total_paid = 0
        
        for j in jobs:
            est = j.get("estimateTotal") or 0
            cost = j.get("costTotal") or 0
            inv = j.get("invoiceTotal") or 0
            total_estimate += est
            total_cost += cost
            total_invoiced += inv
```

Note: This requires that the job query in `_get_pipeline_counts` or the financial query already fetches these fields. If not, add them to the query's `nodes` selection.

- [ ] **Step 3: Compute and add profit margin + A/R to metrics**

```python
        profit_margin = ((total_estimate - total_cost) / total_estimate * 100) if total_estimate > 0 else 0
        accounts_receivable = total_invoiced - total_paid  # Will need amountPaid from documents
        
        # Add to metrics
        result["metrics"]["profit_margin"] = {
            "value": f"{profit_margin:.1f}%",
            "spark": [],
        }
```

- [ ] **Step 4: Query document-level payment data**

Add a new query function for payments:

```python
def _get_payment_data(grant_key, org_id):
    """Get payments received vs invoiced from documents."""
    result = _query(grant_key, {
        "organization": {
            "$": {"id": org_id},
            "documents": {
                "$": {"size": 50, "filter": {"type": "customerInvoice"}},
                "nodes": {
                    "price": True,
                    "amountPaid": True,
                    "status": True,
                }
            }
        }
    })
    docs = result.get("organization", {}).get("documents", {}).get("nodes", [])
    total_invoiced = sum(d.get("price", 0) or 0 for d in docs)
    total_paid = sum(d.get("amountPaid", 0) or 0 for d in docs)
    return {"invoiced": total_invoiced, "paid": total_paid, "ar": total_invoiced - total_paid}
```

- [ ] **Step 5: Wire payment data into the return dict**

```python
        payments = _get_payment_data(grant_key, org_id)
        result["metrics"]["accounts_receivable"] = {
            "value": fmt_dollars(payments["ar"]),
            "spark": [],
        }
```

- [ ] **Step 6: Commit**

```bash
git add tools/pull_jobtread.py
git commit -m "feat: add profit margin and accounts receivable to JobTread pull"
```

---

## Task 6: Expand pull_constant_contact.py — Per-Link Click Details

**Files:**
- Modify: `tools/pull_constant_contact.py`

- [ ] **Step 1: Read pull_constant_contact.py to find the existing campaign query**

Find where campaigns are fetched and where the top_by_clicks list is built.

- [ ] **Step 2: Add per-link click details for top campaigns**

After fetching the top campaigns, for the top 3 campaigns by clicks, fetch link-level data:

```python
    # Per-link click details for top 3 campaigns
    link_details = []
    top_campaigns = sorted(campaigns, key=lambda c: c.get("clicks", 0), reverse=True)[:3]
    for campaign in top_campaigns:
        campaign_id = campaign.get("campaign_id", "")
        if not campaign_id:
            continue
        try:
            links_resp = requests.get(
                f"https://api.cc.email/v3/reports/email_reports/{campaign_id}/links",
                headers=headers,
                timeout=15,
            )
            if links_resp.status_code == 200:
                for link in links_resp.json().get("link_click_counts", [])[:5]:
                    link_details.append({
                        "campaign": campaign.get("name", ""),
                        "url": link.get("link_url", ""),
                        "clicks": link.get("unique_clicks", 0),
                    })
        except Exception:
            pass
```

- [ ] **Step 3: Add to return dict**

```python
    result["link_clicks"] = link_details
```

- [ ] **Step 4: Wire into pull_all.py**

In `transform_for_dashboard()`:

```python
    if "error" not in email_source:
        result["emailLinkClicks"] = email_source.get("link_clicks", [])
```

- [ ] **Step 5: Commit**

```bash
git add tools/pull_constant_contact.py tools/pull_all.py
git commit -m "feat: add per-link click details to Constant Contact pull"
```

---

## Task 7: New Tool — pull_ghl_conversations.py

**Files:**
- Create: `tools/pull_ghl_conversations.py`

- [ ] **Step 1: Create the file with the full implementation**

```python
"""
Pull GHL conversation/message data for sales activity metrics.

Uses the message export endpoint to compute:
- Average response time (first inbound → first outbound)
- Outbound activity counts (SMS, email, call)
- Activity volume by day (sparkline)

.env keys required:
    GHL_API_KEY_{brand}
    GHL_LOCATION_ID_{brand}
"""

import os
from collections import Counter, defaultdict
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://services.leadconnectorhq.com/"
API_VERSION = "2021-07-28"


def _ghl_get(path, headers, params=None):
    url = f"{API_BASE}{path.lstrip('/')}"
    return requests.get(url, headers=headers, params=params or {}, timeout=30)


def _daily_buckets(timestamps, start_date, end_date):
    dt_start = datetime.strptime(start_date, "%Y-%m-%d")
    dt_end = datetime.strptime(end_date, "%Y-%m-%d")
    num_days = (dt_end - dt_start).days + 1
    buckets = [0] * num_days
    for ts in timestamps:
        if not ts:
            continue
        try:
            entry_date = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            idx = (entry_date.date() - dt_start.date()).days
            if 0 <= idx < num_days:
                buckets[idx] += 1
        except (ValueError, AttributeError):
            continue
    return buckets


def pull(api_key: str, location_id: str, start_date: str = None, end_date: str = None) -> dict:
    """Pull conversation/message data from GHL for sales activity metrics."""
    if not api_key or not location_id:
        return {"error": "Missing GHL_API_KEY or GHL_LOCATION_ID"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        # Export messages for the location
        params = {"locationId": location_id, "limit": 100}
        if start_date:
            params["startDate"] = f"{start_date}T00:00:00Z"
        if end_date:
            params["endDate"] = f"{end_date}T23:59:59Z"

        messages = []
        for _ in range(10):
            resp = _ghl_get("conversations/messages/export", headers, params)
            if resp.status_code in (404, 403):
                return {"error": "Message export endpoint not available"}
            if resp.status_code != 200:
                break
            data = resp.json()
            batch = data.get("messages", [])
            messages.extend(batch)
            cursor = data.get("nextCursor") or data.get("meta", {}).get("nextCursor")
            if not cursor or len(batch) < params["limit"]:
                break
            params["cursor"] = cursor

        # Classify messages
        outbound_sms = 0
        outbound_email = 0
        outbound_call = 0
        inbound_count = 0
        outbound_timestamps = []

        # Group by conversation for response time calculation
        conversations = defaultdict(list)

        for msg in messages:
            msg_type = (msg.get("type", "") or "").lower()
            direction = (msg.get("direction", "") or "").lower()
            ts = msg.get("dateAdded", msg.get("createdAt", ""))
            conv_id = msg.get("conversationId", "")

            if conv_id and ts:
                conversations[conv_id].append({
                    "direction": direction,
                    "type": msg_type,
                    "timestamp": ts,
                })

            if direction == "outbound":
                outbound_timestamps.append(ts)
                if "sms" in msg_type or "text" in msg_type:
                    outbound_sms += 1
                elif "email" in msg_type:
                    outbound_email += 1
                elif "call" in msg_type or "voice" in msg_type:
                    outbound_call += 1
            elif direction == "inbound":
                inbound_count += 1

        # Compute average response time
        response_times = []
        for conv_id, msgs in conversations.items():
            sorted_msgs = sorted(msgs, key=lambda m: m["timestamp"])
            first_inbound = None
            for m in sorted_msgs:
                if m["direction"] == "inbound" and first_inbound is None:
                    first_inbound = m["timestamp"]
                elif m["direction"] == "outbound" and first_inbound is not None:
                    try:
                        dt_in = datetime.fromisoformat(first_inbound.replace("Z", "+00:00"))
                        dt_out = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
                        delta = (dt_out - dt_in).total_seconds()
                        if 0 < delta < 86400 * 3:  # Cap at 3 days
                            response_times.append(delta)
                    except (ValueError, AttributeError):
                        pass
                    break  # Only measure first response per conversation

        avg_response_secs = sum(response_times) / len(response_times) if response_times else 0
        avg_response_mins = avg_response_secs / 60

        if avg_response_mins >= 60:
            response_str = f"{avg_response_mins / 60:.1f}h"
        elif avg_response_mins >= 1:
            response_str = f"{avg_response_mins:.0f}m"
        else:
            response_str = f"{avg_response_secs:.0f}s"

        total_outbound = outbound_sms + outbound_email + outbound_call
        spark = _daily_buckets(outbound_timestamps, start_date, end_date) if start_date and end_date else []

        return {
            "metrics": {
                "avg_response_time": {"value": response_str if avg_response_secs else "N/A", "spark": []},
                "outbound_total": {"value": f"{total_outbound:,}", "spark": spark},
                "outbound_sms": {"value": f"{outbound_sms:,}", "spark": []},
                "outbound_email": {"value": f"{outbound_email:,}", "spark": []},
                "outbound_calls": {"value": f"{outbound_call:,}", "spark": []},
                "inbound_total": {"value": f"{inbound_count:,}", "spark": []},
            },
        }

    except requests.exceptions.RequestException as exc:
        return {"error": f"GHL Conversations API error: {exc}"}
    except Exception as exc:
        return {"error": f"GHL Conversations error: {exc}"}


if __name__ == "__main__":
    import json
    key = os.getenv("GHL_API_KEY_RC", "")
    loc = os.getenv("GHL_LOCATION_ID_RC", "")
    result = pull(key, loc, "2026-03-01", "2026-03-31")
    print(json.dumps(result, indent=2))
```

- [ ] **Step 2: Commit**

```bash
git add tools/pull_ghl_conversations.py
git commit -m "feat: add GHL conversations pull for response times and outbound activity"
```

---

## Task 8: New Tool — pull_ghl_tasks.py

**Files:**
- Create: `tools/pull_ghl_tasks.py`

- [ ] **Step 1: Create the file**

```python
"""
Pull GHL task/follow-up data for sales team visibility.

Fetches tasks across contacts to surface overdue follow-ups
and completion rates.

.env keys required:
    GHL_API_KEY_{brand}
    GHL_LOCATION_ID_{brand}
"""

import os
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://services.leadconnectorhq.com/"
API_VERSION = "2021-07-28"


def _ghl_get(path, headers, params=None):
    url = f"{API_BASE}{path.lstrip('/')}"
    return requests.get(url, headers=headers, params=params or {}, timeout=30)


def pull(api_key: str, location_id: str) -> dict:
    """Pull task data from GHL — overdue count, completion rate.
    
    This is a snapshot (not date-ranged) since tasks represent current state.
    """
    if not api_key or not location_id:
        return {"error": "Missing GHL_API_KEY or GHL_LOCATION_ID"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        # Get recent contacts to scan for tasks
        # GHL requires tasks to be fetched per-contact
        contacts_resp = _ghl_get("contacts/", headers, {
            "locationId": location_id,
            "limit": 50,
            "sortBy": "dateAdded",
            "order": "desc",
        })

        if contacts_resp.status_code != 200:
            return {"error": f"Could not fetch contacts: {contacts_resp.status_code}"}

        contacts = contacts_resp.json().get("contacts", [])
        
        total_tasks = 0
        completed_tasks = 0
        overdue_tasks = 0
        now = datetime.utcnow()

        for contact in contacts[:30]:  # Limit to 30 contacts to stay under rate limits
            contact_id = contact.get("id", "")
            if not contact_id:
                continue

            tasks_resp = _ghl_get(f"contacts/{contact_id}/tasks", headers)
            if tasks_resp.status_code != 200:
                continue

            tasks = tasks_resp.json().get("tasks", [])
            for task in tasks:
                total_tasks += 1
                is_completed = task.get("completed", False)
                if is_completed:
                    completed_tasks += 1
                else:
                    # Check if overdue
                    due_date = task.get("dueDate", "")
                    if due_date:
                        try:
                            dt_due = datetime.fromisoformat(due_date.replace("Z", "+00:00")).replace(tzinfo=None)
                            if dt_due < now:
                                overdue_tasks += 1
                        except (ValueError, AttributeError):
                            pass

        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

        return {
            "total": total_tasks,
            "completed": completed_tasks,
            "overdue": overdue_tasks,
            "open": total_tasks - completed_tasks,
            "completion_rate": f"{completion_rate:.0f}%",
        }

    except requests.exceptions.RequestException as exc:
        return {"error": f"GHL Tasks API error: {exc}"}
    except Exception as exc:
        return {"error": f"GHL Tasks error: {exc}"}


if __name__ == "__main__":
    import json
    key = os.getenv("GHL_API_KEY_RC", "")
    loc = os.getenv("GHL_LOCATION_ID_RC", "")
    result = pull(key, loc)
    print(json.dumps(result, indent=2))
```

- [ ] **Step 2: Commit**

```bash
git add tools/pull_ghl_tasks.py
git commit -m "feat: add GHL tasks pull for overdue follow-ups"
```

---

## Task 9: New Tool — pull_google_ads_search_terms.py

**Files:**
- Create: `tools/pull_google_ads_search_terms.py`

- [ ] **Step 1: Read pull_google_ads.py to understand auth pattern**

The existing Google Ads tool uses service account credentials and GAQL queries. Copy the same auth pattern.

- [ ] **Step 2: Create the file**

```python
"""
Pull Google Ads search term report.

Shows what people actually searched for (vs keyword targeting).
Helps marketing identify wasted spend and new keyword opportunities.

.env keys required:
    GOOGLE_APPLICATION_CREDENTIALS
    GOOGLE_ADS_CUSTOMER_ID_{brand}
    GOOGLE_ADS_DEVELOPER_TOKEN
"""

import os
import json

from dotenv import load_dotenv

load_dotenv()


def pull(customer_id: str, developer_token: str, start_date: str, end_date: str) -> dict:
    """Pull top search terms from Google Ads."""
    if not customer_id or not developer_token:
        return {"error": "Missing Google Ads credentials"}

    try:
        from google.ads.googleads.client import GoogleAdsClient

        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        client = GoogleAdsClient.load_from_dict({
            "developer_token": developer_token,
            "json_key_file_path": credentials_path,
            "login_customer_id": customer_id,
        })

        ga_service = client.get_service("GoogleAdsService")

        query = f"""
            SELECT
                search_term_view.search_term,
                metrics.impressions,
                metrics.clicks,
                metrics.ctr,
                metrics.conversions,
                metrics.cost_micros
            FROM search_term_view
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
            ORDER BY metrics.clicks DESC
            LIMIT 15
        """

        response = ga_service.search_stream(customer_id=customer_id, query=query)

        terms = []
        for batch in response:
            for row in batch.results:
                cost = row.metrics.cost_micros / 1_000_000
                terms.append({
                    "term": row.search_term_view.search_term,
                    "impressions": row.metrics.impressions,
                    "clicks": row.metrics.clicks,
                    "ctr": f"{row.metrics.ctr * 100:.1f}%",
                    "conversions": int(row.metrics.conversions),
                    "cost": f"${cost:.2f}",
                })

        return {"terms": terms}

    except Exception as exc:
        return {"error": f"Google Ads Search Terms error: {exc}"}


if __name__ == "__main__":
    cid = os.getenv("GOOGLE_ADS_CUSTOMER_ID_RC", "")
    token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    result = pull(cid, token, "2026-03-01", "2026-03-31")
    print(json.dumps(result, indent=2))
```

- [ ] **Step 3: Commit**

```bash
git add tools/pull_google_ads_search_terms.py
git commit -m "feat: add Google Ads search terms pull"
```

---

## Task 10: New Tool — pull_google_ads_geo.py

**Files:**
- Create: `tools/pull_google_ads_geo.py`

- [ ] **Step 1: Create the file**

```python
"""
Pull Google Ads geographic performance report.

Shows which cities/regions produce conversions at what cost.
Critical for service-area businesses like remodeling.

.env keys required:
    GOOGLE_APPLICATION_CREDENTIALS
    GOOGLE_ADS_CUSTOMER_ID_{brand}
    GOOGLE_ADS_DEVELOPER_TOKEN
"""

import os
import json

from dotenv import load_dotenv

load_dotenv()


def pull(customer_id: str, developer_token: str, start_date: str, end_date: str) -> dict:
    """Pull geographic performance from Google Ads."""
    if not customer_id or not developer_token:
        return {"error": "Missing Google Ads credentials"}

    try:
        from google.ads.googleads.client import GoogleAdsClient

        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        client = GoogleAdsClient.load_from_dict({
            "developer_token": developer_token,
            "json_key_file_path": credentials_path,
            "login_customer_id": customer_id,
        })

        ga_service = client.get_service("GoogleAdsService")

        query = f"""
            SELECT
                geographic_view.country_criterion_id,
                geographic_view.location_type,
                campaign_criterion.location.geo_target_constant,
                metrics.impressions,
                metrics.clicks,
                metrics.conversions,
                metrics.cost_micros
            FROM geographic_view
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
                AND geographic_view.location_type = 'LOCATION_OF_PRESENCE'
            ORDER BY metrics.conversions DESC
            LIMIT 10
        """

        response = ga_service.search_stream(customer_id=customer_id, query=query)

        locations = []
        for batch in response:
            for row in batch.results:
                cost = row.metrics.cost_micros / 1_000_000
                conversions = int(row.metrics.conversions)
                cpc = cost / row.metrics.clicks if row.metrics.clicks > 0 else 0
                # geo_target_constant gives resource name like "geoTargetConstants/1014044"
                # For now, use the ID; a lookup table can resolve names later
                geo_resource = row.campaign_criterion.location.geo_target_constant or ""
                locations.append({
                    "geo_id": geo_resource.split("/")[-1] if "/" in geo_resource else geo_resource,
                    "impressions": row.metrics.impressions,
                    "clicks": row.metrics.clicks,
                    "conversions": conversions,
                    "cost": f"${cost:.2f}",
                    "cpc": f"${cpc:.2f}",
                })

        return {"locations": locations}

    except Exception as exc:
        return {"error": f"Google Ads Geo error: {exc}"}


if __name__ == "__main__":
    cid = os.getenv("GOOGLE_ADS_CUSTOMER_ID_RC", "")
    token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    result = pull(cid, token, "2026-03-01", "2026-03-31")
    print(json.dumps(result, indent=2))
```

- [ ] **Step 2: Commit**

```bash
git add tools/pull_google_ads_geo.py
git commit -m "feat: add Google Ads geographic performance pull"
```

---

## Task 11: Dashboard HTML — Executive Summary Tab

**Files:**
- Modify: `marketing-dashboard.html`

This is the biggest HTML task. The Executive Summary becomes the default landing tab.

- [ ] **Step 1: Add the Executive Summary tab button**

Find the subtab/tab navigation in the HTML. The current tabs are `overview`, `marketing`, `sales`. Add `executive` as the first tab and make it the default active.

Find:
```html
<button class="subtab active" data-tab="overview"
```

Change the tab bar to add Executive as first and active:
```html
<button class="subtab active" data-tab="executive" onclick="switchTab('executive', this)">Executive</button>
<button class="subtab" data-tab="overview" onclick="switchTab('overview', this)">Overview</button>
<button class="subtab" data-tab="marketing" onclick="switchTab('marketing', this)">Marketing</button>
<button class="subtab" data-tab="sales" onclick="switchTab('sales', this)">Sales & Pipeline</button>
```

- [ ] **Step 2: Add the Executive tab panel container**

Add a new tab panel div before the existing `tab-overview` panel:
```html
<div id="tab-executive" class="tab-panel"></div>
```

And add `hidden` class to the overview panel (since executive is now default):
```html
<div id="tab-overview" class="tab-panel hidden"></div>
```

- [ ] **Step 3: Update switchTab and renderCurrentTab**

In the JS, update `renderCurrentTab()`:
```javascript
function renderCurrentTab() {
  if (currentTab === 'executive') renderExecutive(currentBrand);
  if (currentTab === 'overview')  renderOverview(currentBrand);
  if (currentTab === 'marketing') renderMarketing(currentBrand);
  if (currentTab === 'sales')     renderSales(currentBrand);
}
```

Update the initial `currentTab`:
```javascript
let currentTab = 'executive';
```

Add `TAB_TITLES` entry:
```javascript
const TAB_TITLES = {
  executive: 'Executive Summary',
  overview: 'Overview',
  marketing: 'Marketing',
  sales: 'Sales & Pipeline',
};
```

- [ ] **Step 4: Write the renderExecutive function**

Add this function in the JS section:

```javascript
function renderExecutive(brand) {
  const container = document.getElementById('tab-executive');
  if (brand === 'both') {
    // Aggregate across brands for executive view
    let html = '';
    ['rc','rnr','wl'].forEach(b => {
      const d = DATA[b];
      if (!d || d._error) return;
      html += `<div style="margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
          <span style="width:10px;height:10px;border-radius:50%;background:${getBrandColor(b)}"></span>
          <span style="font-size:14px;font-weight:700">${BRAND_NAMES[b]}</span>
        </div>`;
      html += buildExecutiveGrid(d, b);
      html += '</div>';
    });
    container.innerHTML = html;
  } else {
    const d = DATA[brand];
    if (!d) { container.innerHTML = ''; return; }
    container.innerHTML = buildExecutiveGrid(d, brand);
  }
  flushSparklines();
}

function buildExecutiveGrid(brandData, brand) {
  const exec = brandData.executive || {};
  let html = '';

  // Hero KPI row — 6 large cards
  html += '<div class="grid" style="display:grid;grid-template-columns:repeat(6,1fr);gap:16px;margin-bottom:24px">';

  const kpis = [
    {key: 'revenue_closed', label: 'Revenue Closed', icon: '&#128176;', iconClass: 'ci-green'},
    {key: 'pipeline_value', label: 'Pipeline Value', icon: '&#128200;', iconClass: 'ci-blue'},
    {key: 'total_leads', label: 'Total Leads', icon: '&#128101;', iconClass: 'ci-orange'},
    {key: 'cost_per_lead', label: 'Cost Per Lead', icon: '&#128181;', iconClass: 'ci-purple'},
    {key: 'win_rate', label: 'Win Rate', icon: '&#127942;', iconClass: 'ci-green'},
    {key: 'appointments', label: 'Appointments', icon: '&#128197;', iconClass: 'ci-blue'},
  ];

  kpis.forEach(kpi => {
    const d = exec[kpi.key] || {};
    const val = d.value || '--';
    const trend = d.trend || '';
    const dir = d.dir || 'neutral';
    const sub = d.sub || '';
    const spark = d.spark || [];
    const sparkId = spark.length ? `exec-spark-${sparkCounter++}` : '';

    html += `<div class="card" style="padding:20px;text-align:center">
      <div class="card-hd" style="justify-content:center;margin-bottom:12px">
        <div class="card-icon-wrap ${kpi.iconClass}">${kpi.icon}</div>
      </div>
      <div class="card-label" style="margin-bottom:6px">${kpi.label}</div>
      <div style="font-size:32px;font-weight:800;letter-spacing:-0.03em;line-height:1">${val}</div>
      ${trend && trend !== '--' ? `<div class="trend-pill ${trendClass(dir)}" style="margin-top:8px">${trendIcon(dir)} ${trend}</div>` : ''}
      ${sub ? `<div style="font-size:12px;color:var(--text3);margin-top:4px">${sub}</div>` : ''}
      ${sparkId ? `<canvas class="spark-canvas" data-spark-id="${sparkId}" data-spark-values="${spark.join(',')}" data-spark-color="${kpi.iconClass.includes('green') ? 'var(--green)' : 'var(--accent)'}" style="margin-top:8px;height:30px"></canvas>` : ''}
    </div>`;
  });

  html += '</div>';

  // Mini funnel + Lead sources row
  html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">';

  // Sales funnel from JobTread
  const jt = brandData.jobtread;
  if (jt && !jt._error) {
    const pipeline = jt.sales_pipeline || [];
    const funnelStages = ['New Lead', 'Appointment', 'Quoted', 'Sold'];
    const metrics = jt.metrics || {};
    const funnelData = [
      {label: 'Active Leads', value: metrics.active_leads ? metrics.active_leads.value : '0'},
      {label: 'Quoted', value: String(pipeline.filter(p => p.name.includes('Quoted')).reduce((s,p) => s + p.count, 0))},
      {label: 'Sold', value: metrics.sold ? metrics.sold.value : '0'},
    ];
    const maxFunnel = Math.max(...funnelData.map(f => parseInt(f.value.replace(/,/g,'')) || 0), 1);

    let funnelHtml = funnelData.map(f => {
      const num = parseInt(f.value.replace(/,/g,'')) || 0;
      const pct = Math.round(num / maxFunnel * 100);
      return `<div class="pipe-row">
        <span class="pipe-label">${f.label}</span>
        <div class="pipe-track"><div class="pipe-fill" style="width:${pct}%;background:var(--brand-color)"></div></div>
        <span class="pipe-count">${f.value}</span>
      </div>`;
    }).join('');

    html += statCard({
      label: 'Sales Funnel',
      icon: '&#128200;',
      iconClass: 'ci-blue',
      colClass: '',
      rows: null,
      sub: 'JobTread Pipeline',
    }).replace('</div></div></div>', funnelHtml + '</div></div></div>');
  } else {
    html += `<div class="card" style="padding:20px"><div class="card-label">Sales Funnel</div><div class="not-configured">Not configured</div></div>`;
  }

  // Lead sources
  const traffic = brandData.traffic || [];
  if (traffic.length) {
    const maxTraffic = Math.max(...traffic.map(t => parseInt(String(t.val).replace(/,/g,'')) || 0), 1);
    let trafficHtml = traffic.slice(0, 6).map(t => {
      const num = parseInt(String(t.val).replace(/,/g,'')) || 0;
      const pct = Math.round(num / maxTraffic * 100);
      return `<div class="pipe-row">
        <span class="pipe-label">${t.name}</span>
        <div class="pipe-track"><div class="pipe-fill" style="width:${pct}%;background:${t.color || 'var(--brand-color)'}"></div></div>
        <span class="pipe-count">${t.val}</span>
      </div>`;
    }).join('');

    html += `<div class="card" style="padding:20px">
      <div class="card-hd"><div class="card-label">Lead Sources</div><div class="card-icon-wrap ci-orange">&#127760;</div></div>
      <div style="font-size:12px;color:var(--text3);margin-bottom:12px">GA4 Traffic</div>
      ${trafficHtml}
    </div>`;
  } else {
    html += `<div class="card" style="padding:20px"><div class="card-label">Lead Sources</div><div class="not-configured">Not configured</div></div>`;
  }

  html += '</div>';
  return html;
}
```

- [ ] **Step 5: Add responsive CSS for the 6-column hero grid**

Add to the `<style>` block:
```css
@media (max-width: 1200px) {
  .grid[style*="grid-template-columns:repeat(6"] {
    grid-template-columns: repeat(3, 1fr) !important;
  }
}
@media (max-width: 768px) {
  .grid[style*="grid-template-columns:repeat(6"] {
    grid-template-columns: repeat(2, 1fr) !important;
  }
}
```

- [ ] **Step 6: Verify by loading localhost:5051 and checking the Executive tab renders**

Open http://localhost:5051/ — the Executive Summary tab should be the default. With placeholder data it should show 6 KPI cards (values may be `--` without live data) and the funnel/sources cards below.

- [ ] **Step 7: Commit**

```bash
git add marketing-dashboard.html
git commit -m "feat: add Executive Summary tab as default landing page"
```

---

## Task 12: Dashboard HTML — Marketing Tab Enhancements

**Files:**
- Modify: `marketing-dashboard.html`

- [ ] **Step 1: Add trend display to all existing metric cards**

Find the `buildMarketingGrid()` function. Every call to `kpiCard()` or `normMetrics()` already supports `trend` and `dir` fields. The data now has these fields from Task 1. Verify that `kpiCard()` renders the `trend-pill` when `trend` is present. If it already does (check the `kpiCard` function), no changes needed here.

- [ ] **Step 2: Add Top Landing Pages card**

In `buildMarketingGrid()`, after the website section, add:

```javascript
  // Top Landing Pages
  const landingPages = brandData.landingPages || [];
  if (landingPages.length) {
    let lpHtml = '<div style="overflow-x:auto"><table class="form-table"><thead><tr><th>Page</th><th>Sessions</th><th>Conversions</th><th>Conv Rate</th><th>Bounce</th></tr></thead><tbody>';
    landingPages.forEach(lp => {
      lpHtml += `<tr>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${lp.page}">${lp.page}</td>
        <td style="font-weight:700">${lp.sessions.toLocaleString()}</td>
        <td style="font-weight:700">${lp.conversions}</td>
        <td>${lp.conv_rate}</td>
        <td>${lp.bounce_rate}</td>
      </tr>`;
    });
    lpHtml += '</tbody></table></div>';
    // Insert as a card using the existing card pattern
    html += `<div class="card col-6" style="padding:20px">
      <div class="card-hd"><div class="card-label">Top Landing Pages</div><div class="card-icon-wrap ci-blue">&#128196;</div></div>
      <div style="font-size:12px;color:var(--text3);margin-bottom:12px">By Conversions</div>
      ${lpHtml}
    </div>`;
  }
```

- [ ] **Step 3: Add New vs Returning stat to Web card**

Find where the web/GA4 card is built in `buildMarketingGrid()`. Add after the existing metrics:

```javascript
  const nvr = brandData.newVsReturning || {};
  if (nvr.new || nvr.returning) {
    const total = (nvr.new || 0) + (nvr.returning || 0);
    const newPct = total > 0 ? Math.round(nvr.new / total * 100) : 0;
    // Add as stat rows to the web card content
    webContent += `<div class="stat-row" style="margin-top:12px">
      <span class="stat-name">New Visitors</span>
      <span class="stat-val">${(nvr.new || 0).toLocaleString()} (${newPct}%)</span>
    </div>
    <div class="stat-row">
      <span class="stat-name">Returning</span>
      <span class="stat-val">${(nvr.returning || 0).toLocaleString()} (${100 - newPct}%)</span>
    </div>`;
  }
```

- [ ] **Step 4: Add Search Terms card**

```javascript
  // Search Terms
  const searchTerms = brandData.searchTerms || [];
  if (searchTerms.length) {
    let stHtml = '<div style="overflow-x:auto"><table class="form-table"><thead><tr><th>Search Term</th><th>Clicks</th><th>Conv</th><th>Cost</th></tr></thead><tbody>';
    searchTerms.slice(0, 10).forEach(t => {
      stHtml += `<tr>
        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.term}</td>
        <td style="font-weight:700">${t.clicks}</td>
        <td>${t.conversions}</td>
        <td>${t.cost}</td>
      </tr>`;
    });
    stHtml += '</tbody></table></div>';
    html += `<div class="card col-4" style="padding:20px">
      <div class="card-hd"><div class="card-label">Top Search Terms</div><div class="card-icon-wrap ci-orange">&#128270;</div></div>
      <div style="font-size:12px;color:var(--text3);margin-bottom:12px">Google Ads</div>
      ${stHtml}
    </div>`;
  }
```

- [ ] **Step 5: Add Geographic Performance card**

```javascript
  // Geographic Performance
  const geoData = brandData.geoPerformance || [];
  if (geoData.length) {
    let geoHtml = '<div style="overflow-x:auto"><table class="form-table"><thead><tr><th>Location</th><th>Clicks</th><th>Conv</th><th>Cost</th><th>CPC</th></tr></thead><tbody>';
    geoData.forEach(g => {
      geoHtml += `<tr>
        <td>${g.geo_id}</td>
        <td>${g.clicks}</td>
        <td style="font-weight:700">${g.conversions}</td>
        <td>${g.cost}</td>
        <td>${g.cpc}</td>
      </tr>`;
    });
    geoHtml += '</tbody></table></div>';
    html += `<div class="card col-4" style="padding:20px">
      <div class="card-hd"><div class="card-label">Top Locations</div><div class="card-icon-wrap ci-green">&#128205;</div></div>
      <div style="font-size:12px;color:var(--text3);margin-bottom:12px">Google Ads Geo</div>
      ${geoHtml}
    </div>`;
  }
```

- [ ] **Step 6: Commit**

```bash
git add marketing-dashboard.html
git commit -m "feat: add landing pages, search terms, geo performance, new vs returning to Marketing tab"
```

---

## Task 13: Dashboard HTML — Sales Tab Enhancements

**Files:**
- Modify: `marketing-dashboard.html`

- [ ] **Step 1: Add Sales Activity card to renderSalesGrid**

In `renderSalesGrid()`, after the Appointments card, add:

```javascript
  // Sales Activity card
  const activity = brandData.salesActivity;
  if (activity && !activity._error) {
    const actMetrics = activity;
    let actContent = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:16px">';
    
    const actItems = [
      {label: 'Outbound Total', key: 'outbound_total'},
      {label: 'SMS Sent', key: 'outbound_sms'},
      {label: 'Emails Sent', key: 'outbound_email'},
      {label: 'Calls Made', key: 'outbound_calls'},
      {label: 'Avg Response', key: 'avg_response_time'},
    ];

    actItems.forEach(item => {
      const m = actMetrics[item.key] || {};
      const trend = m.trend || '';
      const dir = m.dir || 'neutral';
      actContent += `<div>
        <div class="card-label">${item.label}</div>
        <div style="font-size:22px;font-weight:800">${m.value || '--'}</div>
        ${trend && trend !== '--' ? `<div class="trend-pill ${trendClass(dir)}" style="margin-top:4px">${trendIcon(dir)} ${trend}</div>` : ''}
      </div>`;
    });
    actContent += '</div>';

    html += `<div class="card col-5" style="padding:20px">
      <div class="card-hd"><div class="card-label">Sales Activity</div><div class="card-icon-wrap ci-orange">&#128172;</div></div>
      <div style="font-size:12px;color:var(--text3);margin-bottom:12px">GHL Conversations</div>
      ${actContent}
    </div>`;
  }
```

- [ ] **Step 2: Add Overdue Tasks indicator**

```javascript
  // Overdue Follow-ups
  const tasksData = brandData.tasks;
  if (tasksData && !tasksData._error) {
    const overdueColor = tasksData.overdue > 0 ? 'var(--red)' : 'var(--green)';
    html += `<div class="card col-3" style="padding:20px">
      <div class="card-hd"><div class="card-label">Follow-ups</div><div class="card-icon-wrap ci-purple">&#9745;</div></div>
      <div style="font-size:12px;color:var(--text3);margin-bottom:12px">GHL Tasks</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div>
          <div class="card-label">Overdue</div>
          <div style="font-size:28px;font-weight:800;color:${overdueColor}">${tasksData.overdue}</div>
        </div>
        <div>
          <div class="card-label">Open</div>
          <div style="font-size:28px;font-weight:800">${tasksData.open}</div>
        </div>
        <div>
          <div class="card-label">Completed</div>
          <div style="font-size:28px;font-weight:800;color:var(--green)">${tasksData.completed}</div>
        </div>
        <div>
          <div class="card-label">Completion Rate</div>
          <div style="font-size:28px;font-weight:800">${tasksData.completion_rate}</div>
        </div>
      </div>
    </div>`;
  }
```

- [ ] **Step 3: Add profit margin and A/R to Revenue & Pipeline card**

Find the Revenue & Pipeline card in `renderSalesGrid()`. After the existing Avg Job Value section, add:

```javascript
    // Profit Margin
    const profitMargin = jtMetrics.profit_margin;
    if (profitMargin) {
      finContent += `<div>
        <div class="card-label">Profit Margin</div>
        <div style="font-size:28px;font-weight:800;letter-spacing:-0.02em">${profitMargin.value}</div>
        ${profitMargin.trend && profitMargin.trend !== '--' ? `<div class="trend-pill ${trendClass(profitMargin.dir)}" style="margin-top:4px">${trendIcon(profitMargin.dir)} ${profitMargin.trend}</div>` : ''}
      </div>`;
    }

    // Accounts Receivable
    const ar = jtMetrics.accounts_receivable;
    if (ar) {
      finContent += `<div>
        <div class="card-label">Receivable (A/R)</div>
        <div style="font-size:28px;font-weight:800;letter-spacing:-0.02em;color:var(--yellow)">${ar.value}</div>
      </div>`;
    }
```

- [ ] **Step 4: Verify by loading localhost:5051 and checking the Sales tab**

- [ ] **Step 5: Commit**

```bash
git add marketing-dashboard.html
git commit -m "feat: add sales activity, follow-ups, profit margin, A/R to Sales tab"
```

---

## Task 14: Final Integration Test

**Files:**
- All modified files

- [ ] **Step 1: Run a test pull to verify no Python errors**

```bash
cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard"
python -c "
from tools.pull_all import get_previous_range, compute_trend, extract_raw_number, build_executive_summary
print('Imports OK')
print('Previous range:', get_previous_range('2026-03-10', '2026-04-08'))
print('Trend:', compute_trend(120, 100))
print('Rate trend:', compute_trend(45.2, 42.1, is_rate=True))
print('Extract:', extract_raw_number({'metrics': {'spend': {'value': '$1,234.56'}}}, 'spend'))
"
```

Expected: All functions work, no import errors.

- [ ] **Step 2: Run a real data pull (if credentials are configured)**

```bash
cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard"
python tools/pull_all.py --days 30 --brand rc
```

Check `.tmp/data.json` for:
- `executive` key with 6 hero KPIs
- `searchTerms`, `geoPerformance`, `landingPages`, `newVsReturning` keys
- `salesActivity`, `tasks` keys
- `trend` and `dir` fields on metrics throughout

- [ ] **Step 3: Load the dashboard and verify all three tabs render**

Open http://localhost:5051/ and check:
1. Executive Summary tab loads as default with 6 KPI cards
2. Marketing tab shows new cards (landing pages, search terms, geo) where data exists
3. Sales tab shows sales activity, follow-ups, profit margin, A/R where data exists
4. Trend arrows appear on metric cards where previous period data exists

- [ ] **Step 4: Commit final state**

```bash
git add -A
git commit -m "feat: complete dashboard metrics overhaul — executive summary, enhanced marketing + sales tabs, period-over-period trends"
```
