"""
Live GHL query tools for the Ask AI agent.

Thin, Claude-friendly wrappers around the GHL API v2. Each function returns
JSON-serializable dicts and is scoped to Remodeling Concepts only.

All functions are READ-ONLY. No create/update/delete.
"""

import os
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://services.leadconnectorhq.com/"
API_VERSION = "2021-07-28"

MAX_LIMIT = 100


def _creds():
    api_key = os.environ.get("GHL_API_KEY_RC")
    location_id = os.environ.get("GHL_LOCATION_ID_RC")
    if not api_key or not location_id:
        raise RuntimeError("GHL_API_KEY_RC and GHL_LOCATION_ID_RC must be set in .env")
    return api_key, location_id


def _headers():
    api_key, _ = _creds()
    return {
        "Authorization": f"Bearer {api_key}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _to_epoch_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)


def _clip(items, limit):
    limit = min(max(1, int(limit or 25)), MAX_LIMIT)
    return items[:limit]


def _compact_contact(c: dict) -> dict:
    return {
        "id": c.get("id"),
        "name": c.get("contactName") or f"{c.get('firstName','')} {c.get('lastName','')}".strip(),
        "email": c.get("email"),
        "phone": c.get("phone"),
        "source": c.get("source"),
        "tags": c.get("tags") or [],
        "date_added": c.get("dateAdded"),
        "last_activity": c.get("lastActivity"),
    }


def _compact_opportunity(o: dict) -> dict:
    return {
        "id": o.get("id"),
        "name": o.get("name"),
        "status": o.get("status"),
        "stage": o.get("pipelineStageName") or o.get("stageId"),
        "pipeline": o.get("pipelineName") or o.get("pipelineId"),
        "monetary_value": o.get("monetaryValue"),
        "contact_id": (o.get("contact") or {}).get("id") or o.get("contactId"),
        "contact_name": (o.get("contact") or {}).get("name"),
        "source": o.get("source"),
        "created_at": o.get("createdAt"),
        "updated_at": o.get("updatedAt"),
    }


def _compact_appointment(a: dict) -> dict:
    return {
        "id": a.get("id"),
        "title": a.get("title"),
        "status": a.get("appointmentStatus") or a.get("status"),
        "start_time": a.get("startTime"),
        "end_time": a.get("endTime"),
        "contact_id": a.get("contactId"),
        "assigned_user_id": a.get("assignedUserId"),
    }


# ---------------------------------------------------------------------------
# Tools exposed to Claude
# ---------------------------------------------------------------------------

def search_contacts(query: str = "", start_date: str = "", end_date: str = "", limit: int = 25) -> dict:
    """Search RC contacts by name/email/phone and/or date range."""
    _, location_id = _creds()
    params = {"locationId": location_id, "limit": min(int(limit or 25), MAX_LIMIT)}
    if query:
        params["query"] = query
    if start_date:
        params["startAfter"] = _to_epoch_ms(start_date)
    if end_date:
        params["startBefore"] = _to_epoch_ms(end_date)

    resp = requests.get(f"{API_BASE}contacts/", headers=_headers(), params=params, timeout=30)
    if resp.status_code != 200:
        return {"error": f"GHL {resp.status_code}: {resp.text[:200]}"}

    data = resp.json()
    contacts = [_compact_contact(c) for c in data.get("contacts", [])]
    return {
        "total_returned": len(contacts),
        "total_available": data.get("meta", {}).get("total"),
        "contacts": _clip(contacts, limit),
    }


def get_contact(contact_id: str) -> dict:
    """Fetch a single RC contact by ID, including custom fields."""
    if not contact_id:
        return {"error": "contact_id required"}
    resp = requests.get(f"{API_BASE}contacts/{contact_id}", headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return {"error": f"GHL {resp.status_code}: {resp.text[:200]}"}
    c = resp.json().get("contact", {})
    out = _compact_contact(c)
    out["custom_fields"] = c.get("customFields") or []
    out["notes"] = c.get("notes") or []
    return out


def list_opportunities(start_date: str = "", end_date: str = "", status: str = "", limit: int = 50) -> dict:
    """List RC opportunities (pipeline items). Optional date range and status filter (open/won/lost/abandoned)."""
    _, location_id = _creds()
    filters = []
    if start_date:
        filters.append({"field": "createdAt", "operator": "gte", "value": f"{start_date}T00:00:00Z"})
    if end_date:
        filters.append({"field": "createdAt", "operator": "lte", "value": f"{end_date}T23:59:59Z"})
    if status:
        filters.append({"field": "status", "operator": "eq", "value": status})

    payload = {
        "locationId": location_id,
        "filters": filters,
        "limit": min(int(limit or 50), MAX_LIMIT),
    }
    resp = requests.post(f"{API_BASE}opportunities/search", headers=_headers(), json=payload, timeout=30)
    if resp.status_code != 200:
        return {"error": f"GHL {resp.status_code}: {resp.text[:200]}"}

    data = resp.json()
    opps = [_compact_opportunity(o) for o in data.get("opportunities", [])]
    total_value = sum((o.get("monetary_value") or 0) for o in opps)
    by_status = {}
    for o in opps:
        by_status[o.get("status") or "unknown"] = by_status.get(o.get("status") or "unknown", 0) + 1
    return {
        "total_returned": len(opps),
        "total_value": total_value,
        "by_status": by_status,
        "opportunities": _clip(opps, limit),
    }


def list_appointments(start_date: str, end_date: str, limit: int = 50) -> dict:
    """List RC calendar appointments in a date range. Dates required (YYYY-MM-DD)."""
    _, location_id = _creds()
    if not start_date or not end_date:
        return {"error": "start_date and end_date required (YYYY-MM-DD)"}
    params = {
        "locationId": location_id,
        "startDate": f"{start_date}T00:00:00Z",
        "endDate": f"{end_date}T23:59:59Z",
        "limit": min(int(limit or 50), MAX_LIMIT),
    }
    resp = requests.get(f"{API_BASE}calendars/events", headers=_headers(), params=params, timeout=30)
    if resp.status_code != 200:
        return {"error": f"GHL {resp.status_code}: {resp.text[:200]}"}

    data = resp.json()
    appts = [_compact_appointment(a) for a in data.get("events", [])]
    by_status = {}
    for a in appts:
        by_status[a.get("status") or "unknown"] = by_status.get(a.get("status") or "unknown", 0) + 1
    return {
        "total_returned": len(appts),
        "by_status": by_status,
        "appointments": _clip(appts, limit),
    }


def list_pipelines() -> dict:
    """List the pipelines and stages configured in RC's GHL account."""
    _, location_id = _creds()
    resp = requests.get(
        f"{API_BASE}opportunities/pipelines",
        headers=_headers(),
        params={"locationId": location_id},
        timeout=30,
    )
    if resp.status_code != 200:
        return {"error": f"GHL {resp.status_code}: {resp.text[:200]}"}
    data = resp.json()
    pipelines = []
    for p in data.get("pipelines", []):
        pipelines.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "stages": [{"id": s.get("id"), "name": s.get("name")} for s in p.get("stages", [])],
        })
    return {"pipelines": pipelines}


# ---------------------------------------------------------------------------
# Tool registry (for the /api/chat agent loop)
# ---------------------------------------------------------------------------

TOOLS_SCHEMA = [
    {
        "name": "ghl_search_contacts",
        "description": "Search Remodeling Concepts contacts in GoHighLevel. Optional free-text query (name/email/phone) and/or date range of when the contact was created.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search over name, email, phone."},
                "start_date": {"type": "string", "description": "Created on/after YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "Created on/before YYYY-MM-DD."},
                "limit": {"type": "integer", "description": "Max contacts to return (1-100, default 25)."},
            },
        },
    },
    {
        "name": "ghl_get_contact",
        "description": "Fetch one Remodeling Concepts contact by ID with full custom fields and notes.",
        "input_schema": {
            "type": "object",
            "properties": {"contact_id": {"type": "string"}},
            "required": ["contact_id"],
        },
    },
    {
        "name": "ghl_list_opportunities",
        "description": "List Remodeling Concepts opportunities (pipeline items). Filter by creation date range and/or status (open, won, lost, abandoned). Returns total value and status breakdown.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Created on/after YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "Created on/before YYYY-MM-DD."},
                "status": {"type": "string", "description": "One of: open, won, lost, abandoned."},
                "limit": {"type": "integer", "description": "Max to return (1-100, default 50)."},
            },
        },
    },
    {
        "name": "ghl_list_appointments",
        "description": "List Remodeling Concepts calendar appointments within a date range, with status breakdown (showed/noshow/cancelled/confirmed).",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "ghl_list_pipelines",
        "description": "List the pipelines and stages configured in Remodeling Concepts' GHL account. Useful before filtering opportunities by stage.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


TOOL_IMPLS = {
    "ghl_search_contacts": search_contacts,
    "ghl_get_contact": get_contact,
    "ghl_list_opportunities": list_opportunities,
    "ghl_list_appointments": list_appointments,
    "ghl_list_pipelines": list_pipelines,
}


def run_tool(name: str, args: dict) -> dict:
    """Dispatch a tool call. Catches exceptions and returns as {'error': ...}."""
    fn = TOOL_IMPLS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} failed: {e}"}
