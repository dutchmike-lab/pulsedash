"""
Live JobTread query tools for the Ask AI agent.

Uses the Pave Query API. Scoped to Remodeling Concepts. Read-only.

Pave constraints (as of 2026-04):
- Max 100 jobs per query; no cursor pagination exposed
- Job timestamps not on Job directly — use customFieldValues[].createdAt
  (the timestamp the current value was set, = when job entered stage)
"""

import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://api.jobtread.com/pave"
PAGE_SIZE = 50


def _creds():
    grant = os.environ.get("JOBTREAD_GRANT_KEY")
    org = os.environ.get("JOBTREAD_ORG_ID_RC")
    if not grant or not org:
        raise RuntimeError("JOBTREAD_GRANT_KEY and JOBTREAD_ORG_ID_RC must be set in .env")
    return grant, org


def _query(query_body: dict, timeout: int = 30) -> dict:
    grant, _ = _creds()
    payload = {"query": {"$": {"grantKey": grant}, **query_body}}
    resp = requests.post(API_URL, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise Exception(f"JobTread {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _days_since(iso: str) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 86400, 1)
    except Exception:
        return None


def _custom_field_ids() -> dict:
    _, org_id = _creds()
    result = _query({
        "organization": {
            "$": {"id": org_id},
            "customFields": {"$": {"size": 100}, "nodes": {"id": True, "name": True}},
        }
    })
    return {f["name"]: f["id"] for f in result["organization"]["customFields"]["nodes"]}


def _fetch_jobs(size: int = PAGE_SIZE) -> tuple[list, int]:
    """Fetch up to `size` jobs with their custom field values. Returns (nodes, total_count)."""
    _, org_id = _creds()
    result = _query({
        "organization": {
            "$": {"id": org_id},
            "jobs": {
                "$": {"size": size, "sortBy": [{"field": "createdAt", "order": "desc"}]},
                "count": True,
                "nodes": {
                    "id": True, "name": True,
                    "customFieldValues": {
                        "nodes": {"value": True, "createdAt": True, "customField": {"id": True, "name": True}}
                    },
                },
            },
        }
    })
    jobs_data = result["organization"]["jobs"]
    return jobs_data.get("nodes", []) or [], jobs_data.get("count", 0)


def _stage_for(job: dict, target_fid: str) -> tuple[str | None, str | None]:
    """Return (value, entered_at_iso) for the custom field matching target_fid on this job."""
    for cfv in (job.get("customFieldValues") or {}).get("nodes", []):
        if (cfv.get("customField") or {}).get("id") == target_fid:
            return cfv.get("value"), cfv.get("createdAt")
    return None, None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def pipeline_counts() -> dict:
    """Counts of RC jobs by Lead Status and Production Status (sample of up to 100 jobs)."""
    fids = _custom_field_ids()
    lead_fid = fids.get("Lead Status", "")
    prod_fid = fids.get("Production Status") or fids.get("Production Pipeline", "")

    jobs, total = _fetch_jobs()
    lead_counts: dict = {}
    prod_counts: dict = {}
    for j in jobs:
        for cfv in (j.get("customFieldValues") or {}).get("nodes", []):
            cid = (cfv.get("customField") or {}).get("id")
            val = cfv.get("value")
            if not val:
                continue
            if cid == lead_fid:
                lead_counts[val] = lead_counts.get(val, 0) + 1
            elif cid == prod_fid:
                prod_counts[val] = prod_counts.get(val, 0) + 1

    return {
        "sampled": len(jobs),
        "total_jobs_in_org": total,
        "note": f"API returns max 100 jobs per call. Org has {total} jobs total; counts below reflect the 100-job sample." if total > len(jobs) else None,
        "lead_status_counts": lead_counts,
        "production_status_counts": prod_counts,
    }


def list_jobs_in_stage(stage: str, field: str = "Lead Status", limit: int = 25) -> dict:
    """List RC jobs currently in a given stage, sorted by longest-in-stage first."""
    fids = _custom_field_ids()
    target_fid = fids.get(field) or fids.get(
        "Production Pipeline" if field.startswith("Production") else field, ""
    )
    if not target_fid:
        return {"error": f"Custom field '{field}' not found."}

    jobs, total = _fetch_jobs()
    matches = []
    for j in jobs:
        val, entered_at = _stage_for(j, target_fid)
        if val != stage:
            continue
        matches.append({
            "id": j.get("id"),
            "name": j.get("name"),
            "entered_stage_at": entered_at,
            "days_in_stage": _days_since(entered_at),
        })
    matches.sort(key=lambda m: m.get("days_in_stage") or 0, reverse=True)
    limit = max(1, min(int(limit or 25), 100))
    return {
        "stage": stage,
        "field": field,
        "sampled": len(jobs),
        "total_jobs_in_org": total,
        "returned": min(len(matches), limit),
        "jobs": matches[:limit],
    }


def stage_durations(field: str = "Lead Status") -> dict:
    """Per-stage aggregate: count, avg/median/max days in stage, and top-3 oldest jobs."""
    fids = _custom_field_ids()
    target_fid = fids.get(field) or fids.get(
        "Production Pipeline" if field.startswith("Production") else field, ""
    )
    if not target_fid:
        return {"error": f"Custom field '{field}' not found."}

    jobs, total = _fetch_jobs()
    by_stage: dict = {}
    for j in jobs:
        val, entered_at = _stage_for(j, target_fid)
        if not val:
            continue
        days = _days_since(entered_at)
        if days is None:
            continue
        by_stage.setdefault(val, []).append({
            "id": j.get("id"),
            "name": j.get("name"),
            "days_in_stage": days,
        })

    summary = []
    for stage, items in by_stage.items():
        durations = sorted(i["days_in_stage"] for i in items)
        n = len(durations)
        avg = round(sum(durations) / n, 1)
        median = durations[n // 2] if n % 2 else round((durations[n // 2 - 1] + durations[n // 2]) / 2, 1)
        summary.append({
            "stage": stage,
            "count": n,
            "avg_days_in_stage": avg,
            "median_days_in_stage": median,
            "max_days": durations[-1],
            "oldest_jobs": sorted(items, key=lambda x: x["days_in_stage"], reverse=True)[:3],
        })
    summary.sort(key=lambda s: s["avg_days_in_stage"], reverse=True)
    return {
        "field": field,
        "sampled": len(jobs),
        "total_jobs_in_org": total,
        "note": f"API returns max 100 jobs per call. Analyzed {len(jobs)} of {total} total RC jobs." if total > len(jobs) else None,
        "stages": summary,
    }


def search_jobs(query: str, limit: int = 25) -> dict:
    """Find RC jobs by name substring (case-insensitive). Scans ALL 771 jobs via Pave
    like-filter — not limited to recent sample."""
    _, org_id = _creds()
    q = (query or "").strip()
    if not q:
        return {"error": "query required"}
    limit = max(1, min(int(limit or 25), 50))
    result = _query({
        "organization": {
            "$": {"id": org_id},
            "jobs": {
                "$": {
                    "size": limit,
                    "where": {"like": [{"field": ["name"]}, {"value": f"%{q}%"}]},
                },
                "count": True,
                "nodes": {"id": True, "name": True},
            },
        }
    })
    data = result["organization"]["jobs"]
    nodes = data.get("nodes", []) or []
    return {
        "query": query,
        "total_matches": data.get("count", 0),
        "returned": len(nodes),
        "jobs": [{"id": n.get("id"), "name": n.get("name")} for n in nodes],
    }


def search_contacts(name: str, limit: int = 25) -> dict:
    """Search ALL RC contacts by name (case-insensitive substring). Uses Pave's like-filter,
    so it scans the full 2400+ contacts, not a 50-item sample."""
    _, org_id = _creds()
    q = (name or "").strip()
    if not q:
        return {"error": "name required"}
    limit = max(1, min(int(limit or 25), 50))
    result = _query({
        "organization": {
            "$": {"id": org_id},
            "contacts": {
                "$": {
                    "size": limit,
                    "where": {"like": [{"field": ["name"]}, {"value": f"%{q}%"}]},
                },
                "count": True,
                "nodes": {
                    "id": True, "name": True, "firstName": True, "lastName": True,
                    "account": {"id": True, "name": True},
                },
            },
        }
    })
    data = result["organization"]["contacts"]
    nodes = data.get("nodes", []) or []
    matches = [{
        "id": c.get("id"),
        "name": c.get("name") or f"{c.get('firstName','')} {c.get('lastName','')}".strip(),
        "account_id": (c.get("account") or {}).get("id"),
        "account_name": (c.get("account") or {}).get("name"),
    } for c in nodes]
    return {
        "query": name,
        "total_matches": data.get("count", 0),
        "returned": len(matches),
        "contacts": matches,
    }


def get_account_jobs(account_id: str) -> dict:
    """Get all jobs tied to an account (customer). Use after search_contacts to find
    someone's projects and their current pipeline stages + appointment custom fields."""
    if not account_id:
        return {"error": "account_id required"}
    result = _query({
        "account": {
            "$": {"id": account_id},
            "id": True, "name": True,
            "jobs": {
                "$": {"size": 50},
                "count": True,
                "nodes": {
                    "id": True, "name": True,
                    "customFieldValues": {
                        "nodes": {"value": True, "createdAt": True, "customField": {"id": True, "name": True}}
                    },
                },
            },
        }
    })
    acc = result.get("account") or {}
    jobs_out = []
    for j in (acc.get("jobs") or {}).get("nodes", []):
        fields = {}
        timestamps = {}
        for cfv in (j.get("customFieldValues") or {}).get("nodes", []):
            n = (cfv.get("customField") or {}).get("name")
            if not n:
                continue
            fields[n] = cfv.get("value")
            if cfv.get("createdAt"):
                timestamps[n] = cfv["createdAt"]
        jobs_out.append({
            "id": j.get("id"),
            "name": j.get("name"),
            "custom_fields": fields,
            "field_timestamps": timestamps,
        })
    return {
        "account_id": acc.get("id"),
        "account_name": acc.get("name"),
        "job_count": (acc.get("jobs") or {}).get("count", 0),
        "jobs": jobs_out,
    }


def list_tasks(start_date: str = "", end_date: str = "", query: str = "", limit: int = 50) -> dict:
    """List RC scheduled tasks (work items, appointments, follow-ups). Filter by startDate range
    and/or name substring. Use to answer 'what's on the schedule this week', 'upcoming appointments', etc."""
    _, org_id = _creds()
    limit = max(1, min(int(limit or 50), 50))
    conditions = []
    if start_date:
        conditions.append({">=": [{"field": ["startDate"]}, {"value": start_date}]})
    if end_date:
        conditions.append({"<=": [{"field": ["startDate"]}, {"value": end_date}]})
    if query:
        conditions.append({"like": [{"field": ["name"]}, {"value": f"%{query.strip()}%"}]})

    args: dict = {"size": limit, "sortBy": [{"field": "startDate", "order": "asc"}]}
    if len(conditions) == 1:
        args["where"] = conditions[0]
    elif len(conditions) > 1:
        args["where"] = {"and": conditions}

    result = _query({
        "organization": {
            "$": {"id": org_id},
            "tasks": {
                "$": args,
                "count": True,
                "nodes": {
                    "id": True, "name": True, "description": True,
                    "startDate": True, "endDate": True,
                    "startTime": True, "endTime": True,
                    "job": {"id": True, "name": True},
                },
            },
        }
    })
    data = result["organization"]["tasks"]
    return {
        "filters": {"start_date": start_date, "end_date": end_date, "query": query},
        "total_matches": data.get("count", 0),
        "returned": len(data.get("nodes", []) or []),
        "tasks": data.get("nodes", []),
    }


def list_daily_logs(job_id: str = "", query: str = "", start_date: str = "", end_date: str = "", limit: int = 25) -> dict:
    """Fetch RC daily-log entries (field/project activity notes). Filter by job, date range, or
    substring. Use to answer 'what happened on Terry McKeever's project this week'."""
    _, org_id = _creds()
    limit = max(1, min(int(limit or 25), 50))
    conditions = []
    if job_id:
        conditions.append({"=": [{"field": ["job", "id"]}, {"value": job_id}]})
    if start_date:
        conditions.append({">=": [{"field": ["date"]}, {"value": start_date}]})
    if end_date:
        conditions.append({"<=": [{"field": ["date"]}, {"value": end_date}]})
    if query:
        conditions.append({"like": [{"field": ["notes"]}, {"value": f"%{query.strip()}%"}]})

    args: dict = {"size": limit, "sortBy": [{"field": "date", "order": "desc"}]}
    if len(conditions) == 1:
        args["where"] = conditions[0]
    elif len(conditions) > 1:
        args["where"] = {"and": conditions}

    result = _query({
        "organization": {
            "$": {"id": org_id},
            "dailyLogs": {
                "$": args,
                "count": True,
                "nodes": {
                    "id": True, "date": True, "notes": True,
                    "job": {"id": True, "name": True},
                    "user": {"id": True, "name": True},
                },
            },
        }
    })
    data = result["organization"]["dailyLogs"]
    return {
        "filters": {"job_id": job_id, "start_date": start_date, "end_date": end_date, "query": query},
        "total_matches": data.get("count", 0),
        "returned": len(data.get("nodes", []) or []),
        "logs": data.get("nodes", []),
    }


def ar_aging(limit: int = 50) -> dict:
    """Accounts receivable aging: list outstanding (unpaid or partially-paid) customer invoices
    across ALL RC jobs, sorted by largest balance first. Each entry includes invoice, job, and
    days-since-issue. Use to answer 'who owes us money' or 'which invoices are oldest'."""
    _, org_id = _creds()
    limit = max(1, min(int(limit or 50), 50))
    result = _query({
        "organization": {
            "$": {"id": org_id},
            "documents": {
                "$": {
                    "size": limit,
                    "where": {"and": [
                        {"=": [{"field": ["type"]}, {"value": "customerInvoice"}]},
                        {"<": [{"field": ["amountPaid"]}, {"field": ["price"]}]},
                    ]},
                    "sortBy": [{"field": "issueDate", "order": "asc"}],
                },
                "count": True,
                "nodes": {
                    "id": True, "name": True, "price": True, "amountPaid": True,
                    "issueDate": True, "status": True,
                    "job": {"id": True, "name": True},
                },
            },
        }
    })
    data = result["organization"]["documents"]
    rows = []
    total_outstanding = 0.0
    for d in data.get("nodes", []) or []:
        price = d.get("price") or 0
        paid = d.get("amountPaid") or 0
        outstanding = price - paid
        total_outstanding += outstanding
        rows.append({
            "invoice_id": d.get("id"),
            "invoice_name": d.get("name"),
            "job_id": (d.get("job") or {}).get("id"),
            "job_name": (d.get("job") or {}).get("name"),
            "issue_date": d.get("issueDate"),
            "status": d.get("status"),
            "price": price,
            "amount_paid": paid,
            "outstanding": round(outstanding, 2),
            "days_outstanding": _days_since(f"{d['issueDate']}T00:00:00Z") if d.get("issueDate") else None,
        })
    rows.sort(key=lambda r: r["outstanding"], reverse=True)
    return {
        "total_unpaid_invoices": data.get("count", 0),
        "returned": len(rows),
        "total_outstanding_in_sample": round(total_outstanding, 2),
        "invoices": rows,
    }


def jobs_by_sales_person(sales_person: str, limit: int = 50) -> dict:
    """Find RC jobs where the 'Sales Person' custom field matches (case-insensitive).
    Returns each job with its current Lead Status. Use to answer 'how is Mark's pipeline'."""
    fids = _custom_field_ids()
    sp_fid = fids.get("Sales Person")
    lead_fid = fids.get("Lead Status")
    if not sp_fid:
        return {"error": "Custom field 'Sales Person' not found."}
    name_q = (sales_person or "").strip()
    if not name_q:
        return {"error": "sales_person required"}

    jobs, total = _fetch_jobs()  # 50-job sample
    matches = []
    for j in jobs:
        sp_val = None
        lead_val = None
        lead_at = None
        for cfv in (j.get("customFieldValues") or {}).get("nodes", []):
            cid = (cfv.get("customField") or {}).get("id")
            if cid == sp_fid:
                sp_val = cfv.get("value")
            elif cid == lead_fid:
                lead_val = cfv.get("value")
                lead_at = cfv.get("createdAt")
        if sp_val and name_q.lower() in str(sp_val).lower():
            matches.append({
                "id": j.get("id"),
                "name": j.get("name"),
                "sales_person": sp_val,
                "lead_status": lead_val,
                "days_in_current_stage": _days_since(lead_at),
            })

    by_stage: dict = {}
    for m in matches:
        by_stage[m["lead_status"] or "Unknown"] = by_stage.get(m["lead_status"] or "Unknown", 0) + 1

    limit = max(1, min(int(limit or 50), 50))
    return {
        "sales_person_query": sales_person,
        "sampled": len(jobs),
        "total_jobs_in_org": total,
        "note": f"Matched from a 50-job sample of {total} total. Re-run if a known job isn't included.",
        "matched_count": len(matches),
        "stage_breakdown": by_stage,
        "jobs": matches[:limit],
    }


def lead_sources(start_date: str = "", end_date: str = "") -> dict:
    """Breakdown of RC jobs by 'Lead Source' custom field. Optional date range on 'Lead Created'.
    Use to answer 'which lead source drives the most leads' or 'where did this month's leads come from'."""
    fids = _custom_field_ids()
    src_fid = fids.get("Lead Source")
    created_fid = fids.get("Lead Created")
    if not src_fid:
        return {"error": "'Lead Source' custom field not found."}

    jobs, total = _fetch_jobs()
    counts: dict = {}
    examples: dict = {}
    filtered_in = 0
    for j in jobs:
        src = None
        lead_created = None
        for cfv in (j.get("customFieldValues") or {}).get("nodes", []):
            cid = (cfv.get("customField") or {}).get("id")
            if cid == src_fid:
                src = cfv.get("value")
            elif cid == created_fid:
                lead_created = cfv.get("value")
        if start_date and (not lead_created or lead_created < start_date):
            continue
        if end_date and (not lead_created or lead_created > end_date):
            continue
        if not src:
            src = "(none set)"
        filtered_in += 1
        counts[src] = counts.get(src, 0) + 1
        examples.setdefault(src, []).append({"id": j.get("id"), "name": j.get("name"), "lead_created": lead_created})

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "filters": {"start_date": start_date, "end_date": end_date},
        "sampled": len(jobs),
        "total_jobs_in_org": total,
        "jobs_in_date_window": filtered_in,
        "note": f"Based on the {len(jobs)} most-recent jobs of {total} total. For older lead-source data, widen date range or request a specific job by name.",
        "lead_sources_ranked": [{"source": s, "count": c} for s, c in ranked],
        "examples_per_source": {s: examples[s][:3] for s in examples},
    }


def get_job(job_id: str) -> dict:
    """Full detail for one RC job with all custom field values and their set-timestamps."""
    if not job_id:
        return {"error": "job_id required"}
    result = _query({
        "job": {
            "$": {"id": job_id},
            "id": True, "name": True,
            "costItems": {
                "sum": {"$": {"field": "cost"}},
                "count": True,
            },
            "documents": {
                "$": {"size": 50},
                "count": True,
                "nodes": {
                    "id": True, "name": True, "type": True, "status": True,
                    "price": True, "amountPaid": True, "issueDate": True,
                },
            },
            "customFieldValues": {
                "nodes": {"value": True, "createdAt": True, "customField": {"id": True, "name": True}}
            },
        }
    })
    j = result.get("job") or {}
    fields: dict = {}
    timestamps: dict = {}
    for cfv in (j.get("customFieldValues") or {}).get("nodes", []):
        name = (cfv.get("customField") or {}).get("name")
        if not name:
            continue
        fields[name] = cfv.get("value")
        if cfv.get("createdAt"):
            timestamps[name] = {
                "value_set_at": cfv["createdAt"],
                "days_since": _days_since(cfv["createdAt"]),
            }

    docs = (j.get("documents") or {}).get("nodes", []) or []
    # Summarize by (type, status)
    approved_orders = sum(d.get("price", 0) or 0 for d in docs
                          if d.get("type") == "customerOrder" and d.get("status") == "approved")
    invoiced = sum(d.get("price", 0) or 0 for d in docs if d.get("type") == "customerInvoice")
    collected = sum(d.get("amountPaid", 0) or 0 for d in docs if d.get("type") == "customerInvoice")
    total_cost = (j.get("costItems") or {}).get("sum")

    financials = {
        "approved_orders_total": approved_orders,
        "invoiced_total": invoiced,
        "collected_total": collected,
        "outstanding_balance": invoiced - collected,
        "cost_items_sum_raw": total_cost,
        "_note": (
            "approved_orders_total = sum of approved customerOrder prices (what the job was sold for). "
            "invoiced_total = sum of customerInvoice prices. collected_total = sum of amountPaid on invoices. "
            "cost_items_sum_raw is the sum of ALL costItems.cost — may include hierarchical/duplicate rollups "
            "and is NOT a reliable 'actual cost' figure; don't compute profit from it."
        ),
    }

    return {
        "id": j.get("id"),
        "name": j.get("name"),
        "financials": financials,
        "documents": docs,
        "custom_fields": fields,
        "field_timestamps": timestamps,
    }


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS_SCHEMA = [
    {
        "name": "jobtread_pipeline_counts",
        "description": "Counts of RC jobs by Lead Status and Production Status stage. Note: samples up to 100 jobs of the organization's total (API limit).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "jobtread_stage_durations",
        "description": "For each stage in the given pipeline, returns count, avg/median/max days-in-stage, and the 3 oldest (stalest) jobs. Use this to answer 'how long are leads sitting in each stage' questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "'Lead Status' (sales pipeline) or 'Production Status' (production pipeline). Default Lead Status."}
            },
        },
    },
    {
        "name": "jobtread_list_jobs_in_stage",
        "description": "List RC jobs currently in a specific stage, sorted by longest-in-stage first, with days-in-stage per job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "description": "Exact stage name, e.g. 'Retail: Appointment Set'."},
                "field": {"type": "string", "description": "'Lead Status' or 'Production Status'. Default Lead Status."},
                "limit": {"type": "integer"},
            },
            "required": ["stage"],
        },
    },
    {
        "name": "jobtread_search_jobs",
        "description": "Find ALL RC jobs by name/address substring, case-insensitive. Scans the full 771-job history — useful for older projects not in recent samples.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
    },
    {
        "name": "jobtread_search_contacts",
        "description": "Search ALL RC contacts (2400+) by name substring, case-insensitive. Returns matches with account_id. Use first when asked about a specific person, then call jobtread_get_account_jobs with the account_id to see their jobs and appointment fields.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["name"],
        },
    },
    {
        "name": "jobtread_get_account_jobs",
        "description": "Fetch all jobs linked to a specific account (customer) with full custom fields including appointment dates, lead status, production status, etc. Use after jobtread_search_contacts.",
        "input_schema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "required": ["account_id"],
        },
    },
    {
        "name": "jobtread_list_tasks",
        "description": "List RC scheduled tasks (work items, appointments, follow-ups) filtered by date range and/or name substring. Use for 'what's on the schedule this week', 'any appointments tomorrow', 'upcoming windows installs'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Earliest startDate YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "Latest startDate YYYY-MM-DD."},
                "query": {"type": "string", "description": "Name substring filter."},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "jobtread_list_daily_logs",
        "description": "RC daily-log field notes. Filter by job_id, date range, or note text. Use for 'what happened on X job this week' or activity-feed questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "query": {"type": "string", "description": "Substring search within note text."},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "jobtread_ar_aging",
        "description": "Accounts receivable aging — lists all unpaid/partially-paid customer invoices across RC jobs, sorted by biggest balance. Includes days-outstanding and job name. Use for 'who owes us money', 'what's our AR', 'oldest unpaid invoices'.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "jobtread_jobs_by_sales_person",
        "description": "Find jobs assigned to a sales person (matches the 'Sales Person' custom field). Returns stage breakdown + per-job lead status and time-in-current-stage. Use for 'how is Mark's pipeline'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sales_person": {"type": "string", "description": "Name substring (e.g. 'Mark' or 'Lopergolo')."},
                "limit": {"type": "integer"},
            },
            "required": ["sales_person"],
        },
    },
    {
        "name": "jobtread_lead_sources",
        "description": "Breakdown of RC jobs by 'Lead Source' custom field (Website, Google, Referral, Builder Services, Previous Customer, etc.). Optional date filter on the 'Lead Created' field to scope to e.g. this month. Use for 'where are leads coming from', 'which source drives the most leads'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Earliest Lead Created date YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "Latest Lead Created date YYYY-MM-DD."},
            },
        },
    },
    {
        "name": "jobtread_get_job",
        "description": "Fetch one RC job by ID with full financials (approved orders total, invoiced, collected, outstanding balance, total costs incurred, profit vs approved price), every document (proposals, invoices, deposits), and all custom field values with per-field set timestamps. Use to answer 'did the job come in over/under budget' or 'how much was collected'.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
]


TOOL_IMPLS = {
    "jobtread_pipeline_counts": pipeline_counts,
    "jobtread_stage_durations": stage_durations,
    "jobtread_list_jobs_in_stage": list_jobs_in_stage,
    "jobtread_search_jobs": search_jobs,
    "jobtread_search_contacts": search_contacts,
    "jobtread_get_account_jobs": get_account_jobs,
    "jobtread_list_tasks": list_tasks,
    "jobtread_list_daily_logs": list_daily_logs,
    "jobtread_ar_aging": ar_aging,
    "jobtread_jobs_by_sales_person": jobs_by_sales_person,
    "jobtread_lead_sources": lead_sources,
    "jobtread_get_job": get_job,
}


def run_tool(name: str, args: dict) -> dict:
    fn = TOOL_IMPLS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} failed: {e}"}
