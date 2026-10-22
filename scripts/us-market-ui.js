/* MY Stock Room US MASTER v2: independent US fundamental, cycle, theme and trading. */
(()=>{'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const valid=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
const number=(v,d=2)=>valid(v)?Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
const pct=v=>valid(v)?(Number(v)>0?'+':'')+number(v,2)+'%':'—';
const css=v=>!valid(v)?'muted':Number(v)>0?'pos':Number(v)<0?'neg':'muted';
const usd=v=>valid(v)&&Number(v)>0?'$'+number(v):'—';
const billions=v=>valid(v)?'$'+number(Number(v)/1e9,2)+'B':'—';
const confidence=v=>['HIGH','MEDIUM','LOW','INSUFFICIENT'].includes(v)?v:'INSUFFICIENT';
const chip=(txt,extra='')=>'<span class="status-chip '+extra+'">'+esc(txt)+'</span>';
let data={meta:{},indices:{},sectors:[],items:[],sector_cycles:[],theme_popularity:[]};
let byTicker=new Map(), selectedIndex='ALL', sectorKey='ALL', query='', selected=null;

function scope(t){return (selectedIndex==='ALL'||(t.indices||[]).includes(selectedIndex))&&
 (sectorKey==='ALL'||t.sector_key===sectorKey) &&
 (!query||(String(t.ticker||'')+' '+String(t.name||'')).toLowerCase().includes(query))}
function relevant(){return data.items.filter(scope)}
function domRow(t,values){
 return '<tr tabindex="0" role="button" data-ticker="'+esc(t.ticker)+'" aria-label="'+esc(t.name||t.ticker)+' 상세보기">'+
 values.map(x=>'<td>'+x+'</td>').join('')+'</tr>';
}
function listenRows(){
 document.querySelectorAll('tr[data-ticker]').forEach(tr=>{
  const select=()=>choose(tr.dataset.ticker);
  tr.addEventListener('click',select);
  tr.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select()}});
 });
}
function indexCards(){
 const names=[['NASDAQ100','NASDAQ-100'],['SP500','S&P 500'],['DOW30','DOW 30'],['NASDAQCOMP','NASDAQ Composite']];
 $('indexCards').innerHTML=names.map(([key,label])=>{
  const item=(data.indices||{})[key]||{};
  return '<div class="card mcap"><span>'+esc(label)+'</span><strong>'+number(item.close)+'</strong>'+
    '<em class="'+css(item.change_pct)+'">'+pct(item.change_pct)+' · '+esc(item.date||'미확인')+'</em></div>';
 }).join('');
}
function renderSectors(){
 const arr=data.items.filter(t=>selectedIndex==='ALL'||(t.indices||[]).includes(selectedIndex));
 const counts=new Map();arr.forEach(t=>counts.set(t.sector_key,(counts.get(t.sector_key)||0)+1));
 const controls=[{key:'ALL',label:'전체 US Sector'},...(data.sectors||[])];
 $('sectorButtons').innerHTML=controls.map(t=>'<button class="sector'+(sectorKey===t.key?' active':'')+'" data-sector="'+esc(t.key)+'">'+
  esc(t.key==='ALL'?'전체 US Sector':t.key+' · '+t.label)+' <span class="cnt">'+(t.key==='ALL'?arr.length:(counts.get(t.key)||0))+'</span></button>').join('');
 const label=controls.find(x=>x.key===sectorKey)?.label||'전체 US Sector';
 $('sectorLabel').textContent=label;
 $('sectorButtons').querySelectorAll('[data-sector]').forEach(btn=>btn.addEventListener('click',()=>{
  sectorKey=btn.dataset.sector;renderSectors();renderAll();}));
}
function fundamentalOrder(rows){
 return rows.slice().sort((a,b)=>
  (Number(b.fundamental_score||0)-Number(a.fundamental_score||0)) ||
  Number(b.fundamental_coverage||0)-Number(a.fundamental_coverage||0) ||
  a.ticker.localeCompare(b.ticker));
}
function confident(t){return valid(t.fundamental_score)&&['HIGH','MEDIUM'].includes(t.fundamental_confidence)}
function renderFundamental(){
 const rows=relevant();
 const rated=fundamentalOrder(rows.filter(confident));
 const low=fundamentalOrder(rows.filter(x=>x.fundamental_confidence==='LOW'&&valid(x.fundamental_score)));
 const missing=rows.filter(x=>!valid(x.fundamental_score)||
    !['HIGH','MEDIUM','LOW'].includes(x.fundamental_confidence)).sort((a,b)=>
    (Number(b.fundamental_coverage||0)-Number(a.fundamental_coverage||0))||
    Number(b.close!=null)-Number(a.close!=null)||a.ticker.localeCompare(b.ticker));
 $('fundTitle').textContent=(sectorKey==='ALL'?'미국':(data.sectors.find(s=>s.key===sectorKey)?.label||'미국'))+' Fundamental TOP20';
 const met=(data.meta||{});
 const issued=rated.length;
 const denom=rows.length;
 $('fundNotice').textContent=(issued?('HIGH/MEDIUM 근거가 확보된 '+issued+'개 기업 중 상위 20개를 선정했습니다.'):
  'HIGH/MEDIUM SEC 재무근거가 확보되지 않아 Fundamental TOP20은 아직 발행하지 않습니다.')+
  ' · 현재 범위 '+denom.toLocaleString('ko-KR')+'종목 / 평가점수 '+(issued+low.length)+'종목'+
  ' · NO_CONSENSUS_PROFILE은 Revision(R) 배점을 Growth(G)와 KPI(K)에 50%씩 재배분합니다. 미확인 요소는 근거를 공개합니다.'+
  (met.full_us_market_fundamental_rank_verified?'':' · 전체 미국장 완전 커버리지 검증 전입니다.');
 const fmtConf=t=>{
  const level=confidence(t.fundamental_confidence);
  return chip(level,level==='HIGH'?'good':level==='LOW'?'low':'')+
   '<div class="evidence"><i style="width:'+Math.min(100,Math.max(0,Number(t.fundamental_coverage||0)*100))+'%"></i></div>'+
   '<small>'+number(Number(t.fundamental_coverage||0)*100,1)+'% 근거</small>';
 };
 $('fundRows').innerHTML=rated.slice(0,20).map((t,i)=>{
   const f=t.financials||{},detail=f.score_detail||{},v=detail.valuation_context||{};
   const kpis=(data.sectors||[]).find(s=>s.key===t.sector_key)?.kpis||[];
   return domRow(t,[i+1,'<b>'+esc(t.name)+'</b><small>'+esc(t.ticker)+'</small>',
    '<span class="tiny">'+esc(t.sector_key)+'</span><br>'+esc(t.sector_label||'미분류'),
    '<b class="mono">'+number(t.fundamental_score,1)+'</b>',fmtConf(t),
    pct(f.revenue_growth),valid(f.operating_income)?billions(f.operating_income):'—',
    valid(v.pe_fy)?number(v.pe_fy)+'× (FY)':'—',
    kpis.slice(0,2).map(esc).join('<br>')||'—',
    esc(detail.missing?.join(', ')||'자료 출처·기간 추가 확인')]);
 }).join('')||'<tr><td colspan="10">검증된 미국 Fundamental TOP20 미발행 · SEC/GUIDANCE/CONSENSUS 데이터 확보 대기</td></tr>';
 $('fundPendingRows').innerHTML=[...low,...missing].slice(0,30).map(t=>{
  const f=t.financials||{},d=f.score_detail||{},lv=confidence(t.fundamental_confidence);
  return domRow(t,['<b>'+esc(t.ticker)+'</b><small>'+esc(t.name)+'</small>',esc(t.sector_label||'업종 확인 필요'),
   valid(t.fundamental_score)?number(t.fundamental_score,1):'발행 보류',
   chip(lv,lv==='LOW'?'low':'')+'<small>Coverage '+number(Number(t.fundamental_coverage||0)*100,1)+'%</small>',
   usd(t.close),esc(d.reason||f.reason||'SEC·매출·산업 핵심 KPI 검증 미완료')]);
 }).join('')||'<tr><td colspan="6">현재 해당 범위에서 검증 대기 종목이 없습니다.</td></tr>';
}
function renderTrading(){
 const eligible=relevant().filter(t=>valid(t.trading_score)&&valid(t.close)&&
 ['상승','상승초기'].includes(t.trend) && Number(t.rsi||0)<78)
 .sort((a,b)=>Number(b.trading_score)-Number(a.trading_score)||
  Number(b.return5d||0)-Number(a.return5d||0)).slice(0,10);
 $('tradeRows').innerHTML=eligible.map((t,i)=>domRow(t,[i+1,'<b>'+esc(t.ticker)+'</b><small>'+esc(t.name)+'</small>',
  usd(t.close),'<b class="mono">'+number(t.trading_score,0)+'</b>',
  chip((t.entry_quality||'C')+' · 가격구조 후보',t.entry_quality==='S'?'good':''),
  number(t.rsi,1),'<span class="'+css(t.return20d)+'">'+pct(t.return20d)+'</span> / <span class="'+css(t.return60d)+'">'+pct(t.return60d)+'</span>',
  esc(t.data_confidence||'LOW')])).join('')||
  '<tr><td colspan="8">검증된 Long Only 가격구조 후보가 없습니다.</td></tr>';
}
function renderCycles(){
 const cycles=data.sector_cycles||[];
 const matched=cycles.filter(c=>(c.count||0)>0).sort((a,b)=>{
  const bScore=valid(b.cycle_score)?Number(b.cycle_score):-1,aScore=valid(a.cycle_score)?Number(a.cycle_score):-1;
  return bScore-aScore||Number(b.median_20d||-999)-Number(a.median_20d||-999);
 });
 const verified=matched.filter(t=>valid(t.cycle_score));
 $('cycleNotice').textContent=verified.length?
   verified.length+'개 US Sector Cycle은 검증된 요인에 따라 산출되었습니다. 나머지는 미판정입니다.':
   '미국 Sector Cycle 100점은 아직 미발행입니다. 7개 요인 중 확인되지 않은 공급·재고·수주·Guidance 등을 가격 모멘텀으로 임의 대체하지 않습니다. 아래 20D 값은 단순 모니터링 순서입니다.';
 $('cycleRows').innerHTML=matched.slice(0,10).map((t,i)=>{
  const sec=data.sectors.find(x=>x.key===t.key)||{};
  return '<tr><td>'+(valid(t.cycle_score)?i+1:'—')+'</td><td><b>'+esc(t.label)+'</b><small>'+esc(t.key)+'</small></td>'+
  '<td>'+esc(t.actual_cycle||'미판정')+'</td><td>'+esc(t.cycle_attractiveness||'미판정')+'</td>'+
  '<td>'+(valid(t.cycle_score)?number(t.cycle_score,1):'<span class="score-missing">점수 미발행</span>')+'</td>'+
  '<td>'+esc(t.fundamental_environment||'미평가')+'</td><td>'+number(t.investment_score,1)+'</td>'+
  '<td class="'+css(t.median_20d)+'">'+pct(t.median_20d)+'</td><td><small>'+esc(t.note||'검증 필요')+'</small></td></tr>';
 }).join('')||'<tr><td colspan="9">미국 Sector 데이터 검증 대기</td></tr>';
}
function renderThemes(){
 const themes=data.theme_popularity||[];
 const ranked=themes.filter(t=>valid(t.popularity_score)).sort((a,b)=>b.popularity_score-a.popularity_score);
 const provisional=themes.filter(t=>!valid(t.popularity_score) && t.members>0);
 provisional.sort((a,b)=>Number(b.price_momentum_20d||-999)-Number(a.price_momentum_20d||-999));
 $('themeNotice').textContent=ranked.length?
  '확인된 Popularity 점수만 인기테마로 분류합니다. Narrative Only는 수익화 수혜주로 확정하지 않습니다.':
  '뉴스·실적 Catalyst·매출 관련성을 검증하지 못해 인기테마 점수와 인기 판정을 발행하지 않았습니다. 아래는 20D 가격 변화에 따른 관심 테마 후보이며, 인기순위가 아닙니다.';
 const arr=[...ranked,...provisional].slice(0,10);
 $('themeRows').innerHTML=arr.map((t,i)=>'<tr><td>'+(valid(t.popularity_score)?i+1:'—')+'</td>'+
  '<td><b>'+esc(t.theme)+'</b></td><td>'+(valid(t.popularity_score)?number(t.popularity_score,1):'미발행')+'</td>'+
  '<td>'+number(t.sector_count,0)+'개</td>'+
  '<td>'+esc((t.representative_tickers||[]).slice(0,3).join(' · ')||'—')+'</td>'+
  '<td class="'+css(t.price_momentum_20d)+'">'+pct(t.price_momentum_20d)+'</td>'+
  '<td>'+esc(t.catalyst||'미검증')+'</td>'+
  '<td>'+chip(t.monetization_stage||'NARRATIVE ONLY')+'</td></tr>').join('')||
  '<tr><td colspan="8">검증된 테마 관련성이 없습니다.</td></tr>';
}
function renderSectorTop3(){
 const inScope=data.items.filter(x=>(selectedIndex==='ALL'||(x.indices||[]).includes(selectedIndex)) &&
 (!query||(x.ticker+' '+x.name).toLowerCase().includes(query)));
 const rows=(data.sectors||[]).map(sec=>{
  const candidates=inScope.filter(x=>x.sector_key===sec.key);
  candidates.sort((a,b)=>Number(confident(b))-Number(confident(a))||
    Number(valid(b.fundamental_score))-Number(valid(a.fundamental_score))||
    Number(b.fundamental_score||0)-Number(a.fundamental_score||0)||
    Number(b.close!=null)-Number(a.close!=null)||a.ticker.localeCompare(b.ticker));
  const top=candidates.slice(0,3);
  const verified=top.filter(confident).length;
  const cells=top.map(t=>'<button class="btn" data-choose="'+esc(t.ticker)+'" title="'+esc(t.name)+'">'+esc(t.ticker)+'</button>');
  while(cells.length<3)cells.push('<span class="score-missing">추가 후보 미확인</span>');
  return '<tr><td><b>'+esc(sec.key)+' · '+esc(sec.label)+'</b><small>후보 '+candidates.length+'</small></td>'+
   cells.map(c=>'<td>'+c+'</td>').join('')+'<td>'+(verified?'검증 '+verified+'개 · ':'')+
   (verified===top.length&&top.length===3?'HIGH/MEDIUM':'추가적인 분석 필요')+'</td></tr>';
 });
 $('sectorTop3Rows').innerHTML=rows.join('');
}
function renderThemeTop3(){
 const vals=data.theme_popularity||[],byName=new Map();
 data.items.filter(t=>selectedIndex==='ALL'||(t.indices||[]).includes(selectedIndex)).forEach(t=>{
  (t.primary_themes||[]).forEach(theme=>{
   if(!byName.has(theme.theme))byName.set(theme.theme,[]);
   byName.get(theme.theme).push({stock:t,theme});
  });
 });
 const rows=(data.themes||[]).map(th=>{
  const list=(byName.get(th.name)||[]).filter(x=>!query||(x.stock.ticker+' '+x.stock.name).toLowerCase().includes(query));
  list.sort((a,b)=>Number(confident(b.stock))-Number(confident(a.stock))||
    Number(b.stock.fundamental_score||0)-Number(a.stock.fundamental_score||0)||
    a.stock.ticker.localeCompare(b.stock.ticker));
  const top=list.slice(0,3),labels=top.map(x=>'<button class="btn" data-choose="'+esc(x.stock.ticker)+'">'+esc(x.stock.ticker)+'</button>').join(' ');
  const stages=new Set(top.map(x=>x.theme.status));
  return '<tr><td><b>'+esc(th.name)+'</b><small>후보 '+list.length+'</small></td><td>'+
  new Set(top.map(x=>x.stock.sector_key).filter(Boolean)).size+'개 Sector</td><td>'+
  (labels||'<span class="score-missing">확인 필요</span>')+'</td><td>'+
  chip(stages.size===1?[...stages][0]:'NARRATIVE ONLY')+'</td></tr>';
 });
 $('themeTop3Rows').innerHTML=rows.join('');
}
function renderInvestment(){
 const summary=data.investment_best5||{};
 $('investmentStatus').textContent=summary.note||'Risk/Reward·Catalyst 등 검증된 정보가 부족하여 Investment BEST5는 미발행입니다.';
 const results=(summary.ranking||[]).filter(x=>valid(x.investment_score)).slice(0,5);
 $('best5Rows').innerHTML=results.map((r,i)=>'<tr><td>'+(i+1)+'</td><td>'+esc(r.ticker)+'</td>'+
   '<td>'+number(r.fundamental_score,1)+'</td><td>'+number(r.investment_score,1)+'</td>'+
   '<td>'+esc(r.risk_reward||'미확인')+'</td><td>'+esc(r.evidence||'검증 미완료')+'</td></tr>').join('')||
  '<tr><td colspan="6">Fundamental 60 + Valuation/RR 20 + Catalyst 10 + Technical 5 + Liquidity 5의 검증이 완료되지 않아 BEST5를 발행하지 않습니다.</td></tr>';
}
function renderAll(){
 const scopeRows=relevant(),met=data.meta||{},fund=scopeRows.filter(t=>valid(t.fundamental_score));
 $('universeInfo').textContent='현재 필터 '+scopeRows.length.toLocaleString('ko-KR')+
  '개 / 기준일 일봉 확인 '+scopeRows.filter(t=>valid(t.close)).length+
  '개 / SEC Fundamental 평가 '+fund.length+'개 · 투자섹터는 공식 GICS와 구분한 Internal Classification입니다.';
 renderFundamental();renderTrading();renderCycles();renderThemes();
 renderSectorTop3();renderThemeTop3();renderInvestment();listenRows();
 document.querySelectorAll('[data-choose]').forEach(btn=>btn.addEventListener('click',()=>choose(btn.dataset.choose)));
}
function chart(rows){
 if(!Array.isArray(rows)||rows.length<2)return '<p class="muted">검증된 일봉 데이터가 없습니다.</p>';
 const cand=rows.slice(-65).filter(x=>valid(x.close));if(cand.length<2)return '';
 const val=cand.map(x=>Number(x.close)),mn=Math.min(...val)*.98,mx=Math.max(...val)*1.02,w=680,h=210,l=58,top=18,diff=Math.max(.001,mx-mn);
 const xx=i=>l+i*w/(val.length-1),yy=v=>top+(mx-v)/diff*h;
 const guides=[0,.25,.5,.75,1].map(t=>'<line x1="'+l+'" x2="'+(l+w)+'" y1="'+(top+t*h)+'" y2="'+(top+t*h)+'" stroke="#e6edf5"/><text x="50" y="'+(top+t*h+4)+'" text-anchor="end" fill="#708099" font-size="12">'+number(mx-t*diff)+'</text>').join('');
 const points=val.map((v,i)=>xx(i).toFixed(1)+','+yy(v).toFixed(1)).join(' ');
 return '<svg class="chart" viewBox="0 0 760 260" role="img" aria-label="검증된 일봉 종가 차트">'+guides+
   '<polyline fill="none" stroke="#1769e0" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="'+points+'"/>'+
   '<text x="'+l+'" y="249" font-size="12" fill="#66768b">'+esc(cand[0].date)+'</text>'+
   '<text x="'+(l+w)+'" y="249" text-anchor="end" font-size="12" fill="#66768b">'+esc(cand.at(-1).date)+'</text></svg>';
}
function facts(rows){
 return '<div class="facts">'+rows.map(([name,value])=>'<div class="fact"><small>'+esc(name)+'</small><strong>'+value+'</strong></div>').join('')+'</div>';
}
function details(t){
 const f=t.financials||{},d=f.score_detail||{},v=d.valuation_context||{};
 const sec=data.sectors.find(x=>x.key===t.sector_key)||{};
 const score=valid(t.fundamental_score)?number(t.fundamental_score,1):'미발행';
 const grade=confidence(t.fundamental_confidence);
 const model=f.score_detail?.model||sec.model||'미분류';
 const account=f.accounting_standard||(t.foreign_issuer?'20-F / IFRS 확인 필요':'SEC 검증 필요');
 const themeHtml=(t.primary_themes||[]).map(x=>chip(x.theme+' · '+x.status)).join(' ');
 const weights=d.effective_weights||sec.score_weights||{};
 const comp=d.components||{};
 const components=['G','Q','B','V','K','R'].map(k=>{
  const c=comp[k]||{},w=weights[k]??0;
  return '<div><small>'+esc(k)+' · '+w+'점</small><strong>'+(valid(c.value_0_5)?number(c.value_0_5,1)+'/5':'—')+'</strong><small>근거 '+number(Number(c.coverage||0)*100,0)+'%</small></div>';
 }).join('');
 const caution=d.neutral_imputation||[];
 $('stockDetail').innerHTML='<div class="dhead"><div><span class="eyebrow">'+esc(t.exchange||'US EXCHANGE')+' · '+esc((t.indices||[]).join(' / '))+'</span>'+
  '<h3>'+esc(t.name)+' <span class="muted">('+esc(t.ticker)+')</span></h3>'+
  '<small>'+esc(t.sector_key||'—')+' · '+esc(t.sector_label||'업종 확인 필요')+' · '+esc(t.sector_classification||'Internal Sector Classification')+
  ' · '+esc(t.classification_method||'UNCLASSIFIED')+'</small></div>'+
  chip(grade,grade==='HIGH'?'good':grade==='LOW'?'low':'')+'</div>'+
  '<div class="principal">US 전용 모델: <b>'+esc(model)+'</b> · 점수 '+score+' / 100 · Evidence Coverage '+number(Number(t.fundamental_coverage||0)*100,1)+
  '% · '+esc(d.profile||'NO_CONSENSUS_PROFILE 검증 전')+'. 리포트와 Guidance가 없다고 Revision을 임의 산출하지 않습니다.</div>'+
  facts([['현재 종가',usd(t.close)],['Fundamental Score',score],['Trading Score',number(t.trading_score,0)],['FY 매출',billions(f.revenue)],
   ['FY 매출 성장',pct(f.revenue_growth)],['영업이익률',pct(f.opm)],['FY EPS',usd(f.eps_fy)],['FY P/E',valid(v.pe_fy)?number(v.pe_fy)+'×':'—']])+
  '<div class="twocol section-spacing"><div><h3>최근 일봉 · 가격 추이</h3>'+chart(t.chart||[])+
  '<p class="detail-note">USD 종가 · '+esc(t.price_date||'거래일 미확인')+' · '+esc(t.price_source||'검증 소스 미확인')+'. Chart/M’s Trading은 Fundamental에 혼합하지 않습니다.</p></div>'+
  '<div><h3>업종별 Fundamental 6개 영역</h3><div class="score-dash">'+components+'</div>'+
  '<p class="detail-note">G 성장 · Q 수익성 · B 재무/FCF · V Valuation · K 업종 KPI · R Revision. 미확인 데이터는 0점 처리하지 않으며, 검증범위 50% 미만은 점수 미발행. 미확인 배점의 중립 가정은 근거와 함께 표시합니다.</p>'+
  '<p class="detail-note">보수적 중립대체: '+esc(caution.map(x=>x.component+' '+number(x.weight_points,1)+'점').join(' · ')||'없음')+'</p></div></div>'+
  '<h3 class="section-spacing">주요 업종 KPI · 확인 필요 항목</h3><div class="detail-kpi">'+(sec.kpis||[]).map(x=>chip(x)).join(' ')+'</div>'+
  '<h3 class="section-spacing">Theme 후보 · 수익화 단계</h3><div class="detail-kpi">'+(themeHtml||chip('테마 관련성 검증 필요'))+'</div>'+
  '<p class="detail-note">Theme는 Sector와 별도로 관리하며 후보 연결만으로 VERIFIED REVENUE를 표시하지 않습니다.</p>'+
  '<div class="principal">회계 기준 '+esc(account)+' · FY 종료일 '+esc(f.fiscal_year_end||'미확인')+' · SEC 제출 '+esc(f.filing_date||'미확인')+
  ' · Source Basis '+esc(f.source_basis||'자료 미확인')+' · Foreign Issuer '+(t.foreign_issuer?'ADR 확인 필요':'표시 없음')+
  '. 회사 Guidance: 미연동 / Licensed Analyst Consensus: 미연동 / AI Scenario: 사용 안 함.</div>';
 $('volumeFlow').innerHTML='<h3>'+esc(t.ticker)+' · 최근 5거래일 거래량</h3>'+
  facts([['5D 가격 변화',pct(t.return5d)],['상대거래량',valid(t.volume_ratio)?number(t.volume_ratio)+'×':'—'],
   ['5D OBV 방향',esc((t.flow5d||{}).obv_trend||'미확인')],['5D 거래량',(t.flow5d||{}).volume==null?'—':number(t.flow5d.volume,0)+'주']])+
  '<p class="detail-note">단기 거래량·OBV는 미국장 매매 압력의 간접 참고 지표이며, 한국처럼 외국인·기관·개인별 순매수로 식별할 수 없습니다. [AI]</p>';
 const linkTicker=encodeURIComponent(t.ticker||'');
 const cik=String(f.cik||'').padStart(10,'0');
 const secCik=f.cik?'<a class="btn" target="_blank" rel="noopener noreferrer" href="https://data.sec.gov/api/xbrl/companyfacts/CIK'+encodeURIComponent(cik)+'.json">Company Facts 원자료 ↗</a>':'';
 $('researchLinks').innerHTML='<h3>'+esc(t.ticker)+' · SEC / Nasdaq 원문 확인</h3>'+
  '<p class="desc">Historical SEC Facts, 회사 Guidance, 정식 Consensus, AI 시나리오는 서로 다른 자료입니다. 애널리스트 3개월 목표주가·Earnings Revision은 라이선스 데이터 확인 전 표시하지 않습니다.</p>'+
  '<div class="links"><a class="btn" href="https://www.sec.gov/edgar/search/#/q='+linkTicker+'" rel="noopener noreferrer" target="_blank">SEC EDGAR 검색 ↗</a>'+
  secCik+'<a class="btn" href="https://www.nasdaq.com/market-activity/stocks/'+encodeURIComponent(t.ticker.toLowerCase())+'" rel="noopener noreferrer" target="_blank">Nasdaq 시세 확인 ↗</a></div>'+
  '<p class="detail-note">공시 Risk: 유상증자·전환증권·Stock Based Compensation·Diluted Shares·ADR 비율은 SEC 문서와 단위가 확인된 경우에만 확정합니다.</p>';
 document.title=t.ticker+' | US Stock Research · MY Stock Room';
}
function choose(ticker){
 const t=byTicker.get(ticker);if(!t)return;
 selected=t;details(t);
 $('deepdive').scrollIntoView({behavior:'smooth',block:'start'});
}
function search(){
 query=$('tickerSearch').value.trim().toLowerCase();
 renderAll();
 const exact=data.items.find(t=>t.ticker.toLowerCase()===query);
 if(exact){choose(exact.ticker);return}
 const entries=relevant();
 if(entries.length===1)choose(entries[0].ticker);
 if(entries.length===0)$('universeInfo').textContent='검색 결과 없음 · 티커를 확인하세요.';
}
async function boot(){
 try{
  const response=await fetch('./data/us-market.json?v='+Date.now(),{cache:'no-store'});
  if(!response.ok)throw new Error('HTTP '+response.status);
  const payload=await response.json();if(!payload||!Array.isArray(payload.items))throw new Error('Invalid US market payload');
  data=payload;byTicker=new Map(data.items.map(t=>[t.ticker,t]));
  const meta=data.meta||{},stats=data.items.filter(t=>valid(t.close)).length;
  $('updated').textContent='미국장 기준 '+(meta.as_of||'미확인')+' ET · '+(meta.status||'unknown');
  $('marketNotice').textContent='미국 상장 후보 '+data.items.length+'개 / 일봉 확보 '+stats+'개 / SEC Fundamental 점수 '+(meta.fundamental_scored_count||0)+
   '개. US Investment Sector 44개는 독립적인 내부 투자분류이며, 공식 GICS 검증 여부를 구분합니다. '+
   (meta.sec?.status==='not_configured'?'SEC 미연결: GitHub Secret US_SEC_USER_AGENT 설정 필요. ':'')+
   '가격은 USD, 거래일은 ET. 전체 미국시장 Fundamental 100% 탐색·평가가 완료됐다고 해석하지 마세요.';
  if((data.sectors||[]).length!==44){
   $('marketNotice').textContent+=' · 이전 데이터 구조가 표시 중입니다. 신규 44개 Sector 배포 데이터를 확인하세요.';
  }
  indexCards();renderSectors();renderAll();
  if(!data.items.length)$('stockDetail').textContent='미국장 검증 대상이 없습니다.';
 }catch(e){
  $('updated').textContent='미국장 데이터 확인 실패';
  $('marketNotice').textContent='미국장 데이터 오류: '+e.message+' · 임의 종가/점수 생성 없이 이전 페이지를 유지합니다.';
  $('universeInfo').textContent='수집 실패';$('fundRows').innerHTML='<tr><td colspan="10">검증된 자료가 없습니다.</td></tr>';
  $('tradeRows').innerHTML='<tr><td colspan="8">일봉 데이터가 없습니다.</td></tr>';
 }
}
document.querySelectorAll('[data-index]').forEach(btn=>btn.addEventListener('click',()=>{
 selectedIndex=btn.dataset.index;sectorKey='ALL';
 document.querySelectorAll('[data-index]').forEach(x=>x.classList.toggle('active',x===btn));
 renderSectors();renderAll();
}));
$('tickerSearch').addEventListener('input',()=>{
 query=$('tickerSearch').value.trim().toLowerCase();renderAll();
});
$('tickerSearch').addEventListener('keydown',e=>{if(e.key==='Enter')search()});
$('searchButton').addEventListener('click',search);
boot();
})();