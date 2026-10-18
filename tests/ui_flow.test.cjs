"use strict";

// Модель DOM проверяет обработчики и гонки; она не проверяет отрисовку браузера.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../ritm/web');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const elements = [], ids = new Map();

class Element {
  constructor(tag = 'div', attrs = {}) {
    this.tagName = tag.toUpperCase(); this.attrs = attrs; this.dataset = {};
    for (const [key, value] of Object.entries(attrs)) if (key.startsWith('data-')) {
      this.dataset[key.slice(5).replace(/-([a-z])/g, (_, x) => x.toUpperCase())] = value;
    }
    this.id = attrs.id; this.name = attrs.name; this.type = attrs.type;
    this.value = attrs.value || ''; this.checked = Object.hasOwn(attrs, 'checked');
    this.disabled = Object.hasOwn(attrs, 'disabled'); this.hidden = Object.hasOwn(attrs, 'hidden');
    this.listeners = new Map(); this.textContent = ''; this.innerHTML = '';
    this.classList = {toggle() {}, add() {}, remove() {}};
    this.style = {setProperty() {}};
  }
  addEventListener(type, fn) { const list = this.listeners.get(type) || []; list.push(fn); this.listeners.set(type, list); }
  async fire(type, target = this) { for (const fn of this.listeners.get(type) || []) await fn({target, preventDefault() {}}); }
  setAttribute(key, value) { this.attrs[key] = value; }
  removeAttribute(key) { delete this.attrs[key]; }
  hasAttribute(key) { return Object.hasOwn(this.attrs, key); }
  closest() { return this; }
  focus() { document.activeElement = this; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  reset() { for (const e of elements.filter(e => e.id?.startsWith('task'))) { e.value = e.attrs.value || ''; e.checked = false; } }
  reportValidity() { return true; }
  getBoundingClientRect() { return {left:400,top:200,bottom:218}; }
  setCustomValidity() {}
  scrollIntoView() {}
  append() {}
}
for (const match of html.matchAll(/<(input|button|section|dialog|form|div|p|span|strong|h[123]|output|select|textarea|aside|meta|label|a)\b([^>]*)>/g)) {
  const attrs = {};
  for (const attr of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[attr[1]] = attr[2] || '';
  const element = new Element(match[1], attrs); elements.push(element);
  if (element.id) { assert.ok(!ids.has(element.id), 'duplicate ID: ' + element.id); ids.set(element.id, element); }
}
const quick_submit = new Element('button', {type:'submit'});
const name_submit = new Element('button', {type:'submit'});
function select(selector) {
  if (selector === '#quickAdd button[type=submit]') return [quick_submit];
  if (selector === '#nameForm button[type="submit"]') return [name_submit];
  if (selector === 'meta[name="theme-color"]') return elements.filter(e => e.tagName === 'META' && e.name === 'theme-color');
  if (selector === '#appearanceForm input[name="theme"]') return elements.filter(e => e.name === 'theme');
  if (/^#[\w-]+$/.test(selector)) return ids.has(selector.slice(1)) ? [ids.get(selector.slice(1))] : [];
  if (selector === 'dialog') return elements.filter(e => e.tagName === 'DIALOG');
  if (selector.startsWith('.')) {
    const klass = selector.slice(1).split('[')[0];
    return elements.filter(e => (e.attrs.class || '').split(' ').includes(klass));
  }
  const attr = selector.match(/^\[([\w-]+)\]$/);
  if (attr) return elements.filter(e => e.hasAttribute(attr[1]));
  throw new Error('Unsupported DOM selector: ' + selector);
}
const document = new Element('document');
document.documentElement = new Element('html');
document.body = new Element('body');
document.querySelector = selector => select(selector)[0] || null;
document.querySelectorAll = select;
document.createElement = tag => new Element(tag);
for (const e of elements) {
  e.querySelector = selector => {
    if (selector === 'label') return {textContent: e.dataset.autoColor};
    return document.querySelector(selector);
  };
  e.querySelectorAll = select;
  if (e.dataset.autoColor) e.parentElement = e;
}
const storage = new Map();
const timers = [];
const task_id = '11111111-1111-4111-8111-111111111111';
let templates = [{id:task_id, title:'План', notes:'', category:'Личное', priority:'normal',
  start_date:'2026-10-20', recurrence:'once', time:null, reminder:false}];
const current = {
  version:fs.readFileSync('ritm/__init__.py','utf8').match(/__version__ = "([^"]+)"/)[1], today:'2026-10-15', selected_date:'2026-10-15', server_time:'2026-10-15T12:00:00+03:00',
  tasks:[], history:[], notifications:[], has_tasks:true,
  week:Array.from({length:7}, (_, i) => ({date:`2026-10-${12+i}`, total:0, done:0})),
  activity:[{date:'2026-10-15', done:0}], runtime:{tray:false},
  summary:{done:0, xp:0, level:1, streak:0, longest_streak:0},
};
let patch_gate = null;
let notification_delivery = {accepted:true,message:'Windows приняла уведомление'};
const day_review = {date:'2026-10-14',is_past:true,done:[{title:'Сделано <script>',completed_at:'2026-10-14T21:00:00+03:00'}],unfinished:[{title:'Лекция & физика',completed_at:null}]};
const requests = [];
const context = {document, localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
  window:{scrollTo(){},innerWidth:1200,innerHeight:800}, navigator:{}, console,
  setTimeout:()=>1, clearTimeout(){}, setInterval:fn=>{timers.push(fn);return 1;}, clearInterval(){},
  fetch: async (url, options={}) => {
    const endpoint = url.split('?')[0].replace('/api/v1','');
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({endpoint, method:options.method, body});
    let result;
    if (endpoint === '/state') result = current;
    else if (endpoint === '/tasks') result = {tasks:templates};
    else if (endpoint === '/app/exit-fullscreen') result = {exited:true};
    else if (endpoint === '/notification-test') result = {delivery:notification_delivery};
    else if (endpoint === '/progress/day') result = day_review;
    else if (endpoint === '/occurrences/77/dismiss') {
      const row=[...current.tasks,...current.unfinished].find(row=>row.id===77);
      row.dismissed_at=body.dismissed?'2026-10-15T03:00:00+03:00':null;
      current.tasks=current.tasks.filter(item=>item.id!==77);
      current.unfinished=current.unfinished.filter(item=>item.id!==77);
      (body.dismissed?current.tasks:current.unfinished).push(row);
      result={id:77,dismissed_at:row.dismissed_at};
    }
    else if (endpoint === '/settings') {
      if (patch_gate) await patch_gate;
      Object.assign(current.settings, body); result = current.settings;
    } else if (endpoint === '/tasks/'+task_id) {
      Object.assign(templates[0], body); result=templates[0];
    } else throw new Error('Unexpected API: ' + endpoint);
    return {ok:true, json:async()=>JSON.parse(JSON.stringify(result))};
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root,'theme.js'),'utf8'),context);
for(const name of ['awards.js','progress.js']) vm.runInContext(fs.readFileSync(path.join(root,name),'utf8'),context);
current.settings = {...context.window.RitmTheme.defaults, display_name:'Клио', notifications:true, sound:true, autostart:false};
vm.runInContext(fs.readFileSync(path.join(root,'app.js'),'utf8'),context);
const get=id=>ids.get(id);
const run=code=>vm.runInContext(code,context);
async function tick() { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }
(async()=>{
  await run("load_state()");
  assert.equal(get('connectionError').hidden,true,get('connectionError').textContent);
  const installedVersion=current.version;
  current.version='0.0.0';
  await run('load_state()');
  assert.equal(get('connectionError').hidden,false);
  current.version=installedVersion;
  await run('load_state()');
  assert.equal(get('connectionError').hidden,true);
  assert.equal(get('displayName').value,'Клио');
  assert.equal(get('appearanceForm').listeners.get('input').length,1);
  assert.ok(!ids.has('dictorStatus') && !ids.has('lastDictorCommand'));

  get('taskRecurrence').value='every_3_hours';
  run('update_reminder_fields()');
  assert.equal(get('taskCheckins').checked,false);
  assert.equal(get('taskRepeatFields').hidden,false);
  assert.equal(get('taskCheckinTimes').hidden,true);
  get('taskCheckins').checked=true;
  await get('taskCheckins').fire('change');
  assert.equal(get('taskCheckinTimes').hidden,false);
  get('taskRecurrence').value='once';
  run('update_reminder_fields()');
  assert.equal(get('taskCheckins').checked,false);
  assert.equal(get('taskRepeatFields').hidden,true);

  // Поле имени переживает потерю фокуса и очередной серверный опрос.
  get('displayName').value='Новое имя';
  await get('displayName').fire('input'); document.activeElement=null;
  await run('load_state()');
  assert.equal(get('displayName').value,'Новое имя');
  await get('nameForm').fire('submit');
  assert.equal(current.settings.display_name,'Новое имя');

  // Клик на кнопку редактирования открывает шаблон и разрешает перенос раньше.
  await document.fire('click',new Element('button',{'data-edit':task_id}));
  assert.equal(get('taskDialog').open,true);
  assert.equal(get('taskDate').min,'2026-10-15');
  assert.equal(get('taskDate').value,'2026-10-20');
  get('taskTitle').value='Исправленная задача'; get('taskNotes').value='Новая заметка';
  get('taskDate').value='2026-10-16'; get('taskTime').value='19:00'; get('taskReminder').checked=true;
  await get('taskForm').fire('submit');
  assert.equal(templates[0].title,'Исправленная задача');
  assert.equal(templates[0].notes,'Новая заметка');
  assert.equal(templates[0].start_date,'2026-10-16');
  assert.equal(templates[0].time,'19:00');
  assert.equal(get('taskDialog').open,false);

  // Спектр меняет гамму; polling не стирает предпросмотр; сохранение фиксирует цвета.
  get('paletteHue').value='270';
  await get('appearanceForm').fire('input',get('paletteHue'));
  assert.equal(run('appearance_draft.palette_hue'),270);
  assert.equal(run('appearance_dirty'),true);
  await run('load_state()');
  assert.equal(get('paletteHue').value,270);
  get('color_background_color').value='#123456';
  await get('appearanceForm').fire('input',get('color_background_color'));
  await get('appearanceForm').fire('submit');
  assert.equal(current.settings.background_color,'#123456');
  assert.equal(current.settings.palette_hue,270);
  assert.equal(run('appearance_dirty'),false);
  assert.equal(get('appearanceSave').disabled,true);

  // Отмена и сброс имеют явную семантику, без скрытой записи в базу.
  get('paletteHue').value='90';
  await get('appearanceForm').fire('input',get('paletteHue'));
  await get('appearanceCancel').fire('click');
  assert.equal(run('appearance_draft.palette_hue'),270);
  await get('appearanceReset').fire('click');
  assert.equal(run('appearance_draft.palette_hue'),190);
  assert.equal(current.settings.palette_hue,270);
  assert.equal(run('appearance_dirty'),true);

  // Изменение во время сохранения остаётся отдельным несохранённым черновиком.
  let release; patch_gate = new Promise(resolve=>{release=resolve;});
  const saving=get('appearanceForm').fire('submit'); await tick();
  get('paletteHue').value='320';
  await get('appearanceForm').fire('input',get('paletteHue'));
  assert.equal(get('appearanceSave').disabled,true);
  release(); await saving; patch_gate=null;
  assert.equal(current.settings.palette_hue,190);
  assert.equal(run('appearance_draft.palette_hue'),320);
  assert.equal(run('appearance_dirty'),true);
  assert.equal(get('appearanceSave').disabled,false);
  await get('appearanceForm').fire('submit');
  assert.equal(current.settings.palette_hue,320);
  assert.equal(run('appearance_dirty'),false);
  // Esc не меняет полноэкранное окно, пока открыт диалог.
  run("state.runtime.platform='nt'");
  const keydown=document.listeners.get('keydown')[0];
  const keyup=document.listeners.get('keyup')[0];
  const before=requests.filter(r=>r.endpoint==='/app/exit-fullscreen').length;
  get('taskDialog').showModal();keydown({key:'Escape'});get('taskDialog').close();
  await keyup({key:'Escape'});
  assert.equal(requests.filter(r=>r.endpoint==='/app/exit-fullscreen').length,before);
  keydown({key:'Escape',repeat:false});await tick();
  assert.equal(requests.filter(r=>r.endpoint==='/app/exit-fullscreen').length,before);
  await keyup({key:'Escape',repeat:false});
  assert.equal(requests.filter(r=>r.endpoint==='/app/exit-fullscreen').length,before+1);
  keydown({key:'Escape',repeat:true});await keyup({key:'Escape',repeat:true});
  assert.equal(requests.filter(r=>r.endpoint==='/app/exit-fullscreen').length,before+1);
  let htmlExits=0;
  document.fullscreenElement={};document.exitFullscreen=async()=>{htmlExits++;document.fullscreenElement=null;};
  keydown({key:'Escape'});await keyup({key:'Escape'});
  assert.equal(htmlExits,1);
  assert.equal(requests.filter(r=>r.endpoint==='/app/exit-fullscreen').length,before+1);
  get('taskRecurrence').value='every_3_hours';get('taskReminder').checked=false;
  await get('taskRecurrence').fire('change');
  assert.equal(get('taskReminder').checked,true);assert.equal(get('taskTime').required,true);
  await run('open_task()');
  assert.equal(get('taskTime').value,'09:00');
  assert.equal(get('taskReminderEnd').value,'2026-10-15');
  get('taskDate').value='2026-10-16';await get('taskDate').fire('change');
  assert.equal(get('taskReminderEnd').value,'2026-10-16');
  get('taskRecurrence').value='event';
  await get('taskRecurrence').fire('change');
  assert.equal(get('taskEventFields').hidden,false);
  assert.equal(get('taskEventDate').required,true);
  assert.equal(get('taskReminder').checked,true);
  assert.equal(get('taskTime').disabled,true);
  get('taskRecurrence').value='daily';
  await get('taskRecurrence').fire('change');
  assert.equal(get('taskEventFields').hidden,true);
  assert.equal(get('taskEventDate').required,false);
  assert.equal(get('taskTime').disabled,false);
  await run(`open_task('${task_id}')`);
  get('taskRecurrence').value='event';
  await get('taskRecurrence').fire('change');
  get('taskEventDate').value='2026-10-20';get('taskReminderEnd').value='2026-10-20';get('taskEscalationDays').value='2';
  await get('taskForm').fire('submit');
  assert.equal(templates[0].event_date,'2026-10-20');
  assert.equal(templates[0].reminder_end_date,'2026-10-20');
  assert.equal(templates[0].escalation_days,2);
  await run(`open_task('${task_id}')`);
  assert.equal(get('taskEscalationDays').value,2);
  assert.equal(get('taskReminderEnd').value,'2026-10-20');
  await get('testNotification').fire('click');
  assert.equal(get('toast').textContent,'Windows приняла уведомление');
  notification_delivery={accepted:false,message:'Windows запретила уведомления'};
  await get('testNotification').fire('click');
  assert.equal(get('toast').textContent,'Windows запретила уведомления');
  assert.equal(get('testNotification').disabled,false);
  await run("show_day_review('2026-10-14')");
  assert.match(get('dayReview').innerHTML,/Сделано &lt;script&gt;/);
  assert.match(get('dayReview').innerHTML,/Лекция &amp; физика/);
  assert.match(get('dayReview').innerHTML,/Не выполнено к концу дня/);
  const cell=new Element('button',{'data-activity-day':'2026-10-14'});cell.isConnected=true;
  get('activityTooltip').offsetWidth=330;get('activityTooltip').offsetHeight=200;
  await get('heatmap').fire('mouseover',cell);await tick();
  assert.equal(get('activityTooltip').hidden,false);
  assert.match(get('activityTooltip').innerHTML,/Лекция &amp; физика/);
  const overdue={id:77,task_id,date:'2026-10-13',title:'Лекция <script>',category:'Учёба',status:'pending',can_dismiss:true,dismissed_at:null};
  assert.match(run('render_task('+JSON.stringify(overdue)+',false)'),/data-dismiss="77"/);
  assert.doesNotMatch(run('render_task('+JSON.stringify({...overdue,can_dismiss:false})+',false)'),/data-dismiss=/);
  current.unfinished=[overdue];
  await document.fire('click',new Element('button',{'data-dismiss':'77','data-dismissed':'true'}));
  assert.doesNotMatch(get('unfinishedList').innerHTML,/is-dismissed/);
  assert.match(get('taskList').innerHTML,/is-dismissed/);
  assert.match(get('taskList').innerHTML,/Лекция &lt;script&gt;/);
  assert.match(get('taskList').innerHTML,/−5 XP/);
  await document.fire('click',new Element('button',{'data-dismiss':'77','data-dismissed':'false'}));
  assert.equal(current.unfinished[0].dismissed_at,null);
  console.log('UI handlers: task editing, earlier date, name draft, live palette, polling, save/cancel/reset and concurrent changes OK.');
})().catch(error=>{console.error(error);process.exitCode=1;});
