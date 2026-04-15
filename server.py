"""
Local dashboard server.

Usage:
    python server.py              # Starts on http://localhost:5050
    python server.py --port 8080  # Custom port

Serves pre-cached data files for each date range (7d, 30d, 90d).
Date switching is instant — no re-pulling from APIs.
Background refresh runs every hour.
"""

import os
import sys
import json
import argparse
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, send_file, jsonify, request, make_response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(PROJECT_DIR, ".tmp")
DASHBOARD_FILE = os.path.join(PROJECT_DIR, "marketing-dashboard.html")

REFRESH_INTERVAL = 3600  # 1 hour

# Brands to pull — override via ACTIVE_BRANDS env var (comma-separated)
# e.g. ACTIVE_BRANDS=rc          → RC only (online)
# e.g. ACTIVE_BRANDS=rnr         → RNR only (local)
# e.g. ACTIVE_BRANDS=rc,rnr      → both
from dotenv import load_dotenv
load_dotenv()
ACTIVE_BRANDS = [b.strip() for b in os.getenv("ACTIVE_BRANDS", "rc,rnr,wl").split(",") if b.strip()]


def data_file(range_key="30d"):
    return os.path.join(TMP_DIR, f"data_{range_key}.json")


def pull_range(days, range_key):
    """Pull data for a specific date range and save to file."""
    from tools.pull_all import pull_brand, transform_for_dashboard
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    output = {
        "generated_at": datetime.now().isoformat(),
        "date_range": {"start": start_date, "end": end_date},
        "range_key": range_key,
    }

    for b in ACTIVE_BRANDS:
        raw = pull_brand(b, start_date, end_date)
        output[b] = transform_for_dashboard(raw)

    os.makedirs(TMP_DIR, exist_ok=True)
    with open(data_file(range_key), "w") as f:
        json.dump(output, f, indent=2)

    # Also save as default
    with open(data_file("default"), "w") as f:
        json.dump(output, f, indent=2)

    return output


def pull_all_ranges():
    """Pull data for all standard date ranges."""
    for days, key in [(7, "7d"), (30, "30d"), (90, "90d")]:
        try:
            pull_range(days, key)
            print(f"  Pulled {key}")
        except Exception as e:
            print(f"  Failed {key}: {e}")


def background_refresh():
    """Background thread that refreshes data every hour."""
    while True:
        time.sleep(REFRESH_INTERVAL)
        print(f"\n[{datetime.now().strftime('%H:%M')}] Background refresh starting...")
        try:
            pull_all_ranges()
            print(f"[{datetime.now().strftime('%H:%M')}] Background refresh complete")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M')}] Background refresh failed: {e}")


@app.route("/")
def dashboard():
    resp = make_response(send_file(DASHBOARD_FILE))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route("/api/data")
def get_data():
    """Return cached data for a date range. Switching is instant."""
    range_key = request.args.get("range", "30d")

    # Try the requested range, fall back to default
    path = data_file(range_key)
    if not os.path.exists(path):
        path = data_file("default")
    if not os.path.exists(path):
        # Try any existing file
        for fallback in ["30d", "7d", "90d"]:
            p = data_file(fallback)
            if os.path.exists(p):
                path = p
                break

    if not os.path.exists(path):
        return jsonify({"error": "No data yet. Run: python tools/pull_all.py"}), 404

    with open(path, "r") as f:
        data = json.load(f)

    return jsonify(data)


@app.route("/api/refresh", methods=["POST"])
def refresh_data():
    """Force a refresh for a specific range or all ranges."""
    range_key = request.args.get("range", "")
    days = request.args.get("days", 30, type=int)
    start_param = request.args.get("start")
    end_param = request.args.get("end")

    try:
        if start_param and end_param:
            # Custom date range
            from tools.pull_all import pull_brand, transform_for_dashboard
            output = {
                "generated_at": datetime.now().isoformat(),
                "date_range": {"start": start_param, "end": end_param},
                "range_key": "custom",
            }
            for b in ACTIVE_BRANDS:
                raw = pull_brand(b, start_param, end_param)
                output[b] = transform_for_dashboard(raw)

            os.makedirs(TMP_DIR, exist_ok=True)
            with open(data_file("custom"), "w") as f:
                json.dump(output, f, indent=2)

            return jsonify({"status": "ok", "generated_at": output["generated_at"]})

        elif range_key:
            # Refresh specific range
            days_map = {"7d": 7, "30d": 30, "90d": 90}
            d = days_map.get(range_key, days)
            pull_range(d, range_key)
            return jsonify({"status": "ok"})

        else:
            # Refresh all ranges
            pull_all_ranges()
            return jsonify({"status": "ok"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    """Ask Claude questions about the last pulled snapshot."""
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set in .env"}), 500

    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    range_key = body.get("range", "30d")
    brand = (body.get("brand") or "rc").lower()
    BRAND_NAMES = {"rc": "Remodeling Concepts", "rnr": "Ryann Reed", "wl": "Wood Lane Golf", "both": "All Brands"}
    brand_label = BRAND_NAMES.get(brand, "Remodeling Concepts")

    if not messages:
        return jsonify({"error": "messages required"}), 400

    # Load the snapshot for the requested range, falling back to default
    path = data_file(range_key)
    if not os.path.exists(path):
        path = data_file("default")
    if not os.path.exists(path):
        return jsonify({"error": "No data snapshot yet. Run: python tools/pull_all.py"}), 404

    with open(path) as f:
        snapshot = f.read()

    from tools import jobtread_live, google_ads_live

    today = datetime.now().strftime("%Y-%m-%d")

    system = [
        {
            "type": "text",
            "text": (
                "You are an analytics assistant embedded in the PulseDash marketing dashboard "
                "for three brands: Remodeling Concepts (rc), Ryann Reed (rnr), and Wood Lane Golf (wl).\n"
                f"CURRENT BRAND SCOPE: {brand_label} ({brand}). "
                "Unless the user explicitly asks about another brand, keep all answers, examples, and tool calls scoped to "
                f"{brand_label}. The JSON snapshot contains all brands, so filter to the {brand} key. "
                "When the user asks cross-brand comparison questions, use the full snapshot.\n"
                f"Today's date is {today}. "
                "Primary source: the JSON snapshot below. "
                "For deeper Remodeling Concepts questions use the jobtread_* tools — JobTread is the system of record "
                "for RC's sales pipeline, production pipeline, projects, customers, appointments, and lead sources. "
                "For Google Ads performance (any brand where configured), use the google_ads_* tools.\n"
                "Tool playbook:\n"
                "- Stage/duration questions ('how long are leads sitting') → jobtread_stage_durations.\n"
                "- 'Which jobs are in X stage' → jobtread_list_jobs_in_stage.\n"
                "- Questions about a specific person → jobtread_search_contacts first, then jobtread_get_account_jobs "
                "with the returned account_id. Appointment dates live in job custom fields "
                "(e.g. 'Initial Appointment Start', 'Initial Appointment End') — surface those directly.\n"
                "- Questions about a known project/address → jobtread_search_jobs, then jobtread_get_job.\n"
                "- Lead source / 'where are leads coming from' questions → jobtread_lead_sources. "
                "Lead source lives in JobTread as a job custom field, NOT in the snapshot — always use the tool.\n"
                "- Only RC has live tool access. For rnr and wl, answer from the snapshot only.\n"
                "Notes on data limits:\n"
                "- Contact/job search tools scan the FULL history (all 2400+ contacts, all 771 jobs) by substring.\n"
                "- jobtread_stage_durations and jobtread_pipeline_counts sample up to 100 jobs — counts may be partial "
                "for high-volume stages. Caveat if relevant.\n"
                "Be concise, cite specific numbers, and say plainly when data is missing. Format money with $ and percentages with %."
            ),
        },
        {
            "type": "text",
            "text": f"DASHBOARD SNAPSHOT (JSON):\n{snapshot}",
            "cache_control": {"type": "ephemeral"},
        },
    ]

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        convo = list(messages)
        max_tool_turns = 8
        all_tools = jobtread_live.TOOLS_SCHEMA + google_ads_live.TOOLS_SCHEMA

        def dispatch(name: str, args: dict) -> dict:
            if name.startswith("jobtread_"):
                return jobtread_live.run_tool(name, args)
            if name.startswith("google_ads_"):
                return google_ads_live.run_tool(name, args, default_brand=brand if brand in ("rc", "rnr", "wl") else "rc")
            return {"error": f"Unknown tool namespace: {name}"}

        for _ in range(max_tool_turns):
            resp = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1024,
                system=system,
                tools=all_tools,
                messages=convo,
            )

            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
                return jsonify({"reply": text})

            convo.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
            tool_results = []
            for block in resp.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                result = dispatch(block.name, block.input or {})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result)[:12000],
                })
            convo.append({"role": "user", "content": tool_results})

        return jsonify({"reply": "(Stopped: hit max tool-call limit before finishing.)"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/status")
def status():
    from dotenv import load_dotenv
    load_dotenv()

    # Check which ranges are cached
    cached = {}
    for rng in ["7d", "30d", "90d", "custom"]:
        p = data_file(rng)
        if os.path.exists(p):
            with open(p) as f:
                d = json.load(f)
            cached[rng] = d.get("generated_at", "unknown")
        else:
            cached[rng] = None

    return jsonify({"cached_ranges": cached})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Marketing Dashboard Server")
    parser.add_argument("--port", type=int, default=5050, help="Port (default: 5050)")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-refresh", action="store_true", help="Skip background refresh")
    args = parser.parse_args()

    # Start background refresh thread
    if not args.no_refresh:
        t = threading.Thread(target=background_refresh, daemon=True)
        t.start()
        print(f"  Background refresh: every {REFRESH_INTERVAL // 60} minutes")

    print(f"\n  PulseDash Marketing Dashboard")
    print(f"  http://localhost:{args.port}")
    print(f"  Cached ranges: 7d, 30d, 90d")
    print(f"\n  To force refresh: POST /api/refresh\n")

    app.run(host="0.0.0.0", port=args.port, debug=args.debug)
