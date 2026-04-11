"""LLM coach — one-shot categorization suggestions via the Anthropic API.

Design principles:
1. **Scrubber-first**: every byte sent outbound passes through
   `security.scrubber.scrub()`. PII never leaves the machine.
2. **Cost ceiling**: before any call, the estimated cost is checked against the
   configurable monthly ceiling in `llm_budget`. The call is refused if it
   would push spend over the ceiling.
3. **Model switcher**: Opus / Sonnet / Haiku selectable at runtime. Pricing is
   hardcoded per the 2026-04 Anthropic price list and can be updated in one
   place.
4. **No runtime LLM**: suggestions become permanent DB rules on user approval.
   The hot categorization path stays deterministic.
5. **Audit log**: every call is recorded with payload hash, model, cost.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

import db
from security import scrubber as pii_scrubber
from security import audit
from security.keyring import get_anthropic_key

logger = logging.getLogger(__name__)

# Price list (USD per 1M tokens) — 2026-04 Anthropic pricing
MODELS = {
    "claude-opus-4-6":          {"label": "Opus 4.6",   "in_per_mtok": 15.00, "out_per_mtok": 75.00},
    "claude-sonnet-4-6":        {"label": "Sonnet 4.6", "in_per_mtok": 3.00,  "out_per_mtok": 15.00},
    "claude-haiku-4-5-20251001":{"label": "Haiku 4.5",  "in_per_mtok": 0.80,  "out_per_mtok": 4.00},
}
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_CEILING_USD = 10.0


class BudgetExceeded(Exception):
    pass


class LLMUnavailable(Exception):
    pass


def _active_budget() -> dict:
    rows = db.query(
        "SELECT * FROM llm_budget WHERE active = 1 ORDER BY id DESC LIMIT 1"
    )
    if rows:
        return dict(rows[0])
    # Bootstrap default budget
    now = datetime.utcnow().strftime("%Y-%m-01 00:00:00")
    db.execute(
        "INSERT INTO llm_budget (period, period_start, ceiling_usd, spent_usd, active) VALUES ('month', ?, ?, 0, 1)",
        (now, DEFAULT_CEILING_USD),
    )
    return dict(db.query("SELECT * FROM llm_budget WHERE active = 1 ORDER BY id DESC LIMIT 1")[0])


def _period_spent(budget: dict) -> float:
    since = budget["period_start"]
    if budget["period"] == "session":
        since = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")  # no window
    rows = db.query(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM llm_suggestions WHERE created_at >= ?",
        (since,),
    )
    return float(rows[0]["total"] or 0)


def get_budget_state() -> dict:
    b = _active_budget()
    spent = _period_spent(b)
    return {
        "id": b["id"],
        "period": b["period"],
        "period_start": b["period_start"],
        "ceiling_usd": b["ceiling_usd"],
        "spent_usd": round(spent, 4),
        "remaining_usd": round(max(0, b["ceiling_usd"] - spent), 4),
        "percent_used": round(100 * spent / b["ceiling_usd"], 1) if b["ceiling_usd"] else 0,
    }


def update_budget(ceiling_usd: float | None = None, period: str | None = None) -> dict:
    b = _active_budget()
    if ceiling_usd is not None:
        db.execute(
            "UPDATE llm_budget SET ceiling_usd = ?, updated_at = datetime('now') WHERE id = ?",
            (ceiling_usd, b["id"]),
        )
    if period in ("month", "session"):
        now = datetime.utcnow().strftime("%Y-%m-01 00:00:00") if period == "month" else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "UPDATE llm_budget SET period = ?, period_start = ?, updated_at = datetime('now') WHERE id = ?",
            (period, now, b["id"]),
        )
    return get_budget_state()


def get_active_model() -> str:
    rows = db.query("SELECT value FROM schema_meta WHERE key = 'active_model'")
    if rows:
        return rows[0]["value"]
    db.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('active_model', ?)",
        (DEFAULT_MODEL,),
    )
    return DEFAULT_MODEL


def set_active_model(model_id: str) -> str:
    if model_id not in MODELS:
        raise ValueError(f"Unknown model: {model_id}. Options: {list(MODELS)}")
    db.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('active_model', ?)",
        (model_id,),
    )
    return model_id


def estimate_cost(input_tokens: int, output_tokens: int, model_id: str | None = None) -> float:
    model_id = model_id or get_active_model()
    pricing = MODELS[model_id]
    return (input_tokens * pricing["in_per_mtok"] + output_tokens * pricing["out_per_mtok"]) / 1_000_000


def _rough_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _client():
    try:
        import anthropic
    except ImportError:
        raise LLMUnavailable("anthropic SDK not installed. pip install anthropic")
    key = get_anthropic_key()
    if not key:
        raise LLMUnavailable("ANTHROPIC_API_KEY not set (use /api/llm/key or env var)")
    return anthropic.Anthropic(api_key=key)


SCHEDULE_C_CATEGORIES = [
    "Advertising", "Car and truck expenses", "Commissions and fees", "Contract labor",
    "Depreciation", "Employee benefit programs", "Insurance (other than health)",
    "Interest (mortgage)", "Interest (other)", "Legal and professional",
    "Office expense", "Pension and profit-sharing", "Rent - vehicles/equipment",
    "Rent - other", "Repairs and maintenance", "Supplies", "Taxes and licenses",
    "Travel", "Meals (50% limit)", "Utilities", "Wages", "Other expenses",
    "Gross receipts", "Returns and allowances",
    None,  # Excluded (goes to 1040 or Schedule A, not Schedule C)
]


def suggest_rules(items: list[dict], config: dict, year: int) -> dict:
    """Ask Claude to propose categorization rules for uncategorized items.

    Each item should be a dict with {memo_pattern, samples, group_total, group_count}.
    Returns {suggestions: [...], cost_usd, estimated_cost_usd, model, ...}.
    """
    if not items:
        return {"suggestions": [], "cost_usd": 0, "model": get_active_model()}

    model_id = get_active_model()
    budget = get_budget_state()
    scrubbed_payload, redaction_map = pii_scrubber.scrub(items, config)

    system_prompt = (
        "You are a tax categorization assistant for a US self-employed Schedule C filing. "
        "For each transaction group, propose the most likely IRS Schedule C category, "
        "a confidence score (0.0-1.0), and a brief rationale. Valid categories: "
        + ", ".join(str(c) for c in SCHEDULE_C_CATEGORIES) + ". "
        "Use `null` for the category field if the transaction should NOT appear on Schedule C "
        "(e.g., federal tax payments, personal expenses, transfers between accounts). "
        "Return a JSON object with a top-level key `suggestions` whose value is an array. "
        "Each array entry must have keys: memo_pattern, category, confidence, rationale, pattern_suggestion. "
        "`pattern_suggestion` is the memo substring that should match future transactions."
    )
    user_prompt = (
        f"Year: {year}\n"
        f"Entities (tokenized): {[e.get('name') for e in config.get('entities', [])]}\n\n"
        f"Uncategorized transaction groups to classify:\n{scrubbed_payload}"
    )

    input_text = system_prompt + user_prompt
    est_input = _rough_tokens(input_text)
    est_output = 600
    est_cost = estimate_cost(est_input, est_output, model_id)

    if est_cost + budget["spent_usd"] > budget["ceiling_usd"]:
        raise BudgetExceeded(
            f"Estimated call cost ${est_cost:.4f} + already spent ${budget['spent_usd']:.4f} "
            f"exceeds ceiling ${budget['ceiling_usd']:.2f}. Raise ceiling in AI Settings or select a cheaper model."
        )

    audit.log_action(
        actor="user",
        action="llm_call",
        target=f"anthropic:{model_id}:suggest_rules",
        payload={"item_count": len(items), "estimated_cost": est_cost, "scrubbed_preview": scrubbed_payload[:300]},
    )

    try:
        client = _client()
        response = client.messages.create(
            model=model_id,
            max_tokens=2000,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as e:
        logger.exception("Anthropic API call failed")
        db.execute(
            """INSERT INTO llm_suggestions (prompt_hash, scrubbed_batch_json, response_json, cost_usd, model_id, error)
               VALUES (?, ?, ?, 0, ?, ?)""",
            (
                audit._hash(scrubbed_payload),
                scrubbed_payload[:4000],
                None,
                model_id,
                str(e),
            ),
        )
        raise

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    unscrubbed_text = pii_scrubber.unscrub(raw_text, redaction_map)

    try:
        # Try object format first: {"suggestions": [...]}
        start = unscrubbed_text.find("{")
        end = unscrubbed_text.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(unscrubbed_text[start:end + 1])
            suggestions = parsed.get("suggestions", [])
        else:
            # Fallback: bare array format [...]
            arr_start = unscrubbed_text.find("[")
            arr_end = unscrubbed_text.rfind("]")
            if arr_start >= 0 and arr_end > arr_start:
                suggestions = json.loads(unscrubbed_text[arr_start:arr_end + 1])
            else:
                suggestions = []
        if not isinstance(suggestions, list):
            suggestions = []
    except Exception as e:
        logger.warning(f"suggestion JSON parse failed: {e}")
        suggestions = []

    actual_cost = estimate_cost(
        response.usage.input_tokens,
        response.usage.output_tokens,
        model_id,
    )

    db.execute(
        """INSERT INTO llm_suggestions
           (prompt_hash, scrubbed_batch_json, response_json, cost_usd, model_id)
           VALUES (?, ?, ?, ?, ?)""",
        (
            audit._hash(scrubbed_payload),
            scrubbed_payload[:4000],
            json.dumps(suggestions),
            round(actual_cost, 6),
            model_id,
        ),
    )

    return {
        "suggestions": suggestions,
        "model": model_id,
        "cost_usd": round(actual_cost, 6),
        "estimated_cost_usd": round(est_cost, 6),
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "budget": get_budget_state(),
    }


def suggest_filename(file_bytes_preview: bytes, filename: str, config: dict) -> dict:
    """File triage — propose a semantic filename + destination folder.

    Reads the first ~8KB of a mystery file, extracts text, and asks Claude to
    propose {folder_category, new_filename, confidence, reasoning}. Only text
    metadata is sent; the scrubber strips PII first.
    """
    try:
        text = file_bytes_preview.decode("utf-8", errors="ignore")[:4000]
    except Exception:
        text = str(file_bytes_preview[:200])
    model_id = get_active_model()
    budget = get_budget_state()
    entity_names = [e.get("name") for e in config.get("entities", [])]

    scrubbed_text, rmap = pii_scrubber.scrub({"filename": filename, "preview": text}, config)

    system_prompt = (
        "You are a document triage assistant. Given a file's name and a text preview, "
        "propose: (1) which folder category it belongs to, chosen from "
        f"{entity_names} or one of ['Personal','Spouse_Income','Vehicle','Home_Office','Investments','Crypto','Contractors_1099s','Tax_Payments','Prior_Returns','Final_Worksheets','Unknown']. "
        "(2) A new filename following the convention '{YYYY-MM}_{institution}_{docType}.pdf' "
        "when the data supports it, otherwise 'keep_original'. (3) Confidence 0.0-1.0. "
        "(4) One-sentence reasoning. Return strict JSON: "
        '{"folder_category": "...", "new_filename": "...", "confidence": 0.0, "reasoning": "..."}'
    )

    est_cost = estimate_cost(_rough_tokens(system_prompt + scrubbed_text), 200, model_id)
    if est_cost + budget["spent_usd"] > budget["ceiling_usd"]:
        raise BudgetExceeded(f"file triage would exceed budget (est ${est_cost:.4f})")

    audit.log_action("user", "llm_call", f"anthropic:{model_id}:triage", {"filename": filename})

    client = _client()
    response = client.messages.create(
        model=model_id,
        max_tokens=300,
        system=system_prompt,
        messages=[{"role": "user", "content": scrubbed_text}],
    )
    text_out = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    text_out = pii_scrubber.unscrub(text_out, rmap)

    try:
        s, e = text_out.find("{"), text_out.rfind("}")
        parsed = json.loads(text_out[s:e + 1]) if s >= 0 and e > s else {}
    except Exception:
        parsed = {"error": "parse_failed", "raw": text_out}

    actual = estimate_cost(response.usage.input_tokens, response.usage.output_tokens, model_id)
    db.execute(
        """INSERT INTO llm_suggestions (prompt_hash, scrubbed_batch_json, response_json, cost_usd, model_id)
           VALUES (?, ?, ?, ?, ?)""",
        (audit._hash(scrubbed_text), scrubbed_text[:2000], json.dumps(parsed), round(actual, 6), model_id),
    )

    parsed["cost_usd"] = round(actual, 6)
    parsed["model"] = model_id
    return parsed
