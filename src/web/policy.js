"use strict";
const $ = (id) => document.getElementById(id);
let current = null;
let candidate = null;
let dirty = false;
let jsonDirty = false;
const routes = ["CONTRACT_PO","EMERGENCY_PO","SPOT_PO","RFQ","RFP","CAPEX_SOURCING","NEW_ITEM_SOURCING"];

function message(text, error = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
}
async function api(url, payload) {
  const response = await fetch(url, payload === undefined ? {} : {
    method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}
function actor() {
  const value = $("actor").value.trim();
  if (!value) throw new Error("Enter your name for the audit record.");
  return value;
}
function button(text, action) {
  const node = document.createElement("button");
  node.type = "button";
  node.textContent = text;
  node.addEventListener("click", () => perform(node, action));
  return node;
}
async function perform(node, action) {
  node.disabled = true;
  try { await action(); }
  catch (error) { message(error.message, true); }
  finally { node.disabled = false; if (current) controls(); }
}
function controls() {
  const approved = current.status === "approved";
  $("save").disabled = approved || jsonDirty;
  $("approve").disabled = approved || dirty || jsonDirty || !current.reviewed;
  $("add-rule").disabled = approved;
  $("apply-json").disabled = approved;
  $("policy-json").disabled = approved;
  $("reviewed").disabled = approved;
  $("approval-help").textContent = approved
    ? "Approved by " + current.approved_by + " · effective " + current.candidate.effective_date
    : dirty || jsonDirty ? "Save your reviewed changes before approval."
    : current.reviewed ? "Approval makes this version available from its effective date."
    : "Check the source and save a reviewed draft to enable approval.";
}
function changed() {
  dirty = true;
  current.reviewed = false;
  $("reviewed").checked = false;
  $("policy-json").value = JSON.stringify(candidate, null, 2);
  controls();
}
function renderRules() {
  $("rules").replaceChildren();
  candidate.rules.forEach((rule, index) => {
    const box = document.createElement("div"); box.className = "rule";
    const bar = document.createElement("div"); bar.className = "rule-bar";
    const clause = document.createElement("input"); clause.value = rule.clause;
    clause.setAttribute("aria-label", "Clause identifier");
    clause.addEventListener("change", () => { rule.clause = clause.value; changed(); });
    const route = document.createElement("select"); route.setAttribute("aria-label", "Outcome route");
    routes.forEach(value => route.add(new Option(value, value)));
    route.value = rule.route;
    route.addEventListener("change", () => {rule.route = route.value; changed();});
    const up = button("↑", () => {
      [candidate.rules[index-1],candidate.rules[index]] = [candidate.rules[index],candidate.rules[index-1]];
      changed(); renderRules();
    });
    up.setAttribute("aria-label","Move rule earlier"); up.disabled = index === 0;
    const down = button("↓", () => {
      [candidate.rules[index+1],candidate.rules[index]] = [candidate.rules[index],candidate.rules[index+1]];
      changed(); renderRules();
    });
    down.setAttribute("aria-label","Move rule later"); down.disabled = index === candidate.rules.length-1;
    const remove = button("Remove", () => {candidate.rules.splice(index,1); changed(); renderRules();});
    bar.append(clause,route,up,down,remove); box.append(bar);
    const label = document.createElement("small");
    label.textContent = "Conditions · all / any groups of field, operator and value";
    box.append(label);
    const when = document.createElement("textarea"); when.rows = 4;
    when.value = JSON.stringify(rule.when, null, 2); when.setAttribute("aria-label","Rule conditions");
    when.addEventListener("input", () => {
      try { rule.when = JSON.parse(when.value); when.setCustomValidity(""); changed(); }
      catch { when.setCustomValidity("Enter valid condition JSON."); dirty = true; controls(); }
    });
    box.append(when);
    const countLabel = document.createElement("label"); countLabel.textContent = "Supplier suggestions";
    const count = document.createElement("input"); count.className = "count"; count.type="number"; count.min="0"; count.max="100";
    count.value = rule.suggestions; count.addEventListener("input", () => {rule.suggestions = Number(count.value); changed();});
    countLabel.append(count); box.append(countLabel);
    if (current.status === "approved") box.querySelectorAll("input,textarea,select,button").forEach(n => n.disabled=true);
    $("rules").append(box);
  });
}
function renderDraft(draft) {
  current = draft; candidate = structuredClone(draft.candidate); dirty = false; jsonDirty = false;
  $("empty").hidden = true; $("review").hidden = false;
  $("draft-title").textContent = "Policy " + candidate.version;
  $("draft-status").textContent = draft.status + " · revision " + draft.revision;
  $("source-name").textContent = draft.source_name + " · based on " + draft.base_version;
  $("source").textContent = draft.source_text;
  $("unresolved-box").hidden = !draft.unresolved.length;
  $("unresolved").replaceChildren(...draft.unresolved.map(text => {
    const li=document.createElement("li"); li.textContent=text; return li;
  }));
  $("changes").replaceChildren(...draft.changes.map(text => {
    const li=document.createElement("li"); li.textContent=text; return li;
  }));
  $("policy-json").value = JSON.stringify(candidate,null,2);
  $("reviewed").checked = draft.reviewed;
  $("audit").replaceChildren(...draft.audit.map(event => {
    const p=document.createElement("p"); p.textContent=event.action+" · "+event.actor+" · "+event.occurred_at; return p;
  }));
  renderRules(); controls();
}
async function refresh() {
  const data = await api("/api/policies");
  for (const id of ["base","run-policy","preferred-policy"]) {
    const previous = $(id).value;
    $(id).replaceChildren();
    if (id !== "base") $(id).add(new Option("All approved versions by effective date", ""));
    data.policies.forEach(p => $(id).add(new Option("v"+p.version+" · "+p.effective_date,p.version)));
    if (Array.from($(id).options).some(o => o.value === previous)) $(id).value=previous;
    else if(id === "base") $(id).value=data.policies.at(-1)?.version || "";
  }
  $("versions").replaceChildren(...data.policies.map(p => {
    const row=document.createElement("div"); row.className="history-row";
    const text=document.createElement("span"); text.textContent="Version "+p.version;
    const small=document.createElement("small"); small.textContent="Effective "+p.effective_date;
    text.append(small); row.append(text);
    const badge=document.createElement("span"); badge.className="pill"; badge.textContent="Approved"; row.append(badge);
    return row;
  }));
  $("drafts").replaceChildren(...data.drafts.map(d => {
    const row=document.createElement("div"); row.className="history-row";
    const text=document.createElement("span"); text.textContent=d.candidate.version+" · "+d.status;
    row.append(text,button("Open",async () => {renderDraft(await api("/api/policy-drafts/"+d.draft_id));}));
    return row;
  }));
}
$("proposal").addEventListener("submit", event => {
  event.preventDefault();
  perform(event.submitter, async () => {
    const payload={actor:actor(),base_version:$("base").value,version:$("version").value,effective_date:$("effective").value,text:$("amendment").value};
    const file=$("document").files[0];
    if (file && payload.text.trim()) throw new Error("Choose either a document or a chat amendment for this draft.");
    if(file) {
      if (file.size > 7*1024*1024) throw new Error("Upload a document of at most 7 MiB.");
      const bytes=new Uint8Array(await file.arrayBuffer()); let binary="";
      bytes.forEach(b => binary += String.fromCharCode(b));
      payload.file_base64=btoa(binary); payload.filename=file.name; delete payload.text;
    }
    renderDraft(await api("/api/policy-drafts",payload)); await refresh();
    message("Draft created. Check the source and proposed changes before approval.");
  });
});
$("add-rule").addEventListener("click",() => {
  candidate.rules.push({clause:"new-"+(candidate.rules.length+1),route:"RFQ",when:{all:[]},suggestions:3});
  changed(); renderRules();
});
$("policy-json").addEventListener("input",() => {jsonDirty=true;dirty=true;controls();});
$("apply-json").addEventListener("click",() => {
  try {
    const parsed=JSON.parse($("policy-json").value);
    if(!Array.isArray(parsed.rules)) throw new Error("Policy needs a rules array.");
    candidate=parsed;jsonDirty=false;changed();renderRules();
    message("Editor applied. Save the reviewed draft to validate and refresh the change preview.");
  } catch(error) {message(error.message,true);}
});
$("reviewed").addEventListener("change",() => {dirty=true;controls();});
$("save").addEventListener("click",event => perform(event.target,async () => {
  for(const field of $("rules").querySelectorAll("textarea,input")) if(!field.reportValidity()) throw new Error("Correct the highlighted rule field.");
  const reviewed=$("reviewed").checked;
  renderDraft(await api("/api/policy-drafts/"+current.draft_id+"/save",{
    actor:actor(),revision:current.revision,candidate,reviewed
  }));
  message(reviewed ? "Reviewed draft saved. Check the final change preview, then approve." : "Draft saved. Review it before approval.");
  await refresh();
}));
$("approve").addEventListener("click",event => perform(event.target,async () => {
  renderDraft(await api("/api/policy-drafts/"+current.draft_id+"/approve",{actor:actor(),revision:current.revision}));
  await refresh();message("Policy "+current.candidate.version+" approved. It applies from "+current.candidate.effective_date+".");
}));
$("batch-form").addEventListener("submit",event => {
  event.preventDefault();
  perform(event.submitter,async () => {
    const file=$("batch-file").files[0];if(!file) throw new Error("Select a PR batch CSV.");
    const query=$("run-policy").value ? "?policy_version="+encodeURIComponent($("run-policy").value) : "";
    const response=await fetch("/api/process-batch"+query,{method:"POST",headers:{"Content-Type":"text/csv"},body:file});
    const result=await response.json();if(!response.ok) throw new Error(result.error);
    const counts={};result.decisions.forEach(r => counts[r.route]=(counts[r.route]||0)+1);
    const summary=document.createElement("p");summary.textContent=result.decisions.length+" lines · "+
      Object.entries(counts).map(([route,count]) => route+" "+count).join(" · ");
    const id=document.createElement("p");id.textContent="Run "+result.run_id;
    const download=button("Download decisions CSV",() => {
      const fields=["pr_no","pr_line","resolved_item_code","group_id","po_id","decision","route","supplier_id","unit_price","group_value_inr","suggested_supplier_ids","policy_version","reason"];
      const quote=value => '"'+String(value??"").replaceAll('"','""')+'"';
      const csv=[fields.join(","),...result.decisions.map(r => fields.map(f => quote(r[f])).join(","))].join("\r\n")+"\r\n";
      const url=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));
      const a=document.createElement("a");a.href=url;a.download="decisions.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    $("batch-result").replaceChildren(summary,id,download);
    message("Batch processed. Its inputs, policy and results are saved.");
  });
});
$("preferred-form").addEventListener("submit", event => {
  event.preventDefault();
  perform(event.submitter, async () => {
    $("preferred-result").textContent = "";
    const query = new URLSearchParams({as_of: $("preferred-date").value});
    const version = $("preferred-policy").value;
    if (version) query.set("policy_version", version);
    const response = await fetch("/api/preferred-suppliers.csv?" + query);
    if (!response.ok) throw new Error((await response.json()).error || "Export failed");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url; link.download = "preferred.csv"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("preferred-result").textContent = "Downloaded preferred.csv for " + $("preferred-date").value + ".";
  });
});
api("/api/me").then(user => {
  if(user.role !== "procurement") throw new Error("Policy changes require the Procurement role.");
  $("actor").value = user.username; $("actor").readOnly = true; return refresh();
}).catch(error => {message(error.message,true); if(error.message.includes("sign in")) location.href="/";});
