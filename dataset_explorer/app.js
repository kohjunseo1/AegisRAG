"use strict";
const $ = id => document.getElementById(id);
const E = (tag, cls, text) => { const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=String(text); return n; };
const F = n => Number(n).toLocaleString('ko-KR');
const splitName={dev:'Dev',test:'Test',holdout:'Holdout'};
const labelName={person:'이름',phone_number:'전화번호',email:'이메일',address:'주소',resident_registration_number:'주민등록번호',account_number:'계좌번호',card_number:'카드번호'};
const titles={overview:['데이터 전체 구성','Dev, Test, Holdout의 문서와 정답 라벨 구성을 한눈에 비교합니다.'],documents:['문서 탐색','원문과 정답 개체 구간을 함께 살펴보세요. 필터는 교차 적용됩니다.'],quality:['스키마 · 품질','분할별 필드 차이와 annotation의 기본 정합성을 확인합니다.']};
let data, selected=-1, filtered=[], page='overview';
function button(text,action,cls='quiet'){const n=E('button',cls,text);n.type='button';n.addEventListener('click',action);return n;}
function showPage(name){page=name;document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!==name);document.querySelectorAll('.nav').forEach(n=>n.classList.toggle('active',n.dataset.page===name));$('page-title').textContent=titles[name][0];$('page-description').textContent=titles[name][1];}
function table(headers,rows){const t=E('table');const head=E('thead'),r=E('tr');headers.forEach(h=>r.append(E('th','',h)));head.append(r);t.append(head);const body=E('tbody');rows.forEach(row=>{const tr=E('tr');row.forEach(cell=>{const td=E('td');td.append(cell instanceof Node?cell:document.createTextNode(String(cell)));tr.append(td);});body.append(tr);});t.append(body);return t;}
function options(id,placeholder,items,names={}){$(id).replaceChildren(new Option(placeholder,''),...items.map(v=>new Option(names[v]||v,v)));}
function browse(split='',type='',hard=''){ $('split-filter').value=split;$('type-filter').value=type;$('hard-filter').value=hard;$('label-filter').value='';$('search').value='';$('quality-filter').value='';showPage('documents');filterDocs();}
function renderOverview(){
 const s=data.summary;
 $('stats').replaceChildren(...[[s.documents,'전체 문서','기준 제공 3개 분할'],[s.entities,'정답 개체 구간','모델 예측이 아닌 annotation'],[s.doc_types,'문서 유형','분할 전체의 고유 유형'],[s.hard_case_tags,'어려운 사례 태그','문서별 다중 태그']].map(([v,k,n])=>{const el=E('article','stat');el.append(E('div','stat-label',k),E('strong','stat-value',F(v)),E('small','muted',n));return el;}));
 $('nav-count').textContent=F(s.documents);
 data.splits.forEach(split=>{
  const side=button('',()=>browse(split.key),'side-split');side.append(E('span','split-dot '+split.key),E('span','',splitName[split.key]),E('b','',F(split.documents)));$('side-splits').append(side);
  const card=E('article','split-card '+split.key);const top=E('div','card-top');top.append(E('span','split-badge '+split.key,splitName[split.key]),E('small','',split.key==='holdout'?'Holdout 분할':'기준 분할'));
  const amount=E('div','split-amount');amount.append(E('strong','',F(split.documents)),E('span','','문서'));
  const metrics=E('div','card-metrics');metrics.append(E('span','',F(split.entities)+'개 개체'),E('span','',Object.keys(split.doc_types).length+'개 유형'),E('span','',F(split.mean_chars)+'자 / 평균'));
  card.append(top,amount,metrics,E('code','source-name',split.filename),button('이 분할 탐색 →',()=>browse(split.key),'card-link'));$('split-cards').append(card);
 });
 renderLabels();
 const max=Math.max(...data.splits.map(s=>s.max_chars),1);
 data.splits.forEach(s=>{const row=E('div','length-row');row.append(E('strong','',splitName[s.key]));const track=E('div','length-track');const range=E('div','length-range '+s.key);range.style.left=(s.min_chars/max*100)+'%';range.style.width=((s.max_chars-s.min_chars)/max*100)+'%';const dot=E('div','length-dot '+s.key);dot.style.left=(s.mean_chars/max*100)+'%';track.append(range,dot);row.append(track,E('span','',F(s.mean_chars)+'자'));$('length-chart').append(row);});
 $('length-table').append(table(['분할','최소','중앙값','최대'],data.splits.map(s=>[splitName[s.key],F(s.min_chars),F(s.median_chars),F(s.max_chars)])));
 const types=[...new Set(data.splits.flatMap(s=>Object.keys(s.doc_types)))].sort((a,b)=>a.localeCompare(b,'ko'));
 const maxType=Math.max(...data.splits.flatMap(s=>Object.values(s.doc_types)),1);
 $('type-table').append(table(['문서 유형',...data.splits.map(s=>splitName[s.key]),'합계'],types.map(type=>[type,...data.splits.map(s=>{const n=s.doc_types[type]||0;const b=button(n||'—',()=>browse(s.key,type),'heat '+s.key);b.disabled=!n;if(n)b.style.opacity=0.4+n/maxType*0.6;return b;}),F(data.splits.reduce((a,s)=>a+(s.doc_types[type]||0),0))])));
 const hard={};data.splits.forEach(s=>Object.entries(s.hard_cases).forEach(([k,n])=>hard[k]=(hard[k]||0)+n));
 Object.entries(hard).sort((a,b)=>b[1]-a[1]).forEach(([k,n])=>$('hard-cases').append(button(k+'  '+F(n),()=>browse('','',k),'tag')));
 options('type-filter','전체 문서 유형',types);options('label-filter','전체 PII 라벨',data.labels,labelName);options('hard-filter','전체 사례 태그',Object.keys(hard).sort());
}
function renderLabels(){
 const field=$('label-mode').value;const max=Math.max(...data.splits.flatMap(s=>Object.values(s[field])),1);$('label-chart').replaceChildren();
 data.labels.forEach(label=>{const row=E('div','chart-row');const name=E('div','chart-label',labelName[label]||label);name.append(E('small','',label));const group=E('div','bar-group');data.splits.forEach(s=>{const n=s[field][label]||0;const line=E('div','bar-line');const bar=E('div','bar '+s.key);bar.style.width=(n/max*100)+'%';bar.title=splitName[s.key]+': '+F(n);line.append(bar,E('span','',F(n)));group.append(line);});row.append(name,group);$('label-chart').append(row);});
}
function renderQuality(){
 const fields=[...new Set(data.splits.flatMap(s=>Object.keys(s.fields)))];$('schema-table').append(table(['필드',...data.splits.map(s=>splitName[s.key])],fields.map(f=>[f,...data.splits.map(s=>s.fields[f]===undefined?'없음':F(s.fields[f])+' / '+F(s.documents))])));
 $('quality-table').append(table(['점검 항목',...data.splits.map(s=>splitName[s.key])],[['오프셋·원문 불일치',...data.splits.map(s=>s.annotation_issues)],['개체 구간 겹침 문서',...data.splits.map(s=>s.overlap_documents)],['개체가 없는 문서',...data.splits.map(s=>s.empty_entities)]]));
 const sum=data.summary;
 $('insights').append(E('p','insight',`Holdout의 문서 유형 ${sum.holdout_new_doc_types.length}개가 Dev·Test에 없는 유형입니다.`),E('p','muted',sum.holdout_new_doc_types.join(' · ')),E('p','insight','Holdout에는 template · persons · roles 필드가 제공되지 않습니다. 해당 필드 부재를 누락 라벨로 해석하지 않습니다.'),E('p','insight',`원문이 완전히 같은 중복 그룹 ${sum.duplicate_groups.length}개 · 분할 간 중복 그룹 ${sum.cross_split_duplicates.length}개`),E('p','muted','중복은 공백·줄바꿈까지 동일한 원문 기준입니다. 의미가 비슷하거나 일부 개인정보만 다른 문서는 별도로 판정하지 않습니다.'));
 if(sum.duplicate_groups.length){const details=E('details');details.append(E('summary','','중복 문서 ID 보기'));sum.duplicate_groups.forEach(g=>details.append(E('p','muted',g.map(d=>splitName[d.split]+':'+d.id).join(' / '))));$('insights').append(details);}
 $('file-table').append(table(['분할 / 파일','문서 수','SHA256'],data.splits.map(s=>[splitName[s.key]+' / '+s.filename,F(s.documents),E('code','hash',s.sha256)])));
}
function filterDocs(){
 const query=$('search').value.trim().toLocaleLowerCase(), split=$('split-filter').value,type=$('type-filter').value,label=$('label-filter').value,hard=$('hard-filter').value,quality=$('quality-filter').value;
 filtered=data.documents.map((d,i)=>({d,i})).filter(({d})=>{const r=d.record;return (!split||d.split_key===split)&&(!type||r.doc_type===type)&&(!label||r.entities.some(e=>e.label===label))&&(!hard||(r.hard_cases||[]).includes(hard))&&(!query||(r.id+' '+r.text+' '+r.entities.map(e=>e.text||'').join(' ')).toLocaleLowerCase().includes(query))&&(!quality||(quality==='issues'&&d.issues.length)||(quality==='empty'&&!r.entities.length)||(quality==='overlap'&&d.has_overlap));});
 const sort=$('sort').value;if(sort==='long')filtered.sort((a,b)=>b.d.char_count-a.d.char_count);if(sort==='entities')filtered.sort((a,b)=>b.d.record.entities.length-a.d.record.entities.length);
 $('result-count').textContent=F(filtered.length)+' / '+F(data.documents.length)+' 문서';
 if(!filtered.some(v=>v.i===selected))selected=filtered.length?filtered[0].i:-1;
 $('document-list').replaceChildren();
 filtered.forEach(({d,i})=>{const r=d.record;const item=button('',()=>selectDoc(i),'doc-item'+(selected===i?' selected':''));item.setAttribute('role','listitem');item.setAttribute('aria-label',splitName[d.split_key]+' '+r.id+' '+r.doc_type);const line=E('div','doc-top');line.append(E('strong','',r.id),E('span','split-badge '+d.split_key,splitName[d.split_key]));item.append(line,E('span','doc-type',r.doc_type||'미지정'),E('p','doc-excerpt',r.text),E('small','muted',F(d.char_count)+'자 · 개체 '+F(r.entities.length)+'개'+(d.issues.length?' · 점검 필요':'')));item.dataset.index=i;$('document-list').append(item);});
 renderDoc();
}
function selectDoc(i){selected=i;document.querySelectorAll('.doc-item').forEach(b=>b.classList.toggle('selected',Number(b.dataset.index)===i));renderDoc();}
function renderDoc(){
 const panel=$('document-detail');panel.replaceChildren();if(selected<0){panel.append(E('div','empty','조건에 맞는 문서가 없습니다. 필터를 바꿔보세요.'));return;}
 const d=data.documents[selected],r=d.record;const head=E('div','detail-head');const title=E('div');title.append(E('span','split-badge '+d.split_key,splitName[d.split_key]),E('h2','',r.id),E('p','muted',(r.doc_type||'미지정')+' · '+F(d.char_count)+'자 · 정답 개체 '+r.entities.length+'개'));head.append(title);panel.append(head);
 if(d.issues.length)panel.append(E('div','notice warning','오프셋 또는 개체 텍스트 불일치 '+d.issues.length+'건이 있습니다. 아래 개체 표에서 확인하세요.'));
 const tags=E('div','tag-cloud');(r.hard_cases||[]).forEach(t=>tags.append(E('span','tag static',t)));if(tags.childNodes.length)panel.append(tags);
 const tools=E('div','text-tools');tools.append(E('h3','','원문 · 정답 라벨'));
 const toggle=E('label','toggle');const check=E('input');check.type='checkbox';check.checked=true;toggle.append(check,document.createTextNode('개체 강조'));tools.append(toggle);panel.append(tools);
 const original=E('div','original');original.tabIndex=0;const points=Array.from(r.text);const valid=r.entities.filter(e=>Number.isInteger(e.start)&&Number.isInteger(e.end)&&e.start>=0&&e.end>e.start&&e.end<=points.length);
 const bounds=[...new Set([0,points.length,...valid.flatMap(e=>[e.start,e.end])])].sort((a,b)=>a-b);
 for(let i=0;i<bounds.length-1;i++){const a=bounds[i],b=bounds[i+1],active=valid.filter(e=>e.start<b&&a<e.end);const span=E('span',active.length?'annotation label-'+active[0].label:'',points.slice(a,b).join(''));span.dataset.start=a;span.dataset.end=b;if(active.length)span.title=active.map(e=>(labelName[e.label]||e.label)+' ['+e.start+', '+e.end+')').join(' / ');original.append(span);}
 check.addEventListener('change',()=>original.classList.toggle('no-highlight',!check.checked));panel.append(original);
 const legend=E('div','entity-legend');[...new Set(r.entities.map(e=>e.label))].forEach(l=>legend.append(E('span','label-pill label-'+l,labelName[l]||l)));panel.append(legend,E('h3','entity-heading','정답 개체 '+r.entities.length+'개'));
 const entityRows=r.entities.map((e,i)=>{const issues=d.issues.filter(x=>x.entity===i).map(x=>x.kind==='invalid_offset'?'오프셋 오류':'원문 불일치');const jump=button(e.text||'(텍스트 없음)',()=>{original.querySelectorAll('.focused-span').forEach(n=>n.classList.remove('focused-span'));const nodes=[...original.children].filter(n=>Number(n.dataset.start)<e.end&&e.start<Number(n.dataset.end));nodes.forEach(n=>n.classList.add('focused-span'));if(nodes.length)nodes[0].scrollIntoView({block:'nearest',behavior:'smooth'});},'entity-text');return [i+1,E('span','label-pill label-'+e.label,labelName[e.label]||e.label),jump,'['+e.start+', '+e.end+')',e.form||'—',e.person_id||'—',issues.join(', ')||'정상'];});
 const holder=E('div','table-scroll');holder.append(table(['#','라벨','개체 텍스트','구간','form','person_id','점검'],entityRows));panel.append(holder);
 const metadata=E('div','metadata');['template','persons','roles'].forEach(k=>{const block=E('div');block.append(E('strong','',k),E('pre','',Object.hasOwn(r,k)?JSON.stringify(r[k],null,2):'제공되지 않은 필드'));metadata.append(block);});panel.append(metadata);
 const raw=E('details','raw-json');raw.append(E('summary','','원본 JSON 레코드 보기'),E('pre','',JSON.stringify(r,null,2)));panel.append(raw);
}
async function start(){
 try{const response=await fetch('/api/data',{cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);data=await response.json();renderOverview();renderQuality();filterDocs();$('loading').hidden=true;showPage('overview');}
 catch(err){$('loading').hidden=true;$('error').hidden=false;$('error').textContent='데이터를 불러오지 못했습니다. 서버와 원본 파일을 확인하세요. ('+err.message+')';}
}
document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>showPage(b.dataset.page)));
$('browse-all').addEventListener('click',()=>browse());$('label-mode').addEventListener('change',renderLabels);
['split-filter','type-filter','label-filter','hard-filter','sort'].forEach(id=>$(id).addEventListener('change',filterDocs));
let timer;$('search').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(filterDocs,120);});$('clear-filters').addEventListener('click',()=>browse());
const qualitySelect=E('select');qualitySelect.id='quality-filter';qualitySelect.setAttribute('aria-label','Annotation 상태');[['전체 annotation 상태',''],['점검 필요','issues'],['개체 없음','empty'],['구간 겹침','overlap']].forEach(([t,v])=>qualitySelect.add(new Option(t,v)));$('clear-filters').before(qualitySelect);qualitySelect.addEventListener('change',filterDocs);
start();
