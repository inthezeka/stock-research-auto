/* MY Stock Room / US market. All displayed values are sourced from a verified dated JSON snapshot. */
(()=>{'use strict';
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data={meta:{},indices:{},sectors:[],items:[]},byTicker=new Map(),selectedIndex='ALL',sectorKey='ALL',searchText='',selected=null;
const n=(x,d=2)=>x!=null&&x!==''&&Number.isFinite(Number(x))?Number(x).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
const pct=x=>x!=null&&x!==''&&Number.isFinite(Number(x))?(Number(x)>0?'+':'')+n(x,2)+'%':'—';
const css=x=>x==null?'muted':Number(x)>0?'pos':Number(x)<0?'neg':'muted';
const price=x=>Number.isFinite(Number(x))&&Number(x)>0?'$'+n(x):'—';
const allOf=t=>selectedIndex==='ALL'||(t.indices||[]).includes(selectedIndex);
const sectorOf=t=>sectorKey==='ALL'||t.sector_key===sectorKey;
const relevant=()=>data.items.filter(t=>allOf(t)&&sectorOf(t)&&(searchText.length===0||(t.ticker+' '+t.name).toLowerCase().includes(searchText)));
const safeUrl=s=>/^https:\/\//.test(String(s||''))?s:'#';
const sourceDate=t=>t.price_date||'미검증';
function indexCards(){
 const keys=[['NASDAQ100','NASDAQ-100','^NDX'],['SP500','S&P 500','^GSPC'],['DOW30','DOW 30','^DJI'],['NASDAQCOMP','NASDAQ Composite','^IXIC']];
 $('indexCards').innerHTML=keys.map(([k,label])=>{
  const x=data.indices[k]||{},v=x.close;
  return '<div class="card mcap"><span>'+label+'</span><strong>'+n(v)+'</strong><em class="'+css(x.change_pct)+'">'+pct(x.change_pct)+' · '+esc(x.date||'미확인')+'</em></div>';
 }).join('');
}
function renderSectors(){
 const visible=data.items.filter(allOf);
 const counts=new Map();
 visible.forEach(x=>counts.set(x.sector_key,(counts.get(x.sector_key)||0)+1));
 const buttons=[{key:'ALL',label:'전체 Sector'},...data.sectors];
 $('sectorButtons').innerHTML=buttons.map(x=>'<button class="sector'+(sectorKey===x.key?' active':'')+'" data-sector="'+esc(x.key)+'">'+esc(x.label)+' <span class="cnt">'+(x.key==='ALL'?visible.length:counts.get(x.key)||0)+'</span></button>').join('');
 $('sectorLabel').textContent=buttons.find(x=>x.key===sectorKey)?.label||'전체 Sector';
 $('fundTitle').textContent=(sectorKey==='ALL'?'전체':buttons.find(x=>x.key===sectorKey)?.label||'선택')+' · Fundamental TOP 20';
 $('sectorButtons').querySelectorAll('[data-sector]').forEach(b=>b.addEventListener('click',()=>{sectorKey=b.dataset.sector;renderSectors();renderTables()}));
}
function sortedFund(){
 return relevant().slice().sort((a,b)=>(b.fundamental_score!=null?1:0)-(a.fundamental_score!=null?1:0)||Number(b.fundamental_score??-1)-Number(a.fundamental_score??-1)||Number(b.trading_score??-1)-Number(a.trading_score??-1)||a.ticker.localeCompare(b.ticker));
}
function sortedTrade(){
 return relevant().filter(x=>Number.isFinite(Number(x.trading_score))&&x.close>0&&['상승','상승초기'].includes(x.trend)&&Number(x.rsi||0)<78)
 .sort((a,b)=>Number(b.trading_score)-Number(a.trading_score)||Number(b.return5d??-99)-Number(a.return5d??-99));
}
function linkedRow(t,cols){return '<tr tabindex="0" data-ticker="'+esc(t.ticker)+'">'+cols.map(c=>'<td>'+c+'</td>').join('')+'</tr>'}
function renderTables(){
 const found=sortedFund(),top=found.slice(0,20);
 const verifiedFund=found.filter(t=>t.fundamental_score!=null).length;
 const chosenSector=$('sectorLabel').textContent||'전체 Sector';
 $('fundTitle').textContent=chosenSector+' · '+(verifiedFund?'Fundamental TOP 20':'미국장 종목 탐색 (Fundamental 자료 연동 대기)');
 const firstHeader=$('fundRows').closest('table')?.querySelector('thead th');
 if(firstHeader)firstHeader.textContent=verifiedFund?'순위':'목록';
 $('universeInfo').textContent='필터 '+found.length.toLocaleString('ko-KR')+'종목 · 검증 종가 '+found.filter(x=>x.close>0).length+'종목 · Fundamental 산출 '+found.filter(x=>x.fundamental_score!=null).length+'종목';
 $('fundRows').innerHTML=top.length?top.map((t,i)=>linkedRow(t,[i+1,'<b>'+esc(t.ticker)+'</b><br><span class="tiny">'+esc(t.name)+'</span>',esc(t.sector_label||'미분류'),(t.indices||[]).map(x=>'<span class="chip">'+esc(x==='NASDAQ100'?'NDX':x==='SP500'?'S&P':'DOW')+'</span>').join(' '),price(t.close)+'<br><span class="tiny">'+esc(sourceDate(t))+'</span>','<span class="'+css(t.return5d)+'">'+pct(t.return5d)+'</span>',t.fundamental_score!=null?n(t.fundamental_score,1):'<span class="muted">자료 미확인</span>',t.trading_score!=null?n(t.trading_score,0):'—'])).join(''):'<tr><td colspan="8">현재 필터에 해당하는 검증 종목이 없습니다.</td></tr>';
 const tr=sortedTrade().slice(0,10);
 $('tradeRows').innerHTML=tr.length?tr.map((t,i)=>linkedRow(t,[i+1,'<b>'+esc(t.ticker)+'</b><br><span class="tiny">'+esc(t.name)+'</span>',price(t.close),n(t.trading_score,0),'<span class="chip">'+esc(t.entry_quality||'대기')+'</span>',n(t.rsi,1),'<span class="'+css(t.return20d)+'">'+pct(t.return20d)+'</span> / <span class="'+css(t.return60d)+'">'+pct(t.return60d)+'</span>',esc(t.data_confidence||'LOW')])).join(''):'<tr><td colspan="8">기준을 충족하는 기술 분석 종목이 없습니다.</td></tr>';
 for(const node of document.querySelectorAll('tr[data-ticker]')){
  const fn=()=>choose(node.dataset.ticker);
  node.addEventListener('click',fn);
  node.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();fn()}});
 }
}
function svgChart(rows){
 if(!Array.isArray(rows)||rows.length<2)return '<p class="muted">검증된 일봉 추이가 없습니다.</p>';
 const dat=rows.slice(-65).filter(x=>Number.isFinite(Number(x.close)));if(dat.length<2)return '';
 const v=dat.map(x=>Number(x.close)),low=Math.min(...v)*.98,high=Math.max(...v)*1.02,spread=Math.max(.01,high-low);
 const left=54,top=15,w=680,h=210,xx=i=>left+i*w/(v.length-1),yy=x=>top+(high-x)/spread*h;
 const points=v.map((x,i)=>xx(i).toFixed(1)+','+yy(x).toFixed(1)).join(' ');
 const grid=[0,.25,.5,.75,1].map(t=>'<line x1="'+left+'" x2="'+(left+w)+'" y1="'+(top+t*h)+'" y2="'+(top+t*h)+'" stroke="#e5ebf3"/><text x="46" y="'+(top+t*h+4)+'" text-anchor="end" fill="#708099" font-size="12">'+n(high-spread*t)+'</text>').join('');
 return '<svg class="chart" viewBox="0 0 760 260" role="img" aria-label="최근 일봉 종가 차트"><rect x="'+left+'" y="'+top+'" width="'+w+'" height="'+h+'" fill="#fbfcfe"/>'+grid+'<polyline points="'+points+'" stroke="#1769e0" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"/><text x="'+left+'" y="248" fill="#66768b" font-size="12">'+esc(dat[0].date)+'</text><text x="'+(left+w)+'" y="248" text-anchor="end" fill="#66768b" font-size="12">'+esc(dat.at(-1).date)+'</text></svg>';
}
function facts(rows){return '<div class="facts">'+rows.map(([l,v])=>'<div class="fact"><small>'+esc(l)+'</small><strong>'+v+'</strong></div>').join('')+'</div>'}
function choose(ticker){
 const t=byTicker.get(ticker);if(!t)return;selected=t;
 const f=t.financials||{},sector=t.sector_label||'미분류';
 $('stockDetail').innerHTML='<div class="dhead"><div><span class="eyebrow">'+esc((t.indices||[]).join(' / '))+'</span><h3>'+esc(t.name)+' <span class="muted">('+esc(t.ticker)+')</span></h3><small>'+esc(sector)+' · 종가 '+esc(sourceDate(t))+' · USD · '+esc(t.price_source||'가격 확인 필요')+'</small></div><span class="chip">'+esc(t.entry_quality||'NO LONG TRADE')+' · '+esc(t.data_confidence||'LOW')+'</span></div>'+
 facts([['현재 종가',price(t.close)],['최근 5D','<span class="'+css(t.return5d)+'">'+pct(t.return5d)+'</span>'],['Fundamental Score',t.fundamental_score==null?'검증자료 부족':n(t.fundamental_score,1)],['Trading Score',t.trading_score==null?'미확인':n(t.trading_score,0)],['RSI 14',n(t.rsi,1)],['상대거래량',t.volume_ratio==null?'—':n(t.volume_ratio)+'x'],['20D MA',t.m20==null?'—':price(t.m20)],['60D MA',t.m60==null?'—':price(t.m60)]])+
 '<div class="twocol" style="margin-top:18px"><div><h3>일봉 종가 추이</h3>'+svgChart(t.chart||[])+'<p class="legend">과거 일봉, 종가 기준. 진입신호가 아니라 추세 참고용입니다.</p></div><div><h3>실적 · Valuation</h3>'+
 facts([['최근 FY 매출',f.revenue==null?'—':'$'+n(f.revenue/1e9)+'B'],['매출 YoY',pct(f.revenue_growth)],['영업이익률',pct(f.opm)],['P/E (FY EPS)',f.trailing_fy_pe==null?'—':n(f.trailing_fy_pe)+'x']])+
 '<p class="detail-note">SEC Company Facts 기반 FY 실적만 사용합니다. FY EPS P/E는 Forward PER가 아니며, 자료 미확인 시 계산하지 않습니다.</p></div></div>'+
 '<p class="detail-note">Long Only 후보: '+esc(t.trade_reason||'가격구조 미확인')+'. ICT MSS / Liquidity Sweep / FVG / OB는 추가 차트 검증이 필요합니다.</p>';
 const flow=t.flow5d||{};
 $('volumeFlow').innerHTML='<h3>'+esc(t.ticker)+' · 최근 5거래일</h3>'+facts([['가격 변화',pct(t.return5d)],['20D 평균 대비 거래량',t.volume_ratio==null?'—':n(t.volume_ratio)+'x'],['5D OBV 방향',esc(flow.obv_trend||'미확인')],['최근 5D 총거래량',flow.volume==null?'—':n(flow.volume,0)+'주']])+'<p class="detail-note">OBV는 전일 종가대비 상승일 거래량을 더하고 하락일 거래량을 빼는 계산지표입니다. 외국인·기관·개인의 순매수 집계가 아닙니다. [AI]</p>';
 const tt=encodeURIComponent(t.ticker.replace('.','-'));
 $('researchLinks').innerHTML='<h3>'+esc(t.name)+' · 공식/원문 확인</h3><p class="desc">리포트 및 목표주가 확인 전: 숫자를 생성하지 않습니다. 유상증자·전환증권·희석 위험은 SEC 제출 서류를 직접 확인하세요.</p><div class="links">'+
 '<a class="btn" rel="noopener noreferrer" target="_blank" href="https://www.sec.gov/edgar/search/#/q='+tt+'">SEC EDGAR 공시 ↗</a>'+
 '<a class="btn" rel="noopener noreferrer" target="_blank" href="https://www.nasdaq.com/market-activity/stocks/'+encodeURIComponent(t.ticker.toLowerCase())+'">Nasdaq 종목정보 ↗</a>'+
 '<a class="btn" rel="noopener noreferrer" target="_blank" href="https://stockanalysis.com/stocks/'+encodeURIComponent(t.ticker.toLowerCase().replace('.','-'))+'/forecast/">애널리스트 전망 직접 확인 ↗</a></div>';
 document.title=t.ticker+' · 미국주식 | MY Stock Room';
 $('deepdive').scrollIntoView({behavior:'smooth',block:'start'});
}
function search(){
 searchText=$('tickerSearch').value.trim().toLowerCase();renderTables();
 const exact=data.items.find(t=>t.ticker.toLowerCase()===searchText);
 if(exact){choose(exact.ticker);return}
 const results=relevant();
 if(results.length===1)choose(results[0].ticker);
}
async function boot(){
 try{
  const r=await fetch('./data/us-market.json?v='+Date.now(),{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);
  const j=await r.json();if(!j||!Array.isArray(j.items))throw new Error('invalid data shape');
  data=j;byTicker=new Map(data.items.map(t=>[t.ticker,t]));
  $('updated').textContent='최신 확인 · '+(data.meta?.as_of||'미확인')+' ET · '+(data.meta?.status||'unavailable');
  $('marketNotice').textContent='US 정규장 종가 · '+(data.meta?.as_of||'미확인')+' ET / '+data.items.filter(t=>t.close>0).length+'개 종목 가격 확인 / 출처: '+(data.meta?.price_source||'미확인')+' · 미확인 정보는 임의값으로 대체하지 않습니다.';
  indexCards();renderSectors();renderTables();
  if(data.items.length){const start=sortedFund().find(x=>x.close>0)||data.items.find(x=>x.close>0);if(start){selected=start;$('stockDetail').innerHTML='TOP20이나 MY Trading에서 종목을 선택해 상세 정보를 보세요.'}}
  if(!data.items.length){$('stockDetail').textContent='미국장 데이터 수집을 시작하지 않았거나 아직 검증되지 않았습니다.'}
 }catch(e){$('updated').textContent='미국장 데이터 미확인';$('marketNotice').textContent='미국장 데이터 파일을 읽지 못했습니다: '+e.message;$('universeInfo').textContent='수집 실패 · 원시가격을 추정값으로 대체하지 않습니다.';$('fundRows').innerHTML='<tr><td colspan="8">미국장 검증 데이터 준비 중</td></tr>';$('tradeRows').innerHTML='<tr><td colspan="8">데이터 준비 중</td></tr>';}
}
document.querySelectorAll('[data-index]').forEach(b=>b.addEventListener('click',()=>{selectedIndex=b.dataset.index;sectorKey='ALL';document.querySelectorAll('[data-index]').forEach(x=>x.classList.toggle('active',x===b));renderSectors();renderTables()}));
$('tickerSearch').addEventListener('input',()=>{searchText=$('tickerSearch').value.trim().toLowerCase();renderTables()});
$('tickerSearch').addEventListener('keydown',e=>{if(e.key==='Enter')search()});
$('searchButton').addEventListener('click',search);
boot();
})();