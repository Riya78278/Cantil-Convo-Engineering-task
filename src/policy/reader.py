"""Conservative extraction of proposed changes with explicit unresolved source text."""
import copy
import io
import json
import re
from decimal import Decimal

from src.engine.policy import validate, ROUTES

LABELS = {
    "creation_days": "Creation window (calendar days)", "need_by_days": "Need-by tolerance (days)",
    "po_hours": "PO consolidation window (hours)", "history_days": "Purchase history window (days)",
    "preferred_days": "Preferred supplier history window (days)", "ranking_weights": "Supplier ranking weights",
    "version": "Version", "effective_date": "Effective date",
}
WINDOWS = {
    "clubbing window": "creation_days", "creation window": "creation_days",
    "need-by tolerance": "need_by_days", "need by tolerance": "need_by_days",
    "consolidation window": "po_hours", "purchase history window": "history_days",
    "preferred supplier history window": "preferred_days",
}
OPERATORS = {"eq": "equals", "lt": "is below", "lte": "is at most", "gt": "exceeds",
             "gte": "is at least", "in": "is one of"}


def describe(condition):
    if "all" in condition or "any" in condition:
        key = "all" if "all" in condition else "any"
        children = condition[key]
        if not children:
            return "always" if key == "all" else "never"
        joiner = " AND " if key == "all" else " OR "
        return "(" + joiner.join(describe(c) for c in children) + ")"
    field = condition["field"].replace("_", " ")
    value = condition["value"]
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    return f"{field} {OPERATORS[condition['op']]} {value}"


def preview(base, candidate):
    changes = []
    for key, label in LABELS.items():
        if base[key] != candidate[key]:
            changes.append(f"{label}: {base[key]} → {candidate[key]}")
    before = {r["clause"]: r for r in base["rules"]}
    after = {r["clause"]: r for r in candidate["rules"]}
    for clause in before.keys() - after.keys():
        changes.append(f"Remove clause {clause}")
    for rule in candidate["rules"]:
        clause = rule["clause"]
        if before.get(clause) != rule:
            old = before.get(clause)
            if old:
                changes.append(f"Previous clause {clause}: when {describe(old['when'])}, use {old['route']}, "
                               f"suggest {old['suggestions']}")
            changes.append(f"{'Update' if old else 'Add'} clause {clause}: when {describe(rule['when'])}, "
                           f"use {rule['route']}, suggest {rule['suggestions']}")
    old_order = [r["clause"] for r in base["rules"]]
    new_order = [r["clause"] for r in candidate["rules"]]
    if old_order != new_order:
        changes.append("Rule order: " + " → ".join(new_order))
    return changes


def amount(text):
    match = re.fullmatch(r"(?:INR\s*|Rs\.?\s*|₹\s*)?([\d,]+(?:\.\d+)?)\s*(lakh|lakhs|crore|crores|million)?",
                         text.strip(), re.I)
    if not match:
        raise ValueError("Unrecognized amount")
    number = Decimal(match[1].replace(",", ""))
    factor = {"lakh": 100000, "lakhs": 100000, "crore": 10000000, "crores": 10000000, "million": 1000000}
    return str(number * factor.get((match[2] or "").lower(), 1))


def value_condition(rule):
    def find(node):
        if node.get("field") == "value":
            return [node]
        return [found for child in node.get("all", node.get("any", [])) for found in find(child)]
    found = find(rule["when"])
    if len(found) != 1:
        raise ValueError("Threshold rule needs exactly one value condition")
    return found[0]


def read_source(data, filename):
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader
        try:
            document = PdfReader(io.BytesIO(data))
            if len(document.pages) > 50:
                raise ValueError("Upload at most 50 pages")
            text = "\n".join(page.extract_text() or "" for page in document.pages)
        except Exception as exc:
            raise ValueError(f"Cannot read this PDF: {type(exc).__name__}") from None
        if not text.strip():
            raise ValueError("This PDF has no readable text. Paste its text or upload a text-based PDF.")
        return text
    if not filename.lower().endswith((".txt", ".json", ".md")):
        raise ValueError("Upload a PDF, TXT, MD or JSON document")
    return data.decode("utf-8-sig")


def propose(text, base, version, effective_date):
    candidate = copy.deepcopy(base)
    candidate.update(version=version, effective_date=effective_date)
    if text.lstrip().startswith("{"):
        candidate = json.loads(text)
        validate(candidate)
        return candidate, []
    unresolved = []
    # Each line/semicolon is processed independently; unsupported text remains visible for review.
    for sentence in re.split(r"[;\n]+", text):
        sentence = sentence.strip().rstrip(".")
        if not sentence:
            continue
        trial = copy.deepcopy(candidate)
        try:
            apply_sentence(sentence, trial)
            candidate = trial
        except ValueError:
            unresolved.append(sentence)
    validate(candidate)
    return candidate, unresolved


def apply_sentence(sentence, policy):
    lowered = sentence.lower()
    version = re.fullmatch(r"(?:policy )?version\s+([\w.\-]+)", sentence, re.I)
    if version:
        policy["version"] = version[1]
        return
    effective = re.fullmatch(r"effective(?: from)?\s+(\d{4}-\d{2}-\d{2})", sentence, re.I)
    if effective:
        policy["effective_date"] = effective[1]
        return
    for phrase, key in WINDOWS.items():
        match = re.fullmatch(r"(?:set |change (?:the )?)?" + re.escape(phrase) + r" (?:to |is )?(\d+) (days|hours)", lowered)
        if match:
            if match[2] != ("hours" if key == "po_hours" else "days"):
                raise ValueError("Window units disagree")
            policy[key] = int(match[1])
            return
    threshold = re.fullmatch(
        r"(?:set |change |increase |reduce )?(?:the )?(spot(?: po)?|emergency(?: po)?|rfq|rfp) "
        r"(?:limit|threshold)(?: from .+?)? (?:to |is )?(.+)", sentence, re.I)
    if threshold:
        route = {"spot": "SPOT_PO", "spot po": "SPOT_PO", "emergency": "EMERGENCY_PO",
                 "emergency po": "EMERGENCY_PO", "rfq": "RFQ", "rfp": "RFP"}[threshold[1].lower()]
        rules = [r for r in policy["rules"] if r["route"] == route]
        if len(rules) != 1:
            raise ValueError("Ambiguous route")
        value_condition(rules[0])["value"] = amount(threshold[2])
        # RFQ and RFP share one boundary in v5.0.
        if route in {"RFQ", "RFP"}:
            counterpart = "RFP" if route == "RFQ" else "RFQ"
            other = [r for r in policy["rules"] if r["route"] == counterpart]
            if len(other) != 1:
                raise ValueError("Ambiguous boundary")
            value_condition(other[0])["value"] = amount(threshold[2])
        return
    order = re.fullmatch(r"move clause ([\w.\-]+) (before|after) clause ([\w.\-]+)", sentence, re.I)
    if order:
        rules = policy["rules"]
        rule = next((r for r in rules if r["clause"] == order[1]), None)
        if rule is None or order[1] == order[3]:
            raise ValueError("Unknown clause")
        rules.remove(rule)
        target = next((i for i, r in enumerate(rules) if r["clause"] == order[3]), None)
        if target is None:
            raise ValueError("Unknown clause")
        rules.insert(target + (order[2].lower() == "after"), rule)
        return
    suggestion = re.fullmatch(r"(?:set )?clause ([\w.\-]+) suggestions to (\d+)", sentence, re.I)
    if suggestion:
        rule = next((r for r in policy["rules"] if r["clause"] == suggestion[1]), None)
        if rule is None:
            raise ValueError("Unknown clause")
        rule["suggestions"] = int(suggestion[2])
        return
    # General rule addition with several composable facts and explicit priority.
    addition = re.fullmatch(r"add clause ([\w.\-]+) (before|after) clause ([\w.\-]+):? "
                           r"(?:when|for) (.+), (?:use|require) ([A-Z_]+)(?:, suggest (\d+))?", sentence, re.I)
    if addition:
        clause, direction, anchor, text, route, count = addition.groups()
        route = route.upper()
        if route not in ROUTES or any(r["clause"] == clause for r in policy["rules"]):
            raise ValueError("Invalid new rule")
        conditions = []
        for part in re.split(r"\s+and\s+", text, flags=re.I):
            part = part.strip()
            fact = re.fullmatch(r'(plant|category|item type) (?:is |equals )?["\']?(.+?)["\']?', part, re.I)
            boolean = re.fullmatch(r"(emergency|has contract|has history|new item) (true|false)", part, re.I)
            value = re.fullmatch(r"value (below|at most|at least|above|equals) (.+)", part, re.I)
            if fact:
                conditions.append(dict(field=fact[1].lower().replace(" ", "_"), op="eq", value=fact[2]))
            elif boolean:
                conditions.append(dict(field=boolean[1].lower().replace(" ", "_"), op="eq", value=boolean[2].lower() == "true"))
            elif value:
                op = {"below": "lt", "at most": "lte", "at least": "gte", "above": "gt", "equals": "eq"}[value[1].lower()]
                conditions.append(dict(field="value", op=op, value=amount(value[2])))
            else:
                raise ValueError("Unrecognized condition")
        target = next((i for i, r in enumerate(policy["rules"]) if r["clause"] == anchor), None)
        if target is None:
            raise ValueError("Unknown priority clause")
        default_count = 3 if route == "RFQ" else 5 if route in {"RFP", "CAPEX_SOURCING"} else 0
        policy["rules"].insert(target + (direction.lower() == "after"),
                               dict(clause=clause, route=route, when={"all": conditions},
                                    suggestions=int(count) if count else default_count))
        return
    raise ValueError("Please review this statement manually")
