"""
Pull JobTread pipeline/CRM data for the marketing dashboard.

Uses the Pave Query API at https://api.jobtread.com/pave

Required .env keys:
  JOBTREAD_GRANT_KEY
  JOBTREAD_ORG_ID_RC / JOBTREAD_ORG_ID_RNR
"""

import os
import json
import requests
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://api.jobtread.com/pave"


def fmt_number(n):
    return f"{n:,.0f}" if isinstance(n, (int, float)) else str(n)


def fmt_dollars(n):
    """Format as dollar amount."""
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n / 1_000:.1f}K"
    return f"${n:,.0f}"


def _query(grant_key, query_body, timeout=30):
    """Execute a Pave query."""
    payload = {"query": {"$": {"grantKey": grant_key}, **query_body}}
    resp = requests.post(API_URL, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise Exception(f"JobTread API {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _get_custom_field_ids(grant_key, org_id):
    """Find the Lead Status and Production Status custom field IDs for this org."""
    result = _query(grant_key, {
        "organization": {
            "$": {"id": org_id},
            "customFields": {"$": {"size": 100}, "nodes": {"id": True, "name": True}}
        }
    })
    fields = result["organization"]["customFields"]["nodes"]

    field_map = {}
    for f in fields:
        name = f["name"]
        if name in ("Lead Status", "Production Status", "Production Pipeline", "Design Pipeline"):
            field_map[name] = f["id"]
    return field_map


def _get_pipeline_options(grant_key, field_id):
    """Get the ordered list of options for a custom field."""
    result = _query(grant_key, {
        "customField": {"$": {"id": field_id}, "name": True, "options": True}
    })
    return result.get("customField", {}).get("options", [])


def _get_pipeline_counts(grant_key, org_id, batch_size=100):
    """Get job counts by Lead Status and Production Status custom fields.

    The Pave API does not support pagination (no pageInfo, no after cursor,
    max size=100), so we sample the most recent 100 jobs and scale counts
    by total/sampled ratio.
    """
    field_ids = _get_custom_field_ids(grant_key, org_id)
    lead_fid = field_ids.get("Lead Status", "")
    prod_fid = field_ids.get("Production Status", "") or field_ids.get("Production Pipeline", "")
    design_fid = field_ids.get("Design Pipeline", "")

    lead_counts = Counter()
    prod_counts = Counter()
    design_counts = Counter()

    result = _query(grant_key, {
        "organization": {
            "$": {"id": org_id},
            "jobs": {
                "$": {"size": batch_size},
                "count": True,
                "nodes": {
                    "customFieldValues": {
                        "nodes": {"value": True, "customField": {"id": True}}
                    }
                }
            }
        }
    })

    jobs_data = result["organization"]["jobs"]
    total = jobs_data.get("count", 0)
    jobs = jobs_data.get("nodes", [])
    fetched = len(jobs)

    for j in jobs:
        for cfv in j.get("customFieldValues", {}).get("nodes", []):
            cid = cfv.get("customField", {}).get("id", "")
            val = cfv.get("value", "")
            if not val:
                continue
            if cid == lead_fid:
                lead_counts[val] += 1
            elif cid == prod_fid:
                prod_counts[val] += 1
            elif cid == design_fid:
                design_counts[val] += 1

    # Scale sample counts up to estimated totals
    scale = (total / fetched) if fetched > 0 else 1

    def scale_counts(c):
        return {k: round(v * scale) for k, v in c.items()}

    return {
        "sampled": fetched,
        "total": total,
        "scale": scale,
        "lead_status": scale_counts(lead_counts),
        "production_status": scale_counts(prod_counts),
        "design_pipeline": scale_counts(design_counts),
    }


# Pipeline stage colors
LEAD_COLORS = {
    "Builders: New Bid": "#3b82f6",
    "Retail: New Lead": "#22c55e",
    "Retail: Needs Follow Up": "#f59e0b",
    "Builders: Estimating": "#6366f1",
    "Retail: Appointment Set": "#8b5cf6",
    "Retail: Estimating": "#a855f7",
    "Builders: Quoted": "#0ea5e9",
    "Retail: Quoted": "#14b8a6",
    "Retail: Proposal Follow Up": "#f97316",
    "Builders: Bid Lost": "#ef4444",
    "Retail: Lost Lead Did not sell": "#dc2626",
    "Sold": "#16a34a",
    "Dead do not call": "#6b7280",
    "Drip Campaign": "#9ca3af",
    # RR lead statuses
    "New Lead": "#22c55e",
    "Future Lead Follow Up": "#f59e0b",
    "Appointment Scheduled": "#8b5cf6",
    "Appointment Seen": "#a855f7",
    "PDA in Progress": "#6366f1",
    "PDA Ready for Review": "#0ea5e9",
    "PDA Follow Up": "#f97316",
    "Dead": "#6b7280",
    "Lost": "#ef4444",
    "Disqualified": "#9ca3af",
    "Cancelled Contract": "#dc2626",
    "Service Agreement": "#14b8a6",
}

PROD_COLORS = {
    "Pre-Production Review": "#3b82f6",
    "Permit Filed": "#6366f1",
    "Pending Shingle Color": "#8b5cf6",
    "Pending Measurement": "#a855f7",
    "Measurement Scheduled": "#0ea5e9",
    "Materials Ordered": "#14b8a6",
    "Ready to Schedule": "#22c55e",
    "Work Scheduled": "#16a34a",
    "Work in Progress": "#f59e0b",
    "Punch List": "#f97316",
    "On Hold": "#9ca3af",
    "Completed": "#22c55e",
    "Invoiced": "#0d9488",
    "Cancelled": "#ef4444",
    "Closed": "#6b7280",
    # RR production
    "Ordering & Permitting": "#3b82f6",
    "Production Handoff Meeting": "#6366f1",
    "Pre-Construction Meeting": "#8b5cf6",
    "Ready for Project Start": "#22c55e",
    "In Production": "#f59e0b",
    "Punchlist": "#f97316",
    "Final Walkthrough": "#14b8a6",
    "Warranty": "#9ca3af",
}


def _get_payment_data(grant_key, org_id):
    """Query customer invoices to calculate accounts receivable."""
    result = _query(grant_key, {
        "organization": {
            "$": {"id": org_id},
            "documents": {
                "$": {"size": 50, "filter": {"type": "customerInvoice"}},
                "nodes": {"price": True, "amountPaid": True, "status": True}
            }
        }
    })
    docs = result.get("organization", {}).get("documents", {}).get("nodes", [])
    total_invoiced = sum(d.get("price", 0) or 0 for d in docs)
    total_paid = sum(d.get("amountPaid", 0) or 0 for d in docs)
    ar = total_invoiced - total_paid
    return {"accounts_receivable": ar}


def pull(grant_key: str, org_id: str = None, start_date: str = None, end_date: str = None) -> dict:
    """Pull JobTread pipeline data with actual Lead Status and Production Status."""
    if not grant_key:
        return {"error": "Missing JOBTREAD_GRANT_KEY"}

    try:
        if not org_id:
            result = _query(grant_key, {
                "organizations": {"nodes": {"id": True, "name": True, "jobs": {"count": True}}}
            })
            orgs = result.get("organizations", {}).get("nodes", [])
            if not orgs:
                return {"error": "No organizations found"}
            org_id = orgs[0]["id"]

        # Get org name and total jobs
        result = _query(grant_key, {
            "organization": {"$": {"id": org_id}, "name": True, "jobs": {"count": True}}
        })
        org_name = result["organization"]["name"]
        total_jobs = result["organization"]["jobs"]["count"]

        # Get pipeline field options (ordered stages)
        field_ids = _get_custom_field_ids(grant_key, org_id)

        lead_options = []
        prod_options = []
        if "Lead Status" in field_ids:
            lead_options = _get_pipeline_options(grant_key, field_ids["Lead Status"])
        if "Production Status" in field_ids:
            prod_options = _get_pipeline_options(grant_key, field_ids["Production Status"])
        elif "Production Pipeline" in field_ids:
            prod_options = _get_pipeline_options(grant_key, field_ids["Production Pipeline"])

        # Get actual counts from job data
        counts = _get_pipeline_counts(grant_key, org_id, batch_size=30)

        # Build sales pipeline (Lead Status) in the correct order
        sales_pipeline = []
        lead_data = counts["lead_status"]
        for stage in lead_options:
            count = lead_data.get(stage, 0)
            sales_pipeline.append({
                "name": stage,
                "count": count,
                "color": LEAD_COLORS.get(stage, "#6b7280"),
            })
        # Add any stages in data but not in options
        for stage, count in lead_data.items():
            if stage not in lead_options:
                sales_pipeline.append({
                    "name": stage,
                    "count": count,
                    "color": LEAD_COLORS.get(stage, "#6b7280"),
                })

        # Build production pipeline
        production_pipeline = []
        prod_data = counts["production_status"]
        for stage in prod_options:
            count = prod_data.get(stage, 0)
            production_pipeline.append({
                "name": stage,
                "count": count,
                "color": PROD_COLORS.get(stage, "#6b7280"),
            })
        for stage, count in prod_data.items():
            if stage not in prod_options:
                production_pipeline.append({
                    "name": stage,
                    "count": count,
                    "color": PROD_COLORS.get(stage, "#6b7280"),
                })

        # Calculate active/sold/lost
        active_sales = sum(c for s, c in lead_data.items()
                          if s not in ("Sold", "Dead do not call", "Drip Campaign",
                                       "Dead", "Lost", "Disqualified",
                                       "Builders: Bid Lost", "Retail: Lost Lead Did not sell"))
        sold = lead_data.get("Sold", 0)
        lost = sum(lead_data.get(s, 0) for s in [
            "Builders: Bid Lost", "Retail: Lost Lead Did not sell",
            "Dead do not call", "Dead", "Lost", "Disqualified"
        ])

        # Win rate = sold / (sold + lost)
        decided = sold + lost
        win_rate = (sold / decided * 100) if decided > 0 else 0

        # Profit margin from financial data (estimate vs cost totals)
        try:
            fin_result = _query(grant_key, {
                "organization": {
                    "$": {"id": org_id},
                    "jobs": {
                        "$": {"size": 50},
                        "nodes": {"estimateTotal": True, "costTotal": True}
                    }
                }
            })
            fin_jobs = fin_result.get("organization", {}).get("jobs", {}).get("nodes", [])
            total_estimate = sum(j.get("estimateTotal", 0) or 0 for j in fin_jobs)
            total_cost = sum(j.get("costTotal", 0) or 0 for j in fin_jobs)
            profit_margin = ((total_estimate - total_cost) / total_estimate * 100) if total_estimate > 0 else 0
        except Exception:
            profit_margin = 0

        # Accounts receivable from customer invoices
        try:
            ar_data = _get_payment_data(grant_key, org_id)
            ar_value = ar_data.get("accounts_receivable", 0)
        except Exception:
            ar_value = 0

        return {
            "org_name": org_name,
            "metrics": {
                "total_jobs": {"value": fmt_number(total_jobs), "spark": []},
                "active_leads": {"value": fmt_number(active_sales), "spark": []},
                "sold": {"value": fmt_number(sold), "spark": []},
                "lost": {"value": fmt_number(lost), "spark": []},
                "win_rate": {"value": f"{win_rate:.0f}%", "spark": []},
                "profit_margin": {"value": f"{profit_margin:.1f}%", "spark": []},
                "accounts_receivable": {"value": fmt_dollars(ar_value), "spark": []},
            },
            "sales_pipeline": sales_pipeline,
            "production_pipeline": production_pipeline,
        }

    except Exception as e:
        return {"error": f"JobTread API error: {str(e)}"}


if __name__ == "__main__":
    grant_key = os.getenv("JOBTREAD_GRANT_KEY", "")
    for suffix in ["RC", "RNR"]:
        org_id = os.getenv(f"JOBTREAD_ORG_ID_{suffix}", "")
        if org_id:
            result = pull(grant_key, org_id)
            print(f"\n=== {suffix} ===")
            print(json.dumps(result, indent=2)[:3000])
