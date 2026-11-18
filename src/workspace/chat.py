"""Conversational PR drafts with catalogue grounding and explicit confirmation."""
import copy
import html
import json
import re
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.engine import service
from src.engine.core import normalized
from src.imports.load import valid_number
from src.policy.store import key, query
from .auth import scalar

IST = ZoneInfo("Asia/Kolkata")
FIELDS = ["item", "quantity", "plant", "need_by_date", "urgency", "uom", "estimated_unit_price"]


def today():
    return datetime.now(IST).date()


def candidates(description, items):
    def singular(tokens):
        return {token[:-1] if len(token)>3 and token.endswith("s") else token for token in tokens}
    tokens = singular(normalized(description))
    if not tokens:
        return []
    numeric = re.findall(r"\d+(?:\.\d+)?", description)
    return [r for r in items if tokens <= singular(normalized(r["description"]))
            and (not numeric or numeric == re.findall(r"\d+(?:\.\d+)?",r["description"]))]


def date_phrase(text, base):
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b",text)
    if match:
        try: return date.fromisoformat(match[0]), match[0]
        except ValueError: return None, match[0]
    for word, days in [("tomorrow",1),("today",0)]:
        if re.search(r"\b"+word+r"\b",text,re.I):
            return base+timedelta(days=days),word
    match=re.search(r"\bin (\d+) days?\b",text,re.I)
    if match:
        days=int(match[1])
        if days<=36500:return base+timedelta(days=days),match[0]
        return None,match[0]
    weekdays="monday tuesday wednesday thursday friday saturday sunday".split()
    match=re.search(r"\b(?:next )?("+"|".join(weekdays)+r")\b",text,re.I)
    if match:
        target=weekdays.index(match[1].lower())
        return base+timedelta(days=(target-base.weekday())%7 or 7),match[0]
    return None,""


def advance(state, message, tables, as_of=None):
    as_of=as_of or today()
    state=copy.deepcopy(state)
    fields=state.setdefault("fields",{})
    state["ready"]=False
    if not isinstance(message,str):
        raise ValueError("Enter a text message")
    text=html.unescape(message).strip().strip("\\").strip()
    text=re.sub(r"^(?:type|enter|say)\s+(?=(?:please\s+)?(?:i\s+|we\s+)?(?:need|want|order|buy)\b)","",text,flags=re.I)
    if not text or len(text)>4000:
        raise ValueError("Enter a message of 1–4000 characters")
    if text.lower() in {"restart","start over"}:
        return {"fields":{},"ready":False,"awaiting":"item"}, "What item do you need? Include quantity, plant and need-by date if you know them."
    remaining=text
    warnings=[]
    parsed_date, phrase=date_phrase(text,as_of)
    if phrase:
        remaining=re.sub(re.escape(phrase)," ",remaining,flags=re.I)
        if parsed_date is None or parsed_date<as_of:
            warnings.append("The need-by date must be a valid date today or later.")
            fields.pop("need_by_date",None)
        else:fields["need_by_date"]=parsed_date.isoformat()
    urgency=re.search(r"\b(emergency|urgent|normal)\b",text,re.I)
    if urgency:
        fields["urgency"]="Normal" if urgency[1].lower()=="normal" else "Emergency"
        remaining=re.sub(r"\b(emergency|urgent|normal)\b"," ",remaining,flags=re.I)
    plant_hits=[]
    for plant in tables["plants"]:
        aliases=[plant["plant"]]+[w for w in re.findall(r"[A-Za-z0-9]+",plant["name"]) if w.lower() not in {"plant","the"}]
        for alias in aliases:
            if re.search(r"\b"+re.escape(alias)+r"\b",text,re.I):
                plant_hits.append(plant["plant"])
                remaining=re.sub(r"\b"+re.escape(alias)+r"\b"," ",remaining,flags=re.I)
    if len(set(plant_hits))==1:fields["plant"]=plant_hits[0]
    elif plant_hits:
        fields.pop("plant",None);warnings.append("Choose one plant for this request.")
    estimate=re.search(r"(?:estimate(?:d)?(?: unit price)?|price|cost)\s*(?:is|of|to|:)?\s*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d+)?)",text,re.I)
    if estimate:
        fields["estimated_unit_price"]=estimate[1].replace(",","")
        remaining=remaining.replace(estimate[0]," ")
    elif state.get("awaiting")=="estimated_unit_price" and re.fullmatch(r"(?:INR|Rs\.?|₹)?\s*[\d,]+(?:\.\d+)?",text,re.I):
        fields["estimated_unit_price"]=re.sub(r"[^\d.]","",text);remaining=""
    quantity=re.search(r"(?:quantity|qty|need|want|order|buy)\s*(?:is|to|of|:)?\s*(-?[\d,]+(?:\.\d+)?)\b",text,re.I)
    if not quantity and (not fields.get("quantity") or state.get("awaiting")=="quantity"):
        quantity=re.match(r"\s*(-?\d+(?:\.\d+)?)(?=\s|$)",text)
    if quantity and not (state.get("awaiting")=="estimated_unit_price" and not re.search(r"quantity|qty|need|want|order|buy",text,re.I)):
        fields["quantity"]=quantity[1].replace(",","")
        remaining=remaining.replace(quantity[0]," ",1)
    unit=re.search(r"\b(NOS|KG|LTR|MTR|PAIR|SET|ROL|REAM|JOB|MON|BKT|BRL|QTR|CYL|pieces|units|pairs)\b",remaining,re.I)
    if unit:
        fields["uom"]={"PIECES":"NOS","UNITS":"NOS","PAIRS":"PAIR"}.get(unit[0].upper(),unit[0].upper())
        remaining=remaining.replace(unit[0]," ",1)
    item_change=re.search(r"change (?:the )?item to|instead|\bneed\b|\bwant\b|\border\b|\bbuy\b",text,re.I)
    proposed_description=re.sub(r"change (?:the )?item to|instead", "", remaining,flags=re.I)
    proposed_description=re.sub(r"\b(i|we|it|please|need|want|order|buy|for|at|by|in|plant|deliver|delivered|to|a|an)\b"," ",proposed_description,flags=re.I).strip(" ,.-")
    if fields.get("item_code") and item_change and proposed_description.strip():
        fields.pop("item_code",None)
        fields.pop("uom",None)
        state["awaiting"]="item"
        remaining=proposed_description
    codes=[r for r in tables["items"] if re.search(r"\b"+re.escape(r["item_code"])+r"\b",text,re.I)]
    if len(codes)==1:
        item=codes[0];fields.update(item_code=item["item_code"],description=item["description"],uom=item["uom"])
        state.pop("choices",None);state.pop("unknown",None)
    elif re.fullmatch(r"(?:yes[, ]+)?(?:this is a )?new item",text,re.I) and state.get("unknown"):
        fields["item_code"]="NEW";state.pop("unknown",None)
    elif not fields.get("item_code") and state.get("awaiting","item") in {"item","item_choice"}:
        description=re.sub(r"\b(i|we|please|need|want|order|buy|for|at|by|in|plant|deliver|delivered|to|a|an)\b"," ",remaining,flags=re.I)
        description=" ".join(description.strip(" ,.-").split())
        if description:
            found=candidates(description,tables["items"])
            if len(found)==1:
                item=found[0];fields.update(item_code=item["item_code"],description=item["description"],uom=item["uom"])
                state.pop("choices",None);state.pop("unknown",None)
            else:
                fields["description"]=description
                state["choices"]=[{"item_code":r["item_code"],"description":r["description"]} for r in found[:12]]
                state["unknown"]=not found
    if unit:
        fields["uom"]={"PIECES":"NOS","UNITS":"NOS","PAIRS":"PAIR"}.get(unit[0].upper(),unit[0].upper())
    expected_unit=next((r["uom"] for r in tables["items"] if r["item_code"]==fields.get("item_code")),None)
    if not fields.get("item_code"):
        state["awaiting"]="item_choice"
        if state.get("choices"):
            response="Which item did you mean? Reply with its item code. "+"; ".join(
                r["item_code"]+" — "+r["description"] for r in state["choices"])
        elif state.get("unknown"):
            response=f'I could not match “{fields.get("description","")}” to the catalogue. Give more detail or reply “new item” to request it through sourcing.'
        else:response="What item do you need? A description or item code will help."
    elif not valid_number(fields.get("quantity",""),0,strict=True):
        state["awaiting"]="quantity";response="What quantity do you need? Enter a positive number."
    elif not fields.get("plant"):
        state["awaiting"]="plant";response="Which plant? "+", ".join(r["plant"]+" ("+r["name"]+")" for r in tables["plants"])
    elif not fields.get("need_by_date"):
        state["awaiting"]="need_by_date";response="When do you need it? Use YYYY-MM-DD, tomorrow or a weekday."
    elif not fields.get("urgency"):
        state["awaiting"]="urgency";response="Is this Normal or Emergency?"
    elif expected_unit and fields.get("uom")!=expected_unit:
        state["awaiting"]="uom";response=f"The catalogue uses {expected_unit} for this item. Please provide the quantity in {expected_unit} and confirm that unit."
    elif not fields.get("uom"):
        state["awaiting"]="uom";response="What unit should be used? For example NOS, KG, LTR or PAIR."
    elif fields["item_code"]=="NEW" and not valid_number(fields.get("estimated_unit_price",""),0):
        state["awaiting"]="estimated_unit_price";response="What is the estimated unit price in INR, excluding GST?"
    else:
        state["awaiting"]="confirm";state["ready"]=True
        response=(f'Please confirm {fields["quantity"]} {fields["uom"]} of {fields["description"]}, '
                  f'for {fields["plant"]}, needed by {fields["need_by_date"]}, urgency {fields["urgency"]}.')
        if fields["item_code"]=="NEW":response+=f' Estimated unit price INR {fields["estimated_unit_price"]}. This is a new item for sourcing.'
        response+=" You can change the details in chat or select Confirm request."
    return state," ".join(warnings+[response])


def get(conversation_id,user):
    cid=key(conversation_id)
    raw=query(f"SELECT row_to_json(c) FROM corvo.conversations c WHERE conversation_id='{cid}' AND username={scalar(user['username'])};")
    if not raw:raise ValueError("Conversation not found")
    return json.loads(raw)


def create(user):
    cid=str(uuid4())
    state={"fields":{},"ready":False,"awaiting":"item"}
    messages=[dict(role="assistant",text="What do you need? Tell me the item, quantity, plant and need-by date.")]
    query(f"INSERT INTO corvo.conversations(conversation_id,username,state,messages) VALUES ('{cid}',"
          f"{scalar(user['username'])},{service.document(state)},{service.document(messages)});")
    return get(cid,user)


def send(conversation_id,user,revision,message):
    draft=get(conversation_id,user)
    if draft["pr_no"]:raise ValueError("This request was confirmed. Start a new request.")
    if type(revision) is not int:raise ValueError("Revision must be an integer")
    state,response=advance(draft["state"],message,service.snapshot())
    messages=draft["messages"]+[dict(role="user",text=message),dict(role="assistant",text=response)]
    cid=draft["conversation_id"]
    sql=f"""BEGIN;
DO $body$ BEGIN
 UPDATE corvo.conversations SET state={service.document(state)},messages={service.document(messages)},revision=revision+1
 WHERE conversation_id='{cid}' AND username={scalar(user['username'])} AND revision={revision} AND pr_no IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'POLICY_CONFLICT: Conversation changed. Reload it.'; END IF;
END $body$; COMMIT;"""
    query(sql)
    return get(cid,user)


def confirm(conversation_id,user,revision):
    draft=get(conversation_id,user)
    if draft["pr_no"]:return draft
    if type(revision) is not int:raise ValueError("Revision must be an integer")
    if not draft["state"].get("ready"):raise ValueError("Complete the details before confirming")
    fields=draft["state"]["fields"]
    if date.fromisoformat(fields["need_by_date"])<today():raise ValueError("Need-by date has passed. Update it in chat.")
    cid=draft["conversation_id"];pr_no="CHAT-"+cid
    row=dict(pr_no=pr_no,pr_line="1",created_at=datetime.now(IST).strftime("%Y-%m-%d %H:%M"),
             requester=user["display_name"],department="Chat",plant=fields["plant"],
             item_code="" if fields["item_code"]=="NEW" else fields["item_code"],
             description=fields["description"],quantity=fields["quantity"],uom=fields["uom"],
             estimated_unit_price=fields.get("estimated_unit_price",""),
             need_by_date=fields["need_by_date"],urgency=fields["urgency"])
    sql=f"""BEGIN; SELECT pg_advisory_xact_lock(53422017);
DO $body$ DECLARE d corvo.conversations%ROWTYPE; BEGIN
 SELECT * INTO d FROM corvo.conversations WHERE conversation_id='{cid}' FOR UPDATE;
 IF d.pr_no IS NOT NULL THEN RETURN; END IF;
 IF d.revision <> {revision} THEN RAISE EXCEPTION 'POLICY_CONFLICT: Conversation changed. Review it again.'; END IF;
 INSERT INTO corvo.requests(pr_no,conversation_id,username,input)
 VALUES ({scalar(pr_no)},'{cid}',{scalar(user['username'])},{service.document(row)});
 UPDATE corvo.conversations SET pr_no={scalar(pr_no)} WHERE conversation_id='{cid}';
 INSERT INTO corvo.workspace_events(actor,action,entity,detail)
 VALUES ({scalar(user['username'])},'request_confirmed','{cid}',{service.document(row)});
END $body$; COMMIT;"""
    query(sql)
    return get(cid,user)
