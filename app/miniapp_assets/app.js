const launchParams=new URLSearchParams(location.hash.slice(1));
const bale=window.Bale?.WebApp; const tg=window.Telegram?.WebApp; const bridge=bale?.initData?bale:tg;
const platform=bale?.initData?"bale":"telegram"; bridge?.ready(); bridge?.expand();
const initData=bridge?.initData||launchParams.get("tgWebAppData")||""; const fa=n=>String(n??0).replace(/\d/g,d=>"۰۱۲۳۴۵۶۷۸۹"[d]);
async function launchContext(){
  if(initData)return {data:initData,platform};
  for(let i=0;i<30;i++){
    const b=window.Bale?.WebApp,t=window.Telegram?.WebApp;
    const app=b?.initData?b:t;
    if(app?.initData){app.ready?.();app.expand?.();return {data:app.initData,platform:b?.initData?"bale":"telegram"}}
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error("مینی‌اپ را از دکمهٔ داخل ربات باز کنید.");
}
const context=launchContext();
const api=async(path,opt={})=>{
  const auth=await context,controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),12000);
  try{const r=await fetch(`/miniapp/api/${path}`,{...opt,signal:controller.signal,headers:{"Content-Type":"application/json","X-Telegram-Init-Data":auth.data,"X-Mini-App-Platform":auth.platform,...opt.headers}});if(!r.ok)throw new Error(r.status===401?"مینی‌اپ را دوباره از داخل ربات باز کنید.":"دریافت اطلاعات انجام نشد؛ دوباره تلاش کنید.");return await r.json()}
  catch(e){if(e.name==="AbortError")throw new Error("اتصال طول کشید؛ مینی‌اپ را دوباره باز کنید.");throw e}
  finally{clearTimeout(timer)}
};
const fmt=v=>new Intl.DateTimeFormat("fa-IR",{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}).format(new Date(v));
const time=v=>new Intl.DateTimeFormat("fa-IR",{hour:"2-digit",minute:"2-digit"}).format(new Date(v));
const empty=t=>`<div class="empty">${t}</div>`; const toast=t=>{const e=document.querySelector("#toast");e.textContent=t;e.classList.add("show");if(t.includes("ورود امن")){heroText.textContent="اتصال برقرار نشد؛ مینی‌اپ را از داخل ربات دوباره باز کنید.";document.querySelectorAll(".skeleton").forEach(x=>{x.classList.remove("skeleton");x.innerHTML=empty("اطلاعات دریافت نشد")})}setTimeout(()=>e.classList.remove("show"),4000)};
async function overview(){try{const d=await api("overview"),s=d.stats,p=s.completion_rate||0; completion.textContent=fa(p)+"٪";ring.style.background=`conic-gradient(var(--cyan) ${p}%,#34415f 0)`;heroText.textContent=p?`${fa(s.done)} تسک با موفقیت تکمیل شده`:"با اولین تسک شروع کن";openCount.textContent=fa(s.open);overdueCount.textContent=fa(s.overdue);meetingCount.textContent=fa(d.today_meetings.length);reminderCount.textContent=fa(d.reminders_week);attention.innerHTML=d.attention.length?d.attention.map(taskCard).join(""):empty("همه‌چیز مرتب است؛ مورد فوری نداری");todayMeetings.innerHTML=d.today_meetings.length?d.today_meetings.map(meetingCard).join(""):empty("امروز جلسه‌ای ثبت نشده");syncState.classList.toggle("connected",d.google_connected);syncState.querySelector("small").textContent=d.google_connected?"متصل و همگام":"هنوز متصل نشده"}catch(e){toast("ورود امن از داخل تلگرام لازم است")}}
const taskCard=t=>`<article class="item"><span class="icon ${t.priority<3?'red':'blue'}">✓</span><div class="grow"><b>${esc(t.title)}</b><small>${fmt(t.deadline)} · ${esc(t.assignee||"بدون مسئول")}</small><div class="progress"><i style="width:${t.progress}%"></i></div></div><span class="badge">${fa(t.progress)}٪</span></article>`;
const meetingCard=m=>`<article class="item"><span class="time">${time(m.starts_at)}</span><div class="grow"><b>${esc(m.title)}</b><small>${fmt(m.starts_at)} تا ${time(m.ends_at)}</small></div>${m.recurring?'<span class="badge">تکرار</span>':''}</article>`;
const reminderCard=r=>`<article class="item"><span class="icon amber">♢</span><div class="grow"><b>${esc(r.title)}</b><small>${fmt(r.due_at)}</small></div></article>`;
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function loadTasks(scope="open"){taskList.innerHTML=empty("در حال دریافت…");const rows=await api(`tasks?scope=${scope}`);taskList.innerHTML=rows.length?rows.map(t=>taskCard(t).replace('</div></div><span class="badge">',`</div><input class="task-slider" type="range" min="0" max="100" step="10" value="${t.progress}" data-task="${t.id}"></div><span class="badge">`)).join(""):empty("تسکی در این بخش نیست")}
async function loadMeetings(){const rows=await api("meetings");meetingList.innerHTML=rows.length?rows.map(meetingCard).join(""):empty("جلسهٔ آینده‌ای ثبت نشده")}
async function loadReminders(){const rows=await api("reminders");reminderList.innerHTML=rows.length?rows.map(reminderCard).join(""):empty("یادآوری ثبت نشده")}
function showPage(id){document.querySelectorAll(".page").forEach(x=>x.classList.toggle("active",x.id===id));document.querySelectorAll("nav button").forEach(x=>x.classList.toggle("active",x.dataset.page===id));if(id==="tasks")loadTasks();if(id==="meetings")loadMeetings();if(id==="reminders")loadReminders();tg?.HapticFeedback?.impactOccurred("light")}
document.addEventListener("click",e=>{const p=e.target.closest("[data-page],[data-go]");if(p)showPage(p.dataset.page||p.dataset.go);const c=e.target.closest("[data-scope]");if(c){document.querySelectorAll("[data-scope]").forEach(x=>x.classList.remove("selected"));c.classList.add("selected");loadTasks(c.dataset.scope)}const m=e.target.closest("[data-modal]");if(m)openEditor(m.dataset.modal)});
document.addEventListener("change",async e=>{if(e.target.matches(".task-slider")){await api(`tasks/${e.target.dataset.task}`,{method:"PATCH",body:JSON.stringify({progress:+e.target.value})});toast("پیشرفت ذخیره شد");loadTasks()}});
const fields={task:["تسک جدید",`<div class="field"><label>عنوان</label><input name="title" required></div><div class="field"><label>مهلت</label><input type="datetime-local" name="deadline" required></div><div class="field"><label>اولویت</label><select name="priority"><option value="1">فوری و مهم</option><option value="2" selected>مهم</option><option value="3">فوری</option><option value="4">عادی</option></select></div><div class="field"><label>مسئول</label><input name="assignee"></div>`],meeting:["جلسه جدید",`<div class="field"><label>عنوان جلسه</label><input name="title" required></div><div class="field"><label>شروع</label><input type="datetime-local" name="starts_at" required></div><div class="field"><label>پایان</label><input type="datetime-local" name="ends_at" required></div>`],reminder:["یادآور جدید",`<div class="field"><label>عنوان</label><input name="title" required></div><div class="field"><label>زمان یادآوری</label><input type="datetime-local" name="due_at" required></div>`]};let editorType;
function openEditor(type){editorType=type;modalTitle.textContent=fields[type][0];formFields.innerHTML=fields[type][1];editor.showModal()}
editorForm.addEventListener("submit",async e=>{if(e.submitter?.value==="cancel")return;e.preventDefault();const body=Object.fromEntries(new FormData(editorForm));try{await api(editorType+"s",{method:"POST",body:JSON.stringify(body)});editor.close();toast("با موفقیت ثبت شد");overview();showPage(editorType+"s")}catch(err){toast("ثبت انجام نشد")}});
overview();
setTimeout(()=>{
  const app=window.Telegram?.WebApp||window.Bale?.WebApp;
  app?.ready?.();app?.expand?.();
},3000);
