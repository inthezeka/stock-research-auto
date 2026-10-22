/* MY Stock Room 5D investor net buying (shares), display-only.
   Never changes Trading Score, ranking, or any pricing data. */
const INVESTOR_FLOW_URL='data/investor-flow.json';
const FLOW_DAYS=5;
let investorFlowData=null,investorFlowPromise=null;
async function srLoadInvestorFlowCache(){
  if(investorFlowPromise)return investorFlowPromise;
  investorFlowPromise=srFetchJson(INVESTOR_FLOW_URL+'?v='+Date.now())
    .then(v=>{investorFlowData=v?.meta&&v?.items?v:{meta:{},items:{}};return investorFlowData})
    .catch(err=>{console.warn('Investor flow unavailable',err);investorFlowData={meta:{},items:{}};return investorFlowData});
  return investorFlowPromise;
}
function srFlowStock(code){
  const c=String(code||'').split('.')[0].padStart(6,'0');
  if(!/^[0-9]{6}$/.test(c))return null;
  const meta=investorFlowData?.meta||{},raw=investorFlowData?.items?.[c];
  if(meta.unit!=='shares'||!meta.market_date||meta.market_date!==stockUniverseMeta?.as_of||
    !raw||raw.stale||raw.unit!=='shares'||!Array.isArray(raw.rows))return null;
  const rows=raw.rows.filter(r=>/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(String(r.date))&&r.date<=meta.market_date)
    .sort((a,b)=>a.date.localeCompare(b.date)).slice(-FLOW_DAYS);
  if(!rows.length)return null;
  return{code:c,rows,asOf:rows.at(-1).date,marketAsOf:meta.market_date,
    lagged:rows.at(-1).date<meta.market_date,source:raw.source||meta.source||'확인 필요'};
}
function srFlowSum(flow,kind){
  if(!flow||flow.rows.length<FLOW_DAYS)return null;
  if(flow.rows.some(r=>r[kind]==null||!Number.isSafeInteger(Number(r[kind]))))return null;
  return flow.rows.reduce((a,r)=>a+Number(r[kind]),0);
}
function srFlowSignClass(n){return n==null?'flow-neutral':n>0?'flow-pos':n<0?'flow-neg':'flow-neutral'}
function srFlowNumber(n){
  if(n==null||!Number.isFinite(Number(n)))return '—';
  const abs=Math.abs(Number(n));
  const label=abs>=10000?(abs/10000).toLocaleString('ko-KR',{maximumFractionDigits:2})+'만주':Math.round(abs).toLocaleString('ko-KR')+'주';
  return(n>0?'+':n<0?'−':'')+label;
}
function srFlowExactNumber(n){
  return n==null?'자료 없음':(n>0?'+':n<0?'−':'')+Math.abs(n).toLocaleString('ko-KR')+'주';
}
function srFlowSourceLink(flow,code){
  const c=String(code||'').split('.')[0].padStart(6,'0');
  if(flow?.source?.startsWith('KIS investor'))
    return '<a href="https://apiportal.koreainvestment.com/" target="_blank" rel="noopener noreferrer">KIS 데이터 안내 ↗</a>';
  if(flow?.source?.startsWith('KRX user-authorized'))
    return '<a href="https://data.krx.co.kr/" target="_blank" rel="noopener noreferrer">KRX 데이터 안내 ↗</a>';
  return /^[0-9]{6}$/.test(c)
    ?'<a href="https://finance.naver.com/item/frgn.naver?code='+c+'" target="_blank" rel="noopener noreferrer">수급 원자료 확인 ↗</a>':'';
}
function srFlowMini(code){
  const flow=srFlowStock(code);
  if(!flow||flow.rows.length<FLOW_DAYS)
    return '<div class="flow-mini flow-mini-empty"><strong>5D 자료 미확인</strong><small>최근 5거래일 수급 조회 대기</small></div>';
  const actors=[['foreign','외국인'],['institution','기관'],['individual','개인']];
  return '<div class="flow-mini" aria-label="최근 5거래일 투자자별 순매수 수량">'+
    '<div class="flow-mini-grid">'+actors.map(([key,name])=>{
      const n=srFlowSum(flow,key);
      return '<div class="flow-mini-item" title="'+srEsc(name+' · '+srFlowExactNumber(n))+'">'+
        '<span class="flow-mini-name">'+name+'</span>'+
        '<strong class="flow-mini-value '+srFlowSignClass(n)+'">'+srFlowNumber(n)+'</strong></div>';
    }).join('')+'</div>'+
    '<div class="flow-mini-date">수급 '+srEsc(flow.asOf)+(flow.lagged?' · 종가 기준일과 다름':'')+'</div></div>';
}
function srFlowAxisLabel(n){
  if(!Number.isFinite(n))return '—';
  const v=Math.abs(n),label=v>=10000?(v/10000).toLocaleString('ko-KR',{maximumFractionDigits:1})+'만':Math.round(v).toLocaleString('ko-KR');
  return (n<0?'−':'')+label;
}
function srFlowChart(flow){
  if(!flow||flow.rows.length<FLOW_DAYS)return '';
  const rows=flow.rows.slice(-FLOW_DAYS);
  const actors=[['foreign','외국인','#3F71C9'],['institution','기관','#0B9A88'],['individual','개인','#D99139']];
  const series=actors.map(([key,name,color])=>{
    const daily=rows.map(r=>r[key]);
    if(daily.some(v=>v==null||!Number.isSafeInteger(Number(v))))return null;
    let total=0;
    return{name,color,daily:daily.map(Number),values:[0,...daily.map(v=>(total+=Number(v)))]};
  }).filter(Boolean);
  if(!series.length)return '<div class="flow-viz-empty">일별 수급 데이터를 확인할 수 없습니다.</div>';
  const vals=series.flatMap(s=>s.values).concat([0]);
  let lo=Math.min(...vals),hi=Math.max(...vals);
  if(lo===hi){lo-=1;hi+=1}
  const pad=(hi-lo)*.12;lo-=pad;hi+=pad;
  const x0=72,y0=24,w=686,h=191;
  const x=i=>x0+w*i/FLOW_DAYS,y=v=>y0+h-(v-lo)/(hi-lo)*h;
  const grid=[0,.25,.5,.75,1].map(t=>{
    const val=hi-(hi-lo)*t,yy=y0+h*t;
    return '<line x1="'+x0+'" x2="'+(x0+w)+'" y1="'+yy.toFixed(1)+'" y2="'+yy.toFixed(1)+'" stroke="#E8EEF5" stroke-width="1"/>'+
      '<text x="'+(x0-13)+'" y="'+(yy+4).toFixed(1)+'" text-anchor="end" fill="#66778A" font-size="12">'+
      srEsc(srFlowAxisLabel(val))+'</text>';
  }).join('');
  const zero='<line x1="'+x0+'" x2="'+(x0+w)+'" y1="'+y(0).toFixed(1)+'" y2="'+y(0).toFixed(1)+'" stroke="#97A6BA" stroke-width="1.6" stroke-dasharray="5 5"/>';
  const lines=series.map(s=>{
    const points=s.values.map((v,i)=>x(i).toFixed(1)+','+y(v).toFixed(1)).join(' ');
    const dots=s.values.slice(1).map((v,i)=>{
      const info=s.name+' · '+rows[i].date+' · 누적 '+srFlowExactNumber(v)+' · 당일 '+srFlowExactNumber(s.daily[i]);
      return '<circle cx="'+x(i+1).toFixed(1)+'" cy="'+y(v).toFixed(1)+'" r="4.2" stroke="'+s.color+'" stroke-width="2.5" fill="#FFF"><title>'+srEsc(info)+'</title></circle>';
    }).join('');
    return '<polyline fill="none" stroke="'+s.color+'" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="'+points+'"/>'+dots;
  }).join('');
  const dateLabels=rows.map((r,i)=>'<text x="'+x(i+1).toFixed(1)+'" y="250" font-size="12.5" font-weight="650" text-anchor="middle" fill="#68798C">'+srEsc(r.date.slice(5).replace('-','.'))+'</text>').join('');
  const legend='<div class="flow-chart-legend">'+series.map(s=>'<span class="flow-legend-item"><i style="background:'+s.color+'"></i>'+s.name+'</span>').join('')+'</div>';
  return '<div class="flow-chart-frame"><svg class="flow-chart" viewBox="0 0 805 270" role="img" aria-label="최근 5거래일 투자자별 누적 순매수 수량 그래프">'+
    '<rect x="'+x0+'" y="'+y0+'" width="'+w+'" height="'+h+'" rx="8" fill="#FBFCFE"/>'+
    grid+zero+lines+dateLabels+'</svg>'+legend+'</div>';
}
function srFlowDetails(code){
  const flow=srFlowStock(code),link=srFlowSourceLink(flow,code);
  if(!flow||flow.rows.length<FLOW_DAYS){
    return '<div class="flow-detail flow-detail-empty">'+
      '<div class="flow-empty-icon" aria-hidden="true">↗</div><strong>5D 투자자 수급 자료 확인 중</strong>'+
      '<p>검증된 5거래일 데이터가 없으면 임의의 수치를 표시하지 않습니다.</p>'+
      '<div class="flow-source">'+link+'</div></div>';
  }
  const fields=[['foreign','외국인'],['institution','기관'],['individual','개인']];
  const stats=fields.map(([key,name])=>{
    const n=srFlowSum(flow,key),state=n==null?'데이터 미확인':n>0?'5D 누적 순매수':n<0?'5D 누적 순매도':'매수·매도 균형';
    return '<div class="flow-stat"><span class="flow-stat-name">'+name+'</span>'+
      '<strong class="flow-stat-value '+srFlowSignClass(n)+'" title="'+srEsc(srFlowExactNumber(n))+'">'+srFlowNumber(n)+'</strong>'+
      '<span class="flow-stat-state">'+state+'</span></div>';
  }).join('');
  const lag=flow.lagged?' · 주가 기준 '+srEsc(flow.marketAsOf)+'보다 이전':'';
  return '<div class="flow-detail">'+
    '<div class="flow-panel-heading"><div><p class="flow-eyebrow">INVESTOR FLOW · 5D</p>'+
    '<h3 class="flow-panel-title">외국인 · 기관 · 개인</h3></div>'+
    '<div class="flow-date">수급 기준 '+srEsc(flow.asOf)+lag+'</div></div>'+
    '<div class="flow-stats">'+stats+'</div>'+
    '<div class="flow-viz"><div class="flow-viz-heading"><div><h4>최근 5거래일 누적 수급 추이</h4>'+
    '<p>수집 구간 시작점을 0으로 정규화해 순매수 수량을 누적했습니다. [AI]</p></div>'+
    '<span class="flow-unit">단위 · 주</span></div>'+
    srFlowChart(flow)+'</div>'+
    '<div class="flow-foot"><span>출처: '+srEsc(flow.source)+' · 순매수 수량 기준</span>'+link+'</div></div>';
}
function srRenderDeepDiveInvestorFlow(profile){
  const el=document.getElementById('stockInvestorFlow');if(!el)return;
  el.innerHTML=srFlowDetails(profile?.stock_code||profile?.code||'');
}
