"use strict";
(() => {
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function events(rows) {
    const result=[];
    for(const row of rows) {
      if(row.status==='cancelled'&&!row.slots?.length) continue;
      if(row.slots?.length) {
        for(const [slot,at] of Object.entries(row.checks||{})) if(row.slots.includes(slot)) result.push({...row,slot_time:slot,completed_at:at,status:'done'});
      } else if(row.status==='done') result.push({...row,slot_time:null});
    }
    return result.sort((a,b)=>b.completed_at.localeCompare(a.completed_at));
  }
  function xp(rows) {
    return events(rows).length*10-rows.filter(row=>row.dismissed_at).length*5;
  }
  function timeLabel(row,task=row) {
    if(!row.time) return 'Без времени';
    const hourly=/^(hourly|every_\d+_hours)$/.test(task?.recurrence||row.recurrence||'');
    if(!hourly) return row.time;
    const end=task?.repeat_until||row.repeat_until;
    return end?`${row.time}–${end}`:`С ${row.time}`;
  }
  function awards(completions,rows,today,facts={}) {
    const byDay=new Map(),byAction=new Map(),parts=new Map(),weekdays=new Set();
    let morning=0,evening=0;
    for(const event of completions) {
      const date=event.completed_at.slice(0,10),hour=Number(event.completed_at.slice(11,13));
      if(date>today) continue;
      byDay.set(date,(byDay.get(date)||0)+1);
      const key=event.title.normalize('NFKC').trim().replace(/\s+/g,' ').toLocaleLowerCase('ru-RU');
      const action=byAction.get(key)||{title:event.title,count:0};action.count++;byAction.set(key,action);
      const periods=parts.get(date)||new Set();periods.add(hour<12?0:hour<18?1:2);parts.set(date,periods);
      weekdays.add(new Date(date+'T12:00:00').getDay());
      if(hour<12) morning++;if(hour>=18) evening++;
    }
    const days=[...byDay.keys()].sort();let streak=0,run=0,comeback=0,previous=null;
    for(const date of days) {
      const gap=previous?(new Date(date+'T12:00:00')-new Date(previous+'T12:00:00'))/86400000:0;
      run=gap>=.95&&gap<=1.05?run+1:1;streak=Math.max(streak,run);if(gap>=2.95) comeback++;previous=date;
    }
    const planned=new Map();
    for(const row of rows) if((row.status!=='cancelled'||Object.keys(row.checks||{}).length)&&row.date<=today) {
      const group=planned.get(row.date)||{total:0,done:0};group.total+=row.slots?.length||1;
      group.done+=row.slots?.length?Object.keys(row.checks||{}).length:Number(row.status==='done');planned.set(row.date,group);
    }
    const actions=[...byAction.values()].sort((a,b)=>b.count-a.count||a.title.localeCompare(b.title));
    const metrics={total:[...byDay.values()].reduce((a,b)=>a+b,0),same:actions[0]?.count||0,
      streak,active:days.length,variety:actions.length,daymax:Math.max(0,...byDay.values()),
      perfect:facts.perfect_days??[...planned.values()].filter(x=>x.total&&x.done===x.total).length,
      comeback,morning,evening,balanced:[...parts.values()].filter(x=>x.size>=2).length,
      allparts:[...parts.values()].filter(x=>x.size===3).length,weekdays:weekdays.size};
    const catalog=window.RitmAwardCatalog.map((award,index)=>({...award,
      emoji:['✨','🚀','🌱','🪐','🧩','⚡','🏆','🔥','🌤️'][Math.floor(index/12)%9],
      current:metrics[award.metric]||0,unlocked:(metrics[award.metric]||0)>=award.goal,
      detail:award.metric==='same'&&actions[0]?`Твоё дело: ${actions[0].title}`:''}));
    const personal=[];
    for(const action of actions) for(const goal of [5,10,25,50,100]) if(action.count>=goal)
      personal.push({title:`${action.title} · ${goal} раз`,description:`Личная награда за повторение твоего дела`,emoji:'🏅',current:action.count,goal,unlocked:true});
    return {catalog,personal,earned:catalog.filter(a=>a.unlocked).length,metrics};
  }
  function html(result,filter='all',expanded=false) {
    const selected=result.catalog.filter(a=>filter==='all'||(filter==='earned'?a.unlocked:!a.unlocked));
    const cards=(expanded?selected:selected.slice(0,12)).map(a=>`<div class="award-card ${a.unlocked?'unlocked':''}"><span class="award-emoji" aria-hidden="true">${a.emoji}</span><h3>${esc(a.title)}</h3><p>${esc(a.description)}</p>${a.detail?`<small>${esc(a.detail)}</small>`:''}<progress value="${Math.min(a.current,a.goal)}" max="${a.goal}" aria-label="${esc(a.title)}"></progress><small>${a.unlocked?'Получено ✓':`${Math.min(a.current,a.goal)} / ${a.goal}`}</small></div>`).join('');
    return `<div class="awards-heading"><h3>Коллекция наград</h3><span>${result.earned} / 100</span></div><div class="award-filters">${[['all','Все'],['earned','Получены'],['next','Впереди']].map(([value,label])=>`<button type="button" data-award-filter="${value}" aria-pressed="${filter===value}">${label}</button>`).join('')}</div>${result.personal.length?`<details class="personal-awards"><summary>Награды твоих дел · ${result.personal.length}</summary>${result.personal.map(a=>`<p>🏅 ${esc(a.title)}</p>`).join('')}</details>`:''}<div class="award-grid">${cards||'<p class="muted">Здесь появятся первые награды. Можно начать с любого маленького дела.</p>'}</div>${selected.length>12?`<button type="button" class="button subtle" data-award-expand="${!expanded}">${expanded?'Свернуть':'Показать все '+selected.length}</button>`:''}`;
  }
  function checks(row,phone=false) {
    if(!row.checkins?.length) return '';
    const done=row.checkins.filter(item=>item.status==='done').length;
    return `<div class="checkin-list" role="group" aria-label="Подходы: ${esc(row.title)}"><p class="checkin-summary">${done} / ${row.checkins.length} подходов · каждый по 10 XP</p><div class="checkin-grid">${row.checkins.map(item=>`<button type="button" class="checkin ${item.status}" data-check-slot="${item.time}" ${phone?`data-slot-task="${esc(row.task_id)}"`:`data-slot-id="${row.id}"`} data-slot-date="${row.date}" data-slot-completed="${item.status!=='done'}" aria-pressed="${item.status==='done'}" aria-label="${item.status==='done'?'Отменить':'Отметить'} подход ${item.time}: ${esc(row.title)}" ${!item.available?'disabled':''}><span>${item.time}</span><strong>${{done:'✓',missed:'—',pending:'+',future:'·'}[item.status]}</strong></button>`).join('')}</div><small>Прочерк — пропуск. Нажми, чтобы заменить его галочкой.</small></div>`;
  }
  window.RitmProgress=Object.freeze({events,xp,timeLabel,awards,html,checks});
})();
