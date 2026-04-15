"""
Pull campaign tracker data from the RC Marketing Campaign Tracker.

Supports two sources:
  1. Google Sheets (published CSV URL) — preferred, always up-to-date
  2. Local .xlsx file — fallback

Required .env keys (checked in order):
  CAMPAIGN_TRACKER_URL_RC   — Google Sheets "publish to web" CSV base URL
  CAMPAIGN_TRACKER_PATH_RC  — full path to the local .xlsx file

Google Sheets tab GIDs:
  Campaign Data:   (default, no gid needed)
  Dashboard:       1343838084
  Campaign Ranker: 1306645481
  Assumptions:     1289161274
"""

import os
import io
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Tab GIDs for Google Sheets
TAB_GIDS = {
    "campaign_data": None,  # gid is included in CAMPAIGN_TRACKER_URL_RC directly
    "dashboard":     None,  # update with sheet's dashboard tab gid if available
    "ranker":        None,  # update with sheet's ranker tab gid if available
    "assumptions":   None,  # update with sheet's assumptions tab gid if available
}


def _safe_float(val, default=0.0):
    import math
    try:
        s = str(val).replace("$", "").replace(",", "").replace("-", "").strip()
        if not s or s.lower() == "nan":
            return default
        v = float(s)
        return default if math.isnan(v) else v
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    import math
    try:
        s = str(val).replace(",", "").replace("-", "").strip()
        if not s or s.lower() == "nan":
            return default
        v = float(s)
        return default if math.isnan(v) else int(v)
    except (ValueError, TypeError):
        return default


def _safe_pct(val, default=None):
    """Parse a percentage string like '38%' into a float like 0.38."""
    try:
        s = str(val).replace("%", "").replace(",", "").strip()
        if not s or s.lower() == "nan":
            return default
        return float(s) / 100.0
    except (ValueError, TypeError):
        return default


def _fmt_dollar(n):
    if n == 0:
        return "$0"
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"${n / 1_000:.1f}K"
    return f"${n:,.0f}"


def _fmt_pct(n):
    return f"{n:.1f}%"


def _load_csv_from_url(base_url, gid=None):
    """Fetch a specific tab as CSV from a published Google Sheet."""
    import re
    import requests
    import pandas as pd

    url = base_url
    if gid:
        # Replace existing gid param if present, otherwise append
        if re.search(r'[?&]gid=', url):
            url = re.sub(r'(gid=)[^&]*', rf'\g<1>{gid}', url)
        else:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}gid={gid}"

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text), header=None)


def _load_dataframe(source, sheet_name=None):
    """Load data as a DataFrame from either a URL (CSV) or local file (xlsx)."""
    import pandas as pd

    if source.startswith("http"):
        return _load_csv_from_url(source)
    else:
        if not os.path.exists(source):
            return None
        sn = sheet_name or "📋 Campaign Data"
        return pd.read_excel(source, sheet_name=sn, header=None)


def _parse_ranker_section(df, start_row):
    """Parse a top-5 ranker section starting at the given row."""
    import pandas as pd
    entries = []
    for i in range(start_row + 1, min(start_row + 6, len(df))):
        row = df.iloc[i]
        name = str(row.iloc[2]) if pd.notna(row.iloc[2]) else ""
        if not name or name in ("nan", "-"):
            continue
        entries.append({
            "rank":    _safe_int(str(row.iloc[0]).replace("#", "")),
            "name":    name,
            "service": str(row.iloc[3]) if pd.notna(row.iloc[3]) else "",
            "geo":     str(row.iloc[4]) if pd.notna(row.iloc[4]) else "",
            "channel": str(row.iloc[5]) if pd.notna(row.iloc[5]) else "",
            "spend":   _safe_float(row.iloc[6]),
            "leads":   _safe_int(row.iloc[7]),
            "jobs":    _safe_int(row.iloc[8]),
            "revenue": _safe_float(row.iloc[9]),
            "metric":  str(row.iloc[10]).strip() if pd.notna(row.iloc[10]) else "",
        })
    return entries


def _parse_ranker(source):
    """Parse the Campaign Ranker tab."""
    import pandas as pd

    try:
        if source.startswith("http"):
            df = _load_csv_from_url(source, TAB_GIDS["ranker"])
        else:
            df = pd.read_excel(source, sheet_name="🏆 Campaign Ranker", header=None)
    except Exception:
        return None

    result = {}
    for i in range(len(df)):
        cell = str(df.iloc[i, 0]) if pd.notna(df.iloc[i, 0]) else ""
        if "TOP CAMPAIGNS BY ROAS" in cell.upper():
            # Header row is next, data starts after
            result["by_roas"] = _parse_ranker_section(df, i + 1)
        elif "TOP CAMPAIGNS BY PROFIT" in cell.upper():
            result["by_profit"] = _parse_ranker_section(df, i + 1)
        elif "TOP CAMPAIGNS BY CPL" in cell.upper():
            result["by_cpl"] = _parse_ranker_section(df, i + 1)

    return result if result else None


def _parse_assumptions(source):
    """Parse the Assumptions tab."""
    import pandas as pd

    try:
        if source.startswith("http"):
            df = _load_csv_from_url(source, TAB_GIDS["assumptions"])
        else:
            df = pd.read_excel(source, sheet_name="⚙️ Assumptions", header=None)
    except Exception:
        return None

    margins = {}
    benchmarks = {}
    section = None

    for i in range(len(df)):
        cell = str(df.iloc[i, 0]) if pd.notna(df.iloc[i, 0]) else ""

        if "SERVICE LINE GROSS MARGINS" in cell.upper():
            section = "margins"
            continue
        elif "BENCHMARK TARGETS" in cell.upper():
            section = "benchmarks"
            continue
        elif "CHANNEL NOTES" in cell.upper():
            section = None
            continue

        if section == "margins":
            svc = cell.strip()
            if svc and svc not in ("Service", ""):
                pct = _safe_pct(df.iloc[i, 1])
                if pct is not None:
                    margins[svc] = round(pct * 100, 1)

        elif section == "benchmarks":
            metric = cell.strip()
            if metric and metric not in ("Metric", ""):
                val = str(df.iloc[i, 1]).strip() if pd.notna(df.iloc[i, 1]) else ""
                benchmarks[metric] = val

    return {"margins": margins, "benchmarks": benchmarks} if (margins or benchmarks) else None


def pull(source: str) -> dict:
    """
    Parse the RC Marketing Campaign Tracker from a Google Sheets CSV URL or local Excel file.

    Returns a dict with:
      - rows: list of campaign row dicts
      - totals: portfolio-wide KPI totals
      - by_service: breakdown by service type
      - by_channel: breakdown by channel/medium
      - by_geo: breakdown by geographic area
      - ranker: top campaigns by ROAS, profit, CPL
      - assumptions: gross margins and benchmark targets
    """
    if not source:
        return {"error": "Missing CAMPAIGN_TRACKER_URL_RC or CAMPAIGN_TRACKER_PATH_RC in .env"}

    try:
        import pandas as pd
    except ImportError:
        return {"error": "pandas not installed. Run: pip install pandas openpyxl"}

    try:
        df = _load_dataframe(source)
        if df is None:
            return {"error": f"Campaign tracker not found: {source}"}
    except Exception as e:
        return {"error": f"Could not read spreadsheet: {e}"}

    # Columns: A=image/link, B=ID, C=name, D=service, E=geo, F=channel,
    #          G=start, H=end, I=spend, J=impressions, K=leads, L=appts,
    #          M=proposals, N=jobs, O=revenue, P=CPL, Q=CPA, R=close%, S=rev/lead, T=ROAS, U=profit
    COL = {
        "link":       0,   # A
        "id":         1,   # B
        "name":       2,   # C
        "service":    3,   # D
        "geo":        4,   # E
        "channel":    5,   # F
        "start":      6,   # G
        "end":        7,   # H
        "spend":      8,   # I
        "impressions":9,   # J
        "leads":      10,  # K
        "appts":      11,  # L
        "proposals":  12,  # M
        "jobs":       13,  # N
        "revenue":    14,  # O
    }

    rows = []
    # Data starts at row index 5 (0-based), header is row 4
    for i in range(5, len(df)):
        row = df.iloc[i]

        # Skip empty / totals rows
        name = str(row.iloc[COL["name"]]) if pd.notna(row.iloc[COL["name"]]) else ""
        service = str(row.iloc[COL["service"]]) if pd.notna(row.iloc[COL["service"]]) else ""
        if not name or name in ("nan", "-") or "TOTAL" in name.upper():
            continue
        if not service or service in ("nan", "-"):
            continue

        spend      = _safe_float(row.iloc[COL["spend"]])
        impressions= _safe_int(row.iloc[COL["impressions"]])
        leads      = _safe_int(row.iloc[COL["leads"]])
        appts      = _safe_int(row.iloc[COL["appts"]])
        proposals  = _safe_int(row.iloc[COL["proposals"]])
        jobs       = _safe_int(row.iloc[COL["jobs"]])
        revenue    = _safe_float(row.iloc[COL["revenue"]])

        geo     = str(row.iloc[COL["geo"]]).strip()     if pd.notna(row.iloc[COL["geo"]])     else ""
        channel = str(row.iloc[COL["channel"]]).strip() if pd.notna(row.iloc[COL["channel"]]) else ""

        # Format start/end dates
        def _fmt_date(v):
            if pd.isna(v):
                return ""
            try:
                return pd.Timestamp(v).strftime("%b %d, %Y")
            except Exception:
                return str(v)

        start_raw = row.iloc[COL["start"]]
        end_raw   = row.iloc[COL["end"]]

        # Calculated metrics
        cpl      = spend / leads    if leads    > 0 else None
        cpa      = spend / appts    if appts    > 0 else None
        close_rt = jobs  / leads    if leads    > 0 else None
        roas     = revenue / spend  if spend    > 0 else None

        rows.append({
            "name":       name,
            "service":    service,
            "geo":        geo,
            "channel":    channel,
            "start":      _fmt_date(start_raw),
            "end":        _fmt_date(end_raw),
            "spend":      spend,
            "impressions":impressions,
            "leads":      leads,
            "appts":      appts,
            "proposals":  proposals,
            "jobs":       jobs,
            "revenue":    revenue,
            "cpl":        round(cpl, 2)      if cpl      is not None else None,
            "cpa":        round(cpa, 2)      if cpa      is not None else None,
            "close_rate": round(close_rt*100,1) if close_rt is not None else None,
            "roas":       round(roas, 2)     if roas     is not None else None,
        })

    if not rows:
        return {"error": "No campaign rows found in spreadsheet"}

    # ── Portfolio totals
    total_spend   = sum(r["spend"]       for r in rows)
    total_impr    = sum(r["impressions"] for r in rows)
    total_leads   = sum(r["leads"]       for r in rows)
    total_appts   = sum(r["appts"]       for r in rows)
    total_props   = sum(r["proposals"]   for r in rows)
    total_jobs    = sum(r["jobs"]        for r in rows)
    total_revenue = sum(r["revenue"]     for r in rows)

    blended_cpl   = total_spend / total_leads   if total_leads   > 0 else None
    blended_cpa   = total_spend / total_appts   if total_appts   > 0 else None
    close_rate    = total_jobs  / total_leads   if total_leads   > 0 else None
    blended_roas  = total_revenue / total_spend if total_spend   > 0 else None

    totals = {
        "spend":      _fmt_dollar(total_spend),
        "impressions": f"{total_impr:,}",
        "leads":      total_leads,
        "appts":      total_appts,
        "proposals":  total_props,
        "jobs":       total_jobs,
        "revenue":    _fmt_dollar(total_revenue),
        "cpl":        f"${blended_cpl:.2f}"       if blended_cpl  is not None else "—",
        "cpa":        f"${blended_cpa:.2f}"       if blended_cpa  is not None else "—",
        "close_rate": _fmt_pct(close_rate * 100)  if close_rate   is not None else "—",
        "roas":       f"{blended_roas:.1f}x"      if blended_roas is not None else "—",
    }

    # ── Breakdown helper
    def _breakdown(key):
        groups = {}
        for r in rows:
            g = r[key] or "Unknown"
            if g not in groups:
                groups[g] = {"spend": 0, "leads": 0, "appts": 0, "jobs": 0, "revenue": 0}
            groups[g]["spend"]   += r["spend"]
            groups[g]["leads"]   += r["leads"]
            groups[g]["appts"]   += r["appts"]
            groups[g]["jobs"]    += r["jobs"]
            groups[g]["revenue"] += r["revenue"]

        result = []
        for name, v in sorted(groups.items(), key=lambda x: -x[1]["spend"]):
            sp, ld, jb, rv = v["spend"], v["leads"], v["jobs"], v["revenue"]
            result.append({
                "name":       name,
                "spend":      _fmt_dollar(sp),
                "leads":      ld,
                "appts":      v["appts"],
                "jobs":       jb,
                "revenue":    _fmt_dollar(rv),
                "cpl":        f"${sp/ld:.2f}"  if ld > 0 else "—",
                "close_rate": _fmt_pct(jb/ld*100) if ld > 0 else "—",
                "roas":       f"{rv/sp:.1f}x"  if sp > 0 else "—",
            })
        return result

    result = {
        "rows":       rows,
        "totals":     totals,
        "by_service": _breakdown("service"),
        "by_channel": _breakdown("channel"),
        "by_geo":     _breakdown("geo"),
        "last_updated": datetime.now().strftime("%b %d, %Y %I:%M %p"),
    }

    # ── Pull additional tabs (ranker + assumptions)
    try:
        ranker = _parse_ranker(source)
        if ranker:
            result["ranker"] = ranker
    except Exception:
        pass

    try:
        assumptions = _parse_assumptions(source)
        if assumptions:
            result["assumptions"] = assumptions
    except Exception:
        pass

    return result


if __name__ == "__main__":
    source = os.getenv("CAMPAIGN_TRACKER_URL_RC", "") or os.getenv("CAMPAIGN_TRACKER_PATH_RC", "")
    result = pull(source)
    print(json.dumps(result, indent=2))
