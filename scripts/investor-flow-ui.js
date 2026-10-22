/* MY Stock Room | per-stock investor NET BUY QUANTITY (shares only).
   All results are display-only; Trading Score and ranking are unchanged. */
const INVESTOR_FLOW_URL='data/investor-flow.json';
let investorFlowData=null, investorFlowPromise=null;
async function srLoadInvestorFlowCache(){
  if(investorFlowPromise)return investorFlowPromise;
  investorFlowPromise=srFetchJson(INVESTOR_FLOW_URL+'?v='+Date.now())
    .then(d=>{investorFlowData=d&&d.meta&&d.items?d:{meta:{},items:{}};return investorFlowData})
    .catch(e=>{console.warn('Investor flow cache unavailable',e);investorFlowData={meta:{},items:{}};return investorFlowData});
  return investorFlowPromise;
}
function srFlowStock(code){
  const c=String(code||'').split('.')[0].padStart(6,'0');
  if(!/^[0-9]{6}$/.test(c))return null;
  const meta=investorFlowData?.meta||{},v=investorFlowData?.items?.[c];
  if(meta.unit!=='shares'||!meta.market_date||meta.market_date!==stockUniverseMeta?.as_of||
     !v||v.stale||v.unit!=='shares'||!Array.isArray(v.rows))return null;
  const rows=v.rows.filter(x=>/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(String(x.date))&&x.date<=meta.market_date)
    .sort((a,b)=>a.date.localeCompare(b.date));
  if(!rows.length)return null;
  // Investor data often settles after stock prices. Explicitly identify the last published trading date.
  return{rows,asOf:rows.at(-1).date,marketAsOf:meta.market_date,lagged:rows.at(-1).date<meta.market_date,code:c,source:v.source||meta.source||'미확인'};
}
function srFlowSum(o,k,n){
  if(!o||o.rows.length<n)return null;
  const a=o.rows.slice(-n);
  if(a.some(x=>x[k]===null||x[k]===undefined||!Number.isFinite(Number(x[k]))))return null;
  return a.reduce((s,x)=>s+Number(x[k]),0);
}
function srFlowClass(n){return n==null?'flow-zero':n>0?'flow-pos':n<0?'flow-neg':'flow-zero'}
function srFlowNumber(n){
  if(n==null||!Number.isFinite(Number(n)))return '—';
  const a=Math.abs(n),num=a>=10000?(a/10000).toLocaleString('ko-KR',{maximumFractionDigits:2})+'만주':Math.round(a).toLocaleString('ko-KR')+'주';
  return (n>0?'+':n<0?'−':'')+num;
}
function srFlowMini(code){
  const o=srFlowStock(code);
  if(!o)return '<span class="flow-no-data">수급 조회 불가<br>기준일 검증 대기</span>';
  return '<div class="flow-mini" title="순매수 수량(주) / 기준 '+srEsc(o.asOf)+'">'+
    '<div class="flow-mini-row flow-zero"><b>기간</b><b>외국인</b><b>기관</b><b>개인</b></div>'+
    [5,20,60].map(n=>'<div class="flow-mini-row"><b>'+n+'D</b>'+
      ['foreign','institution','individual'].map(k=>{const x=srFlowSum(o,k,n);return '<span class="'+srFlowClass(x)+'">'+srFlowNumber(x)+'</span>'}).join('')+
      '</div>').join('')+'<div class="flow-meta">수급 기준 '+srEsc(o.asOf)+(o.lagged?' (주가 '+srEsc(o.marketAsOf)+' 대비 지연)':'')+'</div></div>';
}
function srFlowChart(o,days=60){
  if(!o||o.rows.length<2)return '';
  const rows=o.rows.slice(-Math.min(60,Math.max(2,Number(days)||60))),lineDefs=[['foreign','외국인','#1769E0'],['institution','기관','#059669'],['individual','개인','#E3A02A']];
  const series=lineDefs.map(([key,label,color])=>{
    let total=0,ok=true,values=[0];
    for(const r of rows){if(r[key]==null||!Number.isFinite(Number(r[key]))){ok=false;break}total+=Number(r[key]);values.push(total)}
    return{label,color,values,ok};
  }).filter(x=>x.ok);
  if(!series.length)return '<div class="flow-no-data">누적 그래프에 필요한 연속 수급 자료가 없습니다.</div>';
  const all=series.flatMap(x=>x.values).concat([0]),min=Math.min(...all),max=Math.max(...all);
  const lo=min===max?min-1:min,hi=min===max?max+1:max;
  const x=i=>48+548*i/rows.length,y=v=>160-142*(v-lo)/(hi-lo);
  const line=series.map(s=>'<polyline fill="none" stroke="'+s.color+'" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" points="'+
    s.values.map((v,i)=>x(i).toFixed(1)+','+y(v).toFixed(1)).join(' ')+'"/>').join('');
  const zero=y(0).toFixed(1),fmt=n=>Math.abs(n)>10000?(n/10000).toFixed(1)+'만':Math.round(n).toLocaleString('ko-KR');
  return '<svg class="flow-chart" viewBox="0 0 630 197" role="img" aria-label="최근 투자자별 누적 순매수 수량 추이">'+
   '<line x1="48" x2="596" y1="'+zero+'" y2="'+zero+'" stroke="#AAB3C1" stroke-dasharray="4 3"/>'+
   '<line x1="48" x2="596" y1="18" y2="18" stroke="#E7EBF1"/><line x1="48" x2="596" y1="160" y2="160" stroke="#E7EBF1"/>'+
   line+'<text x="3" y="20" font-size="10" fill="#667085">'+srEsc(fmt(hi))+'</text>'+
   '<text x="3" y="160" font-size="10" fill="#667085">'+srEsc(fmt(lo))+'</text>'+
   '<text x="48" y="187" font-size="11" fill="#667085">'+srEsc(rows[0].date.slice(5))+'</text>'+
   '<text x="596" y="187" font-size="11" text-anchor="end" fill="#667085">'+srEsc(rows.at(-1).date.slice(5))+'</text></svg>'+
   '<div class="flow-legend">'+series.map(s=>'<span><i style="background:'+s.color+'"></i>'+s.label+'</span>').join('')+'</div>'+
   '<div class="flow-meta">최근 '+rows.length+'거래일 · 출발점을 0으로 정규화한 누적 순매수 수량 [AI]</div>';
}
function srFlowDetails(code){
  const o=srFlowStock(code),c=String(code||'').split('.')[0].padStart(6,'0');
  const initialDays=o?(o.rows.length>=60?60:o.rows.length>=20?20:5):5;
  const link=/^[0-9]{6}$/.test(c)?'<a target="_blank" rel="noopener noreferrer" href="https://finance.naver.com/item/frgn.naver?code='+c+'">네이버 원자료 보기 ↗</a>':'';
  if(!o)return '<div class="flow-detail"><div class="flow-detail-header"><b>외국인·기관·개인 수급</b><span class="flow-meta">자료 미확인</span></div>'+
    '<div class="flow-no-data">공개된 투자자별 수급이 없거나 데이터가 오래되었습니다. 추정 수치는 표시하지 않습니다.</div><div class="flow-source">'+link+'</div></div>';
  return '<div class="flow-detail"><div class="flow-detail-header"><b>외국인·기관·개인 수급</b><span class="flow-meta">수급 '+srEsc(o.asOf)+(o.lagged?' · 주가 기준 '+srEsc(o.marketAsOf)+' 대비 지연':'')+' · 단위: 주</span></div>'+
    '<div style="overflow-x:auto"><table><thead><tr><th>투자주체</th><th>5거래일</th><th>20거래일</th><th>60거래일</th></tr></thead><tbody>'+
    [['foreign','외국인'],['institution','기관'],['individual','개인']].map(([k,label])=>
       '<tr><th>'+label+'</th>'+[5,20,60].map(n=>{const v=srFlowSum(o,k,n);return '<td class="'+srFlowClass(v)+'">'+srFlowNumber(v)+'</td>'}).join('')+'</tr>'
    ).join('')+'</tbody></table></div>'+ 
    '<div class="flow-chart-controls" aria-label="누적 수급 그래프 표시 기간">'+[5,20,60].map(n=>
      '<button type="button" class="flow-period-btn '+(initialDays===n?'active':'')+'" data-days="'+n+'" data-code="'+srEsc(c)+'" '+(o.rows.length<n?'disabled title="'+n+'거래일 자료 부족"':'')+'>'+n+'D</button>'
    ).join('')+'<span class="flow-meta">차트: '+initialDays+'거래일 기준</span></div>'+
    '<div class="flow-chart-target">'+srFlowChart(o,initialDays)+'</div>'+
    '<div class="flow-source">출처: '+srEsc(o.source)+' · 순매수 수량(주), 실제 순매수 금액이 아닙니다. 자료가 부족한 기간은 —. '+link+'</div></div>';
}
function srRenderDeepDiveInvestorFlow(profile){
  const el=document.getElementById('stockInvestorFlow');if(!el)return;
  el.innerHTML='<h3 class="subhead">종목별 투자자 수급</h3>'+srFlowDetails(profile?.stock_code||profile?.code||'');
}

document.addEventListener('click',event=>{
  const btn=event.target.closest?.('.flow-period-btn');
  if(!btn||btn.disabled)return;
  const days=Number(btn.dataset.days),code=btn.dataset.code;
  if(![5,20,60].includes(days))return;
  const flow=srFlowStock(code),root=btn.closest('.flow-detail');
  if(!flow||flow.rows.length<days||!root)return;
  root.querySelectorAll('.flow-period-btn').forEach(b=>b.classList.toggle('active',b===btn));
  const target=root.querySelector('.flow-chart-target');
  if(target)target.innerHTML=srFlowChart(flow,days);
  const caption=root.querySelector('.flow-chart-controls>.flow-meta');
  if(caption)caption.textContent='차트: '+days+'거래일 기준';
});
