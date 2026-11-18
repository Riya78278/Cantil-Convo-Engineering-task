"""Pure, deterministic procurement evaluation using immutable reference snapshots."""
import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext

from src.imports.load import batch_lines, valid_number
from .policy import matches, select, validate

COLUMNS = "pr_no pr_line resolved_item_code group_id po_id decision route supplier_id unit_price group_value_inr suggested_supplier_ids policy_version reason".split()
D = Decimal


def money(value):
    return str(value.quantize(D("0.01"), rounding=ROUND_HALF_UP))


def normalized(description):
    text = description.lower().replace("×", "x").replace('"', " inch ")
    text = re.sub(r"as per sample|for maintenance|\(repeat\)|\burgent\b", " ", text)
    aliases = {"brg": "bearing", "hyd": "hydraulic", "pneu": "pneumatic", "rod": "electrode"}
    words = re.findall(r"[a-z]+|\d+(?:\.\d+)?", text)
    return set(aliases.get(word, word) for word in words if word not in {"for", "of", "the", "and"})


class Engine:
    def __init__(self, tables, policies):
        self.policies = [validate(p) for p in policies]
        if len({p["version"] for p in policies}) != len(policies):
            raise ValueError("Policy versions must be unique")
        self.items = {r["item_code"]: r for r in tables["items"]}
        self.suppliers = {r["supplier_id"]: r for r in tables["suppliers"]}
        self.plants = {r["plant"] for r in tables["plants"]}
        self.history = defaultdict(list)
        for row in tables["po_history"]:
            self.history[row["item_code"]].append(row)
        for rows in self.history.values():
            rows.sort(key=lambda r: (r["po_date"], r["po_number"]), reverse=True)
        contracts = {r["contract_id"]: r for r in tables["contracts"]}
        self.opening = {cid: D(str(r["consumed_value"])) for cid, r in contracts.items()}
        self.contracts = defaultdict(list)
        for rate in tables["contract_lines"]:
            self.contracts[rate["item_code"]].append({**contracts[rate["contract_id"]], **rate})
        self.tokens = {code: normalized(r["description"]) for code, r in self.items.items()}

    def active(self, supplier_id):
        return self.suppliers[supplier_id]["status"] == "Active"

    def resolve(self, line):
        if line["item_code"]:
            return line["item_code"], None
        tokens = normalized(line["description"])
        exact = [code for code, item in self.tokens.items() if item == tokens
                 and re.findall(r"\d+(?:\.\d+)?", line["description"])
                 == re.findall(r"\d+(?:\.\d+)?", self.items[code]["description"])]
        candidates = exact or [code for code, item in self.tokens.items() if tokens and tokens <= item
                                and (not re.findall(r"\d+(?:\.\d+)?", line["description"])
                                     or re.findall(r"\d+(?:\.\d+)?", line["description"])
                                     == re.findall(r"\d+(?:\.\d+)?", self.items[code]["description"]))]
        if len(candidates) == 1:
            return candidates[0], None
        if candidates:
            return "", "ambiguous item description"
        return "NEW", None

    def recent(self, item, as_of, days):
        start = (as_of - timedelta(days=days)).isoformat()
        end = as_of.isoformat()
        return [r for r in self.history[item] if start <= r["po_date"] < end]

    def preferred(self, item, as_of, policy):
        history = self.recent(item, as_of, policy["preferred_days"])
        contracts = [r for r in self.contracts[item] if r["start_date"] <= as_of.isoformat()]
        by_supplier = defaultdict(list)
        for row in history:
            by_supplier[row["supplier_id"]].append(row)
        candidates = {r["supplier_id"] for r in history + contracts if self.active(r["supplier_id"])}
        maximum = max((len(by_supplier[s]) for s in candidates), default=0)
        ranking = []
        for sid in sorted(candidates):
            supplier = self.suppliers[sid]
            purchases = by_supplier[sid]
            held = sorted([r for r in contracts if r["supplier_id"] == sid],
                          key=lambda r: (r["start_date"], r["contract_id"]), reverse=True)
            age = (as_of - date.fromisoformat(purchases[0]["po_date"])).days if purchases else 1000000
            factors = dict(frequency=D(len(purchases)) / maximum if maximum else D(0),
                           recency=D(1) if age <= 90 else D("0.5") if age <= 365 else D(0),
                           contract=D(bool(held)), delivery=D(str(supplier["on_time_pct"])) / 100,
                           quality=D(str(supplier["quality_rating"])) / 5)
            source = purchases[0] if purchases else held[0]
            price = source["unit_price"] if purchases else source["rate"]
            ranking.append(dict(supplier_id=sid, reference_price=D(str(price)),
                                source=("PO " + source["po_number"]) if purchases else ("contract " + source["contract_id"]),
                                score=sum(factors[k] * D(str(policy["ranking_weights"][k])) for k in factors),
                                factors=factors))
        return sorted(ranking, key=lambda r: (-r["score"], r["supplier_id"]))

    def suggestions(self, item, ranked, count):
        selected = [r["supplier_id"] for r in ranked[:count]]
        category = self.items[item]["category"]
        others = [r for sid, r in self.suppliers.items() if self.active(sid) and sid not in selected
                  and category in re.split(r"\s*[;|]\s*", r["categories"])]
        others.sort(key=lambda r: (-(D(str(r["on_time_pct"])) / 100 + D(str(r["quality_rating"])) / 5),
                                   r["supplier_id"]))
        return (selected + [r["supplier_id"] for r in others])[:count]

    def process(self, content):
        with localcontext() as context:
            context.prec = 50
            return self._process(content)

    def _process(self, content):
        _, lines = batch_lines(content, {c: r["uom"] for c, r in self.items.items()}, self.plants)
        output = [{k: "" for k in COLUMNS} for _ in lines]
        accepted = []
        for line in lines:
            r, index = line["input"], line["row_no"] - 1
            result = output[index]
            result.update(pr_no=r["pr_no"], pr_line=r["pr_line"], route="NOT_PROCESSED")
            code, issue = ("", line["issue"]) if line["issue"] else self.resolve(r)
            result["resolved_item_code"] = code or (r["item_code"] if r["item_code"] in self.items else "")
            if code in self.items and r["uom"] != self.items[code]["uom"]:
                issue = "unit differs from matched item"
            if code == "NEW" and not valid_number(r["estimated_unit_price"], 0):
                issue = "new item requires estimated unit price"
            if issue:
                result["reason"] = issue
                continue
            created = datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M")
            try:
                policy = select(self.policies, created.date())
            except ValueError as exc:
                result["reason"] = str(exc)
                continue
            accepted.append(dict(row=r, index=index, code=code, created=created, policy=policy,
                                 need=date.fromisoformat(r["need_by_date"])))
        accepted.sort(key=lambda r: (r["created"], r["row"]["pr_no"], int(r["row"]["pr_line"])))
        groups = []
        for line in accepted:
            p = line["policy"]
            eligible = [g for g in groups if line["code"] != "NEW" and g["code"] == line["code"]
                        and g["row"]["plant"] == line["row"]["plant"] and g["policy"]["version"] == p["version"]
                        and (line["created"].date() - g["created"].date()).days <= p["creation_days"]
                        and abs((line["need"] - g["need"]).days) <= p["need_by_days"]]
            if eligible:
                eligible[0]["lines"].append(line)
            else:
                groups.append({**line, "lines": [line], "group_id": f"REQ-{len(groups)+1:04d}"})
        balances = self.opening.copy()
        pos, traces = [], []
        for group in groups:
            result, trace = self.evaluate(group, balances)
            if result["decision"] == "PO":
                key = (result["supplier_id"], group["row"]["plant"], result["route"])
                eligible = [po for po in pos if po["key"] == key
                            and group["created"] - po["created"] <= timedelta(hours=group["policy"]["po_hours"])]
                if eligible:
                    po = eligible[0]
                else:
                    po = dict(key=key, created=group["created"], id=f"PO-{len(pos)+1:04d}")
                    pos.append(po)
                result["po_id"] = po["id"]
            for line in group["lines"]:
                output[line["index"]].update(result)
            traces.append(trace)
        return dict(decisions=output, requirements=traces,
                    closing_contract_balances={k: str(v) for k, v in sorted(balances.items())})

    def evaluate(self, group, balances):
        item, policy, as_of = group["code"], group["policy"], group["created"].date()
        quantity = sum(D(line["row"]["quantity"]) for line in group["lines"])
        applicable = sorted([r for r in self.contracts[item] if self.active(r["supplier_id"])
                             and r["start_date"] <= as_of.isoformat() <= r["end_date"]],
                            key=lambda r: (D(str(r["rate"])), r["contract_id"]))
        usable = next((r for r in applicable if balances[r["contract_id"]] + quantity * D(str(r["rate"]))
                       <= D(str(r["value_cap"]))), None)
        history = self.recent(item, as_of, policy["history_days"])
        ranked = self.preferred(item, as_of, policy) if item != "NEW" else []
        if item == "NEW":
            rate, valuation = D(group["row"]["estimated_unit_price"]), "requester estimate"
        elif applicable:
            contract = usable or applicable[0]
            rate, valuation = D(str(contract["rate"])), "contract " + contract["contract_id"]
        elif history:
            rate, valuation = D(str(history[0]["unit_price"])), "PO " + history[0]["po_number"]
        else:
            rate, valuation = D(str(self.items[item]["standard_price"])), "item standard price"
        value = quantity * rate
        facts = dict(item_type=self.items.get(item, {}).get("item_type", ""),
                     category=self.items.get(item, {}).get("category", ""), plant=group["row"]["plant"],
                     value=value, emergency=any(l["row"]["urgency"] == "Emergency" for l in group["lines"]),
                     has_contract=usable is not None, has_history=any(self.active(r["supplier_id"]) for r in history),
                     new_item=item == "NEW")
        rule = next((r for r in policy["rules"] if matches(r["when"], facts)), None)
        if rule is None:
            raise ValueError(f"Policy {policy['version']} has no matching rule")
        route = rule["route"]
        result = dict(group_id=group["group_id"], decision="PO" if route.endswith("_PO") else "SOURCING_REQUEST",
                      route=route, policy_version=policy["version"], group_value_inr=money(value),
                      supplier_id="", unit_price="", suggested_supplier_ids="", po_id="")
        evidence = ""
        if route == "CONTRACT_PO":
            if usable is None:
                raise ValueError("Policy requests a contract PO without an eligible contract")
            result.update(supplier_id=usable["supplier_id"], unit_price=money(D(str(usable["rate"]))))
            balances[usable["contract_id"]] += quantity * D(str(usable["rate"]))
            evidence = "award contract " + usable["contract_id"]
        elif route in {"SPOT_PO", "EMERGENCY_PO"}:
            if not ranked:
                raise ValueError("Policy requests a preferred PO without an eligible supplier")
            result.update(supplier_id=ranked[0]["supplier_id"], unit_price=money(ranked[0]["reference_price"]))
            evidence = "award " + ranked[0]["source"]
        elif item != "NEW":
            result["suggested_supplier_ids"] = ";".join(self.suggestions(item, ranked, rule["suggestions"]))
        result["reason"] = f"Policy {policy['version']} clause {rule['clause']}; valuation {valuation}"
        if evidence:
            result["reason"] += "; " + evidence
        if route == "CAPEX_SOURCING":
            result["reason"] += "; capex approval required"
        trace = dict(group_id=group["group_id"], contract_id=usable["contract_id"] if route == "CONTRACT_PO" else None,
                     plant=group["row"]["plant"], item_code=item, requirement_date=as_of.isoformat(),
                     created_at=group["created"].isoformat(), input_rows=[l["index"] + 1 for l in group["lines"]],
                     quantity=str(quantity), valuation_unit_price=str(rate), value=str(value),
                     clause=rule["clause"], policy_version=policy["version"],
                     preferred=[{**r, "reference_price": str(r["reference_price"]), "score": str(r["score"]),
                                 "factors": {k: str(v) for k, v in r["factors"].items()}} for r in ranked])
        return result, trace


def export_csv(rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()
