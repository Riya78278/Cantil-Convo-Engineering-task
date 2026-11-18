"""Validated declarative procurement policies. No evaluation of executable expressions."""
from datetime import date
from decimal import Decimal

FIELDS = {"item_type", "category", "plant", "value", "emergency", "has_contract", "has_history", "new_item"}
ROUTES = {"CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO", "RFQ", "RFP", "CAPEX_SOURCING", "NEW_ITEM_SOURCING"}


def validate(policy):
    """Reject unknown fields and malformed types before a policy enters the engine."""
    required = {"version", "effective_date", "creation_days", "need_by_days", "po_hours",
                "history_days", "preferred_days", "ranking_weights", "rules"}
    if not isinstance(policy, dict) or set(policy) != required:
        raise ValueError("Policy must contain exactly the documented fields")
    try:
        parsed = date.fromisoformat(policy["effective_date"])
        if parsed.isoformat() != policy["effective_date"]:
            raise ValueError("Effective date must use YYYY-MM-DD")
    except (TypeError, ValueError):
        raise ValueError("Effective date must use YYYY-MM-DD") from None
    if not isinstance(policy["version"], str) or not policy["version"].strip() or len(policy["version"]) > 100:
        raise ValueError("Policy needs a version of at most 100 characters")
    for key in ["creation_days", "need_by_days", "po_hours", "history_days", "preferred_days"]:
        if type(policy[key]) is not int or not 0 <= policy[key] <= 100000:
            raise ValueError(f"Invalid window: {key}")
    def numeric(value):
        try:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError()
            number = Decimal(str(value))
            if not number.is_finite() or not 0 <= number <= Decimal("1e30"):
                raise ValueError()
            return number
        except (ValueError, ArithmeticError):
            raise ValueError("Numeric policy values must be finite, nonnegative and at most 1e30") from None
    weights = policy["ranking_weights"]
    if not isinstance(weights, dict) or set(weights) != {"frequency", "recency", "contract", "delivery", "quality"}:
        raise ValueError("Invalid ranking factors")
    if sum(numeric(v) for v in weights.values()) != 1:
        raise ValueError("Ranking weights must sum to one")

    def condition(node, depth=0):
        if depth > 12 or not isinstance(node, dict):
            raise ValueError("Invalid or overly nested condition")
        if set(node) in ({"all"}, {"any"}):
            children = next(iter(node.values()))
            if not isinstance(children, list) or len(children) > 100:
                raise ValueError("Condition children must be a list with at most 100 entries")
            for child in children:
                condition(child, depth + 1)
            return
        if set(node) != {"field", "op", "value"}:
            raise ValueError("Invalid condition structure")
        field, value, op = node["field"], node["value"], node["op"]
        if not isinstance(field, str) or field not in FIELDS or op not in {"eq", "lt", "lte", "gt", "gte", "in"}:
            raise ValueError("Unsupported condition")
        values = value if op == "in" else [value]
        if not isinstance(values, list) or not values or len(values) > 100:
            raise ValueError("Membership requires a nonempty list of at most 100 entries")
        for member in values:
            if field == "value":
                numeric(member)
            elif field in {"emergency", "has_contract", "has_history", "new_item"}:
                if type(member) is not bool or op not in {"eq", "in"}:
                    raise ValueError("Boolean condition requires equality or membership")
            elif not isinstance(member, str) or not member or op not in {"eq", "in"}:
                raise ValueError("Text condition requires equality or membership")
    if not isinstance(policy["rules"], list) or not 1 <= len(policy["rules"]) <= 100:
        raise ValueError("Policy needs between 1 and 100 rules")
    seen = set()
    for rule in policy["rules"]:
        if not isinstance(rule, dict) or set(rule) != {"clause", "route", "when", "suggestions"}:
            raise ValueError("Rule must contain clause, route, when and suggestions")
        clause = rule["clause"]
        if not isinstance(clause, str) or not clause.strip() or clause in seen:
            raise ValueError("Each rule needs a unique clause")
        seen.add(clause)
        if not isinstance(rule["route"], str) or rule["route"] not in ROUTES:
            raise ValueError("Unsupported route")
        condition(rule["when"])
        if type(rule["suggestions"]) is not int or not 0 <= rule["suggestions"] <= 100:
            raise ValueError("Invalid suggestion count")
    return policy


def matches(node, facts):
    if "all" in node:
        return all(matches(child, facts) for child in node["all"])
    if "any" in node:
        return any(matches(child, facts) for child in node["any"])
    actual, expected, op = facts[node["field"]], node["value"], node["op"]
    if node["field"] == "value":
        expected = [Decimal(str(v)) for v in expected] if op == "in" else Decimal(str(expected))
    if op == "in":
        return actual in expected
    if op == "eq":
        return actual == expected
    if op == "lt":
        return actual < expected
    if op == "lte":
        return actual <= expected
    if op == "gt":
        return actual > expected
    return actual >= expected


def select(policies, as_of):
    eligible = [p for p in policies if p["effective_date"] <= as_of.isoformat()]
    if not eligible:
        raise ValueError(f"No effective policy for {as_of}")
    return max(enumerate(eligible), key=lambda pair: (pair[1]["effective_date"], pair[0]))[1]
