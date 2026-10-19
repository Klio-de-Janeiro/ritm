"use strict";

// Модель DOM проверяет обработчики и гонки; она не проверяет отрисовку браузера.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../android/app/src/main/assets');
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
for (const match of html.matchAll(/<(input|button|section|dialog|form|div|p|span|strong|h[123]|output|select|textarea|aside|meta|label|a|progress|article)\b([^>]*)>/g)) {
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
  if (selector === 'input[name="theme"]' || selector === '#appearanceForm input[name="theme"]') return elements.filter(e => e.name === 'theme');
  if (/^#[\w-]+$/.test(selector)) return ids.has(selector.slice(1)) ? [ids.get(selector.slice(1))] : [];
  if (selector === 'nav button' || selector === 'button') return elements.filter(e => e.tagName === 'BUTTON');
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
const properties=new Map(),storage=new Map();
const get=id=>ids.get(id);
document.documentElement.style.setProperty=(key,value)=>properties.set(key,value);
const data={settings:{hue:270,brightness:8,background:'#120a25',accent:'#b38aff',notifications:true},
  tasks:[],occurrences:[],pending_count:0,connected:false,address:'',last_sync:'',sync_error:'',
  conflicts:[],notification_permission:true};
const context={document,console,localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
  scrollTo(){},setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,confirm:()=>true};
context.window=context;
context.RitmNative={call(encoded){
  const request=JSON.parse(encoded);
  if(request.action==='settings') Object.assign(data.settings,request.values);
  if(request.action==='mutate'&&request.command.kind==='dismiss') {
    const row=data.occurrences.find(row=>row.task_id===request.command.task_id&&row.date===request.command.date);
    row.dismissed_at=request.command.dismissed?'2026-10-17T03:00:00+03:00':null;
  }
  return JSON.stringify({ok:true,data:request.action==='state'?data:{}});
}};
vm.createContext(context);
const run=code=>vm.runInContext(code,context);
run(fs.readFileSync(path.join(__dirname,'../ritm/web/theme.js'),'utf8'));
for(const name of ['awards.js','progress.js']) run(fs.readFileSync(path.join(__dirname,'../ritm/web',name),'utf8'));
run(fs.readFileSync(path.join(root,'appearance.js'),'utf8'));
run(fs.readFileSync(path.join(root,'mobile.js'),'utf8'));
(async()=>{
  get('taskRecurrence').value='every_3_hours';
  run('updateReminderFields()');
  assert.equal(get('taskCheckins').checked,false);
  assert.equal(get('taskRepeatFields').hidden,false);
  assert.equal(get('taskCheckinTimes').hidden,true);
  get('taskCheckins').checked=true;
  run('updateReminderFields()');
  assert.equal(get('taskCheckinTimes').hidden,false);
  get('taskRecurrence').value='once';
  run('updateReminderFields()');
  assert.equal(get('taskCheckins').checked,false);

  assert.equal(run('phoneAppearance(state.settings).background_color'),'#120a25');
  assert.equal(run('phoneAppearance(state.settings).palette_hue'),270);
  run("previewAppearance({border_radius:0,glow_color:'#ff0000',neon_intensity:100})");
  run('refresh(true)');
  assert.equal(properties.get('--radius'),'0px');
  assert.equal(properties.get('--glow'),'rgba(255,0,0,0.420)');
  assert.equal(data.settings.border_radius,undefined);
  get('appearanceForm').onsubmit({preventDefault(){}});
  assert.equal(data.settings.border_radius,0);
  assert.equal(data.settings.glow_color,'#ff0000');
  assert.equal(run('appearanceDirty'),false);
  run("previewAppearance({background_color:'#ffffff'})");
  get('appearanceCancel').onclick();
  assert.equal(properties.get('--background'),'#120a25');
  const light=elements.find(e=>e.name==='theme'&&e.value==='light');
  await get('appearanceForm').fire('input',light);
  assert.equal(document.documentElement.style.colorScheme,'light');
  get('appearanceForm').onsubmit({preventDefault(){}});
  run('refresh(true)');
  assert.equal(data.settings.theme,'light');
  assert.equal(data.settings.notifications,true);
  const value=run('today()');
  data.occurrences=[{task_id:'test',date:value,completed_at:value+'T18:00:00+03:00',status:'done',title:'<script>done</script>'}];
  run('refresh(true)');
  const grid=get('activityGrid').innerHTML;
  assert.equal((grid.match(/data-review-day=/g)||[]).length,91,get('toast').textContent);
  const start=new Date(value+'T12:00:00');start.setDate(start.getDate()-90);
  const padding=(start.getDay()+6)%7;
  assert.equal((grid.split('data-review-day=')[0].match(/activity-pad/g)||[]).length,padding);
  assert.match(grid,new RegExp('data-review-day="'+value+'" data-level="1"'));
  const scroll=document.querySelector('.activity-scroll');scroll.scrollLeft=42;
  data.occurrences.push({...data.occurrences[0],title:'Second'});
  run('refresh(true)');
  assert.equal(scroll.scrollLeft,42);
  assert.match(get('dayReview').innerHTML,/&lt;script&gt;done&lt;\/script&gt;/);
  data.occurrences.push({task_id:'late',date:'2026-10-01',title:'Лекция',status:'pending',can_dismiss:true,in_unfinished:true,dismissed_at:null});
  run('refresh(true)');
  assert.match(get('unfinishedList').innerHTML,/data-dismiss="late"/);
  await document.fire('click',new Element('button',{'data-dismiss':'late','data-date':'2026-10-01','data-dismissed':'true'}));
  assert.match(get('unfinishedList').innerHTML,/is-dismissed/);
  run('day=today();refresh(true)');
  assert.doesNotMatch(get('taskList').innerHTML,/is-dismissed/);
  assert.match(get('unfinishedList').innerHTML,/Вернуть/);
  assert.match(get('unfinishedList').innerHTML,/−5 XP/);
  await document.fire('click',new Element('button',{'data-dismiss':'late','data-date':'2026-10-01','data-dismissed':'false'}));
  assert.equal(data.occurrences.find(row=>row.task_id==='late').dismissed_at,null);
  const recent={task_id:'grace',date:'2026-10-17',title:'Вчерашнее дело',status:'pending',in_grace:true,in_unfinished:false,dismissed_at:null};
  data.occurrences=[recent];run('day=today();refresh(true)');
  assert.match(get('taskList').innerHTML,/Вчерашнее дело/);
  assert.doesNotMatch(get('unfinishedList').innerHTML,/Вчерашнее дело/);
  recent.in_grace=false;recent.in_unfinished=true;run('refresh(true)');
  assert.doesNotMatch(get('taskList').innerHTML,/Вчерашнее дело/);
  assert.match(get('unfinishedList').innerHTML,/Вчерашнее дело/);
  data.occurrences=[{...recent,date:value,title:'Зачёркнуто сегодня',dismissed_at:value+'T10:00:00+03:00'}];run('refresh(true)');
  assert.doesNotMatch(get('taskList').innerHTML,/Зачёркнуто сегодня/);
  assert.match(get('unfinishedList').innerHTML,/Зачёркнуто сегодня/);
  assert.match(get('dailyCount').textContent,/0 \/ 0/);

  assert.match(run("rowHtml({task_id:'range',title:'Спортик',time:'09:00',date:today(),recurrence:'every_3_hours',repeat_until:'23:00',status:'pending'})"),/09:00–23:00/);
  console.log('Mobile: legacy colors, preview during polling, save/cancel, light theme, calendar alignment, scroll and escaped history OK.');
})().catch(error=>{console.error(error);process.exitCode=1;});
