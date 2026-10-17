"use strict";
const $=selector=>document.querySelector(selector);
const $$=selector=>[...document.querySelectorAll(selector)];
const dayString=date=>`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;
const today=()=>dayString(new Date());
const recurrence={three_times_daily:"3 раза в день · 09:00, 15:00, 21:00",event:"До события · с учащением",once:"Один раз",daily:"Каждый день",weekdays:"По будням",weekly:"Раз в неделю",hourly:"Каждый час",every_3_hours:"Каждые 3 часа",every_5_hours:"Каждые 5 часов",every_8_hours:"Каждые 8 часов",every_12_hours:"Каждые 12 часов",every_3_days:"Раз в 3 дня",monthly:"Раз в месяц"};
const esc=text=>String(text??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let state,day=today(),page="today",filter="pending",editing=null,toastTimer,signature="",plan=null,planId=null,lastToday=today(),reviewDay=today(),editorDay=null;
function call(action,values={}) {
  if(!window.RitmNative) throw new Error("Открой этот интерфейс через установленное приложение Ритм.");
  const response=JSON.parse(RitmNative.call(JSON.stringify({action,...values})));
  if(!response.ok) throw new Error(response.error);return response.data;
}
function toast(message) {clearTimeout(toastTimer);$("#toast").textContent=message;$("#toast").hidden=false;toastTimer=setTimeout(()=>$("#toast").hidden=true,5000);}
function protect(action) {try {return action();} catch(error) {toast(error.message);}}
function theme(settings) {
  const hue=Number(settings.hue??190),bright=Number(settings.brightness??6),root=document.documentElement;
  const background=settings.background||`hsl(${hue},50%,${bright}%)`,accent=settings.accent||`hsl(${hue},90%,72%)`;
  root.style.setProperty("--hue",hue);root.style.setProperty("--bg",background);
  root.style.setProperty("--card",`hsl(${hue},40%,${Math.min(bright+7,28)}%)`);
  root.style.setProperty("--line",`hsl(${hue},28%,${bright+22}%)`);root.style.setProperty("--accent",accent);
  const luminance=hex=>{const rgb=hex.match(/[a-f0-9]{2}/gi).map(c=>parseInt(c,16)/255).map(c=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4);return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];};
  const light=settings.background&&luminance(settings.background)>.4;
  root.style.setProperty("--ink",light?"#101a30":"#f1f4ff");root.style.setProperty("--muted",light?"#334661":"#acb9d5");
  // Карточки следуют яркости гаммы; при светлом пользовательском фоне делаем их светлыми.
  if(light) root.style.setProperty("--card",`hsl(${hue},25%,94%)`);
  root.style.setProperty("--accent-ink",settings.accent&&luminance(settings.accent)<.25?"#ffffff":"#071523");
}
function rowHtml(row) {
  const task=state.tasks.find(t=>t.id===row.task_id),done=row.status==="done",future=row.date>today();
  return `<article class="task ${done?"done":""}"><button class="check" data-check="${esc(row.task_id)}" data-date="${esc(row.date)}" data-done="${done}" aria-label="${done?"Отменить выполнение":"Выполнить"}: ${esc(row.title)}" ${future?"disabled":""}>${done?"✓":"○"}</button><div><p class="title">${esc(row.title)}</p><div class="meta">${esc(row.time?(/^(hourly|every_\d+_hours)$/.test(task?.recurrence)?"С ":"")+row.time:"Без времени")} · ${esc(row.category)}${row.priority==="high"?" · Важное":""}<br>${esc(recurrence[task?.recurrence]||"")}${row.date!==day||page==="progress"?" · "+esc(row.date):""}</div>${row.notes?`<p class="note">${esc(row.notes)}</p>`:""}${task&&!task.archived_date?`<button class="edit" data-edit="${esc(task.id)}">Изменить</button>`:""}</div></article>`;
}
function render() {
  const s=state.settings;theme(s);
  $("#syncBadge").textContent=state.pending_count?`В очереди: ${state.pending_count}`:state.connected?"Синхронизация":"Офлайн";
  const date=new Date(day+"T12:00:00"),label=date.toLocaleDateString("ru-RU",{weekday:"long",day:"numeric",month:"long"});
  $("#dayLabel").textContent=label[0].toUpperCase()+label.slice(1);$("#selectedDay").value=day;
  const rows=state.occurrences.filter(row=>row.status!=="cancelled"&&row.date===day);
  const done=rows.filter(row=>row.status==="done").length;
  $("#dailyCount").textContent=`${done} / ${rows.length}`;$("#dailyProgress").max=Math.max(1,rows.length);$("#dailyProgress").value=done;
  const visible=rows.filter(row=>filter==="all"||row.status===filter).sort((a,b)=>(a.time||"99:99").localeCompare(b.time||"99:99"));
  $("#taskList").innerHTML=visible.length?visible.map(rowHtml).join(""):`<div class="empty"><strong>${filter==="done"?"Первые победы впереди":rows.length&&done===rows.length?"На сегодня всё ✓":"Место для нового шага"}</strong>${filter==="done"?"Выполненные задачи появятся здесь и в прогрессе.":"Добавь задачу кнопкой внизу."}</div>`;
  const history=state.occurrences.filter(row=>row.status==="done").sort((a,b)=>(b.completed_at||"").localeCompare(a.completed_at||""));
  const days=new Set(history.map(row=>(row.completed_at||row.date).slice(0,10)));
  $("#progressStats").innerHTML=`<div class="stat"><strong>${history.length*10}</strong><span>Очков опыта</span></div><div class="stat"><strong>${days.size}</strong><span>Дней с победами</span></div>`;
  const missed=state.occurrences.filter(row=>row.status==="pending"&&row.date<today()).sort((a,b)=>b.date.localeCompare(a.date));
  $("#unfinishedList").innerHTML=missed.length?missed.map(rowHtml).join(""):'<p class="muted">Пропущенных дел пока нет.</p>';
  $("#reviewDate").max=today();$("#reviewDate").value=reviewDay;
  const cells=[];for(let offset=90;offset>=0;offset--){const d=new Date(today()+"T12:00:00");d.setDate(d.getDate()-offset);const value=dayString(d),count=history.filter(row=>(row.completed_at||row.date).slice(0,10)===value).length;cells.push(`<button data-review-day="${value}" data-level="${Math.min(3,count)}" aria-label="${value}: выполнено ${count}" title="${value}: выполнено ${count}"></button>`);}
  $("#activityGrid").innerHTML=cells.join("");renderDayReview();
  $("#historyList").innerHTML=history.length?history.slice(0,200).map(rowHtml).join(""):'<div class="empty">Отметь первую задачу. Каждый выполненный шаг приносит 10 очков.</div>';
  $("#syncStatus").textContent=(state.connected?"Подключение: "+state.address:"Подключение не настроено.")+ (state.last_sync?"\nПоследний обмен: "+new Date(state.last_sync).toLocaleString("ru-RU"):"")+ (state.sync_error?"\n"+state.sync_error:"");
  $("#syncStatus").style.whiteSpace="pre-line";
  $("#connectForm").hidden=state.connected;
  $("#disconnect").hidden=!state.connected;
  if(document.activeElement!==$("#serverAddress")&&!$("#serverAddress").value) $("#serverAddress").value=state.address;
  $("#notifications").checked=s.notifications!==false;
  $("#permissionStatus").textContent=state.notification_permission?"Уведомления разрешены системой.":"Уведомления запрещены системой. Нажми кнопку выше.";
  if(document.activeElement!==$("#themeHue")) $("#themeHue").value=s.hue??190;
  if(document.activeElement!==$("#themeBrightness")) $("#themeBrightness").value=s.brightness??6;
  $("#backgroundColor").value=s.background||"#080f26";$("#accentColor").value=s.accent||"#35dcf7";
  $("#conflicts").innerHTML=state.conflicts.map((item,index)=>`<div class="conflict"><h3>Нужно выбрать правку</h3><p>${esc(item.reason)}</p><p class="muted">Правка телефона сохранена ниже. На ноутбуке уже есть другая версия.</p><pre>${esc(JSON.stringify(item.operation.values||{date:item.operation.date,completed:item.operation.completed,kind:item.operation.kind},null,2))}</pre><button data-resolve="${index}" data-phone="false">Оставить версию ноутбука</button><button data-resolve="${index}" data-phone="true">Повторить правку телефона</button><button data-copy-conflict="${index}">Копировать правку</button></div>`).join("");
}
function refresh(force=false) {
  if(today()!==lastToday) {if(day===lastToday) day=today();lastToday=today();}
  protect(()=>{const latest=call("state",{date:day}),next=JSON.stringify(latest);state=latest;if(force||next!==signature){signature=next;render();}});
}
function selectPage(value) {page=value;for(const name of ["today","progress","settings","assistant"]) $("#"+name+"Page").hidden=name!==value;$$('nav button').forEach(b=>b.classList.toggle("active",b.dataset.page===value));$("#addTask").hidden=value!=="today";window.scrollTo(0,0);refresh();}
function editTask(id=null) {
  editing=id;const task=id?state.tasks.find(t=>t.id===id):{};
  $("#editorTitle").textContent=id?"Изменить задачу":"Новая задача";
  $("#taskTitle").value=task.title||"";$("#taskNotes").value=task.notes||"";
  $("#taskDate").value=task.start_date||(day<today()?today():day);
  $("#taskDate").min=task.start_date&&task.start_date<today()?task.start_date:today();
  $("#taskTime").value=id?(task.time||""):"09:00";$("#taskReminderEnd").value=id?(task.reminder_end_date||(task.recurrence==="once"?task.start_date:"")):$("#taskDate").value;editorDay=$("#taskDate").value;$("#taskEventDate").value=task.event_date||"";$("#taskEscalationDays").value=task.escalation_days??3;$("#taskReminder").checked=!!task.reminder;$("#taskTime").required=!!task.reminder;
  $("#taskCategory").value=task.category||"Личное";$("#taskPriority").value=task.priority||"normal";$("#taskRecurrence").value=task.recurrence||"once";
  updateReminderFields();$("#archiveTask").hidden=!id;$("#editorError").textContent="";$("#taskDialog").showModal();
}
document.addEventListener("click",event=>{
  const button=event.target.closest("button");if(!button)return;
  protect(()=>{
    if(button.dataset.reviewDay){reviewDay=button.dataset.reviewDay;$("#reviewDate").value=reviewDay;renderDayReview();$("#dayReview").scrollIntoView({behavior:"smooth",block:"center"});}
    if(button.dataset.page) selectPage(button.dataset.page);
    if(button.dataset.filter) {filter=button.dataset.filter;$$('[data-filter]').forEach(b=>b.classList.toggle("active",b===button));render();}
    if(button.dataset.edit) editTask(button.dataset.edit);
    if(button.dataset.check) {call("mutate",{command:{kind:"complete",task_id:button.dataset.check,date:button.dataset.date,completed:button.dataset.done!=="true"}});refresh();toast(button.dataset.done!=="true"?"+10 очков. Шаг сделан!":"Выполнение отменено.");}
    if(button.dataset.resolve!==undefined) {call("resolve",{index:Number(button.dataset.resolve),keepPhone:button.dataset.phone==="true"});refresh();}
    if(button.dataset.copyConflict!==undefined) {call("copy",{text:JSON.stringify(state.conflicts[Number(button.dataset.copyConflict)],null,2)});toast("Правка скопирована.");}
    if(button.dataset.hue) settings({hue:Number(button.dataset.hue),background:null,accent:null});
  });
});
$("#addTask").onclick=()=>protect(()=>editTask());$("#closeEditor").onclick=()=>$("#taskDialog").close();
$("#taskForm").onsubmit=event=>{
  event.preventDefault();try {
    const values={title:$("#taskTitle").value,notes:$("#taskNotes").value,start_date:$("#taskDate").value,time:$("#taskTime").value||null,reminder:$("#taskReminder").checked,category:$("#taskCategory").value,priority:$("#taskPriority").value,recurrence:$("#taskRecurrence").value,reminder_end_date:$("#taskReminderEnd").value||null,event_date:$("#taskRecurrence").value==="event"?($("#taskEventDate").value||null):null,escalation_days:Number($("#taskEscalationDays").value)};
    call("mutate",{command:{kind:editing?"edit":"create",task_id:editing,values}});$("#taskDialog").close();refresh();toast("Сохранено на телефоне.");
  }catch(error){$("#editorError").textContent=error.message;}
};
$("#archiveTask").onclick=()=>{if(confirm("Убрать задачу в архив? Выполненные записи останутся в прогрессе.")) protect(()=>{call("mutate",{command:{kind:"archive",task_id:editing}});$("#taskDialog").close();refresh();});};
function shiftDay(delta){const date=new Date(day+"T12:00:00");date.setDate(date.getDate()+delta);day=dayString(date);refresh(true);}
$("#previousDay").onclick=()=>shiftDay(-1);$("#nextDay").onclick=()=>shiftDay(1);$("#selectedDay").onchange=()=>{if($("#selectedDay").value){day=$("#selectedDay").value;refresh(true);}};
$("#syncNow").onclick=()=>protect(()=>{call("sync");toast(state?.connected?"Пробуем связаться с ноутбуком. Очередь сохранена.":"Укажи подключение в настройках.");if(!state?.connected)selectPage("settings");});
$("#connectForm").onsubmit=event=>{event.preventDefault();protect(()=>{const parts=$("#serverAddress").value.trim().split("|");call("connect",{address:parts[0],token:parts[1]||$("#serverToken").value});$("#serverToken").value="";$("#serverAddress").value=parts[0];refresh();toast("Подключение сохранено. Пробуем обменяться данными.");});};
$("#disconnect").onclick=()=>protect(()=>{call("disconnect");refresh();toast("Обмен отключён. Данные сохранены.");});
function settings(values){call("settings",{values});refresh();}
$("#notifications").onchange=e=>protect(()=>settings({notifications:e.target.checked}));
$("#notificationPermission").onclick=()=>protect(()=>call("permission"));
$("#themeHue").onchange=e=>protect(()=>settings({hue:Number(e.target.value),background:null,accent:null}));
$("#themeBrightness").onchange=e=>protect(()=>settings({brightness:Number(e.target.value),background:null}));
$("#backgroundColor").onchange=e=>protect(()=>settings({background:e.target.value}));$("#accentColor").onchange=e=>protect(()=>settings({accent:e.target.value}));
$("#resetColors").onclick=()=>protect(()=>settings({background:null,accent:null}));$("#exportBackup").onclick=()=>protect(()=>call("export"));
$("#copyPrompt").onclick=()=>protect(()=>{call("copy",{text:`Составь реалистичный план: ${$("#goal").value||"Помоги спланировать день"}. Сегодня ${today()}. Верни только JSON {"tasks":[...]}, максимум 30 задач. Поля каждой задачи: title (до 200 символов), notes (до 2000), category (Работа/Учёба/Здоровье/Личное), priority (low/normal/high), recurrence (once/hourly/every_3_hours/every_5_hours/every_8_hours/every_12_hours/daily/every_3_days/weekdays/weekly/monthly/three_times_daily/event), start_date (YYYY-MM-DD, не раньше сегодня), time (HH:MM или null), reminder (boolean, true только с time). Для часовых повторов обязательно time и reminder=true. reminder_end_date (YYYY-MM-DD или null, последний день уведомлений включительно), event_date (YYYY-MM-DD, обязательно для event), escalation_days (1–365, по умолчанию 3). Для event и three_times_daily reminder=true и time=09:00. event — ежедневно в 09:00, за escalation_days до event_date и в день события в 09:00, 15:00, 21:00. Разовые напоминания действуют до конца start_date по умолчанию. Никаких других полей или команд.`});toast("Запрос скопирован. Вставь его в ChatGPT.");});
$("#openChatGPT").onclick=()=>protect(()=>call("chatgpt"));
$("#planJson").oninput=()=>{plan=null;$("#importPlan").hidden=true;$("#planPreview").textContent="";};
$("#previewPlan").onclick=()=>{plan=null;$("#importPlan").hidden=true;protect(()=>{const raw=$("#planJson").value.trim().replace(/^```(?:json)?\s*/i,"").replace(/\s*```$/,"");plan=call("preview",{plan:JSON.parse(raw)}).tasks;planId=String(Date.now())+"-"+Math.random().toString(36).slice(2);$("#planPreview").innerHTML="<ol>"+plan.map(t=>`<li>${esc(t.title)} · ${esc(t.start_date)} ${esc(t.time||"")} · ${esc(recurrence[t.recurrence])}</li>`).join("")+"</ol>";$("#importPlan").hidden=false;});};
$("#importPlan").onclick=()=>protect(()=>{if(!plan)return;const result=call("import",{plan:{tasks:plan},request_id:planId});plan=null;$("#importPlan").hidden=true;$("#planJson").value="";$("#planPreview").textContent=`Добавлено задач: ${result.created}`;refresh();toast("План сохранён.");});
window.ritmBack=()=>{if($("#taskDialog").open){$("#taskDialog").close();return true;}if(page!=="today"){selectPage("today");return true;}return false;};
document.addEventListener("visibilitychange",()=>{if(!document.hidden){refresh(true);protect(()=>call("sync"));}});
refresh(true);setInterval(()=>{if(!document.hidden)refresh();},3000);setInterval(()=>{if(!document.hidden)protect(()=>call("sync"));},30000);

function updateReminderFields() {
  const mode=$("#taskRecurrence").value,fixed=mode==="event"||mode==="three_times_daily";
  $("#taskEventFields").hidden=mode!=="event";$("#taskEventDate").required=mode==="event";
  $("#taskFixedTimes").hidden=mode!=="three_times_daily";$("#taskTime").disabled=fixed;$("#taskReminder").disabled=fixed;
  if(fixed){$("#taskTime").value="09:00";$("#taskReminder").checked=true;}
  $("#taskTime").required=$("#taskReminder").checked;
}
$("#reviewDate").onchange=()=>{if($("#reviewDate").value){reviewDay=$("#reviewDate").value;renderDayReview();}};
$("#taskDate").onchange=()=>{if($("#taskRecurrence").value==="once"&&(!$("#taskReminderEnd").value||$("#taskReminderEnd").value===editorDay))$("#taskReminderEnd").value=$("#taskDate").value;editorDay=$("#taskDate").value;};
$("#taskRecurrence").onchange=()=>{if($("#taskRecurrence").value==="once"&&!$("#taskReminderEnd").value)$("#taskReminderEnd").value=$("#taskDate").value;if($("#taskRecurrence").value!=="once"&&$("#taskReminderEnd").value===$("#taskDate").value)$("#taskReminderEnd").value="";if(/^(hourly|every_\d+_hours)$/.test($("#taskRecurrence").value)){$("#taskReminder").checked=true;if(!$("#taskTime").value)$("#taskTime").value="09:00";}updateReminderFields();};
$("#taskReminder").onchange=()=>{$("#taskTime").required=$("#taskReminder").checked;if($("#taskReminder").checked&&!$("#taskTime").value)$("#taskTime").value="09:00";};

function renderDayReview(){
  if(!state)return;
  const actual=row=>(row.completed_at||"").slice(0,10);
  const done=state.occurrences.filter(row=>row.status==="done"&&actual(row)===reviewDay);
  const missed=state.occurrences.filter(row=>row.date===reviewDay&&row.status!=="cancelled"&&(row.status==="pending"||actual(row)>reviewDay));
  const list=rows=>rows.length?"<ul>"+rows.map(row=>`<li>${esc(row.title)}${row.completed_at?`<small>${row.status==="done"&&actual(row)>reviewDay?"Выполнено позже: ":""}${esc(row.completed_at.slice(0,10))} · ${esc(row.completed_at.slice(11,16))}</small>`:""}</li>`).join("")+"</ul>":'<p class="muted">Дел нет.</p>';
  $("#dayReview").innerHTML=`<h3>Выполнено за день · ${done.length}</h3>${list(done)}<h3>${reviewDay<today()?"Не выполнено к концу дня":"Ещё не выполнено"} · ${missed.length}</h3>${list(missed)}`;
}
