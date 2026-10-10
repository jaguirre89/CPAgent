"""CPAgent — Flask application (formerly 'Tax Dashboard')."""

import os
import re
import sys
import json
import time
import queue
import logging
import argparse
import threading
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory, Response

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Ensure this directory is on sys.path so `import db`, `import security.*`,
# `import learning.*` work whether launched via `python app.py` or launchd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import cache as parsed_cache
from learning import rules_store
from security import audit as sec_audit
from security import classifier as sec_classifier
import retention

app = Flask(__name__, static_folder="static")


@app.before_request
def _enforce_localhost_host_header():
    """CC6.1 — reject any Host header that isn't localhost, even if the process
    accidentally binds wider."""
    from flask import request, abort
    host = (request.host or "").split(":")[0].lower()
    if host not in ("localhost", "127.0.0.1", "::1", ""):
        abort(403, description=f"Host header rejected: {host}")

# Global state
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "app_config.json")
PARSED_DATA = {}  # keyed by year
SSE_CLIENTS = []  # list of queue.Queue objects for SSE


def invalidate_all_caches():
    """Clear both in-memory and DB-persisted parse caches.
    Call this after any mutation that affects parsed output (rules, overrides, etc).
    """
    PARSED_DATA.clear()
    try:
        parsed_cache.invalidate()
    except Exception:
        pass


# ─── Config ───────────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return None


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def load_tax_constants(year):
    constants_path = os.path.join(APP_DIR, "tax_constants", f"{year}.json")
    if os.path.exists(constants_path):
        with open(constants_path, "r") as f:
            return json.load(f)
    # Fallback: find nearest year
    constants_dir = os.path.join(APP_DIR, "tax_constants")
    if os.path.isdir(constants_dir):
        files = sorted([f for f in os.listdir(constants_dir) if f.endswith(".json")], reverse=True)
        if files:
            with open(os.path.join(constants_dir, files[0]), "r") as f:
                return json.load(f)
    return {}


def save_tax_constants(year, constants):
    constants_dir = os.path.join(APP_DIR, "tax_constants")
    os.makedirs(constants_dir, exist_ok=True)
    with open(os.path.join(constants_dir, f"{year}.json"), "w") as f:
        json.dump(constants, f, indent=2)


# ─── Folder Discovery ────────────────────────────────────────────────────────

def discover_years(root_path):
    """Scan for directories matching '* Tax Year' pattern."""
    years = []
    if not os.path.isdir(root_path):
        return years
    for name in os.listdir(root_path):
        full_path = os.path.join(root_path, name)
        if os.path.isdir(full_path):
            match = re.match(r"^(\d{4})\s+Tax Year$", name, re.IGNORECASE)
            if match:
                years.append({
                    "year": int(match.group(1)),
                    "folder_name": name,
                    "path": full_path,
                })
    years.sort(key=lambda x: x["year"], reverse=True)
    return years


def discover_entity_folder(year_path, entity_name):
    """Find the folder for an entity by matching name in folder names."""
    if not os.path.isdir(year_path):
        return None
    # Simplify entity name for matching
    simple_name = re.sub(r"[^a-zA-Z0-9]", "", entity_name.lower())
    for folder in os.listdir(year_path):
        if not os.path.isdir(os.path.join(year_path, folder)):
            continue
        folder_simple = re.sub(r"[^a-zA-Z0-9]", "", folder.lower())
        if simple_name in folder_simple:
            return folder
    return None


# ─── Notes ────────────────────────────────────────────────────────────────────

def get_notes_path(year_path):
    """Find the worksheets/final folder and return notes file path."""
    for folder in os.listdir(year_path):
        folder_lower = folder.lower()
        if "final" in folder_lower or "worksheet" in folder_lower:
            notes_dir = os.path.join(year_path, folder)
            if os.path.isdir(notes_dir):
                return os.path.join(notes_dir, "dashboard_notes.json")
    # Fallback: create in year root
    return os.path.join(year_path, "dashboard_notes.json")


def load_notes(year_path):
    notes_path = get_notes_path(year_path)
    if os.path.exists(notes_path):
        try:
            with open(notes_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_notes(year_path, notes):
    notes_path = get_notes_path(year_path)
    os.makedirs(os.path.dirname(notes_path), exist_ok=True)
    with open(notes_path, "w") as f:
        json.dump(notes, f, indent=2)


# ─── Parsing ──────────────────────────────────────────────────────────────────

def parse_year_data(year_info, config):
    """Parse all data for a year. Returns structured data dict."""
    from parser import parse_year
    year = year_info["year"]
    year_path = year_info["path"]
    tax_constants = load_tax_constants(year)

    # Auto-discover entity folders if not set
    for entity in config.get("entities", []):
        if not entity.get("folder_name"):
            found = discover_entity_folder(year_path, entity["name"])
            if found:
                entity["folder_name"] = found

    try:
        data = parse_year(year_path, config, tax_constants)
    except Exception as e:
        logger.error(f"Error parsing year {year}: {e}", exc_info=True)
        data = {
            "overview": {"kpis": {}, "charts": {}},
            "schedule_c": {"schedules": [], "combined": {}, "findings": []},
            "findings": [{"topic": "Parse Error", "priority": "BLOCKED",
                          "finding": f"Failed to parse year data: {e}"}],
            "bank_recon": {"accounts": [], "status": "error"},
            "checklist": {"categories": [], "total_expected": 0, "total_found": 0, "completion_pct": 0},
            "strategy": {"recommendations": [], "total_estimated_savings": 0},
            "parse_errors": [str(e)],
        }

    # Bank reconciliation (separate because it's slow)
    try:
        from parser.bank_parser import build_bank_reconciliation
        bank_data = build_bank_reconciliation(year_path, config, year)
        data["bank_recon"] = bank_data
        data["findings"].extend(bank_data.get("findings", []))
    except Exception as e:
        logger.error(f"Bank reconciliation error: {e}", exc_info=True)
        data["bank_recon"] = {"accounts": [], "status": "error", "error": str(e)}

    # Load notes
    data["notes"] = load_notes(year_path)

    return data


def get_or_parse(year, config):
    """Get cached data or parse fresh.

    Two-tier cache:
      1. In-process PARSED_DATA dict for zero-overhead hot path.
      2. Persistent `parsed_cache` table keyed by year, invalidated by a
         signature over source file mtimes. Survives server restarts.
    """
    if year in PARSED_DATA:
        return PARSED_DATA[year]

    root_path = config.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    year_info = next((y for y in years if y["year"] == year), None)
    if not year_info:
        return None

    disk_cached = parsed_cache.get_cached(year, year_info["path"])
    if disk_cached is not None:
        PARSED_DATA[year] = disk_cached
        logger.info(f"Loaded year {year} from persistent cache")
        return disk_cached

    data = parse_year_data(year_info, config)
    PARSED_DATA[year] = data
    parsed_cache.set_cached(year, year_info["path"], data)
    return data


# ─── SSE ──────────────────────────────────────────────────────────────────────

def send_sse_event(event_type, data):
    """Push an SSE event to all connected clients."""
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    dead = []
    for q in SSE_CLIENTS:
        try:
            q.put_nowait(msg)
        except queue.Full:
            dead.append(q)
    for q in dead:
        SSE_CLIENTS.remove(q)


# ─── File Watcher ─────────────────────────────────────────────────────────────

def start_file_watcher(root_path):
    """Start watchdog observer on the root path."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        class TaxFileHandler(FileSystemEventHandler):
            def __init__(self):
                self._debounce = {}

            def _handle(self, event):
                path = event.src_path
                filename = os.path.basename(path)

                # New Tax Year folder detection — fire an SSE event so the
                # frontend can pull the updated year list without polling.
                if event.is_directory:
                    if re.match(r"^\d{4}\s+Tax Year$", filename, re.IGNORECASE):
                        logger.info(f"New tax year folder detected: {filename}")
                        for year in list(PARSED_DATA.keys()):
                            PARSED_DATA.pop(year, None)
                        send_sse_event("new_year_detected", {
                            "folder": filename,
                            "timestamp": datetime.now().isoformat(),
                            "toast": f"New tax year detected: {filename}",
                        })
                    return

                ext = os.path.splitext(path)[1].lower()
                if ext not in (".csv", ".pdf", ".xlsx", ".json"):
                    return

                # Debounce: ignore events within 2 seconds of last for same file
                now = time.time()
                if path in self._debounce and now - self._debounce[path] < 2:
                    return
                self._debounce[path] = now

                logger.info(f"File changed: {filename}")

                # Invalidate both in-memory and on-disk cache for any year
                # whose files might have changed.
                for year in list(PARSED_DATA.keys()):
                    PARSED_DATA.pop(year, None)
                try:
                    parsed_cache.invalidate()
                except Exception as e:
                    logger.warning(f"persistent cache invalidate failed: {e}")

                send_sse_event("file_changed", {
                    "file": filename,
                    "event": event.event_type,
                    "timestamp": datetime.now().isoformat(),
                    "toast": f"Data refreshed — {filename}",
                })

            def on_created(self, event):
                self._handle(event)

            def on_modified(self, event):
                self._handle(event)

            def on_deleted(self, event):
                self._handle(event)

        observer = Observer()
        observer.schedule(TaxFileHandler(), root_path, recursive=True)
        observer.daemon = True
        observer.start()
        logger.info(f"File watcher started on {root_path}")
    except ImportError:
        logger.warning("watchdog not installed. File watching disabled.")
    except Exception as e:
        logger.error(f"File watcher error: {e}")


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


@app.route("/api/config", methods=["GET"])
def get_config():
    cfg = load_config()
    if cfg is None:
        return jsonify({"setup_required": True}), 200
    return jsonify(cfg)


@app.route("/api/config", methods=["POST"])
def post_config():
    cfg = request.json
    save_config(cfg)

    # Auto-discover entity folders for current year
    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    if years:
        year_path = years[0]["path"]
        for entity in cfg.get("entities", []):
            if not entity.get("folder_name"):
                found = discover_entity_folder(year_path, entity["name"])
                if found:
                    entity["folder_name"] = found
        save_config(cfg)

    # Clear parsed cache
    invalidate_all_caches()

    # Start file watcher if not already running
    start_file_watcher(root_path)

    return jsonify({"status": "saved"})


@app.route("/api/years", methods=["GET"])
def get_years():
    cfg = load_config()
    if not cfg:
        return jsonify([])
    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    return jsonify([{"year": y["year"], "folder_name": y["folder_name"]} for y in years])


@app.route("/api/data/<int:year>", methods=["GET"])
def get_year_data(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    return jsonify(data)


@app.route("/api/overview/<int:year>", methods=["GET"])
def get_overview(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    return jsonify(data.get("overview", {}))


@app.route("/api/schedule-c/<int:year>", methods=["GET"])
def get_schedule_c(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    return jsonify(data.get("schedule_c", {}))


@app.route("/api/findings/<int:year>", methods=["GET"])
def get_findings(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    return jsonify({
        "current": data.get("findings", []),
        "transitions": data.get("findings_transitions", []),
        "summary": data.get("findings_summary", ""),
        "counts": data.get("findings_counts", {}),
    })


@app.route("/api/findings/<int:year>/dismiss", methods=["POST"])
def dismiss_finding(year):
    """Manually resolve/dismiss a finding by fingerprint."""
    body = request.get_json(silent=True) or {}
    fingerprint = body.get("fingerprint", "").strip()
    reason = body.get("reason", "User dismissed")
    if not fingerprint:
        return jsonify({"error": "fingerprint required"}), 400

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    rows = db.query(
        "SELECT id, severity FROM findings_history WHERE fingerprint = ? AND year = ?",
        (fingerprint, year),
    )
    if rows:
        db.execute(
            """UPDATE findings_history
               SET previous_severity = severity, severity = 'RESOLVED', resolved_at = ?, last_seen = ?
               WHERE fingerprint = ? AND year = ?""",
            (now, now, fingerprint, year),
        )
    else:
        # Insert as pre-resolved so it doesn't reappear on next parse
        db.execute(
            """INSERT INTO findings_history (fingerprint, year, severity, previous_severity, topic, title, first_seen, last_seen, resolved_at)
               VALUES (?, ?, 'RESOLVED', 'REQUIRED', 'User Dismissed', ?, ?, ?, ?)""",
            (fingerprint, year, reason, now, now, now),
        )

    sec_audit.log_action("user", "finding_dismiss", f"{year}/{fingerprint[:16]}", {"reason": reason})
    invalidate_all_caches()
    return jsonify({"status": "ok", "fingerprint": fingerprint})


@app.route("/api/readiness/<int:year>", methods=["GET"])
def get_readiness(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    return jsonify(data.get("readiness", {}))


# ─── Rules API (teach loop) ───────────────────────────────────────────────────

@app.route("/api/rules", methods=["GET"])
def list_rules():
    rules = rules_store.load_rules(force=True)
    return jsonify({"rules": rules, "counts": rules_store.count_by_source()})


@app.route("/api/rules/learn", methods=["POST"])
def learn_rule():
    body = request.get_json(silent=True) or {}
    pattern_type = body.get("pattern_type", "account_name")
    pattern = body.get("pattern")
    category = body.get("schedule_c_category")
    entity_scope = body.get("entity_scope")
    notes = body.get("notes", "")
    evidence_file = body.get("evidence_file", "")
    reasoning_parts = ["User-taught via Findings tab"]
    if notes:
        reasoning_parts.append(f"Notes: {notes}")
    if evidence_file:
        reasoning_parts.append(f"Evidence: {evidence_file}")
    reasoning = body.get("reasoning") or " | ".join(reasoning_parts)
    if not pattern:
        return jsonify({"error": "pattern required"}), 400
    rule_id = rules_store.add_rule(
        pattern_type=pattern_type,
        pattern=pattern,
        schedule_c_category=category,
        source="user",
        entity_scope=entity_scope,
        reasoning=reasoning,
    )
    invalidate_all_caches()
    sec_audit.log_action("user", "rule_learn", f"rule:{rule_id}", body)
    send_sse_event("rule_learned", {"rule_id": rule_id, "pattern": pattern, "category": category})
    return jsonify({"status": "ok", "rule_id": rule_id})


@app.route("/api/rules/batch-learn", methods=["POST"])
def batch_learn_rules():
    """Create multiple categorization rules in one request."""
    body = request.get_json(silent=True) or {}
    rules = body.get("rules", [])
    if not rules:
        return jsonify({"error": "rules array required"}), 400
    rule_ids = []
    for r in rules:
        pattern = r.get("pattern")
        if not pattern:
            continue
        rid = rules_store.add_rule(
            pattern_type=r.get("pattern_type", "memo"),
            pattern=pattern,
            schedule_c_category=r.get("schedule_c_category"),
            source=r.get("source", "user"),
            entity_scope=r.get("entity_scope"),
            reasoning=r.get("reasoning", "Batch-applied via AI suggestions"),
        )
        rule_ids.append(rid)
    invalidate_all_caches()
    sec_audit.log_action("user", "batch_rule_learn", f"count:{len(rule_ids)}", body)
    send_sse_event("rules_batch_learned", {"rule_ids": rule_ids, "count": len(rule_ids)})
    return jsonify({"status": "ok", "rule_ids": rule_ids, "count": len(rule_ids)})


@app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
def delete_rule(rule_id):
    rules_store.delete_rule(rule_id)
    invalidate_all_caches()
    sec_audit.log_action("user", "rule_delete", f"rule:{rule_id}", None)
    return jsonify({"status": "deleted"})


@app.route("/api/audit-log", methods=["GET"])
def audit_recent():
    limit = int(request.args.get("limit", 100))
    return jsonify({"entries": sec_audit.recent(limit)})


@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    """Shut down the server AND disable launchd auto-restart.

    If a LaunchAgent is installed (com.batman.cpagent or legacy com.batman.taxdashboard),
    unload it first so macOS's KeepAlive won't respawn the process. Then exit.
    """
    import subprocess
    import signal
    unloaded = []
    la_dir = os.path.expanduser("~/Library/LaunchAgents")
    for label in ("com.batman.cpagent.plist", "com.batman.taxdashboard.plist"):
        plist = os.path.join(la_dir, label)
        if os.path.exists(plist):
            try:
                subprocess.run(["launchctl", "unload", plist], check=False, capture_output=True, timeout=5)
                unloaded.append(label)
            except Exception as e:
                logger.warning(f"launchctl unload failed for {label}: {e}")
    sec_audit.log_action("user", "shutdown", "cpagent", {"unloaded": unloaded})
    logger.info(f"Shutdown requested — unloaded: {unloaded}")

    def _die():
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_die, daemon=True).start()
    return jsonify({"status": "shutting_down", "unloaded_launchagents": unloaded})


# ─── Unknown Accounts API (inference) ─────────────────────────────────────────

@app.route("/api/unknown-accounts", methods=["GET"])
def list_unknown_accounts():
    from inference.account_detector import list_unknown
    return jsonify({"accounts": list_unknown()})


@app.route("/api/unknown-accounts/<path:detected_name>", methods=["POST"])
def update_unknown_account(detected_name):
    from inference.account_detector import set_status
    status = (request.get_json(silent=True) or {}).get("status", "dismissed")
    set_status(detected_name, status)
    invalidate_all_caches()
    sec_audit.log_action("user", "unknown_account_update", detected_name, {"status": status})
    return jsonify({"status": "ok"})


# ─── Filing API (IRS PDF generation) ──────────────────────────────────────────

# ─── LLM Coach API (Phase K) ──────────────────────────────────────────────────

@app.route("/api/llm/budget", methods=["GET"])
def get_llm_budget():
    from learning import llm_coach
    return jsonify(llm_coach.get_budget_state())


@app.route("/api/llm/budget", methods=["PUT"])
def put_llm_budget():
    from learning import llm_coach
    body = request.get_json(silent=True) or {}
    try:
        state = llm_coach.update_budget(
            ceiling_usd=body.get("ceiling_usd"),
            period=body.get("period"),
            llm_enabled=body.get("llm_enabled"),
        )
        sec_audit.log_action("user", "llm_budget_update", "llm_budget", body)
        return jsonify(state)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/llm/model", methods=["GET"])
def get_llm_model():
    from learning import llm_coach
    return jsonify({
        "active_model": llm_coach.get_active_model(),
        "models": llm_coach.MODELS,
    })


@app.route("/api/llm/model", methods=["PUT"])
def put_llm_model():
    from learning import llm_coach
    body = request.get_json(silent=True) or {}
    try:
        model = llm_coach.set_active_model(body.get("model_id", ""))
        sec_audit.log_action("user", "llm_model_change", model, None)
        return jsonify({"active_model": model})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/llm/key", methods=["POST"])
def post_llm_key():
    from security.keyring import set_anthropic_key
    body = request.get_json(silent=True) or {}
    key = body.get("api_key", "").strip()
    if not key.startswith("sk-ant-"):
        return jsonify({"error": "Expected an Anthropic API key starting with sk-ant-"}), 400
    backend = set_anthropic_key(key)
    sec_audit.log_action("user", "llm_key_set", "keychain", {"backend": backend})
    return jsonify({"status": "stored", "backend": backend})


@app.route("/api/rules/suggest", methods=["POST"])
def suggest_rules_via_llm():
    from learning import llm_coach
    body = request.get_json(silent=True) or {}
    items = body.get("items", [])
    year = int(body.get("year", 0))
    cfg = load_config() or {}
    try:
        result = llm_coach.suggest_rules(items, cfg, year)
        return jsonify(result)
    except llm_coach.BudgetExceeded as e:
        return jsonify({"error": "budget_exceeded", "detail": str(e)}), 402
    except llm_coach.LLMUnavailable as e:
        return jsonify({"error": "llm_unavailable", "detail": str(e)}), 503


@app.route("/api/triage/suggest", methods=["POST"])
def triage_file():
    from learning import llm_coach
    body = request.get_json(silent=True) or {}
    filename = body.get("filename", "")
    preview_b64 = body.get("preview_base64", "")
    try:
        import base64
        preview = base64.b64decode(preview_b64) if preview_b64 else filename.encode()
        cfg = load_config() or {}
        result = llm_coach.suggest_filename(preview, filename, cfg)
        return jsonify(result)
    except llm_coach.BudgetExceeded as e:
        return jsonify({"error": "budget_exceeded", "detail": str(e)}), 402
    except llm_coach.LLMUnavailable as e:
        return jsonify({"error": "llm_unavailable", "detail": str(e)}), 503


@app.route("/api/amazon-invoices/<int:year>", methods=["GET"])
def get_amazon_invoices(year):
    from parser.amazon_parser import scan_amazon_folder, build_amazon_summary
    cfg = load_config() or {}
    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    year_info = next((y for y in years if y["year"] == year), None)
    if not year_info:
        return jsonify({"error": f"Year {year} not found"}), 404

    all_invoices = []
    for entity in cfg.get("entities", []):
        entity_path = os.path.join(year_info["path"], entity.get("folder_name", ""))
        invoices = scan_amazon_folder(entity_path)
        for inv in invoices:
            inv["entity"] = entity["name"]
        all_invoices.extend(invoices)

    return jsonify(build_amazon_summary(all_invoices))


@app.route("/api/amazon-invoices/<int:year>/classify", methods=["POST"])
def classify_amazon_invoice(year):
    """LLM classification of multi-item Amazon invoices.
    Only called for invoices with >1 item.
    """
    from learning import llm_coach
    from security import scrubber as pii_scrubber, audit as sec_audit_mod

    body = request.get_json(silent=True) or {}
    items = body.get("items", [])
    entity = body.get("entity", "")
    order_number = body.get("order_number", "")

    if len(items) <= 1:
        return jsonify({"error": "LLM classification only needed for invoices with >1 item"}), 400

    cfg = load_config() or {}
    try:
        budget = llm_coach.get_budget_state()
        if not budget.get("llm_enabled", True):
            return jsonify({"error": "llm_unavailable", "detail": "AI features disabled"}), 503

        model_id = llm_coach.get_active_model()
        item_descriptions = "\n".join(
            f"- {it['name']} (${it.get('price', 0):.2f}, sold by {it.get('seller', 'unknown')})"
            for it in items
        )

        system_prompt = (
            "You are a tax classification assistant. Given a list of items from an Amazon order "
            f"purchased by a business entity ({entity}), classify each item as either "
            "'business' (deductible on Schedule C — office supplies, equipment, industry tools) "
            "or 'personal' (not deductible). "
            "Return strict JSON: {\"classifications\": [{\"name\": \"...\", \"classification\": \"business\"|\"personal\", "
            "\"schedule_c_category\": \"Office expense\"|\"Supplies\"|null, \"reasoning\": \"...\"}]}"
        )
        user_prompt = f"Order #{order_number}\nEntity: {entity}\n\nItems:\n{item_descriptions}"

        scrubbed, rmap = pii_scrubber.scrub({"prompt": user_prompt}, cfg)

        est_cost = llm_coach.estimate_cost(
            llm_coach._rough_tokens(system_prompt + str(scrubbed)), 400, model_id
        )
        if est_cost + budget["spent_usd"] > budget["ceiling_usd"]:
            return jsonify({"error": "budget_exceeded"}), 402

        sec_audit.log_action("user", "llm_call", f"anthropic:{model_id}:amazon_classify",
                             {"order": order_number, "item_count": len(items)})

        client = llm_coach._client()
        response = client.messages.create(
            model=model_id,
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = "".join(
            b.text for b in response.content if getattr(b, "type", "") == "text"
        )
        import json as json_mod
        try:
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            parsed = json_mod.loads(raw_text[start:end + 1]) if start >= 0 and end > start else {}
        except Exception:
            parsed = {"raw": raw_text}

        actual_cost = llm_coach.estimate_cost(
            response.usage.input_tokens, response.usage.output_tokens, model_id
        )
        db.execute(
            """INSERT INTO llm_suggestions (prompt_hash, scrubbed_batch_json, response_json, cost_usd, model_id)
               VALUES (?, ?, ?, ?, ?)""",
            (sec_audit._hash(str(scrubbed)), str(scrubbed)[:2000], json_mod.dumps(parsed), round(actual_cost, 6), model_id),
        )

        parsed["cost_usd"] = round(actual_cost, 6)
        parsed["model"] = model_id
        parsed["budget"] = llm_coach.get_budget_state()
        return jsonify(parsed)

    except llm_coach.BudgetExceeded as e:
        return jsonify({"error": "budget_exceeded", "detail": str(e)}), 402
    except llm_coach.LLMUnavailable as e:
        return jsonify({"error": "llm_unavailable", "detail": str(e)}), 503
    except Exception as e:
        logger.exception("Amazon classify failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/filing/generate/<int:year>", methods=["POST"])
def generate_filing(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    readiness = data.get("readiness", {})
    watermark = not readiness.get("can_generate_filing", False)

    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    year_info = next((y for y in years if y["year"] == year), None)
    if not year_info:
        return jsonify({"error": f"Year {year} folder not found"}), 404

    from filing.irs_forms import generate_1040_schedule_c
    try:
        result = generate_1040_schedule_c(year, data, cfg, year_info["path"], apply_watermark=watermark)
    except Exception as e:
        logger.exception("filing generation failed")
        return jsonify({"error": str(e)}), 500

    sec_audit.log_action("user", "filing_export", result["path"], {"year": year, "watermarked": watermark})
    return jsonify(result)


@app.route("/api/filing/extension/<int:year>", methods=["POST"])
def generate_extension(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404

    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    year_info = next((y for y in years if y["year"] == year), None)
    if not year_info:
        return jsonify({"error": f"Year {year} folder not found"}), 404

    from filing.irs_forms import generate_extension_4868
    try:
        result = generate_extension_4868(year, data, cfg, year_info["path"])
    except Exception as e:
        logger.exception("extension generation failed")
        return jsonify({"error": str(e)}), 500

    sec_audit.log_action("user", "extension_export", result.get("path", ""), {"year": year})
    return jsonify(result)


@app.route("/api/filing/state/<int:year>", methods=["POST"])
def generate_state_filing_route(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    year_info = next((y for y in years if y["year"] == year), None)
    if not year_info:
        return jsonify({"error": f"Year {year} folder not found"}), 404
    from filing.irs_forms import generate_state_filing
    try:
        result = generate_state_filing(year, data, cfg, year_info["path"])
    except Exception as e:
        logger.exception("state filing generation failed")
        return jsonify({"error": str(e)}), 500
    sec_audit.log_action("user", "state_filing_export", result.get("path", ""), {"year": year})
    return jsonify(result)


@app.route("/api/filing/state-extension/<int:year>", methods=["POST"])
def generate_state_extension_route(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    year_info = next((y for y in years if y["year"] == year), None)
    if not year_info:
        return jsonify({"error": f"Year {year} folder not found"}), 404
    from filing.irs_forms import generate_state_extension
    try:
        result = generate_state_extension(year, data, cfg, year_info["path"])
    except Exception as e:
        logger.exception("state extension generation failed")
        return jsonify({"error": str(e)}), 500
    sec_audit.log_action("user", "state_extension_export", result.get("path", ""), {"year": year})
    return jsonify(result)


@app.route("/api/bank-recon/<int:year>", methods=["GET"])
def get_bank_recon(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    return jsonify(data.get("bank_recon", {}))


@app.route("/api/checklist/<int:year>", methods=["GET"])
def get_checklist(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    return jsonify(data.get("checklist", {}))


@app.route("/api/checklist-override/<int:year>", methods=["POST"])
def set_checklist_override(year):
    body = request.get_json(silent=True) or {}
    category = body.get("category", "").strip()
    doc_name = body.get("doc_name", "").strip()
    status = body.get("status", "").strip()  # disabled, stored_elsewhere, or empty to remove

    if not category or not doc_name:
        return jsonify({"error": "category and doc_name required"}), 400

    if status in ("disabled", "stored_elsewhere", "referenced_in"):
        db.execute(
            """INSERT OR REPLACE INTO checklist_overrides
               (year, category, doc_name, status, reason, reference_file, reference_amount)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (year, category, doc_name, status, body.get("reason", ""),
             body.get("reference_file"), body.get("reference_amount")),
        )
    elif status == "" or status == "active":
        db.execute(
            "DELETE FROM checklist_overrides WHERE year = ? AND category = ? AND doc_name = ?",
            (year, category, doc_name),
        )
    else:
        return jsonify({"error": f"Invalid status: {status}"}), 400

    sec_audit.log_action("user", "checklist_override", f"{year}/{category}/{doc_name}", body)

    invalidate_all_caches()

    return jsonify({"status": "ok"})


@app.route("/api/schedule-c-notes/<int:year>", methods=["GET"])
def get_schedule_c_notes(year):
    rows = db.query(
        "SELECT entity, line_number, note_text, implies_rule, updated_at FROM schedule_c_notes WHERE year = ?",
        (year,),
    )
    return jsonify([dict(r) for r in rows])


@app.route("/api/schedule-c-notes/<int:year>", methods=["POST"])
def save_schedule_c_note(year):
    body = request.get_json(silent=True) or {}
    entity = body.get("entity", "").strip()
    line_number = body.get("line_number", "").strip()
    note_text = body.get("note_text", "").strip()

    if not entity or not line_number:
        return jsonify({"error": "entity and line_number required"}), 400

    if note_text:
        db.execute(
            """INSERT OR REPLACE INTO schedule_c_notes (year, entity, line_number, note_text, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (year, entity, line_number, note_text),
        )
    else:
        db.execute(
            "DELETE FROM schedule_c_notes WHERE year = ? AND entity = ? AND line_number = ?",
            (year, entity, line_number),
        )

    sec_audit.log_action("user", "schedule_c_note", f"{year}/{entity}/L{line_number}", {"length": len(note_text)})
    return jsonify({"status": "ok"})


@app.route("/api/conduit-relationships/<int:year>", methods=["GET"])
def get_conduit_relationships(year):
    from parser.conduit import load_conduit_relationships, scan_1099s
    cfg = load_config() or {}
    rels = load_conduit_relationships(year)
    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    year_info = next((y for y in years if y["year"] == year), None)
    issued_1099s = scan_1099s(year_info["path"]) if year_info else []
    return jsonify({"relationships": rels, "issued_1099s": issued_1099s})


@app.route("/api/conduit-relationships/<int:year>", methods=["POST"])
def save_conduit_relationship_route(year):
    from parser.conduit import save_conduit_relationship
    body = request.get_json(silent=True) or {}
    body["year"] = year
    if not body.get("counterparty_name") or not body.get("treatment"):
        return jsonify({"error": "counterparty_name and treatment required"}), 400
    row_id = save_conduit_relationship(body)
    sec_audit.log_action("user", "conduit_save", f"{year}/{body['counterparty_name']}", body)

    invalidate_all_caches()
    return jsonify({"status": "ok", "id": row_id})


@app.route("/api/home-office/<int:year>", methods=["GET"])
def get_home_office(year):
    from parser.home_office import compute_total_deduction, detect_leases_from_folder
    cfg = load_config() or {}
    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    year_info = next((y for y in years if y["year"] == year), None)

    result = compute_total_deduction(year)
    if year_info:
        result["detected_leases"] = detect_leases_from_folder(year_info["path"])
    return jsonify(result)


@app.route("/api/home-office/<int:year>", methods=["POST"])
def save_home_office(year):
    from parser.home_office import save_home_office_period
    body = request.get_json(silent=True) or {}
    body["year"] = year
    if not body.get("address") or not body.get("start_date") or not body.get("end_date"):
        return jsonify({"error": "address, start_date, end_date required"}), 400
    row_id = save_home_office_period(body)
    sec_audit.log_action("user", "home_office_save", f"{year}/{body['address']}", body)
    invalidate_all_caches()
    return jsonify({"status": "ok", "id": row_id})


@app.route("/api/home-office/<int:year>/<int:period_id>", methods=["DELETE"])
def delete_home_office_route(year, period_id):
    from parser.home_office import delete_home_office_period
    delete_home_office_period(period_id)
    sec_audit.log_action("user", "home_office_delete", f"{year}/period:{period_id}", None)
    invalidate_all_caches()
    return jsonify({"status": "ok"})


@app.route("/api/strategy/<int:year>", methods=["GET"])
def get_strategy(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    return jsonify(data.get("strategy", {}))


@app.route("/api/notes/<int:year>", methods=["GET"])
def get_notes(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    return jsonify(data.get("notes", {}))


@app.route("/api/notes/<int:year>", methods=["POST"])
def post_notes(year):
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400

    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    year_info = next((y for y in years if y["year"] == year), None)
    if not year_info:
        return jsonify({"error": f"Year {year} not found"}), 404

    notes = request.json
    try:
        save_notes(year_info["path"], notes)
        if year in PARSED_DATA:
            PARSED_DATA[year]["notes"] = notes
        return jsonify({"status": "saved"})
    except Exception as e:
        logger.error(f"Notes save error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/refresh/<int:year>", methods=["POST"])
def refresh_year(year):
    """Force re-parse of a year's data."""
    PARSED_DATA.pop(year, None)
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400
    data = get_or_parse(year, cfg)
    if data is None:
        return jsonify({"error": f"Year {year} not found"}), 404
    send_sse_event("parse_complete", {"year": year, "timestamp": datetime.now().isoformat()})
    return jsonify({"status": "refreshed"})


@app.route("/api/new-year", methods=["POST"])
def create_new_year():
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400

    new_year = request.json.get("year")
    if not new_year:
        return jsonify({"error": "Year required"}), 400

    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    year_folder = os.path.join(root_path, f"{new_year} Tax Year")

    if os.path.exists(year_folder):
        return jsonify({"error": f"Year folder already exists: {new_year} Tax Year"}), 400

    # Scaffold folder structure
    os.makedirs(year_folder)
    # Create subfolders based on existing year's structure
    years = discover_years(root_path)
    if years:
        existing = years[0]["path"]
        for folder in sorted(os.listdir(existing)):
            if os.path.isdir(os.path.join(existing, folder)) and not folder.startswith("."):
                os.makedirs(os.path.join(year_folder, folder), exist_ok=True)
    else:
        # Default structure
        entities = cfg.get("entities", [])
        idx = 1
        os.makedirs(os.path.join(year_folder, f"{idx:02d}_Personal"), exist_ok=True)
        idx += 1
        os.makedirs(os.path.join(year_folder, f"{idx:02d}_Spouse_Income"), exist_ok=True)
        idx += 1
        for entity in entities:
            name_slug = re.sub(r"[^a-zA-Z0-9_]", "_", entity["name"])
            os.makedirs(os.path.join(year_folder, f"{idx:02d}_{name_slug}"), exist_ok=True)
            idx += 1
        for folder_name in ["Vehicle", "Home_Office", "Investments", "Crypto",
                            "Contractors_1099s", "Tax_Payments", "Prior_Returns", "Final_Worksheets"]:
            os.makedirs(os.path.join(year_folder, f"{idx:02d}_{folder_name}"), exist_ok=True)
            idx += 1

    # Copy tax constants from most recent year
    constants_dir = os.path.join(APP_DIR, "tax_constants")
    if os.path.isdir(constants_dir):
        files = sorted([f for f in os.listdir(constants_dir) if f.endswith(".json")], reverse=True)
        if files:
            import shutil
            src = os.path.join(constants_dir, files[0])
            dst = os.path.join(constants_dir, f"{new_year}.json")
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                # Update year in the new file
                with open(dst, "r") as f:
                    data = json.load(f)
                data["year"] = new_year
                with open(dst, "w") as f:
                    json.dump(data, f, indent=2)

    # Create empty notes file
    notes_dir = None
    for folder in os.listdir(year_folder):
        if "worksheet" in folder.lower() or "final" in folder.lower():
            notes_dir = os.path.join(year_folder, folder)
            break
    if notes_dir:
        with open(os.path.join(notes_dir, "dashboard_notes.json"), "w") as f:
            json.dump({}, f)

    return jsonify({"status": "created", "year": new_year})


@app.route("/api/tax-constants/<int:year>", methods=["GET"])
def get_tax_constants(year):
    constants = load_tax_constants(year)
    return jsonify(constants)


@app.route("/api/tax-constants/<int:year>", methods=["POST"])
def post_tax_constants(year):
    constants = request.json
    save_tax_constants(year, constants)
    PARSED_DATA.pop(year, None)  # invalidate cache
    return jsonify({"status": "saved"})


@app.route("/api/csv-preview/<int:year>/<path:csv_path>", methods=["GET"])
def csv_preview(year, csv_path):
    """Preview a CSV file for the mapping modal."""
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400

    root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
    years = discover_years(root_path)
    year_info = next((y for y in years if y["year"] == year), None)
    if not year_info:
        return jsonify({"error": "Year not found"}), 404

    filepath = os.path.join(year_info["path"], csv_path)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    from parser.csv_parser import detect_csv_format
    fmt = detect_csv_format(filepath)
    return jsonify(fmt)


@app.route("/api/csv-mapping", methods=["POST"])
def save_csv_mapping():
    """Save a CSV column mapping."""
    cfg = load_config()
    if not cfg:
        return jsonify({"error": "Not configured"}), 400

    mapping = request.json
    header_hash = mapping.get("header_hash")
    if not header_hash:
        return jsonify({"error": "header_hash required"}), 400

    if "csv_mappings" not in cfg:
        cfg["csv_mappings"] = {}
    cfg["csv_mappings"][header_hash] = mapping
    save_config(cfg)
    invalidate_all_caches()

    return jsonify({"status": "saved"})


@app.route("/api/stream")
def sse_stream():
    """Server-Sent Events endpoint."""
    def event_stream():
        q = queue.Queue(maxsize=50)
        SSE_CLIENTS.append(q)
        try:
            # Send keepalive comment
            yield ":keepalive\n\n"
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    yield ":keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            if q in SSE_CLIENTS:
                SSE_CLIENTS.remove(q)

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ─── Startup ──────────────────────────────────────────────────────────────────

def bootstrap_db():
    """Initialize the encrypted store and seed categorization rules on first run."""
    diag = db.init_db()
    inserted = rules_store.seed_from_hardcoded()
    classified = sec_classifier.seed_classifications()
    cfg = load_config() or {}
    purge_counts = retention.purge(cfg)
    counts = rules_store.count_by_source()
    warmed = parsed_cache.warm_all()
    sec_audit.log_action("system", "startup", "bootstrap", {"seeded_rules": inserted, "purge": purge_counts, "warmed_years": warmed}, result="ok")
    logger.info(
        f"DB ready — encryption={diag['encryption_active']} seeded_this_run={inserted} "
        f"total_rules={counts} classified_fields={classified} purged={purge_counts} "
        f"persistent_cache_years={warmed}"
    )
    return diag


def main():
    parser = argparse.ArgumentParser(description="Tax Dashboard")
    parser.add_argument("--port", type=int, default=5050, help="Port to run on (default: 5050)")
    args = parser.parse_args()

    bootstrap_db()

    cfg = load_config()
    if cfg:
        root_path = cfg.get("root_path", os.path.dirname(APP_DIR))
        start_file_watcher(root_path)
        logger.info(f"Root path: {root_path}")

        # Pre-parse the most recent year in background
        years = discover_years(root_path)
        if years:
            def preload():
                try:
                    get_or_parse(years[0]["year"], cfg)
                    logger.info(f"Pre-parsed year {years[0]['year']}")
                except Exception as e:
                    logger.error(f"Pre-parse error: {e}")
            threading.Thread(target=preload, daemon=True).start()
    else:
        logger.info("No config found — setup required on first launch")

    logger.info(f"Starting CPAgent on http://localhost:{args.port}")
    # Localhost-only bind (CC6.1). LAN machines cannot reach the API.
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
