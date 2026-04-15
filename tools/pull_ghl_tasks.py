"""
Pull GHL task/follow-up data for sales team visibility.

.env keys: GHL_API_KEY_{brand}, GHL_LOCATION_ID_{brand}
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


def pull(api_key: str, location_id: str, start_date: str = None, end_date: str = None) -> dict:
    if not api_key or not location_id:
        return {"error": "Missing GHL_API_KEY or GHL_LOCATION_ID"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        contacts_resp = _ghl_get("contacts/", headers, {
            "locationId": location_id, "limit": 50, "sortBy": "dateAdded", "order": "desc",
        })
        if contacts_resp.status_code != 200:
            return {"error": f"Could not fetch contacts: {contacts_resp.status_code}"}

        contacts = contacts_resp.json().get("contacts", [])
        total_tasks = 0
        completed_tasks = 0
        overdue_tasks = 0
        now = datetime.utcnow()

        for contact in contacts[:30]:
            contact_id = contact.get("id", "")
            if not contact_id:
                continue
            tasks_resp = _ghl_get(f"contacts/{contact_id}/tasks", headers)
            if tasks_resp.status_code != 200:
                continue
            tasks = tasks_resp.json().get("tasks", [])
            for task in tasks:
                total_tasks += 1
                if task.get("completed", False):
                    completed_tasks += 1
                else:
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
