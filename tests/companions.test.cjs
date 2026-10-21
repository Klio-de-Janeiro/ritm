const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map(),calls=[],copied=[],timers=[];
class Element {
  constructor(){this.value='';this.hidden=false;this.disabled=false;}
  set innerHTML(value){this.html=value;for(const match of value.matchAll(/id="([^"]+)"/g)) if(!nodes.has(match[1]))nodes.set(match[1],new Element());}
  get innerHTML(){return this.html;}
  prepend(){} append(){}
}
const host=new Element();
let status={enabled:true,error:'',addresses:['http://192.168.1.5:8769','http://10.0.0.5:8769'],usb_address:'http://127.0.0.1:8769',token:'test-only-secret',devices:[
  {id:'a',name:'Phone <A>',last_seen:'2026-10-21T12:00:00+03:00',transport:'USB'},
  {id:'b',name:'Tablet B',last_seen:'2026-10-21T12:01:00+03:00',transport:'LAN'}]};
const context={document:{hidden:false,querySelector:selector=>selector.startsWith('#settingsView')?host:nodes.get(selector.slice(1)),createElement:()=>new Element()},
  $:selector=>nodes.get(selector.slice(1)),current_view:'settings',setInterval:fn=>timers.push(fn),
  api:async(...args)=>{calls.push(args);return status;},toast(){},navigator:{clipboard:{writeText:async value=>copied.push(value)}},
  escape_html:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')};
vm.createContext(context);vm.runInContext(fs.readFileSync('ritm/web/companions.js','utf8'),context);
const get=id=>nodes.get(id);
(async()=>{
  await get('syncToggle').onclick();
  assert.match(get('syncDevices').innerHTML,/Phone &lt;A&gt;/);
  assert.match(get('syncDevices').innerHTML,/Tablet B/);
  assert.match(get('syncDevices').innerHTML,/USB/);assert.match(get('syncDevices').innerHTML,/LAN/);
  get('syncAddress').value=status.addresses[1];
  await get('syncCopy').onclick();await get('syncCopyUsb').onclick();
  assert.equal(copied[0],status.addresses[1]+'|'+status.token);
  assert.equal(copied[1],status.usb_address+'|'+status.token);
  await get('syncRefresh').onclick();assert.equal(get('syncAddress').value,status.addresses[1]);
  assert.ok(calls.every(args=>args.length===1),'adding/listing devices must not rotate pairing');
  status={...status,addresses:[],devices:[]};await get('syncRefresh').onclick();
  assert.equal(get('syncCopy').disabled,true);
  await get('syncCopyUsb').onclick();assert.equal(copied.at(-1),status.usb_address+'|'+status.token);
  console.log('Device list escapes names, shows USB/LAN, preserves selected address and copies independent pairing links OK.');
})().catch(error=>{console.error(error);process.exitCode=1;});
