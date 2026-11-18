"use strict";
const $ = id => document.getElementById(id);
let user=null,conversation=null,run=null,selected=null,catalogue=null;
function message(text,error=false){$("message").textContent=text;$("message").classList.toggle("error",error);}
async function api(path,payload){
 const response=await fetch(path,payload===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
 const result=await response.json();
 if(!response.ok){if(response.status===401){$("workspace").hidden=true;$("login-panel").hidden=false;$("account").hidden=true;}throw new Error(result.error);}
 return result;
}
async function perform(node,fn){node.disabled=true;try{await fn();}catch(e){message(e.message,true);}finally{node.disabled=false;}}
function element(tag,text,cls){const node=document.createElement(tag);node.textContent=text;if(cls)node.className=cls;return node;}
function action(text,fn){const b=element("button",text);b.type="button";b.addEventListener("click",()=>perform(b,fn));return b;}
function navigate(name){
 for(const key of ["chat","buyer","documents"]){$(key+"-panel").hidden=key!==name;$("nav-"+key).classList.toggle("selected",key===name);}
}
async function start(){
 user=await api("/api/me");catalogue=await api("/api/catalogue");
 $("login-panel").hidden=true;$("workspace").hidden=false;$("account").hidden=false;
 $("signed-in").textContent=user.display_name+" · "+user.role;
 for(const id of ["nav-buyer","nav-documents"])$(id).hidden=user.role==="employee";
 $("nav-policy").hidden=user.role!=="procurement";
 $("override-supplier").replaceChildren(new Option("Select Active supplier",""));
 catalogue.suppliers.forEach(s=>$("override-supplier").add(new Option(s.name+" · "+s.supplier_id,s.supplier_id)));
 await myRequests();
 if(user.role==="employee"){navigate("chat");await newChat();}
 else{navigate("buyer");await refreshRuns();const data=await api("/api/policies");
 $("policy-version").replaceChildren(new Option("All approved versions",""));data.policies.forEach(p=>$("policy-version").add(new Option(p.version+" · "+p.effective_date,p.version)));}
}
$("login-form").addEventListener("submit",e=>{e.preventDefault();perform(e.submitter,async()=>{
 await api("/api/login",{username:$("username").value,password:$("password").value});$("password").value="";message("Signed in.");await start();
});});
$("logout").addEventListener("click",e=>perform(e.target,async()=>{await api("/api/logout",{});location.reload();}));
$("nav-chat").addEventListener("click",async()=>{navigate("chat");if(!conversation)await newChat();await myRequests();});
$("nav-buyer").addEventListener("click",()=>{navigate("buyer");refreshRuns().catch(e=>message(e.message,true));});
$("nav-documents").addEventListener("click",()=>{navigate("documents");documents().catch(e=>message(e.message,true));});
async function newChat(){conversation=await api("/api/conversations",{});renderChat();}
$("new-chat").addEventListener("click",e=>perform(e.target,newChat));
function renderChat(){
 $("conversation").replaceChildren(...conversation.messages.map(m=>element("div",m.text,"bubble "+m.role)));
 $("conversation").scrollTop=$("conversation").scrollHeight;
 $("choices").replaceChildren();
 if(!conversation.pr_no)for(const item of conversation.state.choices||[]){
 $("choices").append(action(item.item_code+" · "+item.description,()=>sendMessage(item.item_code)));
 }
 $("confirmation").hidden=!conversation.state.ready && !conversation.pr_no;
 $("confirm-request").hidden=!!conversation.pr_no;
 $("confirmation-text").textContent=conversation.pr_no
 ? "Request "+conversation.pr_no+" submitted for buyer review."+
   (conversation.recommendation ? " Proposed "+conversation.recommendation.route+". "+conversation.recommendation.reason :
   " "+(conversation.evaluation_note||""))
 : "Check the item, quantity, plant, need-by date and urgency above before confirming.";
 $("chat-text").disabled=!!conversation.pr_no;
 $("chat-form").querySelector("button").disabled=!!conversation.pr_no;
}
async function sendMessage(text){
 conversation=await api("/api/conversations/"+conversation.conversation_id+"/message",{revision:conversation.revision,message:text});
 $("chat-text").value="";renderChat();
}
$("chat-form").addEventListener("submit",e=>{e.preventDefault();perform(e.submitter,()=>sendMessage($("chat-text").value));});
$("confirm-request").addEventListener("click",e=>perform(e.target,async()=>{
 conversation=await api("/api/conversations/"+conversation.conversation_id+"/confirm",{revision:conversation.revision});renderChat();await myRequests();message("Request confirmed and sent for buyer review.");
}));
async function myRequests(){
 const rows=await api("/api/my-requests");
 $("my-requests").replaceChildren(...rows.map(r=>{const card=element("div","","request-card");
 card.append(element("p",r.input.description),element("p",r.input.quantity+" "+r.input.uom+" · "+r.input.plant+" · "+r.input.need_by_date),
 element("small",r.document_id ? r.effective.route+" · Document "+r.document_id : "Awaiting buyer review"));return card;}));
 if(!rows.length)$("my-requests").textContent="Your confirmed requests will appear here.";
}
async function refreshRuns(){
 const rows=await api("/api/buyer/runs");
 $("runs").replaceChildren(...rows.map(r=>{const b=action(r.origin.toUpperCase()+" · "+r.line_count+" lines · "+r.requirement_count+" requirements\n"+r.created_at,()=>loadRun(r.run_id));b.className="run-card";return b;}));
 if(!rows.length)$("runs").textContent="Evaluate chat requests or upload a batch to begin.";
}
async function loadRun(id){
 run=await api("/api/buyer/runs/"+id);$("requirements-title").textContent=run.groups.length+" requirements";
 $("requirements").replaceChildren(...run.groups.map(g=>{
 const card=element("div","","requirement-card"),text=element("div");
 text.append(element("strong",g.inputs[0].description),element("p",g.group_id+" · "+g.effective.route+" · "+g.inputs[0].plant),
 element("small",g.approval?"Approved · "+g.approval.document_id:g.inputs.length+" PR lines · Ready for review"));
 card.append(text,element("span","₹"+Number(g.effective.group_value_inr).toLocaleString("en-IN"),"money"),action("Review",()=>showGroup(g)));return card;
 }));
 $("rejections").replaceChildren(...run.rejected.map(r=>element("p",r.pr_no+"/"+r.pr_line+" · "+r.reason)));
 if(selected){const current=run.groups.find(g=>g.group_id===selected.group_id);if(current)showGroup(current);}
}
function showGroup(group){
 selected=group;$("detail").hidden=false;$("detail-title").textContent=group.group_id+" · "+group.inputs[0].description;
 $("detail-status").textContent=group.approval?"Approved":group.override_comment?"Override awaiting approval":"Awaiting approval";
 const d=group.effective;
 $("decision-summary").replaceChildren(element("strong",d.route+" · ₹"+Number(d.group_value_inr).toLocaleString("en-IN")),
 element("p",d.supplier_id?"Supplier "+d.supplier_id+" · unit price ₹"+d.unit_price:"Suggested suppliers: "+(d.suggested_supplier_ids||"Buyer to identify suppliers")),
 element("p",d.reason),element("p",group.override_comment?"Override reason: "+group.override_comment:"Policy "+d.policy_version+" · Clause "+group.trace.clause));
 $("input-lines").replaceChildren(...group.inputs.map(r=>{
 const tr=element("tr");for(const text of [r.pr_no+"/"+r.pr_line,r.description,r.quantity+" "+r.uom,r.plant,r.need_by_date,r.urgency])tr.append(element("td",text));return tr;
 }));
 $("ranking").replaceChildren(...group.trace.preferred.map((r,i)=>element("p",
 (i+1)+". "+r.supplier_id+" · reference price ₹"+r.reference_price+" · "+r.source+" · score "+Number(r.score).toFixed(3)+"\n"+
 Object.entries(r.factors).map(([k,v])=>k+" "+Number(v).toFixed(3)).join(" · "))));
 $("original").textContent=JSON.stringify(group.original,null,2);
 $("approval-controls").hidden=!!group.approval;$("capex").checked=false;
 $("override-comment").value="";$("override-price").value=d.unit_price||"";$("override-supplier").value=d.supplier_id||"";
 $("override-route").value=d.route;$("override-contract").value=group.trace.contract_id||"";
 $("group-audit").replaceChildren(...run.audit.filter(e=>e.entity.endsWith("/"+group.group_id)).map(e=>element("p",e.action+" · "+e.actor+" · "+e.occurred_at+"\n"+JSON.stringify(e.detail))));
 $("detail").scrollIntoView({behavior:"smooth",block:"start"});
}
$("refresh-runs").addEventListener("click",e=>perform(e.target,refreshRuns));
$("evaluate-chat").addEventListener("click",e=>perform(e.target,async()=>{const result=await api("/api/buyer/evaluate",{});selected=null;await refreshRuns();await loadRun(result.run_id);message("Confirmed requests evaluated.");}));
$("upload-form").addEventListener("submit",e=>{e.preventDefault();perform(e.submitter,async()=>{
 const file=$("batch-file").files[0];const version=$("policy-version").value;
 const response=await fetch("/api/process-batch"+(version?"?policy_version="+encodeURIComponent(version):""),{method:"POST",headers:{"Content-Type":"text/csv"},body:file});
 const result=await response.json();if(!response.ok)throw new Error(result.error);
 selected=null;await refreshRuns();await loadRun(result.run_id);message("Batch processed. Select a requirement to review.");
});});
$("override-form").addEventListener("submit",e=>{e.preventDefault();perform(e.submitter,async()=>{
 await api("/api/buyer/override",{run_id:run.run_id,group_id:selected.group_id,revision:selected.revision,route:$("override-route").value,
 supplier_id:$("override-supplier").value,unit_price:$("override-price").value,contract_id:$("override-contract").value,comment:$("override-comment").value});
 await loadRun(run.run_id);message("Override saved with its reason. Review it before approval.");
});});
$("approve-request").addEventListener("click",e=>perform(e.target,async()=>{
 const result=await api("/api/buyer/approve",{run_id:run.run_id,group_id:selected.group_id,revision:selected.revision,capex_approved:$("capex").checked});
 await loadRun(run.run_id);message("Approved. Created document "+result.approval.document_id+".");
}));
async function documents(){
 const rows=await api("/api/documents");$("documents").replaceChildren(...rows.map(d=>{
 const card=element("div","","panel document-card");card.append(element("h2",d.route+" · "+d.document_id),element("p",d.plant+" · "+(d.supplier_id||"Sourcing")+" · "+d.created_at));
 for(const l of d.lines)card.append(element("p",l.group_id+" · "+l.inputs.map(r=>r.description+" ("+r.quantity+" "+r.uom+")").join(", ")+" · approved by "+l.actor));
 return card;}));if(!rows.length)$("documents").textContent="Approved POs and sourcing requests will appear here.";
}
$("refresh-documents").addEventListener("click",e=>perform(e.target,documents));
start().catch(error=>{if(!error.message.includes("sign in"))message(error.message,true);});
