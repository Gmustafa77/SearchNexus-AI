const $=s=>document.querySelector(s), log=$("#log"), q=$("#q"), send=$("#send");
let sid=null, busy=false, ctl=null, abortWhy="";
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e};
const examples=["What changed in the latest Python release?","Is it safe to eat eggs every day, according to recent research?","Explain the current state of fusion energy projects"];

function empty(){const d=el("div","empty");d.append(el("p",null,"Ask a question. I'll search the web, check the sources against each other, and show you where each answer came from."));
 const c=el("div","chips");examples.forEach(e=>{const b=el("button","chip",e);b.onclick=()=>ask(e);c.append(b)});d.append(c);log.append(d)}
function scroll(){$("main").scrollTop=$("main").scrollHeight}
function section(parent,title){parent.append(el("h2",null,title))}
function setBusy(b){busy=b;send.textContent=b?"Stop":"Ask";send.classList.toggle("stop",b);send.setAttribute("aria-label",b?"Stop generating":"Ask")}

function actions(r){
 const row=el("div","actions"),copy=el("button","ghost sm","Copy"),up=el("button","ghost sm","Helpful"),down=el("button","ghost sm","Not helpful");
 copy.onclick=async()=>{try{await navigator.clipboard.writeText(r.answer);copy.textContent="Copied"}catch(_){copy.textContent="Copy failed"}setTimeout(()=>copy.textContent="Copy",1500)};
 const rate=async rating=>{up.disabled=down.disabled=true;
  try{const res=await fetch("/api/feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_id:r.session_id,response_id:r.response_id,rating})});
   row.append(el("span","thanks",res.ok?"Thanks for the feedback.":"Couldn't send feedback."))}
  catch(_){row.append(el("span","thanks","Couldn't send feedback."))}};
 up.onclick=()=>rate("up");down.onclick=()=>rate("down");
 row.append(copy);if(r.response_id&&r.session_id)row.append(up,down);return row}

function render(r){
 const b=el("div","bot"), a=el("div","answer");
 r.answer.split(/\n{2,}/).forEach(p=>a.append(el("p",null,p.trim())));b.append(a);
 if(r.key_points.length){section(b,"Key points");const u=el("ul");r.key_points.forEach(k=>u.append(el("li",null,k)));b.append(u)}
 if(r.sources.length){section(b,"Sources");const o=el("ol","src");
  r.sources.forEach(s=>{const li=el("li"),l=el("a",null,s.title);l.href=s.url;l.target="_blank";l.rel="noopener noreferrer";li.append(l);o.append(li)});b.append(o)}
 if((r.search_queries||[]).length)b.append(el("div","queries","Searched for: "+r.search_queries.join(" \u00b7 ")));
 r.warnings.forEach(w=>b.append(el("div","note",w)));
 const m=el("div","meta"),pct=Math.round(r.confidence_score*100),c=el("span");
 const mt=el("span","meter");mt.append(el("i"));mt.firstChild.style.width=pct+"%";c.append(mt,"Confidence "+pct+"%");
 m.append(c,el("span",null,r.search_performed?"Searched the web":"Answered without searching"),el("span",null,(r.latency_ms/1000).toFixed(1)+" s"));
 if(r.cached)m.append(el("span","badge","Cached answer"));
 if(r.summary)m.title=r.summary;b.append(m,actions(r));
 if(r.follow_up_questions.length){const f=el("div","chips");r.follow_up_questions.forEach(x=>{const k=el("button","chip",x);k.onclick=()=>ask(x);f.append(k)});b.append(f)}
 return b}

// ---- loading bubble with live progress ----
function loading(){
 const node=el("div","bot loading"),st=el("span","st","Understanding your question\u2026");
 node.append(el("span","dot"),el("span","dot"),el("span","dot"),st);
 const set=d=>{st.textContent=d.stage==="search"&&d.query?"Searching the web: \u201c"+d.query+"\u201d":
  d.stage==="writing"?"Reading the sources and writing your answer\u2026":
  d.stage==="retry"?"The AI service is busy \u2014 retrying\u2026":"Understanding your question\u2026"};
 return{node,set}}

// ---- transport: Server-Sent Events, with a fallback to the plain JSON endpoint ----
function parseBlock(block,onEvent){
 if(!block||block[0]===":")return;let ev="message";const data=[];
 for(const line of block.split("\n")){if(line.startsWith("event:"))ev=line.slice(6).trim();else if(line.startsWith("data:"))data.push(line.slice(5).trimStart())}
 if(data.length){try{onEvent(ev,JSON.parse(data.join("\n")))}catch(_){}}}
async function readStream(res,onEvent){
 const reader=res.body.getReader(),dec=new TextDecoder();let buf="";
 for(;;){const{done,value}=await reader.read();if(done)break;buf+=dec.decode(value,{stream:true});
  let i;while((i=buf.indexOf("\n\n"))>=0){const block=buf.slice(0,i);buf=buf.slice(i+2);parseBlock(block,onEvent)}}
 if(buf.trim())parseBlock(buf,onEvent)}
async function failure(res){const d=await res.json().catch(()=>({}));return new Error(d.error?.message||"Request failed ("+res.status+").")}
async function request(text,ld){
 const init={method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:text,session_id:sid}),signal:ctl.signal};
 let res=await fetch("/api/chat/stream",init);
 if(res.status===404||res.status===405||!res.body){res=await fetch("/api/chat",init);if(!res.ok)throw await failure(res);return res.json()}
 if(!res.ok)throw await failure(res);
 let result=null,err=null;
 await readStream(res,(ev,d)=>{if(ev==="status")ld.set(d);else if(ev==="result")result=d;else if(ev==="error")err=d});
 if(err)throw new Error(err.message);
 if(!result)throw new Error("The connection closed before an answer arrived. Please try again.");
 return result}

function errorBox(e,text){
 if(abortWhy==="stop"){q.value=text;q.oninput();return el("div","stopped","Stopped. Your question is back in the box if you want to edit it.")}
 const box=el("div","error",abortWhy==="timeout"?"The request timed out. Please try again.":e.message||"Network error.");
 const retry=el("button","ghost sm","Retry");retry.onclick=()=>{box.remove();ask(text,{echo:false})};box.append(retry);return box}

async function ask(text,{echo=true}={}){
 text=text.trim();if(!text||busy)return;if(listening)rec.abort();say("");setBusy(true);abortWhy="";
 if(log.querySelector(".empty"))log.innerHTML="";
 if(echo)log.append(el("div","user",text));q.value="";q.style.height="auto";
 const ld=loading();log.append(ld.node);scroll();
 const my=ctl=new AbortController(),t=setTimeout(()=>{abortWhy="timeout";my.abort()},120000);
 try{const data=await request(text,ld);sid=data.session_id;ld.node.replaceWith(render(data))}
 catch(e){if(abortWhy!=="new")ld.node.replaceWith(errorBox(e,text))}
 finally{clearTimeout(t);if(ctl===my)ctl=null;setBusy(false);q.focus();scroll()}
}

// ---- speech to text (browser Web Speech API) ----
const SR=window.SpeechRecognition||window.webkitSpeechRecognition, mic=$("#mic"), lang=$("#lang"), statusEl=$("#status");
let rec=null, listening=false, base="";
const say=m=>{statusEl.textContent=m};
const MIC_ERR={"not-allowed":"Microphone access is blocked. Allow it in your browser's site settings.","service-not-allowed":"Speech recognition is not allowed here.","no-speech":"I didn't hear anything. Tap the mic and try again.","audio-capture":"No microphone was found.","network":"The speech service could not be reached.","language-not-supported":"That language is not supported by this browser."};
if(!SR){mic.hidden=true;lang.hidden=true}
mic.onclick=()=>{
 if(listening){rec.stop();return}
 if(busy)return;
 rec=new SR();rec.lang=lang.value;rec.interimResults=true;rec.continuous=false;
 base=q.value.trim()?q.value.trim()+" ":"";
 rec.onstart=()=>{listening=true;mic.classList.add("on");mic.setAttribute("aria-pressed","true");say("Listening… speak now")};
 rec.onresult=e=>{let t="";for(const r of e.results)t+=r[0].transcript;q.value=base+t;q.oninput()};
 rec.onerror=e=>{if(e.error!=="aborted")say(MIC_ERR[e.error]||"Speech recognition error: "+e.error)};
 rec.onend=()=>{listening=false;mic.classList.remove("on");mic.setAttribute("aria-pressed","false");if(statusEl.textContent.startsWith("Listening"))say("");q.focus()};
 try{rec.start()}catch(_){say("Could not start the microphone.")}
};


$("#f").onsubmit=e=>{e.preventDefault();if(busy){abortWhy="stop";ctl&&ctl.abort();return}ask(q.value)};
q.onkeydown=e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();ask(q.value)}};
q.oninput=()=>{q.style.height="auto";q.style.height=q.scrollHeight+"px"};
$("#new").onclick=()=>{if(ctl){abortWhy="new";ctl.abort()}if(sid)fetch("/api/sessions/"+sid,{method:"DELETE"}).catch(()=>{});sid=null;log.innerHTML="";empty()};
empty();
