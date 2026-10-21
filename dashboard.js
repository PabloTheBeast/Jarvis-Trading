'use strict';

const $ = id => document.getElementById(id);
const app = {state:null, agents:[], assets:[], revision:-1, connected:false,
  received:0, serverTime:0, tab:'overview', pending:0, polling:false, exchange:null, ticket:null, notices:null,desktopAlerts:false,paperMode:false,trading:null};
const tabs = ['overview','market','decisions','agents','portfolio','risk','history','system','exchange','supervision'];
const sek = new Intl.NumberFormat('sv-SE',{style:'currency',currency:'SEK',maximumFractionDigits:2});
const eur = new Intl.NumberFormat('sv-SE',{style:'currency',currency:'EUR',maximumFractionDigits:2});
const numbers = new Intl.NumberFormat('sv-SE',{maximumFractionDigits:8});
const finite = value => Number.isFinite(Number(value)) && value !== null && value !== '';
const money = value => finite(value) ? sek.format(Number(value)) : '—';
const euro = value => finite(value) ? eur.format(Number(value)) : '—';
const quantity = value => finite(value) ? numbers.format(Number(value)) : '—';
const percent = value => finite(value) ? (Number(value)*100).toFixed(2).replace('.',',')+' %' : '—';
const ratio = (value,total) => total>0 ? value/total : 0;
const localTime = value => {
  const date=new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString('sv-SE',{timeZone:'Europe/Stockholm',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—';
};
const tradeTime=value=>{
  const date=new Date(value);
  return Number.isFinite(date.getTime())?date.toLocaleString('sv-SE',{timeZone:'Europe/Stockholm',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'Okänd tid';
};
function node(tag,props={},children=[]){
  const item=document.createElement(tag);
  for(const [key,value] of Object.entries(props)){
    if(key==='class')item.className=value;
    else if(key==='text')item.textContent=String(value ?? '—');
    else if(key==='click')item.addEventListener('click',value);
    else item.setAttribute(key,String(value));
  }
  for(const child of children)if(child!==null)item.append(child instanceof Node?child:document.createTextNode(String(child)));
  return item;
}
function svgNode(tag,attrs={}){
  const item=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [key,value] of Object.entries(attrs))item.setAttribute(key,String(value));
  return item;
}
function text(id,value){$(id).textContent=String(value ?? '—');}
function badge(value,tone=''){return node('span',{class:'badge '+tone,text:value});}
function tone(value){return value>0?'positive':value<0?'negative':'';}
function statusTone(value){
  if(['ready','ok','CLOSED','FILLED','VALIDATED','AKTIV'].includes(value))return 'good';
  if(['UNKNOWN','VALIDATION_UNKNOWN','REJECTED','error','stale','gap'].includes(value))return 'bad';
  return 'warn';
}
function rows(id,values,empty,columns){
  $(id).replaceChildren(...(values.length?values.map(row=>node('tr',{},row.map(value=>node('td',{},[value ?? '—'])))):
    [node('tr',{},[node('td',{colspan:columns,class:'empty-row',text:empty})])]));
}
function facts(id,values){
  $(id).replaceChildren(...values.map(([label,value])=>node('div',{},[node('dt',{text:label}),node('dd',{},[value])])));
}
function metric(label,value,note=''){
  return node('article',{class:'stat'},[node('span',{text:label}),node('strong',{text:value}),node('small',{text:note})]);
}
function currentPrice(id){return app.state?.prices[id]?.at(-1)||0;}
function positionValue(id){return (app.state.positions[id]?.qty||0)*currentPrice(id);}
function invested(){return Object.keys(app.state.positions).reduce((sum,id)=>sum+positionValue(id),0);}
function equity(){return app.state.cash+invested();}
function agentValue(id){return Object.entries(app.state.positions).reduce((sum,[symbol,p])=>sum+(p.owners[id]||0)*currentPrice(symbol),0);}
function elapsed(ms){const sec=Math.max(0,Math.floor(ms/1000));return [Math.floor(sec/3600),Math.floor(sec%3600/60),sec%60].map(n=>String(n).padStart(2,'0')).join(':');}
function dataAge(){
  const stamp=app.state?.marketData?.fetchedAt||app.state?.marketData?.at;
  return stamp ? (app.serverTime-Date.parse(stamp))/1000+(performance.now()-app.received)/1000 : Infinity;
}
function dataFresh(){return app.state?.marketData.status==='ok' && dataAge()>=-5 && dataAge()<=30;}
function setTab(name,focus=false){
  if(!tabs.includes(name))return;
  app.tab=name;
  for(const tab of tabs){
    $('tab-'+tab).hidden=tab!==name;
    $('nav-'+tab).setAttribute('aria-selected',String(tab===name));
    $('nav-'+tab).tabIndex=tab===name?0:-1;
  }
  if(focus)$('nav-'+name).focus();
  if((name==='decisions'||name==='history'&&app.paperMode)&&app.state?.notifications.some(n=>!n.read))command('read-notices');
  if(name==='exchange')refreshExchange();
}
function toast(message){
  const item=node('div',{class:'toast'},[node('button',{text:'×','aria-label':'Stäng meddelande',click:()=>item.remove()}),node('strong',{text:'JARVIS CHEF'}),node('p',{text:message})]);
  $('toast-stack').prepend(item);
  while($('toast-stack').children.length>3)$('toast-stack').lastElementChild.remove();
}
function showError(message){
  $('command-error').hidden=!message;
  text('command-error',message);
}
function apply(snapshot){
  if(!snapshot.state||!Array.isArray(snapshot.agents)||!snapshot.paperOnly)throw Error('Ogiltigt svar från paper-servern.');
  app.connected=true;
  app.received=performance.now();
  app.serverTime=Date.parse(snapshot.serverTime)||Date.now();
  const revision=snapshot.state.revision||0;
  if(revision<app.revision)return;
  app.revision=revision;app.state=snapshot.state;app.agents=snapshot.agents;app.assets=snapshot.assets;
  app.paperMode=Boolean(snapshot.paperMode);document.body.classList.toggle('paper-mode',app.paperMode);
  $('nav-'+(app.paperMode?'history':'decisions')).append($('nav-alert-count'));
  text('paper-mode',app.paperMode?'Testläge · simulerat kapital':'Kraken-konto');
  const key=n=>n.id||n.tick+'|'+n.msg;
  const notices=new Set(app.state.notifications.map(key));
  if(app.notices)for(const entry of [...app.state.notifications].reverse())if(!app.notices.has(key(entry))){
    if(!app.paperMode&&entry.kind!=='CRYPTO_SIGNAL')continue;
    if(entry.kind==='CRYPTO_SIGNAL'&&(!dataFresh()||Date.parse(entry.validUntil)<=app.serverTime))continue;
    toast(entry.msg+(entry.kind==='CRYPTO_SIGNAL'?' Gäller till '+localTime(entry.validUntil)+'.':''));
    if(entry.kind==='CRYPTO_SIGNAL'&&app.desktopAlerts&&window.Notification?.permission==='granted'){
      try{new Notification('JARVIS CHEF',{body:entry.msg+' Gäller till '+localTime(entry.validUntil)+'.',tag:entry.id});}catch(error){app.desktopAlerts=false;}
    }
  }
  app.notices=notices;
  render();
}
async function getJSON(path){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);
  try{
    const response=await fetch(path,{signal:controller.signal,cache:'no-store'});
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'Servern svarade '+response.status);
    return data;
  }finally{clearTimeout(timeout);}
}
async function post(path,data={},approval=null){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),60000);
  const headers={'Content-Type':'application/json','X-Jarvis-Client':'dashboard','Idempotency-Key':crypto.randomUUID()};
  if(approval)headers['X-Jarvis-Approval']=approval.trim();
  try{
    const response=await fetch(path,{method:'POST',headers,body:JSON.stringify(data),signal:controller.signal});
    const body=await response.json();
    if(!response.ok)throw Error(body.error||'Kommandot misslyckades.');
    return body;
  }catch(error){
    if(error.name==='AbortError')throw Error('Svaret dröjer. Kontrollera status innan du försöker igen; en liveorder kan redan ha skickats.');
    throw error;
  }finally{clearTimeout(timeout);}
}
async function poll(){
  if(app.polling||app.pending)return;
  app.polling=true;
  try{apply(await getJSON('/api/state'));}
  catch(error){app.connected=false;renderHealth();}
  finally{app.polling=false;refreshExchange();refreshTrading();}
}
async function command(name,data={}){
  if(app.pending&&name!=='stop')return;
  let approval=null;
  if(name==='release'||name==='approve'){
    const instruction=name==='release'?'python3 -m backend.ops approve release --amount '+data.amount:'python3 -m backend.ops approve resume';
    approval=prompt('Skapa en engångskod lokalt:\n'+instruction+'\nKlistra in koden här (120 sekunder).');
    if(!approval)return;
  }
  app.pending++;showError('');renderHealth();
  try{apply(await post('/api/commands/'+name,data,approval));}
  catch(error){showError(error.message);}
  finally{app.pending--;renderHealth();}
}
function renderOverview(){
  const s=app.state,inv=invested(),total=equity()+s.vault;
  text('equity',money(equity()));text('cash',money(s.cash));text('vault',money(s.vault));text('total',money(total));
  text('pnl',money(total-s.ownerNetDeposits)+' utan ägarrörelser');$('pnl').className=tone(total-s.ownerNetDeposits);
  text('invested',money(inv));text('exposure',percent(ratio(inv,equity()))+' / 70 %');text('drawdown',percent(s.maxdd));
  text('tick','Steg '+s.tick);text('clock',app.paperMode?'Serversteg '+s.tick:'Bevakning · '+localTime(app.serverTime));
  text('status',s.running?'Kör':'Pausad');text('run',s.running?'Pausa paper':'Starta paper');
  $('market-auto').checked=s.marketAuto;$('auto-pace').value=String(s.autoPace);
  const values=s.hist.slice(-130),lo=Math.min(...values)*.995,hi=Math.max(...values)*1.005,span=Math.max(hi-lo,1);
  const chart=$('chart');chart.replaceChildren();
  for(let i=0;i<5;i++)chart.append(svgNode('line',{x1:10,x2:770,y1:28+i*47,y2:28+i*47,stroke:'#2c3d54','stroke-dasharray':'4 6'}));
  chart.append(svgNode('polyline',{points:values.map((v,i)=>(10+760*i/Math.max(1,values.length-1))+','+(224-(v-lo)/span*190)).join(' '),fill:'none',stroke:'#67d1d3','stroke-width':2.5}));
  if(values.length===1)chart.append(svgNode('circle',{cx:10,cy:224-(values[0]-lo)/span*190,r:4,fill:'#67d1d3'}));
  for(const [value,y] of [[hi,17],[lo,245]]){const label=svgNode('text',{x:10,y});label.textContent=money(value);chart.append(label);}
}
function agentCard(agent){
  const s=app.state,id=agent.id,selected=$('agent-select').value===id;
  if(!app.paperMode){
    const reports=Object.values(s.agentReports||{}).filter(r=>r.agentId===id);
    return node('button',{type:'button',class:'agent-chip','data-agent-id':id,click:()=>{$('agent-select').value=id;setTab('agents');renderAgents();}},[
      node('strong',{text:agent.name}),node('div',{class:'agent-sub',text:agent.strategy}),
      badge(reports.some(r=>r.status==='reading')&&dataFresh()?'Läser kurser':reports.length?'Inväntar kurser':'Datakälla ej ansluten'),
      node('p',{class:'agent-note',text:app.trading?.session?.status==='RUNNING'&&app.trading.session.budgets[id]?'Godkänd sessionsbudget: '+euro(app.trading.session.budgets[id]):reports.length?'Rapporterar till CHEF · ingen aktiv orderrätt':'Svenska aktier ej anslutna'})]);
  }
  return node('button',{type:'button',class:'agent-chip'+(selected?' selected':''),'data-agent-id':id,click:()=>{
    $('agent-select').value=id;setTab('agents');renderAgents();$('agent-detail-panel').scrollIntoView({block:'nearest'});
  }},[node('div',{class:'agent-top'},[node('strong',{text:agent.name}),badge(s.agentStatus[id]==='AKTIV'?'Aktiv':'Karantän',s.agentStatus[id]==='AKTIV'?'good':'warn')]),
    node('div',{class:'agent-sub',text:agent.strategy}),node('div',{class:'agent-value '+tone(s.agentRealized[id]),text:money(s.agentRealized[id])}),
    node('div',{class:'agent-note',text:'Realiserat · innehav '+money(agentValue(id))})]);
}
function renderAgents(){
  const s=app.state,select=$('agent-select'),old=select.value;
  if(select.options.length!==app.agents.length)select.replaceChildren(...app.agents.map(a=>node('option',{value:a.id,text:a.name})));
  if(app.agents.some(a=>a.id===old))select.value=old;
  $('agent-compact').replaceChildren(...app.agents.map(agentCard));$('agent-compact-all').replaceChildren(...app.agents.map(agentCard));
  text('nav-agent-count',app.agents.length);text('agent-count',app.agents.length+' / 30');
  text('agent-summary',app.agents.filter(a=>s.agentStatus[a.id]==='AKTIV').length+' aktiva · '+app.agents.length+' totalt');
  text('agents-caption','Välj en agent. '+app.agents.filter(a=>s.agentStatus[a.id]==='KARANTÄN').length+' agenter i karantän.');
  if(!app.paperMode){text('agent-summary','3 kryptoagenter rapporterar till CHEF · övriga datakällor ej anslutna');text('agents-caption','Välj en agent för marknadsbedömningar och verkliga affärer.');}
  const agent=app.agents.find(a=>a.id===select.value)||app.agents[0];if(!agent)return;
  if(!app.paperMode){
    const reports=Object.values(s.agentReports||{}).filter(r=>r.agentId===agent.id);
    const fills=(app.exchange?.tradeHistory?.executions||[]).filter(f=>f.agentId===agent.id);
    $('agent-detail').replaceChildren(node('h3',{text:agent.name+' · '+agent.strategy}),
      ...reports.map(r=>node('p',{text:(r.instrument==='BTC-SIM'?'BTC/EUR':'ETH/EUR')+' · läst '+tradeTime(r.checkedAt)+' · '+r.side+' · '+r.reason})),
      node('p',{text:fills.length?fills.map(f=>(f.side==='buy'?'Köpt ':'Sålt ')+f.quantity+' '+f.pair+' · '+tradeTime(f.executedAt)).join(' | '):'Inga verkliga affärer från denna agent. Orderrätt finns bara under en godkänd session.'}));
    return;
  }
  const summary=node('div',{class:'stats small-stats'},[metric('Strategi',agent.strategy),metric('Budget',money(agent.budget)),metric('Innehav',money(agentValue(agent.id))),metric('Realiserat',money(s.agentRealized[agent.id]))]);
  const positions=app.assets.filter(a=>(s.positions[a.id]?.owners[agent.id]||0)>0);
  const positionsTable=node('table',{},[node('thead',{},[node('tr',{},['Instrument','Antal','Marknadsvärde','Resultat'].map(v=>node('th',{text:v})))]),
    node('tbody',{},positions.length?positions.map(a=>{const qty=s.positions[a.id].owners[agent.id];return node('tr',{},[a.id,quantity(qty),money(qty*currentPrice(a.id)),money(qty*currentPrice(a.id)-(s.ownerCost[agent.id][a.id]||0))].map(v=>node('td',{text:v})));}):
      [node('tr',{},[node('td',{colspan:4,class:'empty-row',text:'Inga öppna innehav.'})])])]);
  const trades=s.orderHistory.filter(o=>o.agent===agent.id).slice(0,20);
  const history=node('div',{},trades.length?trades.map(o=>node('div',{class:'event'},[node('small',{text:o.orderId+' · '+o.id}),node('span',{text:o.side+' '+quantity(o.qty)+' · '+money(o.amount)+' · avgift '+money(o.fee)})])):[node('p',{class:'muted',text:'Inga affärer ännu.'})]);
  $('agent-detail').replaceChildren(summary,node('h4',{text:'Innehav'}),node('div',{class:'scroll'},[positionsTable]),node('h4',{text:'Senaste paper-affärer'}),history);
}
function renderPortfolio(){
  const s=app.state;
  rows('positions',app.assets.filter(a=>s.positions[a.id]).map(a=>[a.id,a.type,quantity(s.positions[a.id].qty),money(positionValue(a.id)),money(positionValue(a.id)-s.positions[a.id].cost)]),'Inga positioner ännu.',5);
  text('owner-capital',money(s.ownerNetDeposits));text('investment-result',money(equity()+s.vault-s.ownerNetDeposits));
  const next=[15000,25000,50000,100000,200000].find(n=>n>equity());text('agent-next',app.agents.length>=30?'Max antal agenter':next?money(next):'Högsta kapitalnivå');
  $('owner-ledger').replaceChildren(...s.ownerMovements.slice(0,12).map(v=>node('div',{class:'event'},[node('small',{text:'Dag '+v.day}),node('span',{text:v.type+' · '+money(v.amount)})])));
  text('vaultdetails','Skyddat paper-saldo '+money(s.vault)+'. Ny realiserad vinst över avsatt topp: '+money(Math.max(0,s.realizedTotal-s.realizedHighWater))+'.');
  text('next-sweep-date',s.nextSweepDate||'Efter sju kalenderdagar');
  $('ledger').replaceChildren(...s.vaultMovements.slice(0,20).map(v=>node('div',{class:'event'},[node('small',{text:'Dag '+v.day}),node('span',{text:v.type+' · '+money(v.amount)})])));
  const report=s.dayReports[0];text('day-report',report?'Senaste rapport: '+report.date+' · realiserat '+money(report.dayNet)+' · vinstskydd '+money(report.swept):'Inga avslutade dagar ännu.');
  rows('day-report-history',s.dayReports.slice(0,100).map(r=>[r.day,r.date,money(r.dayNet),money(r.swept),money(r.trading),money(r.vault)]),'Inga dagsrapporter.',6);
  $('endday').disabled=s.dayClosed||app.pending>0;
}
function renderRisk(){
  const s=app.state,q=s.globalPause,inv=invested();
  text('recovery',q?(s.approvalPending?'Skuggtest godkänt — väntar på ägargodkännande. ':'Riskpaus — nya paper-köp stoppade. ')+q.reason+' · '+q.trades+' skuggavslut · netto '+money(q.pnl)+' · nästa granskning steg '+q.next:'Normal paper-drift. Ingen global riskpaus.');
  $('recovery').className='notice'+(q?' warn':'');
  $('approve').disabled=!s.approvalPending||app.pending>0||!app.connected;
  $('risk').replaceChildren(...[
    ['Samlad exponering',percent(ratio(inv,equity()))+' / 70 %'],
    ['Per instrument','Högst 25 %'],['Korrelerad grupp','Högst 40 %'],['Ordertak','Högst 2 000 SEK'],
    ['Avgift i paper-modellen','0,15 %'],['Köp',s.halt?'Blockerade':'Tillåtna efter orderkontroll'],
    ['Belåning / blankning','Avstängt']].map(([a,b])=>node('div',{class:'event'},[node('small',{text:a}),node('strong',{text:b})])));
  const quarantined=app.agents.filter(a=>s.quarantine[a.id]);
  $('oversight').replaceChildren(node('p',{class:'caption',text:'Minst 40 steg, tre avslut, positivt skuggnetto, minst 50 % vinster och högst 40 SEK skuggdrawdown.'}),
    ...quarantined.map(a=>{const r=s.quarantine[a.id];return node('div',{class:'event'},[node('strong',{text:a.name}),node('p',{text:r.trades+' avslut · '+money(r.pnl)+' · kontroll steg '+r.until})]);}),
    ...(quarantined.length?[]:[node('p',{class:'muted',text:'Inga agenter i karantän.'})]));
  $('memory').replaceChildren(...s.memory.slice(0,30).map(v=>node('div',{class:'event'},[node('small',{text:v.agent+' · '+v.id+' · '+v.strategy}),node('span',{class:tone(v.pnl),text:money(v.pnl)})])));
}
function renderHistory(){
  const s=app.state,orders=s.orderHistory;
  text('buy-count',orders.filter(o=>o.side==='KÖP').length);text('sell-count',orders.filter(o=>o.side==='SÄLJ').length);
  text('today-orders',orders.filter(o=>o.day===s.day).length);text('order-rate',(orders.length/Math.max(1,s.tick)).toFixed(2));
  text('order-audit','Paper-kapital avstämt vid senaste transaktion · '+s.orders.length+' order. Verkligt Kraken-konto ingår inte i denna avstämning.');
  rows('order-status-rows',s.orders.slice(0,100).map(o=>[node('span',{class:'mono',text:o.orderId}),o.agentId+' / '+o.instrument,o.side,badge(o.status,statusTone(o.status)),quantity(o.filledQty)+' / '+quantity(o.requestedQty),money(o.feeTotal)]),'Inga paper-order ännu.',6);
  const unread=s.notifications.filter(n=>!n.read&&(app.paperMode||n.kind==='CRYPTO_SIGNAL')).length;text('nav-alert-count',unread);$('nav-alert-count').hidden=!unread;
  $('notice-list').replaceChildren(...(s.notifications.length?s.notifications.slice(0,50).map(n=>node('div',{class:'event'},[node('small',{text:localTime(n.date)+' · dag '+n.day}),node('span',{text:n.msg})])):[node('p',{class:'muted',text:'Inga meddelanden.'})]));
  $('log').replaceChildren(...s.logs.slice(0,80).map(l=>node('div',{class:'event'},[node('small',{text:localTime(l.at)+' · '+(l.level||'INFO')+' · '+l.agent}),node('span',{text:l.msg})])));
}
function renderMarket(){
  const s=app.state,symbol=$('market-symbol').value,analysis=s.marketAnalysis?.[symbol],book=s.marketData.orderBooks?.[symbol],rate=s.marketData.eurSek;
  const name=symbol==='BTC-SIM'?'BTC / EUR':'ETH / EUR';
  text('market-chart-title',name+' · 5 minuter');
  text('market-quality',analysis?analysis.reason:'Analys saknas. Uppdatera marknaden för att läsa in analysen.');
  $('market-quality').className='notice'+(!dataFresh()||analysis?.status!=='ready'?' warn':'');
  $('analysis-indicators').replaceChildren(metric('SMA 20',euro(analysis?.sma20),'20 avslutade staplar'),metric('SMA 50',euro(analysis?.sma50),'50 avslutade staplar'),
    metric('RSI 14',analysis?.rsi14?.toFixed(1).replace('.',',')||'—','Historiskt momentum'),metric('ATR 14',finite(analysis?.atrPercent)?Number(analysis.atrPercent).toFixed(3).replace('.',',')+' %':'—','Historiskt prisintervall'));
  facts('market-quotes',[['Köpsida, EUR',book&&rate?euro(book.bids[0][0]/rate):'—'],['Säljsida, EUR',book&&rate?euro(book.asks[0][0]/rate):'—'],
    ['Spread',book?percent(book.asks[0][0]/book.bids[0][0]-1):'—'],['Kursmarkering, SEK',currentPrice(symbol)?money(currentPrice(symbol)):'—'],
    ['ECB EUR/SEK',rate?quantity(rate)+' · '+s.marketData.fxDate:'—'],['Data hämtade',localTime(s.marketData.fetchedAt)],['Senast avslutade stapel',localTime(analysis?.closedAt)]]);
  $('market-signals').replaceChildren(...(analysis?.status==='ready'?Object.entries(analysis.signals).map(([name,value])=>node('div',{class:'event'},[
    node('strong',{text:name}),badge(value||'Avvakta',value==='BUY'?'good':value==='SELL'?'warn':''),node('p',{class:'caption',text:analysis.reasons[name]})])):
    [node('p',{class:'muted',text:analysis?.reason||'Ingen verifierad analys ännu.'})]));
  rows('prices',app.assets.map(a=>[a.id,a.type==='Krypto'?(s.marketData.connector==='kraken-public'?'Kraken, senast hämtad mittkurs':'Startreferens · ej börsdata'):'Syntetisk modell',currentPrice(a.id)?money(currentPrice(a.id)):'—',percent(s.returns[a.id])]),'Inga priser.',4);
  candleChart(analysis?.candles||[]);
}
function candleChart(all){
  const chart=$('market-chart');chart.replaceChildren();
  const candles=all.slice(-60);
  if(!candles.length){const empty=svgNode('text',{x:450,y:145,'text-anchor':'middle'});empty.textContent='Inväntar avslutade Kraken-staplar';chart.append(empty);return;}
  const min=Math.min(...candles.map(r=>r.low)),max=Math.max(...candles.map(r=>r.high)),span=Math.max(max-min,max*.001);
  const y=value=>255-(value-min)/span*215,step=800/candles.length;
  for(let i=0;i<5;i++){
    const level=min+span*i/4;
    chart.append(svgNode('line',{x1:65,x2:890,y1:y(level),y2:y(level),stroke:'#2c3d54','stroke-dasharray':'3 5'}));
    const label=svgNode('text',{x:2,y:y(level)+4});label.textContent=quantity(level);chart.append(label);
  }
  candles.forEach((c,i)=>{
    const x=75+step*i,color=c.close>=c.open?'#8be0b6':'#ff9baa';
    const wick=svgNode('line',{x1:x,x2:x,y1:y(c.high),y2:y(c.low),stroke:color});
    const body=svgNode('rect',{x:x-Math.max(2,step*.5)/2,y:Math.min(y(c.open),y(c.close)),width:Math.max(2,step*.5),height:Math.max(1,Math.abs(y(c.open)-y(c.close))),fill:color});
    const title=svgNode('title');title.textContent=localTime(c.time*1000)+' · O '+c.open+' H '+c.high+' L '+c.low+' C '+c.close+' EUR';body.append(title);chart.append(wick,body);
  });
  for(const [c,x] of [[candles[0],65],[candles.at(-1),750]]){const label=svgNode('text',{x,y:285});label.textContent=localTime(c.time*1000).slice(6,11);chart.append(label);}
}
function renderDecisions(){
  const s=app.state,labels={BUY:'Möjligt köpläge',SELL:'Möjligt säljläge om du äger',WAIT:'Avvakta',BLOCKED:'Riskpaus'};
  text('overview-advisory',['BTC-SIM','ETH-SIM'].map(symbol=>{
    const r=s.chefSignals?.[symbol],fresh=r&&dataFresh()&&Date.parse(r.validUntil)>app.serverTime+(performance.now()-app.received);
    return (symbol==='BTC-SIM'?'BTC: ':'ETH: ')+(fresh?labels[r.side]:'Inväntar färska data');
  }).join(' · '));
  $('chef-signals').replaceChildren(...['BTC-SIM','ETH-SIM'].map(symbol=>{
    const report=s.chefSignals?.[symbol],fresh=report&&dataFresh()&&Date.parse(report.validUntil)>app.serverTime+(performance.now()-app.received);
    return node('article',{class:'panel'},[node('h3',{text:symbol==='BTC-SIM'?'CHEF · BTC/EUR':'CHEF · ETH/EUR'}),
      badge(fresh?labels[report.side]:'Avvakta · ingen färsk bedömning',fresh&&report.side==='BUY'?'good':fresh&&report.side==='SELL'?'warn':''),
      node('p',{text:fresh?report.reason:'Inväntar färska kryptodata och en aktuell avslutad stapel. Starta bevakningsläget eller uppdatera marknaden.'}),
      node('p',{class:'caption',text:'Stapel avslutad: '+localTime(report?.closedAt)+' · Referenskurs: '+euro(report?.referenceEUR)}),
      node('p',{class:'caption',text:'Bedömningen är ingen affär. Aktiva sessioner kan följa signalen efter separat konto- och riskkontroll.'})]);
  }));
  rows('agent-report-rows',Object.values(s.agentReports||{}).map(r=>[r.agent+' · '+(r.instrument==='BTC-SIM'?'BTC/EUR':'ETH/EUR'),localTime(r.checkedAt),
    badge(dataFresh()&&r.status==='reading'&&Date.parse(r.closedAt)+300000>app.serverTime+(performance.now()-app.received)?(r.eligible?(labels[r.side]||'Avvakta'):'Skuggranskning'):'Inväntar färska data'),
    node('span',{class:'explain',text:r.reason+' '+r.conditions})]),'Inväntar den första kurshämtningen. Ingen insättning behövs.',4);
  const notices=s.notifications.filter(n=>n.kind==='CRYPTO_SIGNAL').slice(0,50);
  $('crypto-notices').replaceChildren(...(notices.length?notices.map(n=>node('div',{class:'event'},[
    node('small',{text:localTime(n.date)+(!dataFresh()||Date.parse(n.validUntil)<=app.serverTime+(performance.now()-app.received)?' · Historisk signal':' · Bevakningssignal')}),node('span',{text:n.msg})])):
    [node('p',{class:'muted',text:'Inget nytt köp-/säljläge ännu. CHEF avvaktar när strategierna är oense; meddelanden skapas inte vid varje kursuppdatering.'})]));
  rows('decision-rows',(app.state.lastDecisions||[]).slice(0,100).map(d=>[localTime(d.at),d.agent,d.instrument,badge(d.side,d.side==='BUY'?'good':d.side==='SELL'?'warn':''),node('span',{class:'explain',text:d.reason})]),
    'Inga paper-handelsbeslut. Bevakningsrapporterna visas ovan och kräver inte start av paper-motorn.',5);
}
function renderSession(){
  const s=app.state;
  text('active-time',elapsed(s.activeMs));text('day-index',s.day);text('days-closed',s.dayReports.length);text('day-steps',s.tick-s.dayStartTick);
  text('day-status',s.dayClosed?(s.canOpenDay?'Nästa datum kan startas':'Avslutad till nästa Stockholm-datum'):'Pågående');
  text('day-history',s.dayReports.length?'Senast avslutad: '+s.dayReports[0].date:'Inga avslutade rapportdatum.');
  text('pace-info','Paper-uppdatering var '+s.autoPace/1000+' sekund. Strategiernas femminutersstaplar är oberoende av detta.');
}
function renderHealth(){
  const transport=app.connected && performance.now()-app.received<12000;
  const fresh=transport&&dataFresh();
  text('connection',!transport?'Servern kan inte nås — visade data kan vara gamla.':fresh?'Server ansluten · marknadsdata färska':'Server ansluten · marknadsdata saknas eller är gamla');
  $('connection').dataset.level=transport?(fresh?'good':'warn'):'bad';
  text('overview-data',fresh?'Färska':app.state?.marketData.status==='error'?'Hämtning misslyckades':'Väntar / gamla');
  const blocked=app.state?.dayClosed&&!app.state?.canOpenDay;
  for(const id of ['step','run','deposit-btn','withdraw-btn','weekly','release','testpause'])$(id).disabled=!transport||app.pending>0||Boolean(blocked&&['step','run'].includes(id));
  $('approve').disabled=!transport||app.pending>0||!app.state?.approvalPending;
  const analysis=app.state?.marketAnalysis||{},ready=Object.values(analysis).filter(a=>a.status==='ready').length;
  const checks=[['Server',transport?'Ansluten':'Frånkopplad',transport?'Lokalt API svarar.':'Kontrollera Python-processen.',transport?'good':'bad'],
    ['Marknadsdata',fresh?'Färska':'Inte färska',Number.isFinite(dataAge())?'Äldsta orderdjup: '+Math.floor(Math.max(0,dataAge()))+' sekunder.':'Ingen lyckad hämtning.',fresh?'good':'warn'],
    ['Marknadsanalys',ready+' / 2 par redo','Avslutade femminutersstaplar, SMA, RSI och ATR.',ready===2&&fresh?'good':'warn'],
    [app.paperMode?'Testmotor':'Automatisk agenthandel',app.paperMode?(app.state?.running?'Kör':'Pausad'):(app.trading?.session?.status||'Inte startad'),app.paperMode?'Uttryckligt testläge.':'Orderrätt gäller bara inom en ägargodkänd session och dess budget.',''],
    ['CHEF',app.paperMode?(app.state?.halt?'Riskpaus':'Ingen global paus'):(app.trading?.session?.status||'Bevakning'),'Verkliga köp kräver aktiv session och godkänd konto-/riskkontroll.',''],
    ['Svenska aktiekurser','Ingen extern källa','Aktier/ETF är syntetiska. Nordnet kräver separat anslutning och API-/dataåtkomst. Kalender: XSTO 2025–2027.','warn']];
  $('system-checks').replaceChildren(...checks.map(([label,value,detail,color])=>node('article',{class:'status-card'},[badge(label,color),node('strong',{text:value}),node('p',{text:detail})])));
}
function render(){
  if(!app.state)return;
  renderOverview();renderAgents();renderPortfolio();renderRisk();renderHistory();renderMarket();renderDecisions();renderSession();renderHealth();
  if(app.exchange)renderExchange();
}
async function refreshExchange(){
  try{app.exchange=await getJSON('/api/exchange');renderExchange();}
  catch(error){text('exchange-message','Börsstatus kan inte hämtas: '+error.message);}
}
async function refreshTrading(){
  try{
    const result=await getJSON('/api/trading-session'),old=app.trading?.session,next=result.session;
    if(old&&next&&Date.parse(next.updatedAt)<Date.parse(old.updatedAt))return;
    app.trading=result;renderTrading();refreshSupervision();
  }catch(error){
    if(app.trading){app.trading.enabled=false;renderTrading();}
    text('session-message','Sessionsstatus kan inte hämtas. En redan startad session kan köra vidare. Kontrollera servern och Kraken; inga nya startkommandon skickas automatiskt.');
  }
}
function renderTrading(){
  const t=app.trading,s=t?.session;
  const labels={PLANNED:'Inväntar godkännande',RUNNING:'Trading pågår',WATCHING:'Efterbevakning · inga nya köp',STOPPING:'Avslutar · order kan pågå',PAUSED:'Riskpaus',RECOVERY:'Omstart · manuell avstämning',STOPPED:'Trading avslutad',STOPPED_HOLDINGS:'Avslutad · innehav behållna',CANCELLED:'Plan avbruten'};
  text('session-state',s?labels[s.status]||s.status:'Bevakning');
  $('session-state').className='badge '+(s?.status==='RUNNING'?'warn':['PAUSED','RECOVERY'].includes(s?.status)?'bad':'');
  text('session-message',s?.reason||'Ingen session är startad. Insättningar aktiverar aldrig handel.');
  const active=['RUNNING','WATCHING','STOPPING','PAUSED','RECOVERY'].includes(s?.status);
  const locked=active||s?.status==='PLANNED';
  for(const id of ['session-capital','session-duration','session-policy','session-protection'])$(id).disabled=locked;
  if(locked){$('session-capital').value=s.capitalEUR;$('session-duration').value=String(s.durationMinutes);$('session-policy').value=s.endPolicy;$('session-protection').value=String(s.protectionMinutes||0);}
  $('session-plan').disabled=app.pending>0||locked;
  $('session-start').disabled=app.pending>0||!t?.enabled||s?.status!=='PLANNED'||!Object.values(s?.budgets||{}).some(value=>Number(value)>0);
  text('session-stop',s?.status==='PLANNED'?'Avbryt plan':s?.status==='WATCHING'?'Avsluta bevakning':'Avsluta trading');
  $('session-stop').disabled=!s||!['PLANNED','RUNNING','WATCHING','STOPPING','PAUSED','RECOVERY'].includes(s.status);
  if(!s){text('session-plan-details','Förbered en budgetplan för att se CHEF:s fördelning. Nollsaldo går att granska men kan inte starta trading.');$('session-end-decisions').replaceChildren();return;}
  const details=node('dl',{class:'facts'});
  details.append(...[['Kapitaltak',euro(s.capitalEUR)],['Godkända nya köp totalt, inklusive avgifter',euro(s.newCashEUR)],['Verkliga köp inkl. avgifter hittills',euro(t.grossBuysEUR)],['Återstående mandat för nya köp',euro(t.remainingNewBuysEUR)],
    ...Object.entries(s.budgets).map(([agent,value])=>[agent.toUpperCase()+' · intern budget',euro(value)]),
    ['Förlust-/drawdowngräns',euro(s.maxLossEUR)],['Ordertak',euro(s.maxOrderEUR)],['Slut senast',tradeTime(s.expiresAt)],['Lokal efterbevakning',s.protectionMinutes?'Till '+tradeTime(s.protectionExpiresAt)+' · får sälja, aldrig köpa':'Ingen'],
    ['Avslutspolicy',s.endPolicy==='assess'?'CHEF säljer vid säljsignal/stop-loss, behåller annars':'Behåll alla innehav'],['Sessions-ID',s.id]]
    .map(([label,value])=>node('div',{},[node('dt',{text:label}),node('dd',{class:'mono',text:value})])));
  $('session-plan-details').replaceChildren(details,node('p',{class:'caption',text:t.enabled?'För start: python3 -m backend.ops approve session-start --session '+s.id:
    'Bevakningsläge eller automatisk handel avstängd. För sessionshandel krävs serverstart med JARVIS_KRAKEN_LIVE=1 och JARVIS_AUTO_TRADING=1, utan JARVIS_MONITOR_ONLY=1. En ny granskad plan och ägarkod krävs ändå.'}));
  $('session-end-decisions').replaceChildren(...(s.endDecisions||[]).map(d=>node('p',{text:d.agentId.toUpperCase()+' · '+d.pair+' · '+quantity(d.quantity)+' · '+(d.decision==='HOLD'?'BEHÅLL':'SÄLJ PÅGÅR')+' · '+d.reason+' · '+tradeTime(d.at)})));
}
async function sessionAction(action){
  const s=app.trading?.session;let data,approval=null;
  if(action==='plan')data={capitalEUR:$('session-capital').value.trim().replace(',','.'),endPolicy:$('session-policy').value,durationMinutes:Number($('session-duration').value),protectionMinutes:Number($('session-protection').value)};
  else{
    if(!s)return;
    data={session:s.id};
    if(action==='start'){
      if(!confirm('Tillåt automatisk köp-/säljhandel på Kraken inom planen? Nya köp högst '+euro(s.newCashEUR)+', förlustgräns '+euro(s.maxLossEUR)+', slut senast '+tradeTime(s.expiresAt)+'. Efterbevakning: '+(s.protectionMinutes?'får sälja till '+tradeTime(s.protectionExpiresAt):'ingen')+'. Avslutspolicy: '+s.endPolicy+'. Detta använder riktiga pengar.'))return;
      approval=prompt('Skapa en engångskod i Ubuntu:\npython3 -m backend.ops approve session-start --session '+s.id+'\nKontrollera planen i terminalen och klistra in koden (120 sekunder).');
      if(!approval)return;
    }
  }
  app.pending++;showError('');renderTrading();
  try{
    const result=await post('/api/trading-session/'+action,data,approval);
    app.trading={...app.trading,session:result,...(action==='plan'?{grossBuysEUR:'0',remainingNewBuysEUR:result.newCashEUR}:{})};renderTrading();
  }catch(error){showError(error.message);}
  finally{app.pending--;await refreshTrading();await refreshExchange();}
}
function renderExchange(){
  const e=app.exchange;if(!e)return;
  const h=e.tradeHistory||{executions:[],closed:[],open:[],unmatchedSales:[]},balances=e.account?.balances;
  $('real-overview').replaceChildren(metric('EUR-saldo',balances?euro(balances.EUR.balance):'—','Verkligt saldo från Kraken'),
    metric('BTC-saldo',balances?quantity(balances.XBT.balance):'—'),metric('ETH-saldo',balances?quantity(balances.ETH.balance):'—'),
    metric('Bekräftade avslut',h.executions.length,'Bara verifierade JARVIS-order'));
  text('real-history-status',e.error||e.tickets.some(t=>t.historyError)?'Historiken kunde inte uppdateras fullständigt. Tidigare verifierade affärer visas; kontrollera konto och orderavstämning.':
    'Visar bara bekräftade avslut för order skickade via JARVIS. Senaste kontokontroll: '+(e.checkedAt?tradeTime(e.checkedAt):'ännu inte hämtat')+'.');
  rows('real-roundtrips',h.closed.map(t=>[ (t.agentId||'Manuell')+' · '+t.pair,tradeTime(t.boughtAt),tradeTime(t.soldAt),quantity(t.quantity),euro(t.netEUR)]),'Inga matchade köp och försäljningar ännu.',5);
  rows('real-open-lots',h.open.map(t=>[(t.agentId||'Manuell')+' · '+t.pair,tradeTime(t.boughtAt),quantity(t.quantity),euro(t.costIncludingFeeEUR)]),'Inga verifierade öppna köp i JARVIS-historiken.',4);
  rows('real-executions',h.executions.map(t=>[tradeTime(t.executedAt),t.agentId||'Manuell',t.pair,t.side==='buy'?'Köp':'Sälj',quantity(t.quantity),euro(t.priceEUR),euro(t.feeEUR),node('span',{class:'mono',text:t.tradeId})]),'Inga verkliga avslut. Signaler, förhandsgranskningar och ofyllda order är inga affärer.',8);
  $('real-unmatched').replaceChildren(...(h.unmatchedSales.length?h.unmatchedSales.map(t=>node('p',{text:t.pair+' · '+quantity(t.quantity)+' · sålt '+tradeTime(t.soldAt)+' · '+t.reason})):[node('p',{text:'Inga försäljningar med okänt inköp.'})]));
  text('live-mode',e.monitorOnly?'Bevakningsläge · handel avstängd':e.liveEnabled?'Live tillåtet · ägargodkännande krävs':'Live avstängt');
  $('live-mode').className='badge '+(e.liveEnabled?'warn':'');
  text('live-cap',e.maximumOrderEUR+' EUR');
  text('exchange-message',e.error|| (e.connected?'Kraken-kontot kontrollerat '+localTime(e.checkedAt)+'. Saldon i EUR/BTC/ETH är verkliga och separata från paper.':'Inget privat Kraken-konto har kontrollerats. Börja med lokal konfiguration och Kontrollera konto.'));
  $('exchange-message').className='notice'+(e.error?' error':e.liveEnabled?' warn':'');
  if(e.monitoringActive)$('exchange-message').append(document.createTextNode(' Automatisk bevakning: kryptokurser cirka var 15:e sekund, konto och order cirka var 60:e sekund. Agenthandel kräver en godkänd session.'));
  $('live-balances').replaceChildren(metric('Verkligt EUR-saldo',balances?euro(balances.EUR.balance):'—','Tillgängligt: '+(balances?euro(balances.EUR.available):'—')),
    metric('Verkligt BTC-saldo',balances?quantity(balances.XBT.balance):'—','Tillgängligt: '+(balances?quantity(balances.XBT.available):'—')),
    metric('Verkligt ETH-saldo',balances?quantity(balances.ETH.balance):'—','Tillgängligt: '+(balances?quantity(balances.ETH.available):'—')),
    metric('Öppna Kraken-order',e.account?.openOrderCount??'—',e.monitorOnly?'Uppdateras automatiskt cirka var 60:e sekund':'Hämtas bara vid uttrycklig kontokontroll'));
  if(app.ticket)app.ticket=e.tickets.find(t=>t.id===app.ticket.id)||app.ticket;
  rows('live-orders',e.tickets.map(t=>[node('span',{class:'mono',text:t.id.slice(0,15)+'…'}),t.pair+' '+t.side,t.volume+' / '+t.price,badge(t.state,statusTone(t.state)),
    node('span',{class:'mono',text:t.exchangeOrderId||'—'}),node('button',{type:'button',text:'Visa',click:()=>{app.ticket=t;renderTicket();}})]),'Inga livebiljetter.',6);
  renderTicket();
  if(!app.paperMode&&app.state)renderAgents();
}
function renderTicket(){
  const t=app.ticket,e=app.exchange;
  const active=['SENDING','UNKNOWN','ACKNOWLEDGED','OPEN','PARTIAL','CANCEL_REQUESTED','VALIDATION_UNKNOWN'].includes(t?.state);
  if(t){
    const values=[['Status',badge(t.state,statusTone(t.state))],['Biljett',node('span',{class:'mono',text:t.id})],
      ['Order',t.side.toUpperCase()+' '+t.volume+' '+t.pair],['Limitpris',t.price+' EUR'],['Ordervärde',t.risk.notionalEUR+' EUR'],
      ['Avgiftsreserv',t.risk.feeReserveEUR+' EUR'],['Kraken-order-ID',t.exchangeOrderId||'Inget order-ID ännu']];
    const dl=node('dl',{class:'facts'});$('live-ticket-details').replaceChildren(dl);
    for(const [label,value] of values)dl.append(node('div',{},[node('dt',{text:label}),node('dd',{},[value])]));
    if(t.error)$('live-ticket-details').append(node('p',{class:'negative',text:t.error}));
    const stale=app.state && t.stopGeneration!==app.state.stopGeneration;
    text('live-approval-help',stale?'En paus eller omstart har ogiltigförklarat denna biljett för insändning. Skapa en ny.':
      'Validering skickar ingen order. För insändning: python3 -m backend.ops approve live-order --ticket '+t.id);
    $('live-submit').disabled=app.pending>0||!e?.liveEnabled||t.state!=='VALIDATED'||stale||Boolean(t.sessionId)||Boolean(app.state?.halt&&t.side==='buy');
  }else{
    text('live-ticket-details','Förhandsgranska först ett exakt antal och limitpris.');
    $('live-submit').disabled=true;
  }
  $('live-preview').disabled=app.pending>0||!e?.connected||e?.monitorOnly;
  $('live-validate').disabled=app.pending>0||!t||e?.monitorOnly||Boolean(t?.sessionId)||!['PREVIEW','VALIDATED','VALIDATION_FAILED'].includes(t.state);
  $('live-reconcile').disabled=app.pending>0||!active;
  $('live-cancel').disabled=app.pending>0||!active||!t?.exchangeOrderId;
}
async function exchangeAction(action,data={}){
  if(app.pending)return;
  let approval=null;
  if(action==='submit'||action==='cancel'){
    const t=app.ticket;if(!t)return;
    const instruction='python3 -m backend.ops approve '+(action==='submit'?'live-order':'live-cancel')+' --ticket '+t.id;
    approval=prompt((action==='submit'?'VERKLIG ORDER: ':'ANNULLERA VERKLIG ORDER: ')+t.side.toUpperCase()+' '+t.volume+' '+t.pair+' limit '+t.price+' EUR.\nSkapa koden i en lokal terminal:\n'+instruction+'\nKlistra in engångskoden. API-hemligheter ska aldrig skrivas här.');
    if(!approval)return;
  }
  app.pending++;showError('');renderTicket();
  try{
    const result=await post('/api/exchange/'+action,data,approval);
    if(result.id)app.ticket=result;
    if(action==='check')app.exchange=result;
  }catch(error){showError(error.message);}
  finally{app.pending--;await refreshExchange();renderHealth();}
}
for(const tab of tabs)$('nav-'+tab).addEventListener('click',()=>setTab(tab));
document.querySelector('.tabbar').addEventListener('keydown',event=>{
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  event.preventDefault();
  const visible=tabs.filter(tab=>app.paperMode||!['portfolio','risk'].includes(tab));
  const index=visible.indexOf(app.tab),next=event.key==='Home'?0:event.key==='End'?visible.length-1:(index+(event.key==='ArrowRight'?1:-1)+visible.length)%visible.length;
  setTab(visible[next],true);
});
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>setTab(button.dataset.view)));
$('view-all-agents').addEventListener('click',()=>setTab('agents'));
$('agent-select').addEventListener('change',renderAgents);
$('market-symbol').addEventListener('change',renderMarket);
$('step').onclick=()=>command('step');$('run').onclick=()=>command(app.state?.running?'stop':'start');$('stop').onclick=()=>command('stop');
$('testpause').onclick=()=>command('pause');$('approve').onclick=()=>command('approve');
$('endday').onclick=()=>{if(confirm('Avsluta dagens paper-bokföring? Dagen kan öppnas igen först nästa Stockholm-datum.'))command('close-day');};
for(const kind of ['deposit','withdraw'])$(kind+'-btn').onclick=()=>{
  const amount=Number($(kind+'-amount').value);
  if(!Number.isFinite(amount)||amount<=0){showError('Ange ett giltigt positivt belopp.');return;}
  if(confirm('Registrera simulerad '+(kind==='deposit'?'insättning':'uttag')+' '+money(amount)+'?'))command(kind,{amount});
};
$('weekly').onclick=()=>{if(confirm('Skydda 20 % av ny berättigad realiserad paper-vinst?'))command('sweep');};
$('release').onclick=()=>{
  const answer=prompt('Belopp i SEK att återföra från det skyddade paper-kontot:');if(answer===null)return;
  const amount=Number(answer.replace(',','.'));if(!Number.isFinite(amount)||amount<=0){showError('Ogiltigt belopp.');return;}
  if(confirm('Godkänn återföring '+money(amount)+' till paper-tradingkontot?'))command('release',{amount,approved:true});
};
$('market-auto').onchange=event=>command('settings',{marketAuto:event.target.checked});
$('auto-pace').onchange=event=>command('settings',{autoPace:Number(event.target.value)});
$('mark-notices-read').onclick=()=>command('read-notices');
for(const [id,file] of [['export','trades.csv'],['export-capital','capital.csv'],['export-journal','journal.jsonl']])$(id).onclick=()=>{
  const link=node('a',{href:'/api/export/'+file,download:file});link.click();
};
$('refresh-status').onclick=()=>poll();
for(const action of ['plan','start','stop'])$('session-'+action).onclick=()=>sessionAction(action);
$('refresh-real-history').onclick=()=>exchangeAction('check');
$('enable-alerts').onclick=async()=>{
  if(app.desktopAlerts){app.desktopAlerts=false;text('enable-alerts','Aktivera skrivbordsnotiser');return;}
  if(!('Notification' in window)){showError('Den här webbläsaren stöder inte skrivbordsnotiser. Meddelanden visas ändå här.');return;}
  try{
    app.desktopAlerts=await Notification.requestPermission()==='granted';
    text('enable-alerts',app.desktopAlerts?'Skrivbordsnotiser aktiverade':'Skrivbordsnotiser ej tillåtna');
    if(!app.desktopAlerts)showError('Webbläsaren tillåter inte skrivbordsnotiser. CHEF:s meddelanden sparas ändå i Beslut och Historik.');
  }catch(error){showError('Notiser kunde inte aktiveras. Meddelanden finns i dashboarden.');}
};
$('refresh-market').onclick=()=>command('refresh-market');
$('exchange-check').onclick=()=>exchangeAction('check');
$('live-form').addEventListener('submit',event=>{
  event.preventDefault();
  const data={pair:$('live-pair').value,side:$('live-side').value,volume:$('live-volume').value.trim().replace(',','.'),price:$('live-price').value.trim().replace(',','.')};
  exchangeAction('preview',data);
});
for(const action of ['validate','submit','reconcile','cancel'])$('live-'+action).onclick=()=>{if(app.ticket)exchangeAction(action,{ticket:app.ticket.id});};
poll();refreshExchange();refreshTrading();
setInterval(poll,2000);
setInterval(()=>{renderHealth();if(app.state&&['overview','decisions'].includes(app.tab))renderDecisions();},1000);

async function refreshSupervision(){
  try{
    const s=await getJSON('/api/supervision');
    text('supervision-message','Livekontroll · uppdaterad '+new Date().toLocaleTimeString('sv-SE'));
    const stream=s.market.stream;
    $('supervision-summary').replaceChildren(...[
      ['Skyddad EUR-reserv',euro(s.savings.reservedEUR)],
      ['Vinsttröskel',euro(s.savings.highWaterEUR)],
      ['Marknadsström',stream.enabled?(stream.healthy?'WebSocket · aktuell':'WebSocket · ej aktuell'):'REST · 15 sekunder'],
      ['Signaler',s.market.signals]
    ].map(([label,value])=>node('article',{class:'metric'},[node('span',{text:label}),node('strong',{text:value})])));
    const labels={ACTIVE:'Aktiv pilot',LIMITED:'Begränsad provbudget',QUARANTINE:'Karantän',SHADOW:'Skugghandel',REVIEW_READY:'Redo för ägargranskning'};
    $('supervision-agents').replaceChildren(...s.agents.map(a=>{
      const m=a.metrics,q=a.shadow;
      const card=node('article',{class:'panel'},[node('h3',{text:a.agentId.toUpperCase()}),badge(labels[a.status]||a.status,a.status==='ACTIVE'?'good':'warn'),node('p',{text:a.reason}),
        node('p',{text:'Realiserat netto: '+euro(m.netEUR)+' · nedgång: '+euro(m.drawdownEUR)+' · avslut: '+m.closedTrades}),
        node('p',{text:'Kostnadsbas öppna innehav: '+euro(m.openCostEUR)+' · orealiserat: '+euro(m.unrealizedEUR)+' · avgifter: '+euro(m.feesEUR)}),
        node('p',{text:'Snittvinst: '+euro(m.averageWinEUR)+' · snittförlust: '+euro(m.averageLossEUR)}),
        node('p',{text:q?'SKUGGRESULTAT: '+euro(q.netEUR||0)+' · '+q.trades.length+' avslut · nedgång '+euro(q.maxDrawdownEUR):'Inget separat skuggprov pågår.'})]);
      if(['ACTIVE','LIMITED'].includes(a.status))card.append(node('button',{type:'button',text:'Sätt i karantän',click:async()=>{
        const reason=prompt('Varför ska '+a.agentId+' stoppas från nya köp? Befintliga innehav ligger kvar.');if(!reason)return;
        try{await post('/api/supervision/quarantine',{agent:a.agentId,reason});refreshSupervision();}catch(e){showError(e.message);}
      }}));
      if(a.status==='REVIEW_READY')card.append(node('p',{text:'Granska rapporten och kör lokalt: python3 -m backend.ops approve agent-review --agent '+a.agentId}),node('button',{type:'button',text:'Godkänn begränsad återstart',click:async()=>{
        const approval=prompt('Ange lokal engångskod för agentgranskningen:');if(!approval)return;
        try{await post('/api/supervision/review',{agent:a.agentId},approval);refreshSupervision();}catch(e){showError(e.message);}
      }}));
      return card;
    }));
    $('weekly-reviews').replaceChildren(...(s.weeklyReports.length?s.weeklyReports.map(r=>node('details',{},[node('summary',{text:'Period '+r.period+' · netto '+euro(r.netEUR)+' · reserverat '+euro(r.reservedEUR)}),node('p',{text:r.conclusion}),...(r.trades||[]).map(t=>node('p',{text:(t.agentId||'Manuell')+' · '+t.pair+' · '+tradeTime(t.soldAt)+' · '+euro(t.netEUR)+' · '+t.finding+' Köp: '+t.entryReason+' Sälj: '+t.exitReason}))])):[node('p',{text:'Första granskningen sker efter sju dagar. Ingen avkastning eller historik är påhittad.'})]));
    $('research-ideas').replaceChildren(...(s.research.length?s.research.map(r=>node('article',{class:'status-card'},[badge(r.status==='BACKTEST_REVIEW'?'Historiskt test · inväntar granskning':'Ej verifierad','warn'),node('h4',{text:r.title}),node('a',{href:r.url,target:'_blank',rel:'noopener noreferrer',text:'Öppna källa'}),node('p',{text:r.hypothesis}),node('p',{text:r.evaluation?'Separat testperiod: '+euro(r.evaluation.holdout.netEUR)+' · '+r.evaluation.holdout.closedTrades+' avslut · nedgång '+euro(r.evaluation.holdout.maxDrawdownEUR):'Testkommando: python3 -m backend.ops research-test historik.json --strategy Trend --idea '+r.id}),node('p',{text:r.nextStep})])):[node('p',{text:'Inga inskickade strategiidéer.'})]));
    $('supervision-limitations').replaceChildren(...s.limitations.map(item=>node('p',{text:item})));
  }catch(e){text('supervision-message','Kontrollstatus saknas: '+e.message);}
}
$('refresh-supervision').onclick=refreshSupervision;
$('research-form').onsubmit=async event=>{
  event.preventDefault();
  try{await post('/api/supervision/research',Object.fromEntries(new FormData(event.target)));event.target.reset();await refreshSupervision();}catch(e){showError(e.message);}
};
refreshSupervision();
