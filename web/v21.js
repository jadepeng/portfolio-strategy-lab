(function(){
'use strict';
const RESEARCH=JSON.parse(document.getElementById('v21-data').textContent);
let D=RESEARCH;
const state={view:'overview',overviewRegion:'CN',period:'2026',cnVersion:'baseline',usVersion:'baseline',cnPeriod:'all',usPeriod:'all',riskMarket:'US',tradeMarket:'CN',tradeVersion:'baseline',tradeFilter:'all',tradeQuery:'',tradePage:0};
const assetColor={tech:'#6174e7',red:'#1aa98b',bond:'#5776b5',gold:'#dfaf58',money:'#b1bdcb',qqq:'#5378de',bil:'#b3becb'};
const titles={desk:['决策与调仓台','个人交易决策与调仓','周频目标再平衡、盘中30分钟有效破位防守、冷却拦截与精确到股数的半自动执行清单。'],portfolio:['持仓与账本','真实持仓与资金账本','不可变事件账本投影出双市场实时市值、T+1可卖股数、浮动/已实现盈亏与券商对账校准。'],indicators:['策略指标雷达','双市场策略指标雷达','完整透明展示趋势温度、动量、均线位置、波动率刹车、市场量能、跨市场滞后风险与日内止损参数。'],review:['交易复盘中心','个人交易复盘与归因','对比实盘份额净值（TWR）与策略同期表现，审计滑点、执行漂移、纪律遵守率并沉淀周/日复盘笔记。'],overview:['总览驾驶舱','中美双市场策略驾驶舱','同一份研究快照，追踪A股五资产、美股趋势仓和跨市场风险。可核验收益、仓位、信号、执行成本与模型局限。'],cn:['A股策略','A股五资产趋势轮动','趋势动物纪律、资金量能、条件式防守与五资产风险预算。支持版本比较、目标仓位和模拟实持对照。'],us:['美股策略','美股趋势与风险预算','QQQ长期核心、多周期趋势及双动量外部否决；两套研究版本独立回测，保留成交时点差异。'],cross:['中美联动','美股隔夜与A股风控','只在信息真正公开后传递风险信号，避免将美股周五收盘提前用作中国同周的交易依据。'],intraday:['盘中减仓线','盘中有效跌破减仓线','前一日冻结风险线、30分钟确认、下一根可交易K线执行，分档减仓与冷却；不包含实时券商数据。'],risk:['稳健性审计','执行成本与模型稳健性','比较双倍费用、不同成交时点以及统计不确定性。候选策略没有通过独立样本外测试。'],trades:['交易与记录','逐周交易与信号档案','查看可追溯的研究账本、真实模拟目标与持仓，并冻结当前信号用于未来对照。']};
const $=id=>document.getElementById(id);
const fmt=(x,n=2)=>Number(x).toLocaleString('zh-CN',{minimumFractionDigits:n,maximumFractionDigits:n});
const pct=(x,n=2)=>`${Number(x)>=0?'+':''}${fmt(x,n)}%`;
const pct1=(x)=>`${fmt(x*100,1)}%`;
const escrow=(str)=>String(str??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
const regionModel=(r,k)=>D.models[r][k];
const assets=r=>r==='CN'?D.config.cn_assets:D.config.us_assets;
const metric=(m,label)=>m[label];
function download(name,contents,type='text/plain;charset=utf-8'){const blob=new Blob([contents],{type});const u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),4000)}
function navTo(view){if(!titles[view])return;state.view=view;document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id==='view-'+view));document.querySelectorAll('.nav-item').forEach(x=>{x.classList.toggle('active',x.dataset.view===view);x.setAttribute('aria-current',x.dataset.view===view?'page':'false')});$('breadcrumb').textContent=titles[view][0];$('page-title').innerHTML=escrow(titles[view][1].slice(0,titles[view][1].length-5))+'<span>'+escrow(titles[view][1].slice(-5))+'</span>';$('page-desc').textContent=titles[view][2];window.scrollTo({top:0,behavior:'smooth'});renderView();}
function makeKPIs(data){let m=data.metrics;return [
 {name:'累计模拟收益',value:pct(m.cumulative_pct),hint:'样本期间 · 含模拟交易费用',accent:'#14a989',icon:'↗'},
 {name:'2026年收益',value:pct(m.r2026_pct),hint:`截至${data.cutoff} · ${data.origin?'在线重算':'冻结历史研究'}`,accent:'#5378e5',icon:'◫'},
 {name:'历史最大回撤',value:pct(m.max_dd_pct),hint:'期内净值峰值至后续谷值',accent:'#e6a74f',icon:'⌁'},
 {name:'年化波动率',value:fmt(m.vol_pct)+'%',hint:'周收益标准差 × √52',accent:'#7e83e5',icon:'◇'}];}
function kpiHTML(m){return `<div class="kpi-card" style="--accent:${m.accent}"><div class="kpi-caption"><span>${escrow(m.name)}</span><span class="kpi-icon">${m.icon}</span></div><strong class="kpi-value ${m.name.includes('回撤')?'negative':m.value[0]==='+'?'positive':''}">${escrow(m.value)}</strong><div class="kpi-foot">${escrow(m.hint)}</div></div>`;}
function miniHTML(model){const m=model.metrics;return [{t:'累计收益',v:pct(m.cumulative_pct),c:'完整研究窗口'},{t:'最大回撤',v:pct(m.max_dd_pct),c:'历史峰谷'},{t:'2026年至今',v:pct(m.r2026_pct),c:'不代表未来盈利能力'},{t:'发生调仓的周数',v:m.trade_weeks,c:'模拟交易费用已考虑'}].map(o=>`<div class="mini-metric"><span>${o.t}</span><strong>${o.v}</strong><small>${o.c}</small></div>`).join('');}
function nearestPeriod(row,period){if(period==='2026')return row.d>='2026-01-01';if(period==='2023')return row.d>='2023-01-01';return true;}
function getPair(region,variant){const b=regionModel(region,'baseline'),c=regionModel(region,variant==='baseline'?'advisory':variant);return [b,c]}
function chartLegend(first,second){return `<span><i class="swatch" style="background:#10ae90"></i>${escrow(first.label)}</span><span><i class="swatch" style="background:#5578e7"></i>${escrow(second.label)}</span>`;}
function svg(tag,attrs){const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const k in attrs)e.setAttribute(k,attrs[k]);return e;}
function append(parent,name,attrs,text){const e=svg(name,attrs);if(text!==undefined)e.textContent=text;parent.append(e);return e;}
function drawChart(target,region,variant,period){
 const host=$(target);if(!host || host.clientWidth<100)return;
 const [base,alt]=getPair(region,variant),first=base.history.filter(r=>nearestPeriod(r,period)),second=alt.history.filter(r=>nearestPeriod(r,period));
 const n=Math.min(first.length,second.length),W=Math.max(400,host.clientWidth-2),H=host.clientHeight||280,margin={l:42,r:15,t:16,b:28};
 if(!n){host.textContent='该区间没有已完成周线';return}
 const inner=W-margin.l-margin.r,height=H-margin.t-margin.b;
 const y1=first.slice(0,n).map(x=>x.nav/first[0].nav*100),y2=second.slice(0,n).map(x=>x.nav/second[0].nav*100);
 let min=Math.min(...y1,...y2),max=Math.max(...y1,...y2),pad=Math.max(1,(max-min)*.12);min-=pad;max+=pad;
 const x=i=>margin.l+(n<2?0:i/(n-1)*inner),y=v=>margin.t+(max-v)/(max-min)*height;
 host.replaceChildren();const root=svg('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':`${region}策略模拟净值曲线，从${first[0].d}至${first[n-1].d}`});host.append(root);
 for(let t=0;t<=4;t++){const v=min+(max-min)*t/4,yy=y(v);append(root,'line',{x1:margin.l,y1:yy,x2:W-margin.r,y2:yy,stroke:'#e9eef3','stroke-dasharray':'3 5'});append(root,'text',{x:margin.l-8,y:yy+4,fill:'#a3afba','text-anchor':'end','font-size':'10'},fmt(v,0));}
 const paths=[y1,y2].map(a=>a.map((v,i)=>(i?'L':'M')+x(i).toFixed(2)+','+y(v).toFixed(2)).join(' '));
 const grad=svg('defs',{}),g=append(grad,'linearGradient',{id:'fade-'+target,x1:'0',x2:'0',y1:'0',y2:'1'});append(g,'stop',{offset:'0%','stop-color':'#13ac8f','stop-opacity':'.12'});append(g,'stop',{offset:'100%','stop-color':'#13ac8f','stop-opacity':'0'});root.append(grad);
 append(root,'path',{d:paths[0]+` L${x(n-1)},${y(min)} L${x(0)},${y(min)} Z`,fill:`url(#fade-${target})`});
 append(root,'path',{d:paths[0],fill:'none',stroke:'#12ad8e','stroke-width':2.4,'stroke-linejoin':'round','stroke-linecap':'round'});
 append(root,'path',{d:paths[1],fill:'none',stroke:'#5779eb','stroke-width':2.2,'stroke-linejoin':'round','stroke-linecap':'round'});
 const labels=Math.min(5,n),ixs=Array.from({length:labels},(_,j)=>Math.round(j*(n-1)/(labels-1)));
 for(let i of ixs)append(root,'text',{x:x(i),y:H-5,fill:'#9ba9b6','font-size':'10','text-anchor':i===0?'start':i===n-1?'end':'middle'},first[i].d.slice(0,7));
 const cross=append(root,'line',{x1:0,y1:margin.t,x2:0,y2:H-margin.b,stroke:'#96acbb','stroke-dasharray':'3 4','stroke-width':'1',visibility:'hidden'});
 const circles=[append(root,'circle',{cx:0,cy:0,r:4,fill:'#12ad8e',stroke:'white','stroke-width':'2',visibility:'hidden'}),append(root,'circle',{cx:0,cy:0,r:4,fill:'#5779eb',stroke:'white','stroke-width':'2',visibility:'hidden'})];
 const hit=append(root,'rect',{x:margin.l,y:margin.t,width:inner,height:height,fill:'transparent'});
 const tip=$('hover-tip');
 function point(evt){const rect=hit.getBoundingClientRect(),r=(evt.clientX-rect.left)/rect.width,i=Math.max(0,Math.min(n-1,Math.round(r*(n-1))));
  cross.setAttribute('x1',x(i));cross.setAttribute('x2',x(i));cross.setAttribute('visibility','visible');for(let j=0;j<2;j++){circles[j].setAttribute('cx',x(i));circles[j].setAttribute('cy',y(j?y2[i]:y1[i]));circles[j].setAttribute('visibility','visible')}
  tip.innerHTML=`<small>${first[i].d} · 当周收盘</small><div>${escrow(base.label)}：<b>${fmt(y1[i])}</b></div><div>${escrow(alt.label)}：<b>${fmt(y2[i])}</b></div>`;
  tip.hidden=false;const width=tip.offsetWidth,xx=Math.min(window.innerWidth-width-10,evt.clientX+15);tip.style.left=Math.max(9,xx)+'px';tip.style.top=Math.min(window.innerHeight-tip.offsetHeight-9,evt.clientY+12)+'px';}
 hit.addEventListener('pointermove',point);hit.addEventListener('pointerenter',point);hit.addEventListener('pointerleave',()=>{cross.setAttribute('visibility','hidden');circles.forEach(c=>c.setAttribute('visibility','hidden'));tip.hidden=true});
 if(target==='overview-chart')$('overview-legend').innerHTML=chartLegend(base,alt);
}
function drawLiquidity(){const host=$('cross-chart');if(!host||!host.clientWidth)return;const p=D.liquidity.filter(v=>v.d>='2026-01-01'),W=Math.max(400,host.clientWidth),H=host.clientHeight||288,m={l:43,r:14,t:16,b:30},val=p.map(x=>x.market_turn_100m/10000),max=Math.max(...val)*1.12;
 host.replaceChildren();const s=svg('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':'2026年中证全指日均成交额，单位万亿元'});host.append(s);for(let i=0;i<=4;i++){let yy=m.t+(H-m.t-m.b)*i/4;append(s,'line',{x1:m.l,y1:yy,x2:W-m.r,y2:yy,stroke:'#edf1f5','stroke-dasharray':'3 5'});append(s,'text',{x:m.l-7,y:yy+4,fill:'#a3afba','text-anchor':'end','font-size':'10'},fmt(max*(4-i)/4,1));}const step=(W-m.l-m.r)/p.length;for(let i=0;i<p.length;i++){let h=(H-m.t-m.b)*val[i]/max,ratio=p[i].volume_ratio_4_13;append(s,'rect',{x:m.l+i*step+1,y:H-m.b-h,width:Math.max(3,step-2),height:h,fill:ratio!=null&&ratio<.85?'#dba26b':'#67cbb2',rx:2});}for(let j=0;j<5;j++){let i=Math.round(j*(p.length-1)/4);append(s,'text',{x:m.l+(i+.5)*step,y:H-6,fill:'#9aa7b5','text-anchor':j===0?'start':j===4?'end':'middle','font-size':'10'},p[i].d.slice(0,7));}}
function drawAllocation(region,targetKey,detail){let model=regionModel(region,targetKey),a=assets(region),pcts=a.map(v=>model.target[v[0]]||0),col=a.map(v=>assetColor[v[0]]),gradient='',sum=0;for(let i=0;i<pcts.length;i++){let nex=sum+pcts[i]*100;gradient+=`${col[i]} ${fmt(sum,4)}% ${fmt(nex,4)}%,`;sum=nex;}
 if(region==='CN'&&document.querySelector('#view-overview').classList.contains('active')){$('overview-donut').style.background='conic-gradient('+gradient.slice(0,-1)+')';$('overview-weights').innerHTML=a.map((v,i)=>`<div class="allocation-item"><span class="asset-name"><i class="colorbox" style="background:${col[i]}"></i>${escrow(v[1])}</span><strong>${fmt(pcts[i]*100,0)}%</strong></div>`).join('');}
 if(detail){$(detail).innerHTML=a.map((v,i)=>{let t=model.target[v[0]]||0,h=model.held[v[0]]||0;return `<div class="weight-entry"><div class="weight-entry-head"><strong><i class="colorbox" style="background:${col[i]};margin-right:7px"></i>${escrow(v[1])}</strong><span><small>目标 / 实持</small> <b>${fmt(t*100,0)}% / ${fmt(h*100,1)}%</b></span></div><div class="weight-track"><div class="weight-progress" style="background:${col[i]};width:${t*100}%"></div></div><span class="weight-faint">${escrow(v[2])} · 离线模拟，不是券商实际持仓</span></div>`}).join('');}
}
function overviewWeights(region){const m=regionModel(region,'baseline'),a=assets(region);let gradient='',sum=0;for(let v of a){const p=(m.target[v[0]]||0)*100;gradient+=`${assetColor[v[0]]} ${sum}% ${sum+p}%,`;sum+=p}
 $('overview-donut').style.background='conic-gradient('+gradient.slice(0,-1)+')';$('overview-weights').innerHTML=a.map(v=>`<div class="allocation-item"><span class="asset-name"><i class="colorbox" style="background:${assetColor[v[0]]}"></i>${escrow(v[1])}</span><strong>${fmt(m.target[v[0]]*100,0)}%</strong></div>`).join('');$('overview-holding-note').textContent=`${region==='CN'?'A股':'美股'}实际模拟持仓：`+a.filter(v=>m.held[v[0]]>.01).map(v=>`${v[1]} ${fmt(m.held[v[0]]*100,1)}%`).join(' / ');}
function drawYears(region,variant,el){let selected=regionModel(region,variant),base=regionModel(region,'baseline'),mx=Math.max(...selected.annual.map(a=>Math.abs(a.pct)),...base.annual.map(a=>Math.abs(a.pct)),1);
 $(el).innerHTML=selected.annual.map(a=>{let b=base.annual.find(v=>v.year===a.year);return `<div class="annual-line"><time>${a.year}${a.partial?'*':''}</time><div class="annual-bar ${a.pct<0?'negative':''}"><i style="width:${Math.abs(a.pct)/mx*100}%"></i></div><strong class="${a.pct<0?'text-red':'text-green'}">${pct(a.pct,1)}</strong></div>`}).join('')+'<small style="color:#95a1ae;font-size:9px">*2020、2026为非完整日历年；不同模型使用统一周线窗口</small>';
}
function drawOverview(){let r=state.overviewRegion;const b=regionModel(r,'baseline');$('overview-kpis').innerHTML=makeKPIs(b).map(kpiHTML).join('');drawChart('overview-chart',r,'advisory',state.period);overviewWeights(r);const cn=D.signals.CN,us=D.signals.US;
 $('risk-radar').innerHTML=[['A股牛熊状态',cn.market_weak?'偏弱／防守':'趋势待确认',cn.market_weak?'danger':'safe','中证全指趋势＋市场成交额'],['美股科技趋势',us.risk,'danger',`QQQ ${fmt(us.qqq_close)}美元，30周均线${fmt(us.qqq_ma30)}`],['A股量能4周/13周',fmt(cn.market_liquidity_ratio,2)+'×',cn.market_liquidity_ratio<1?'danger':'safe','中证全指成分股代理成交额'],['双市场风险联动','已开启观察','safe','仅为风险上下文，不自动清仓']].map(x=>`<div class="status-row"><div><strong>${escrow(x[0])}</strong><small>${escrow(x[3])}</small></div><span class="state-pill ${x[2]}">${escrow(x[1])}</span></div>`).join('');
 const allocations=r=>assets(r).filter(a=>regionModel(r,'baseline').target[a[0]]>.001).map(a=>a[1]+' '+pct1(regionModel(r,'baseline').target[a[0]])).join(' / ');
 $('overview-feed').innerHTML=[['A股完整周目标 · '+D.models.CN.baseline.cutoff,allocations('CN')+'。'+D.models.CN.baseline.lastReason,'green'],['美股完整周目标 · '+D.models.US.baseline.cutoff,allocations('US')+'。'+D.models.US.baseline.lastReason,'blue'],['稳健性审计保留原截点','V20候选尚未自动晋级。审计与置信区间为冻结历史研究，不随最新报价更新。','amber']].map(x=>`<div class="feed-item ${x[2]}"><strong>${escrow(x[0])}</strong><p>${escrow(x[1])}</p></div>`).join('');
}
function drawCN(){let variant=state.cnVersion,m=regionModel('CN',variant);$('cn-metrics').innerHTML=miniHTML(m);drawChart('cn-chart','CN',variant,state.cnPeriod);drawAllocation('CN',variant,'cn-weights');drawYears('CN',variant,'cn-years')}
function drawUS(){let variant=state.usVersion,m=regionModel('US',variant);$('us-metrics').innerHTML=miniHTML(m);drawChart('us-chart','US',variant,state.usPeriod);drawAllocation('US',variant,'us-weights');drawYears('US',variant,'us-years');$('us-signal-note').textContent=`最新风险状态：${D.signals.US.risk}；QQQ ${fmt(D.signals.US.qqq_close)}美元，30周均线${fmt(D.signals.US.qqq_ma30)}美元。外部双重否决只阻止新增风险仓位，不触碰长期核心；本版历史研究已使用2026数据调参。`}
function drawCross(){drawLiquidity();let q=D.signals.CN;const latest=D.liquidity.at(-1);$('cross-summary').innerHTML=[['市场成交量4周/13周比值',fmt(q.market_liquidity_ratio,2)+'×','低于1表明短期资金活跃度低于中期均值。'],['中证全指成分股日均成交额',fmt(latest.market_turn_100m/10000,2)+'万亿元','成分股统计代理，不是精确全市场成交额。'],['创业板指成交额占全指',fmt(latest.tech_share_pct,1)+'%','板块占比不等于个股前5%真实拥挤度。'],['隔夜信号执行纪律','先发生 → 后交易','美股周五收盘只能影响其后的A股交易日。']].map(x=>`<div class="cross-fact"><small>${x[0]}</small><b>${x[1]}</b><p>${x[2]}</p></div>`).join('');$('cross-events').querySelector('tbody').innerHTML=D.overnight_events.slice().reverse().map(e=>`<tr><td><strong>${escrow(e.cn_trade_day)}</strong></td><td>${escrow(e.us_session)}</td><td><span class="status-label ${e.level==='S'?'red':''}">${e.level==='S'?'严重风险':'一般预警'}</span></td><td>${escrow(e.available_at_cn)}</td></tr>`).join('');}
function drawRisk(){const us=D.promotion.US,cn=D.promotion.CN;$('risk-gates').innerHTML=[['A股新增仓过滤','CN',cn],['美股双动量过滤','US',us]].map(x=>`<article class="kpi-card" style="--accent:${x[2].promotion_ready?'#12a98c':'#dfae60'}"><div class="kpi-caption">${x[0]}<span class="kpi-icon">◇</span></div><div class="audit-headline"><span class="audit-title">${x[2].promotion_ready?'达到历史晋级检查':'暂不自动晋级'}</span></div><div class="audit-item"><span class="tick ${x[2].checks.drawdown_improved?'':'no'}">${x[2].checks.drawdown_improved?'✓':'!'}</span><span>最大回撤改善 <strong>${fmt(x[2].drawdown_improvement_pp,2)}个百分点</strong></span></div><div class="audit-item"><span class="tick ${x[2].checks.bootstrap_excludes_zero?'':'no'}">${x[2].checks.bootstrap_excludes_zero?'✓':'!'}</span><span>区块重抽样区间 <strong>${x[2].checks.bootstrap_excludes_zero?'未包含零':'仍包含零／证据不足'}</strong></span></div></article>`).join('');
 const arr=D.stress.filter(x=>x.market===state.riskMarket);$('stress-table').querySelector('tbody').innerHTML=arr.map(x=>`<tr><td><strong>${escrow(x.name)}</strong></td><td>${x.fill==='next_week_open'?'下一周开盘':'下一周收盘'}</td><td>${fmt(x.fee,1)}×</td><td class="${x.cum>=0?'text-green':'text-red'}">${pct(x.cum)}</td><td class="text-red">${pct(x.dd)}</td><td class="${x.ret2026>=0?'text-green':'text-red'}">${pct(x.ret2026)}</td></tr>`).join('');
 const b=D.bootstrap.US_advisory;$('bootstrap-note').innerHTML=`<div class="boot-interval"><span class="micro">V20 美股过滤器对比V18</span><h4>+${fmt(b.annualized_log_return_diff_pct_points,2)} pp</h4><p>年化对数超额收益差（仅为已见样本）</p><div class="boot-track"><div class="boot-center"></div></div><div class="boot-label"><span>${fmt(b.empirical_90pct_interval_pp[0],2)} pp</span><span>90%区块重抽样区间</span><span>+${fmt(b.empirical_90pct_interval_pp[1],2)} pp</span></div><p>区间包括零，不能宣称稳定超额收益；连续年份高度相关，多次调参进一步降低证据强度。</p></div>`;
 $('quality-checks').innerHTML=D.quality.map(x=>`<div class="audit-item"><span class="tick">✓</span><span><strong>${x.market}数据：${x.rows}根周线</strong><br>${x.from}—${x.last_week_start}；重复日期${x.duplicate_weeks}，缺失价格${x.missing_values}</span></div>`).join('')+`<div class="audit-item"><span class="tick no">!</span><span><strong>样本隔离风险</strong><br>2026已被V1—V20反复用于开发。未来需锁定新行情、实际成交价、分红和税务口径。</span></div>`;}
function filteredTrades(){let r=state.tradeMarket,model=regionModel(r,state.tradeVersion);return model.history.slice().reverse().filter(x=>{if(state.tradeFilter==='changed'&&x.turnover<=1e-8)return false;if(state.tradeFilter==='2026'&&x.d<'2026')return false;if(state.tradeQuery&&!x.d.includes(state.tradeQuery))return false;return true;});}
function renderTrades(){const rows=filteredTrades(),n=18,pages=Math.max(1,Math.ceil(rows.length/n));state.tradePage=Math.max(0,Math.min(state.tradePage,pages-1));const view=rows.slice(state.tradePage*n,state.tradePage*n+n);const r=state.tradeMarket;const model=regionModel(r,state.tradeVersion);$('trade-count').textContent=`${rows.length} 条模拟周记录`;
 $('trade-table').querySelector('tbody').innerHTML=view.map((x)=>{let eq=r==='CN'?x.held_tech+x.held_red:x.held_qqq;return `<tr><td><strong>${x.d}</strong></td><td>${fmt(x.nav,4)}</td><td class="${x.ret!=null&&x.ret>=0?'text-green':'text-red'}">${x.ret==null?'—':pct(x.ret)}</td><td>${fmt(x.turnover*100,1)}%</td><td>${fmt(x.fee,6)}</td><td>${fmt(eq*100,1)}%</td><td><span class="status-label ${x.turnover>.0001?'':'red'}">${x.turnover>.0001?'有调仓':'持有不变'}</span></td></tr>`}).join('')||'<tr><td colspan="7">没有匹配记录</td></tr>';
 $('trade-page-label').textContent=`第 ${state.tradePage+1} / ${pages} 页`; $('trade-prev').disabled=state.tradePage===0;$('trade-next').disabled=state.tradePage>=pages-1;
 $('forward-records').innerHTML=D.frozen_signals.map(x=>`<div class="frozen-card"><strong>${x.market==='CN'?'A股 · 五资产':'美股 · QQQ/BIL'}</strong><small>信号截点 ${x.cutoff} · 周线标签 ${x.source_week_start}</small><small>目标：${Object.entries(x.target_weights).filter(a=>a[1]>.001).map(a=>a[0].toUpperCase()+' '+fmt(a[1]*100,0)+'%').join(' / ')}</small><small>记录哈希（SHA256）</small><code>${x.record_sha256}</code></div>`).join('');}
function drawIntraday(){
 const g=D.intraday;if(!g){$('guard-chart').innerHTML='<div class="empty">没有盘中数据快照</div>';return}
 const symbols={'159915.SZ':'创业板ETF','515180.SH':'红利ETF','QQQ.US':'QQQ 纳指100'};
 let sym=$('guard-symbol').value,entry=g.next_session_candidates.find(x=>x.symbol===sym),sample=g.samples.find(x=>x.symbol===sym);
 let close=entry.prior_close,line=entry.line;
 const stats=[['前一交易日收盘',close.toFixed(sym.includes('QQQ')?2:3),'已收盘行情，不是实时'],['下一交易日候选线',line.toFixed(sym.includes('QQQ')?2:3),'前一日数据生成，不是实时'],['与减仓线距离',fmt(100*(close/line-1),2)+'%','正数说明收盘高于候选线'],['样本止损指令',String(sample.risk_trades)+' 笔','仅选定事件窗口']];
 $('guard-kpis').innerHTML=stats.map((x,i)=>`<div class="kpi-card" style="--accent:${i===2?'#db9966':'#16a98d'}"><div class="kpi-caption">${escrow(x[0])}<span class="kpi-icon">⌁</span></div><strong class="kpi-value">${escrow(x[1])}</strong><div class="kpi-foot">${escrow(x[2])}</div></div>`).join('');
 let weight=Math.max(0,Math.min(100,Number($('guard-weight').value)||0))/100;
 let floor=entry.core_floor,coreMode=$('guard-floor-mode').value;
 if(coreMode==='half')floor/=2;if(coreMode==='none')floor=0;
 const first=Math.max(floor,weight-Math.max(0,weight-floor)*.5),second=Math.max(floor,Math.min(weight,floor));
 const remain=Math.max(0,weight-floor),pctw=v=>fmt(v*100,1)+'%';
 $('guard-scenario').innerHTML=`<div class="guard-step"><span>当前假设仓位</span><strong>${pctw(weight)}</strong></div><div class="guard-step"><span>连续2根破线后</span><strong>${pctw(first)}</strong></div><div class="guard-step"><span>连续4根破线后</span><strong>${pctw(second)}</strong></div><div class="guard-step protective"><span>保留核心下限</span><strong>${pctw(floor)}</strong></div><p>第一档减少约 <b>${pctw(weight-first)}</b> 的策略资金风险暴露，第二档进一步减少 <b>${pctw(first-second)}</b>。释放仓位转入${sym==='QQQ.US'?'BIL':'货币ETF'}。${remain<.001?'<b>当前目标已等于长期核心，没有可减的战术仓。</b>':''}这里仅计算权重，未生成订单。</p>`;
 $('guard-lines').querySelector('tbody').innerHTML=g.next_session_candidates.map(x=>`<tr><td><strong>${escrow(x.display_name)}</strong><br><small>${escrow(x.symbol)}</small></td><td>${fmt(x.prior_close,3)}</td><td><strong>${fmt(x.line,3)}</strong></td><td class="${x.line<x.prior_close?'text-green':'text-red'}">${fmt((x.line/x.prior_close-1)*100,2)}%</td><td>${pct1(x.baseline_target_weight)}</td><td>${pct1(x.core_floor)}</td><td>${x.trend_prebroken?'既有中期趋势已破，仅激活当日急跌线':'趋势线与日内急跌线取较近的一条'}</td></tr>`).join('');
 $('guard-compare').querySelector('tbody').innerHTML=g.samples.map(x=>`<tr><td><strong>${escrow(symbols[x.symbol])}</strong><br><small>${escrow(x.first_bar.slice(0,10))}—${escrow(x.last_bar.slice(0,10))}</small></td><td class="text-red">${pct(x.baseline_event_return_pct)}</td><td class="${x.guard_event_return_pct>=x.baseline_event_return_pct?'text-green':'text-red'}">${pct(x.guard_event_return_pct)}</td><td class="${x.delta_pp>=0?'text-green':'text-red'}">${x.delta_pp>=0?'+':''}${fmt(x.delta_pp,3)}pp</td></tr>`).join('');
 $('guard-orders').querySelector('tbody').innerHTML=g.intents.map(o=>`<tr><td><strong>${escrow(symbols[o.instrument])}</strong></td><td>${escrow(o.signal_bar_end.slice(0,16))}</td><td>${escrow(o.execute_at.slice(0,16))}</td><td>${o.stage===1?'第一档':'第二档'}</td><td>${fmt(o.assumed_fill_price,o.market==='US'?2:3)}</td><td>${pct1(o.current_weight)} → ${pct1(o.target_weight)}</td><td>${escrow(o.reason)}</td></tr>`).join('');
 const points=g.traces?.[sym]||[];$('guard-chart-period').textContent=points.length?`${points[0].d.slice(0,10)}—${points[points.length-1].d.slice(0,10)}`:'无样本';
 if(points.length>1){
  const W=850,H=290,pad={left:56,right:22,top:20,bottom:40};const width=W-pad.left-pad.right,height=H-pad.top-pad.bottom;
  const ys=points.flatMap(x=>[x.close,x.stop_line]).filter(v=>Number.isFinite(v));let lo=Math.min(...ys),hi=Math.max(...ys);let extra=(hi-lo||hi*.01)*.10;lo-=extra;hi+=extra;
  let X=i=>pad.left+i*width/(points.length-1),Y=v=>pad.top+(hi-v)/(hi-lo)*height;
  const segments=points.map((x,i)=>`${i===0?'M':'L'}${X(i).toFixed(1)},${Y(x.close).toFixed(1)}`).join(' ');
  const stops=points.map((x,i)=>`${i===0?'M':'L'}${X(i).toFixed(1)},${Y(x.stop_line).toFixed(1)}`).join(' ');
  const grid=[0,.25,.5,.75,1].map(t=>{let value=lo+(hi-lo)*t,y=Y(value);return `<line x1="${pad.left}" x2="${W-pad.right}" y1="${y}" y2="${y}" stroke="#e4eaf0"/><text x="${pad.left-9}" y="${y+4}" text-anchor="end" font-size="11" fill="#8594a6">${fmt(value,sym==='QQQ.US'?0:3)}</text>`}).join('');
  const markers=points.map((o,i)=>{let risk=g.intents.find(e=>e.instrument===sym&&e.execute_at.slice(0,16)===o.d.slice(0,16));return risk?`<circle cx="${X(i)}" cy="${Y(o.close)}" r="6" fill="#cf646e" stroke="white" stroke-width="2"/>`:''}).join('');
  $('guard-chart').innerHTML=`<svg class="guard-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${symbols[sym]}30分钟收盘与预设减仓线">${grid}<path d="${stops}" fill="none" stroke="#e7a44b" stroke-width="2.3" stroke-dasharray="8 5"/><path d="${segments}" fill="none" stroke="#5378de" stroke-width="2.6" stroke-linejoin="round"/>${markers}<text x="${pad.left}" y="${H-12}" fill="#8492a4" font-size="12">${points[0].d.slice(0,16)}</text><text x="${W-pad.right}" y="${H-12}" text-anchor="end" fill="#8492a4" font-size="12">${points[points.length-1].d.slice(0,16)}</text></svg><div class="guard-chart-legend"><span><i style="background:#5378de"></i>30分钟收盘价</span><span><i style="background:#e7a44b"></i>冻结风险线</span><span><i style="background:#cf646e"></i>减仓模拟成交</span></div>`;
 }else $('guard-chart').innerHTML='<div class="empty">没有可绘制的盘中行情</div>';
}
function renderView(){requestAnimationFrame(()=>{if(state.view==='overview')drawOverview();if(state.view==='cn')drawCN();if(state.view==='us')drawUS();if(state.view==='cross')drawCross();if(state.view==='risk')drawRisk();if(state.view==='intraday')drawIntraday();if(state.view==='trades')renderTrades();if(typeof window.__ptmsRender==='function')window.__ptmsRender(state.view);});}
for(const b of document.querySelectorAll('.nav-item'))b.addEventListener('click',()=>navTo(b.dataset.view));
for(const b of document.querySelectorAll('[data-goto]'))b.addEventListener('click',()=>navTo(b.dataset.goto));
for(const b of document.querySelectorAll('#overview-market [data-region]'))b.addEventListener('click',()=>{state.overviewRegion=b.dataset.region;document.querySelectorAll('#overview-market button').forEach(x=>x.classList.toggle('selected',x===b));drawOverview()});
for(const b of document.querySelectorAll('#overview-period [data-period]'))b.addEventListener('click',()=>{state.period=b.dataset.period;document.querySelectorAll('#overview-period button').forEach(x=>x.classList.toggle('selected',x===b));drawOverview()});
[['cn-version','cnVersion'],['us-version','usVersion'],['cn-period','cnPeriod'],['us-period','usPeriod'],['risk-market','riskMarket']].forEach(([id,k])=>$(id).addEventListener('change',e=>{state[k]=e.target.value;renderView()}));
[['trade-market','tradeMarket'],['trade-version','tradeVersion'],['trade-filter','tradeFilter']].forEach(([id,k])=>$(id).addEventListener('change',e=>{state[k]=e.target.value;state.tradePage=0;renderTrades()}));
$('trade-query').addEventListener('input',e=>{state.tradeQuery=e.target.value.trim();state.tradePage=0;renderTrades()});
$('trade-prev').addEventListener('click',()=>{state.tradePage--;renderTrades()});$('trade-next').addEventListener('click',()=>{state.tradePage++;renderTrades()});
$('export-trades').addEventListener('click',()=>{let rows=filteredTrades(),r=state.tradeMarket,columns=['d','nav','dd','ret','turnover','fee',...(r==='CN'?['held_tech','held_red','held_bond','held_gold','held_money']:['held_qqq','held_bil'])];let csv='\ufeff'+columns.join(',')+'\n'+rows.map(o=>columns.map(k=>o[k]??'').join(',')).join('\n');download('V21_'+r+'_'+state.tradeVersion+'_trades.csv',csv,'text/csv;charset=utf-8')});
$('export-forward').addEventListener('click',()=>download('策略信号冻结记录.json',JSON.stringify(D.frozen_signals,null,2),'application/json'));
$('export-snapshot').addEventListener('click',()=>download('策略驾驶舱_当前视图.json',JSON.stringify({display:D,live:lastEnvelope?.live,status:lastEnvelope?.status},null,2),'application/json'));
$('footer-method').addEventListener('click',()=>navTo('risk'));
$('guard-symbol').addEventListener('change',e=>{const z=D.intraday.next_session_candidates.find(x=>x.symbol===e.target.value);$('guard-weight').value=fmt(z.baseline_target_weight*100,0);drawIntraday()});
$('guard-floor-mode').addEventListener('change',drawIntraday);$('guard-weight').addEventListener('input',drawIntraday);
$('export-guard').addEventListener('click',()=>{const cols=['instrument','signal_bar_end','execute_at','stage','current_weight','target_weight','assumed_fill_price','estimated_cost_pct','reason'];let csv='\ufeff'+cols.join(',')+'\n'+D.intraday.intents.map(r=>cols.map(k=>String(r[k]??'').replaceAll(',', '，')).join(',')).join('\n');download('V22_盘中减仓模拟指令.csv',csv,'text/csv;charset=utf-8')});
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(renderView,130)});
let lastEnvelope=null,paused=false,pollBusy=false,lastPoll=0,lastFailure='';
const onlineProtocol=['http:','https:'].includes(location.protocol);
function timeLabel(value){if(!value)return '尚无成功更新';const d=new Date(value);return Number.isNaN(d.getTime())?String(value):d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+' 北京时间'}
function ageLabel(seconds){return seconds<60?`${Math.floor(seconds)}秒前`:seconds<3600?`${Math.floor(seconds/60)}分钟前`:`${fmt(seconds/3600,1)}小时前`}
function isLiveMode(){return $('data-mode').value==='live'}
function applyEnvelope(){
 const e=lastEnvelope,live=e?.live;D=JSON.parse(JSON.stringify(RESEARCH));
 if(isLiveMode()&&live){
  for(const r of ['CN','US'])if(live.models?.[r]){D.models[r]=live.models[r];if(live.signals?.[r])D.signals[r]=live.signals[r];}
  if(live.liquidity?.length)D.liquidity=live.liquidity;
  if(live.frozen_signals?.length)D.frozen_signals=live.frozen_signals;
 }
 const basis=['CN','US'].map(r=>`${r==='CN'?'A股':'美股'} ${D.models[r].baseline.cutoff}（${D.models[r].baseline.origin?'在线重算':'历史研究'}）`);
 $('display-cutoff').textContent=isLiveMode()?'中美独立更新':RESEARCH.metadata.asof;
 $('display-basis').textContent=basis.join(' / ');
 $('data-scope').textContent=basis.join('；')+'。收益、目标与模拟持仓按所标注数据集计算。稳健性审计、隔夜事件和下方V22历史回放保留原研究截点；在线观察不推断真实成交。';
 renderView();renderLive();
 if(typeof window.__ptmsOnLive==='function')window.__ptmsOnLive(lastEnvelope);
}
function renderLive(){
 const e=lastEnvelope,st=e?.status,live=e?.live,connectionFailed=!!lastFailure||(lastPoll&&Date.now()-lastPoll>45000);
 $('connection-label').textContent=paused?'已暂停自动刷新':connectionFailed?'连接中断 · 数据未更新':!e?'历史模式 · 服务未连接':st.refreshing?'行情服务更新中':st.state==='connected'?'行情服务已连接':st.state==='starting'?'正在获取行情':st.state==='offline'?'行情源不可用':'部分数据不可用';
 $('live-message').className=connectionFailed||!e||st?.state!=='connected'?'live-warning':'live-ok';
 $('live-message').textContent=!onlineProtocol?'当前是离线文件。双击项目根目录 run_dashboard_windows.bat，或运行 python scripts/serve_dashboard.py 后访问 http://127.0.0.1:8765。':connectionFailed?'行情服务连接失败，保留最后数据。'+lastFailure:!e?'正在等待行情服务响应，当前图表使用冻结历史研究。':`数据源：${st.provider}。${st.refreshing?'后台正在拉取行情与策略数据；':''}${st.state==='offline'?'行情源连接失败，当前不提供新的在线执行建议。':''}最近成功更新：${timeLabel(st.last_success)}。${isLiveMode()?'下方显示各市场可用的最近计算。':'已选择冻结历史研究；行情卡片仍单独跟踪。'}`;
 $('live-market-status').innerHTML=['CN','US'].map(r=>`<span>${r==='CN'?'A股':'美股'} · ${escrow(e?.market_status?.[r]?.label||'交易状态未知')}</span>`).join('');
 if(live){
  const quotes=live.quotes||{};$('live-quotes').innerHTML=Object.entries(SYMBOL_LABELS).map(([symbol,name])=>{
   const q=quotes[symbol];if(!q)return `<article class="quote-card missing"><small>${name} · ${escrow(symbol)}</small><strong>—</strong><span>行情暂不可用</span></article>`;
   const seconds=Math.max(0,(Date.now()-new Date(q.timestamp).getTime())/1000),stale=connectionFailed||q.refresh_failed||seconds>180;
   const stateText=connectionFailed?'连接中断':q.refresh_failed?'更新失败 · 保留上次观察':q.display_state==='last_close'?'休市 · 最近收盘':stale?'过期 / 最近观察':'最近观察 · 可能延迟';
   return `<article class="quote-card ${stale?'stale':''}"><small>${name} · ${escrow(symbol)}</small><strong>${fmt(q.price,symbol.endsWith('.US')?2:3)}</strong><b class="${q.change_pct>=0?'text-green':'text-red'}">${pct(q.change_pct)}</b><span>${stateText} · ${ageLabel(seconds)}</span><small>${escrow(timeLabel(q.timestamp))}</small><small>${escrow(q.source)}</small></article>`;
  }).join('');
  $('live-strategies').innerHTML=['CN','US'].map(r=>{
   const s=live.strategy_status?.[r],m=live.models?.[r]?.baseline;const target=m?Object.entries(m.target).filter(([,v])=>v>.001).map(([k,v])=>`${escrow(k.toUpperCase())} ${pct1(v)}`).join(' / '):'等待完整数据';
   return `<article><strong>${r==='CN'?'A股五资产':'美股QQQ / BIL'} · ${escrow(s?.message||'尚无新的完整周线计算')}</strong><p>${target}</p><small>${m?'在线重算样本截至 '+escrow(m.cutoff):'当前图表保留冻结历史研究'}${connectionFailed||s?.connection_degraded?' · 连接异常，需核对数据时效':''}</small></article>`;
  }).join('');
  $('live-details').textContent=`页面每15秒检查更新；行情采集间隔${st.quote_interval_seconds}秒；历史与策略重算间隔${st.history_interval_seconds}秒。最近请求：${timeLabel(st.last_attempt)}。${Object.entries(st.errors||{}).map(([k,v])=>k+'：'+v).join('；')||'最近刷新无错误。'}`;
  $('bt-reference').innerHTML=['CN','US'].map(r=>{const b=live.external_backtests?.[r];return `<article><strong>${r==='CN'?'A股五资产':'美股QQQ / BIL'} · bt 独立参考</strong><p>${b?.state==='available'?`累计 ${pct(b.cumulative_pct)} / 最大回撤 ${pct(b.max_dd_pct)}`:escrow(b?.message||'等待历史数据或安装 requirements-open-source.txt')}</p><small>${escrow(b?.note||'主策略仍使用现有V18/V20引擎；未运行的参考不展示收益。')}</small></article>`}).join('');
  const rows=Object.values(live.intraday||{});$('live-intraday').innerHTML=rows.length?'<table><thead><tr><th>标的</th><th>交易日 / K线结束</th><th>当日冻结线</th><th>最近完整收盘</th><th>连续破线</th><th>观察状态</th></tr></thead><tbody>'+rows.map(x=>`<tr><td>${escrow(SYMBOL_LABELS[x.symbol]||x.symbol)}</td><td>${escrow(timeLabel(x.last_bar_end))}</td><td>${fmt(x.line,3)}</td><td>${fmt(x.observations.at(-1).close,3)}</td><td>${x.below_bars} 根</td><td>${escrow(x.confirmation)}<br><small>${connectionFailed?'连接中断，未更新':x.data_state==='last_session'?'上个观察交易日；非当前盘中':'已完成K线观察'}</small></td></tr>`).join('')+'</tbody></table>':'<p>暂无可核验的在线30分钟K线和前日收盘数据，未形成新观察信号。</p>';
 }
 $('live-refresh').disabled=pollBusy||!onlineProtocol;
}
const SYMBOL_LABELS={'159915.SZ':'创业板ETF','515180.SH':'红利ETF','511260.SH':'10年国债ETF','518880.SH':'黄金ETF','511880.SH':'货币ETF','QQQ.US':'纳指100','BIL.US':'美元短债','SPY.US':'标普500','SMH.US':'半导体','RSP.US':'标普等权'};
async function pollLive(manual=false){
 if(pollBusy||!onlineProtocol||paused&&!manual)return;
 pollBusy=true;renderLive();
 const control=new AbortController(),timer=setTimeout(()=>control.abort(),10000);
 try{
  if(manual){const trigger=await fetch('/api/refresh',{method:'POST',signal:control.signal});if(!trigger.ok)throw new Error('服务拒绝刷新请求');}
  const response=await fetch('/api/dashboard',{cache:'no-store',signal:control.signal});if(!response.ok)throw new Error(`HTTP ${response.status}`);
  const envelope=await response.json();if(envelope.schema!==1||!envelope.live||!envelope.status)throw new Error('行情接口格式不匹配');
  lastEnvelope=envelope;lastPoll=Date.now();lastFailure='';applyEnvelope();
 }catch(error){lastFailure=error.name==='AbortError'?'请求超时。':String(error.message);}
 finally{clearTimeout(timer);pollBusy=false;renderLive();}
}
$('data-mode').addEventListener('change',applyEnvelope);
$('live-refresh').addEventListener('click',()=>pollLive(true));
$('live-pause').addEventListener('click',()=>{paused=!paused;$('live-pause').textContent=paused?'恢复刷新':'暂停刷新';if(!paused)pollLive();renderLive()});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!paused)pollLive()});
setInterval(()=>{renderLive();if(!document.hidden)pollLive()},15000);
window.__quantlabNavTo=navTo;
navTo('overview');applyEnvelope();pollLive();
})();
