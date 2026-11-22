(function () {
  'use strict';

  const STORAGE_KEY = 'ptms.activeAccount';
  const TEMP_LEVELS = ['冻', '寒', '凉', '平', '温', '热', '沸'];
  const VERSION_LABELS = {
    baseline: 'V18 稳健基准',
    advisory: 'V20 双重风控',
    blend25: '25% 混合增强',
  };
  const ACCOUNT_NAMES = {
    CN_MAIN: 'A股账户',
    US_MAIN: '美股账户',
  };
  const CURRENCY_NAMES = {
    CNY: '人民币',
    USD: '美元',
  };
  const VERDICT_BADGES = {
    ACTION_INTRADAY_STOP: '盘中减仓',
    ACTION_PRIORITY_EXIT: '风控减仓',
    ACTION_REBALANCE: '建议调仓',
    HOLD_WITHIN_DEADBAND: '维持现状',
    NEEDS_INIT: '待初始化',
  };
  const EVENT_LABELS = {
    INIT_POSITION: '期初建账',
    BUY: '买入成交',
    SELL: '卖出成交',
    CASH_IN: '转入资金',
    CASH_OUT: '转出资金',
    DIVIDEND: '分红派息',
    RECONCILE: '余额校准',
  };
  const DISCIPLINE_LABELS = {
    FOLLOW: '按计划执行',
    RISK_CUT: '盘中风控减仓',
    DELAYED: '延迟执行',
    MANUAL_OVERRIDE: '手动调整',
    RECONCILE: '余额校准',
    INIT: '期初建账',
  };
  const PRICE_SOURCE_LABELS = {
    quote: '实时报价',
    daily_raw: '最新收盘',
    research_close: '参考收盘',
    cost_fallback: '按成本估值',
    unavailable: '暂无报价',
  };
  const REENTRY_LABELS = {
    off: '未触发',
    probe: '试仓观察',
    confirm: '趋势确认',
    cooldown: '冷却观察',
  };
  const PERIOD_LABELS = {
    WEEKLY: '周度复盘',
    DAILY: '每日复盘',
    MONTHLY: '月度复盘',
  };
  const STAGE_LABELS = {
    0: '正常监控',
    1: '已减仓一档',
    2: '已清战术仓',
  };

  function readStorage(key, fallback) {
    try {
      return window.localStorage ? (window.localStorage.getItem(key) || fallback) : fallback;
    } catch {
      return fallback;
    }
  }

  function writeStorage(key, val) {
    try {
      if (window.localStorage) window.localStorage.setItem(key, val);
    } catch {
      // ignore in restricted environments
    }
  }

  const ptms = {
    accountId: readStorage(STORAGE_KEY, 'CN_MAIN'),
    stateData: null,
    reviewData: null,
    reviewAccountId: null,
    ledgerFilter: 'ALL',
    loading: false,
    error: '',
    currentView: 'overview',
    switchedDefault: false,
  };

  const $ = (id) => document.getElementById(id);

  function esc(val) {
    return String(val ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[c]));
  }

  function fmtNum(x, digits = 2) {
    if (x == null || !Number.isFinite(Number(x))) return '—';
    return Number(x).toLocaleString('zh-CN', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function fmtPct(x, digits = 2, signed = true) {
    if (x == null || !Number.isFinite(Number(x))) return '—';
    const n = Number(x);
    return `${signed && n >= 0 ? '+' : ''}${fmtNum(n, digits)}%`;
  }

  function fmtW(w, digits = 1) {
    if (w == null || !Number.isFinite(Number(w))) return '—';
    return `${fmtNum(Number(w) * 100, digits)}%`;
  }

  function accountName(accountId) {
    return ACCOUNT_NAMES[accountId] || accountId;
  }

  function priceSourceLabel(src) {
    if (!src) return '暂无报价';
    return PRICE_SOURCE_LABELS[src] || src;
  }

  function translateText(text) {
    if (!text) return '';
    return String(text)
      .replace(/volume\/price warning/gi, '量价背离预警')
      .replace(/no active trend regime/gi, '常规轮动状态');
  }

  function isOnlineHttp() {
    try {
      return ['http:', 'https:'].includes((window.location && window.location.protocol) || location.protocol);
    } catch {
      return false;
    }
  }

  function currentIsoWeek() {
    const now = new Date();
    const d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
    const day = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - day);
    const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    const weekNo = Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
    return `${d.getUTCFullYear()}-W${String(weekNo).padStart(2, '0')}`;
  }

  function currentTodayDate(market) {
    try {
      const tz = market === 'US' ? 'America/New_York' : 'Asia/Shanghai';
      const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: tz,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      }).formatToParts(new Date());
      const map = Object.fromEntries(parts.map((p) => [p.type, p.value]));
      return `${map.year}-${map.month}-${map.day}`;
    } catch {
      return new Date().toISOString().slice(0, 10);
    }
  }

  async function apiGet(url) {
    const resp = await fetch(url, { cache: 'no-store' });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `请求失败 (${resp.status})`);
    return data;
  }

  async function apiPost(url, payload) {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `请求失败 (${resp.status})`);
    return data;
  }

  function activeBundle() {
    if (!ptms.stateData || !ptms.stateData.accounts) return null;
    return ptms.stateData.accounts[ptms.accountId] || ptms.stateData.accounts.CN_MAIN || null;
  }

  function accountSwitcherBar(bundle) {
    const acc = bundle.account;
    const pf = bundle.portfolio;
    const curLabel = CURRENCY_NAMES[acc.currency] || acc.currency;
    return `
      <div class="ptms-account-bar">
        <div class="ptms-account-tabs">
          <button type="button" class="ptms-account-tab ${ptms.accountId === 'CN_MAIN' ? 'active' : ''}" data-ptms-account="CN_MAIN">A股账户（人民币）</button>
          <button type="button" class="ptms-account-tab ${ptms.accountId === 'US_MAIN' ? 'active' : ''}" data-ptms-account="US_MAIN">美股账户（美元）</button>
        </div>
        <div class="ptms-account-meta">
          <span>总资产 <strong>${fmtNum(pf.total_equity)} ${esc(curLabel)}</strong></span>
          <span>可用现金 <strong>${fmtNum(pf.cash)} ${esc(curLabel)}</strong></span>
          <span>策略 <strong>${esc(VERSION_LABELS[acc.strategy_version] || acc.strategy_version)}</strong></span>
          <span>调仓阈值 <strong>${fmtNum(acc.rebalance_deadband * 100, 0)}%</strong></span>
          <button type="button" class="ghost-button" data-ptms-action="reload-state">↻ 刷新数据</button>
        </div>
      </div>
    `;
  }

  function renderVerdictClass(verdict) {
    if (verdict === 'ACTION_INTRADAY_STOP') return 'verdict-stop';
    if (verdict === 'ACTION_PRIORITY_EXIT') return 'verdict-priority';
    if (verdict === 'ACTION_REBALANCE') return 'verdict-rebalance';
    if (verdict === 'NEEDS_INIT') return 'verdict-init';
    return 'verdict-hold';
  }

  function renderDesk() {
    const root = $('ptms-desk-root');
    if (!root) return;
    if (!isOnlineHttp()) {
      root.innerHTML = '<div class="panel"><p class="empty">当前为离线研究快照，请双击 <code>run_dashboard_windows.bat</code> 启动本地服务后查看今日交易决策。</p></div>';
      return;
    }
    const bundle = activeBundle();
    if (!bundle) {
      root.innerHTML = `<div class="panel"><p class="empty">${ptms.error ? esc(ptms.error) : '正在载入今日决策数据...'}</p></div>`;
      return;
    }

    const { account, portfolio, plan, stop_states: stops, tickets } = bundle;
    const cur = account.currency;
    const pendingTickets = (tickets || []).filter((t) => t.status === 'PENDING');
    const pendingWeekly = pendingTickets.find((t) => t.source === 'WEEKLY_REBALANCE' && t.signal_cutoff === plan.signal_cutoff);
    const clipsHtml = (plan.cooldown_clips || []).map((c) => `
      <div class="ptms-alert warn">
        <strong>冷却期加仓已拦截：${esc(c.name)} (${esc(c.symbol)})</strong> — 周频目标原为 ${fmtW(c.weekly_target)}，因盘中触发减仓正处于冷却期（至 ${esc(c.cooldown_until_date || '待执行')}），已自动下调至 ${fmtW(c.clipped_to)}，释放资金转入现金管理标的。
      </div>
    `).join('');

    const deadbandHint = !plan.should_execute && (plan.orders || []).length
      ? `<div class="ptms-alert warn" style="margin-bottom:12px">当前偏离尚未达到 ${fmtNum(plan.deadband * 100, 0)}% 的调仓阈值，以下清单仅作仓位微调参考，建议继续持有以减少摩擦成本。</div>`
      : '';

    const ordersHtml = (plan.orders || []).length
      ? `${deadbandHint}<table>
          <thead><tr><th>方向</th><th>标的</th><th>建议股数</th><th>最小单位</th><th>今日可卖</th><th>参考价</th><th>预估金额</th><th>预估费用</th><th>调仓说明与提示</th></tr></thead>
          <tbody>
            ${plan.orders.map((o) => `
              <tr>
                <td><span class="ptms-badge ${o.side === 'BUY' ? 'green' : 'red'}">${o.side === 'BUY' ? '买入' : '卖出'}</span></td>
                <td><strong>${esc(o.name)}</strong><br><small>${esc(o.symbol)}</small></td>
                <td><strong>${fmtNum(o.quantity, o.lot_size > 1 ? 0 : 2)}</strong></td>
                <td>${o.lot_size} 股</td>
                <td>${fmtNum(o.sellable_shares, o.lot_size > 1 ? 0 : 2)} ${o.t_plus === 1 ? '<span class="ptms-badge amber">T+1</span>' : '<span class="ptms-badge">T+0</span>'}</td>
                <td>${fmtNum(o.ref_price, account.market === 'US' ? 2 : 3)}</td>
                <td>${fmtNum(o.est_amount)}</td>
                <td>${fmtNum(o.est_fee)}</td>
                <td>${esc(o.reason)}${o.warning ? `<br><small class="text-red">${esc(o.warning)}</small>` : ''}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>`
      : '<p class="empty">当前持仓已贴近目标仓位，无需交易操作。</p>';

    const compareHtml = (plan.weights_comparison || []).map((w) => {
      const driftPp = (w.drift || 0) * 100;
      const riskBadge = w.cooldown_active
        ? `<span class="ptms-badge red">冷却至 ${esc(w.cooldown_until_date)}</span>`
        : w.pending_stage > 0
          ? `<span class="ptms-badge red">${w.pending_stage === 1 ? '一档减仓提醒' : '二档减仓提醒'}</span>`
          : '<span class="ptms-badge green">正常</span>';
      return `
        <tr>
          <td><strong>${esc(w.name)}</strong><br><small>${esc(w.symbol)}</small></td>
          <td>${fmtW(w.actual_weight)}${w.symbol === '511880.SH' || w.symbol === 'BIL.US' ? `<br><small>含现金 ${fmtW(w.effective_actual_weight)}</small>` : ''}</td>
          <td>${fmtW(w.weekly_target)}</td>
          <td><strong>${fmtW(w.effective_target)}</strong></td>
          <td class="${Math.abs(driftPp) < 1 ? '' : driftPp > 0 ? 'text-green' : 'text-red'}">${fmtPct(driftPp, 1)} 个百分点</td>
          <td>${w.core_floor > 0 ? `<span class="ptms-badge blue">底仓 ≥ ${fmtW(w.core_floor, 0)}</span>` : '—'}</td>
          <td>${riskBadge}</td>
        </tr>
      `;
    }).join('');

    const stopCardsHtml = Object.values(stops || {}).map((st) => {
      const distStr = st.distance_pct == null ? '—' : fmtPct(st.distance_pct, 2);
      const distCls = st.distance_pct != null && st.distance_pct < 0 ? 'text-red' : 'text-green';
      const stageLabel = STAGE_LABELS[st.stage || 0] || '正常监控';
      return `
        <div class="ptms-stop-card">
          <div class="ptms-stop-head">
            <div>
              <strong>${esc(st.name)} · ${esc(st.symbol)}</strong>
              <small>${esc(st.session_date || '—')} · ${esc(st.status_note || '')}</small>
            </div>
            <div class="ptms-actions-row">
              ${st.pending_stage > 0 ? `<span class="ptms-badge red">待执行第 ${st.pending_stage} 档减仓</span>` : ''}
              ${st.cooldown_active ? `<span class="ptms-badge amber">冷却剩余 ${st.cooldown_remaining_days} 交易日</span>` : ''}
              <button type="button" class="ghost-button" data-ptms-action="open-reset-stop" data-symbol="${esc(st.symbol)}">重置防线</button>
            </div>
          </div>
          <div class="ptms-stop-stats">
            <div class="ptms-stop-stat"><span>最新价 / 前收盘</span><b>${fmtNum(st.last_price, 3)} / ${fmtNum(st.prior_close, 3)}</b></div>
            <div class="ptms-stop-stat"><span>当前防守线</span><b>${fmtNum(st.active_line, 3)}</b></div>
            <div class="ptms-stop-stat"><span>距防线空间 / 破线数</span><b class="${distCls}">${distStr} · 连续 ${st.below_count || 0} 根</b></div>
          </div>
          <div class="ptms-verdict-meta">
            <span>20日趋势防线：${fmtNum(st.raw_trend_line, 3)}</span>
            <span>3% 急跌防线：${fmtNum(st.shock_line, 3)}</span>
            <span>防守状态：${esc(stageLabel)}</span>
            <span>当前 / 底仓：${fmtW(st.actual_weight)} / ${fmtW(st.core_floor, 0)}</span>
          </div>
        </div>
      `;
    }).join('');

    const pendingHtml = pendingTickets.length
      ? pendingTickets.map((t) => {
          const ordCount = ((t.snapshot || {}).orders || []).length;
          return `
            <div class="ptms-ticket-card ${t.source === 'INTRADAY_STOP' ? 'urgent' : ''}">
              <div class="ptms-ticket-head">
                <div>
                  <span class="ptms-badge ${t.source === 'INTRADAY_STOP' ? 'red' : 'blue'}">${t.source === 'INTRADAY_STOP' ? '盘中防守减仓' : t.source === 'WEEKLY_REBALANCE' ? '周频调仓计划' : '手动计划'}</span>
                  <strong>#${t.decision_id} · ${esc(t.reason)}</strong>
                </div>
                <div class="ptms-actions-row">
                  <button type="button" class="solid-button" data-ptms-action="open-execute-ticket" data-decision-id="${t.decision_id}">确认成交（${ordCount} 笔）</button>
                  <button type="button" class="ghost-button" data-ptms-action="open-skip-ticket" data-decision-id="${t.decision_id}">暂不执行</button>
                </div>
              </div>
              <small style="color:#708295">信号日期：${esc(t.signal_cutoff)} · 创建于 ${esc(String(t.created_at).slice(0, 19).replace('T', ' '))}</small>
            </div>
          `;
        }).join('')
      : '<p class="empty">当前没有待确认的调仓单。</p>';

    const badgeText = VERDICT_BADGES[plan.verdict] || '状态更新';

    root.innerHTML = `
      ${accountSwitcherBar(bundle)}
      ${ptms.error ? `<div class="ptms-alert error">${esc(ptms.error)}</div>` : ''}
      <section class="ptms-verdict ${renderVerdictClass(plan.verdict)}">
        <div class="ptms-verdict-main">
          <div class="ptms-verdict-title">
            <span class="ptms-badge ${plan.should_execute ? 'green' : 'blue'}">${esc(badgeText)}</span>
            <strong>${esc(plan.verdict_label)}</strong>
          </div>
          <p>${esc(plan.verdict_reason)}</p>
          <div class="ptms-verdict-meta">
            <span>信号截点：${esc(plan.signal_cutoff || '—')}</span>
            <span>计算来源：${plan.signal_origin === 'online_recomputed_simulation' ? '在线实时重算' : '离线研究基线'}</span>
            <span>双边偏离：${fmtPct(plan.turnover * 100, 1, false)} / 阈值 ${fmtNum(plan.deadband * 100, 0)}%</span>
            <span>预估费用：${fmtNum(plan.total_est_fee)} ${esc(cur)}</span>
            <span>执行后预计留存现金：${fmtNum(plan.projected_cash_after)} ${esc(cur)}</span>
          </div>
        </div>
        <div class="ptms-actions-row">
          ${!portfolio.initialized
            ? '<button type="button" class="solid-button" data-ptms-action="open-init">初始化账户持仓</button>'
            : `
              <button type="button" class="ghost-button" data-ptms-action="create-weekly-ticket" ${pendingWeekly ? 'disabled' : ''}>${pendingWeekly ? `已存待办单 #${pendingWeekly.decision_id}` : '保存为待办单'}</button>
              <button type="button" class="solid-button" data-ptms-action="execute-current-plan" ${(plan.orders || []).length ? '' : 'disabled'}>确认按单成交</button>
              <button type="button" class="ghost-button" data-ptms-action="open-event">手动记一笔</button>
            `}
        </div>
      </section>
      ${clipsHtml}
      <article class="panel full">
        <div class="panel-top">
          <div><span class="micro">ORDER EXECUTION SHEET</span><h3>调仓建议清单（已考虑整数手与 T+1 限制）</h3></div>
          <span class="info-chip">${esc(plan.last_reason || '周频策略与日内防线综合建议')}</span>
        </div>
        <div class="table-wrap">${ordersHtml}</div>
      </article>
      <div class="ptms-grid-2">
        <article class="panel">
          <div class="panel-top"><div><span class="micro">POSITION COMPARISON</span><h3>仓位对比（当前 / 策略 / 执行目标）</h3></div></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>资产</th><th>当前仓位</th><th>策略原目标</th><th>最终目标</th><th>仓位差</th><th>底仓保护</th><th>风控状态</th></tr></thead>
              <tbody>${compareHtml}</tbody>
            </table>
          </div>
        </article>
        <article class="panel">
          <div class="panel-top"><div><span class="micro">PENDING ORDERS</span><h3>待处理调仓单（${pendingTickets.length}）</h3></div></div>
          <div style="display:grid;gap:12px">${pendingHtml}</div>
        </article>
      </div>
      <article class="panel full">
        <div class="panel-top">
          <div><span class="micro">INTRADAY RISK GUARD</span><h3>盘中 30 分钟防守线监控（仅做破位减仓 · 跨日自动延续）</h3></div>
          <span class="info-chip">连续 2 根破线减半战术仓 · 连续 4 根清战术仓 · 减仓后冷却 3 个交易日</span>
        </div>
        <div class="ptms-grid-2">${stopCardsHtml || '<p class="empty">该账户暂无盘中监控标的</p>'}</div>
      </article>
    `;
  }

  function renderPortfolio() {
    const root = $('ptms-portfolio-root');
    if (!root) return;
    if (!isOnlineHttp()) {
      root.innerHTML = '<div class="panel"><p class="empty">请通过本地交易服务打开此页面，管理双市场真实持仓与资金流水。</p></div>';
      return;
    }
    const bundle = activeBundle();
    if (!bundle) {
      root.innerHTML = '<div class="panel"><p class="empty">正在载入持仓与流水账本...</p></div>';
      return;
    }
    const { account, portfolio, plan, events } = bundle;
    const curLabel = CURRENCY_NAMES[account.currency] || account.currency;
    const targetBySym = Object.fromEntries((plan.weights_comparison || []).map((w) => [w.symbol, w.effective_target]));

    const kpis = [
      { name: `账户总资产 (${curLabel})`, value: fmtNum(portfolio.total_equity), hint: `净投入本金 ${fmtNum(portfolio.net_capital_in)}`, accent: '#14a989', icon: '◈' },
      { name: '持仓市值 / 可用现金', value: `${fmtNum(portfolio.holdings_value)}`, hint: `可用现金 ${fmtNum(portfolio.cash)}（占 ${fmtW(portfolio.cash_weight)}）`, accent: '#5378e5', icon: '▤' },
      { name: '累计总盈亏', value: `${portfolio.total_pnl >= 0 ? '+' : ''}${fmtNum(portfolio.total_pnl)}`, hint: `浮动 ${fmtNum(portfolio.unrealized_pnl)} / 已实现 ${fmtNum(portfolio.realized_pnl)}`, accent: '#e4a64d', icon: '↗' },
      { name: '交易费用与分红', value: `${fmtNum(portfolio.total_fees)}`, hint: `累计分红 +${fmtNum(portfolio.total_dividends)} · 共 ${portfolio.event_count} 条记录`, accent: '#7e83e5', icon: '≡' },
    ];

    const filteredEvents = (events || []).filter((e) => ptms.ledgerFilter === 'ALL' || e.event_type === ptms.ledgerFilter);

    root.innerHTML = `
      ${accountSwitcherBar(bundle)}
      <div class="kpi-grid">${kpis.map((k) => `
        <div class="kpi-card" style="--accent:${k.accent}">
          <div class="kpi-caption"><span>${esc(k.name)}</span><span class="kpi-icon">${k.icon}</span></div>
          <strong class="kpi-value ${String(k.value).startsWith('+') ? 'positive' : ''}">${esc(k.value)}</strong>
          <div class="kpi-foot">${esc(k.hint)}</div>
        </div>
      `).join('')}</div>

      <article class="panel full">
        <div class="panel-top">
          <div><span class="micro">CURRENT HOLDINGS</span><h3>当前持仓明细（含今日可卖与 T+1 限制）</h3></div>
          <div class="ptms-actions-row">
            <button type="button" class="solid-button" data-ptms-action="open-init">初始化持仓</button>
            <button type="button" class="ghost-button" data-ptms-action="open-event">+ 记一笔交易 / 出入金</button>
            <button type="button" class="ghost-button" data-ptms-action="open-reconcile">核对券商余额</button>
            <button type="button" class="ghost-button" data-ptms-action="open-config">账户设置</button>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>标的</th><th>交易制度</th><th>持仓股数</th><th>今日可卖</th><th>成本均价</th><th>最新估值价</th><th>持仓市值</th><th>当前占比</th><th>目标占比</th><th>浮动盈亏</th><th>已实现盈亏</th></tr></thead>
            <tbody>
              ${portfolio.positions.map((p) => `
                <tr>
                  <td><strong>${esc(p.name)}</strong><br><small>${esc(p.symbol)}</small></td>
                  <td><span class="ptms-badge ${p.t_plus === 1 ? 'amber' : 'green'}">T+${p.t_plus}</span> <small>${p.lot_size}股/手</small></td>
                  <td><strong>${fmtNum(p.shares, p.lot_size > 1 ? 0 : 2)}</strong></td>
                  <td>${fmtNum(p.sellable_shares, p.lot_size > 1 ? 0 : 2)}${p.today_bought_shares > 0 && p.t_plus === 1 ? `<br><small class="text-red">今日买入锁定 ${fmtNum(p.today_bought_shares, 0)}</small>` : ''}</td>
                  <td>${p.avg_cost > 0 ? fmtNum(p.avg_cost, account.market === 'US' ? 2 : 4) : '—'}</td>
                  <td>${p.mark_price != null ? fmtNum(p.mark_price, account.market === 'US' ? 2 : 4) : '—'}<br><small>${esc(priceSourceLabel(p.price_source))}</small></td>
                  <td>${fmtNum(p.market_value)}</td>
                  <td><strong>${fmtW(p.actual_weight)}</strong>${p.core_floor > 0 ? `<br><small>底仓≥${fmtW(p.core_floor, 0)}</small>` : ''}</td>
                  <td>${fmtW(targetBySym[p.symbol] ?? 0)}</td>
                  <td class="${p.unrealized_pnl >= 0 ? 'text-green' : 'text-red'}">${p.unrealized_pnl >= 0 ? '+' : ''}${fmtNum(p.unrealized_pnl)}</td>
                  <td class="${p.realized_pnl >= 0 ? 'text-green' : 'text-red'}">${p.realized_pnl >= 0 ? '+' : ''}${fmtNum(p.realized_pnl)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </article>

      <article class="panel full">
        <div class="panel-top">
          <div><span class="micro">TRANSACTION & CASH LEDGER</span><h3>资金与交易明细流水</h3></div>
          <label class="small-select">流水筛选
            <select id="ptms-ledger-filter">
              <option value="ALL" ${ptms.ledgerFilter === 'ALL' ? 'selected' : ''}>全部记录</option>
              ${Object.entries(EVENT_LABELS).map(([k, v]) => `<option value="${k}" ${ptms.ledgerFilter === k ? 'selected' : ''}>${v}</option>`).join('')}
            </select>
          </label>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>编号</th><th>交易日</th><th>类型</th><th>标的</th><th>股数</th><th>单价</th><th>手续费</th><th>资金变动</th><th>执行方式</th><th>关联计划</th><th>备注</th></tr></thead>
            <tbody>
              ${filteredEvents.length ? filteredEvents.map((e) => `
                <tr>
                  <td>#${e.event_id}</td>
                  <td><strong>${esc(e.trade_date)}</strong><br><small>${esc(String(e.event_time).slice(11, 19))}</small></td>
                  <td><span class="ptms-badge ${e.event_type === 'BUY' || e.event_type === 'CASH_IN' ? 'green' : e.event_type === 'SELL' || e.event_type === 'CASH_OUT' ? 'red' : 'blue'}">${esc(EVENT_LABELS[e.event_type] || e.event_type)}</span></td>
                  <td>${esc(e.symbol || '资金账户')}</td>
                  <td>${e.quantity ? fmtNum(e.quantity, 2) : '—'}</td>
                  <td>${e.price ? fmtNum(e.price, 4) : '—'}</td>
                  <td>${e.fee ? fmtNum(e.fee) : '0.00'}</td>
                  <td class="${e.cash_delta >= 0 ? 'text-green' : 'text-red'}">${e.cash_delta >= 0 ? '+' : ''}${fmtNum(e.cash_delta)}</td>
                  <td><span class="ptms-badge">${esc(DISCIPLINE_LABELS[e.discipline_tag] || e.discipline_tag)}</span></td>
                  <td>${e.decision_id ? `#${e.decision_id}` : '—'}</td>
                  <td>${esc(e.note)}</td>
                </tr>
              `).join('') : '<tr><td colspan="11">暂无记录，请先点击右上角「初始化持仓」录入当前账户余额</td></tr>'}
            </tbody>
          </table>
        </div>
      </article>
    `;
  }

  function tempStripHtml(stage) {
    return `<div class="ptms-temp-strip">${TEMP_LEVELS.map((label, idx) => `<div class="ptms-temp-cell ptms-temp-${idx} ${Number(stage) === idx ? 'active' : ''}">${label}</div>`).join('')}</div>`;
  }

  function renderIndicators() {
    const root = $('ptms-indicators-root');
    if (!root) return;
    if (!isOnlineHttp()) {
      root.innerHTML = '<div class="panel"><p class="empty">请通过本地交易服务打开此页面，查看双市场完整策略指标与防守线拆解。</p></div>';
      return;
    }
    const bundle = activeBundle();
    const ind = ptms.stateData && ptms.stateData.indicators;
    if (!bundle || !ind) {
      root.innerHTML = '<div class="panel"><p class="empty">正在载入策略指标...</p></div>';
      return;
    }
    const cn = ind.CN || {};
    const us = ind.US || {};
    const stopRows = Object.values(bundle.stop_states || {});

    const cnSection = cn.tech ? `
      <div class="ptms-grid-2">
        <article class="panel">
          <div class="panel-top"><div><span class="micro">CHINEXT 159915 · 成长进攻方向</span><h3>创业板趋势温度与动量</h3></div><span class="ptms-badge ${cn.tech.stage >= 5 ? 'red' : cn.tech.stage >= 4 ? 'amber' : 'blue'}">${esc(cn.tech.temperature)}（${cn.tech.stage} / 6）</span></div>
          ${tempStripHtml(cn.tech.stage)}
          <div class="ptms-kv-list" style="margin-top:12px">
            <div class="ptms-kv-row"><span>周线收盘 / 短期均线 / 长期均线</span><strong>${fmtNum(cn.tech.close, 3)} / ${fmtNum(cn.tech.ma_fast, 3)} / ${fmtNum(cn.tech.ma_slow, 3)}</strong></div>
            <div class="ptms-kv-row"><span>6周动量 / 26周动量 / 综合动量</span><strong>${fmtPct((cn.tech.mom_fast || 0) * 100)} / ${fmtPct((cn.tech.mom_slow || 0) * 100)} / ${fmtPct((cn.tech.momentum || 0) * 100)}</strong></div>
            <div class="ptms-kv-row"><span>相对红利 6 周差 / 年化波动 / 回撤</span><strong>${fmtPct((cn.tech.vs_red_6w || 0) * 100)} / ${fmtPct((cn.tech.vol || 0) * 100, 1, false)} / ${fmtPct((cn.tech.dd || 0) * 100)}</strong></div>
            <div class="ptms-kv-row"><span>右侧买回状态</span><strong>${esc(REENTRY_LABELS[cn.tech.reentry_state] || cn.tech.reentry_state)} · ${cn.tech.probe_signal ? '满足试仓条件' : '未触发试仓'} / ${cn.tech.confirm_signal ? '趋势确认' : '待确认'}</strong></div>
          </div>
        </article>
        <article class="panel">
          <div class="panel-top"><div><span class="micro">DIVIDEND 515180 · 稳健防守方向</span><h3>中证红利趋势与防守资产资格</h3></div><span class="ptms-badge ${cn.red.stage >= 4 ? 'green' : 'amber'}">${esc(cn.red.temperature)}（${cn.red.stage} / 6）</span></div>
          ${tempStripHtml(cn.red.stage)}
          <div class="ptms-kv-list" style="margin-top:12px">
            <div class="ptms-kv-row"><span>红利收盘 / 短期均线 / 长期均线</span><strong>${fmtNum(cn.red.close, 3)} / ${fmtNum(cn.red.ma_fast, 3)} / ${fmtNum(cn.red.ma_slow, 3)}</strong></div>
            <div class="ptms-kv-row"><span>6周动量 / 26周动量 / 年化波动</span><strong>${fmtPct((cn.red.mom_fast || 0) * 100)} / ${fmtPct((cn.red.mom_slow || 0) * 100)} / ${fmtPct((cn.red.vol || 0) * 100, 1, false)}</strong></div>
            <div class="ptms-kv-row"><span>10年国债 (511260) 配置资格</span><strong>${cn.defensive.bond_eligible ? '<span class="text-green">合格</span>' : '<span class="text-red">暂不合格</span>'} · 近6周 ${fmtPct((cn.defensive.bond_mom_fast || 0) * 100)} / 目标 ${fmtW(cn.defensive.bond_target)}</strong></div>
            <div class="ptms-kv-row"><span>黄金 (518880) 配置资格</span><strong>${cn.defensive.gold_eligible ? '<span class="text-green">合格</span>' : '<span class="text-red">暂不合格</span>'} · 近6周 ${fmtPct((cn.defensive.gold_mom_fast || 0) * 100)} / 目标 ${fmtW(cn.defensive.gold_target)}</strong></div>
          </div>
        </article>
      </div>
      <article class="panel full">
        <div class="panel-top">
          <div><span class="micro">PORTFOLIO RISK & LIQUIDITY</span><h3>组合波动预算、市场量能与跨市场风险</h3></div>
          <span class="ptms-badge ${cn.source === 'online' ? 'green' : 'amber'}">${cn.source === 'online' ? '在线实时指标' : '离线研究基线'} · 截至 ${esc(cn.asof_week_start)}</span>
        </div>
        <div class="ptms-grid-3">
          <div class="ptms-stop-stat"><span>组合预测波动率 / 仓位调节系数</span><b>${fmtPct((cn.portfolio.estimated_vol || 0) * 100, 2, false)} · ${fmtNum(cn.portfolio.risk_multiplier, 2)} 倍</b></div>
          <div class="ptms-stop-stat"><span>市场 4周/13周量比 · 日均成交额</span><b>${fmtNum(cn.portfolio.market_liquidity_ratio, 2)} 倍 · ${fmtNum((cn.portfolio.market_turn_100m || 0) / 10000, 2)} 万亿元</b></div>
          <div class="ptms-stop-stat"><span>美股风险参考 · 跨市场动作</span><b>${esc(cn.portfolio.us_risk_state_lagged || '—')} · ${esc(cn.portfolio.cross_market_reason || '—')}</b></div>
        </div>
        <div class="ptms-kv-list" style="margin-top:12px">
          <div class="ptms-kv-row"><span>创业板成交占比 / 红利成交占比</span><strong>${fmtNum(cn.portfolio.tech_share_pct, 1)}% / ${fmtNum(cn.portfolio.red_share_pct, 1)}%</strong></div>
          <div class="ptms-kv-row"><span>组合风控与量价状态</span><strong>${esc(translateText(cn.portfolio.v10_reason))} · ${cn.portfolio.v14_warning ? '顶部量价预警激活' : '量价状态正常'}</strong></div>
        </div>
      </article>
    ` : '<div class="panel"><p class="empty">暂无 A 股完整周频指标</p></div>';

    const usSection = us.qqq_close ? `
      <article class="panel full">
        <div class="panel-top">
          <div><span class="micro">US TREND & VOLATILITY CONTROL</span><h3>美股 QQQ 多周期趋势、波动率控制与市场宽度</h3></div>
          <span class="ptms-badge ${us.us_bear ? 'red' : us.us_warning ? 'amber' : 'green'}">${esc(us.risk_state)} · ${esc(us.trend_decision)}（${esc(us.asof_week_start)}）</span>
        </div>
        <div class="ptms-grid-3">
          <div class="ptms-stop-stat"><span>QQQ 现价 / 趋势综合得分</span><b>$${fmtNum(us.qqq_close, 2)} · ${fmtNum((us.trend_score || 0) * 100, 0)}%</b></div>
          <div class="ptms-stop-stat"><span>13周波动率 / 高波降仓系数 (阈值24%)</span><b>${fmtPct((us.realized_vol || 0) * 100, 2, false)} · ${fmtNum(us.vol_multiplier, 2)} 倍</b></div>
          <div class="ptms-stop-stat"><span>半导体(SMH) / 标普等权(RSP) 4周相对强弱</span><b>${fmtPct((us.smh_relative_4w || 0) * 100)} / ${fmtPct((us.rsp_relative_4w || 0) * 100)}</b></div>
        </div>
        <div class="ptms-kv-list" style="margin-top:12px">
          <div class="ptms-kv-row"><span>QQQ 10周 / 20周 / 30周均线与乖离</span><strong>10周 ${fmtNum(us.qqq_10w, 2)} (${fmtPct(us.qqq_dist_10w_pct)}) | 20周 ${fmtNum(us.qqq_20w, 2)} (${fmtPct(us.qqq_dist_20w_pct)}) | 30周 ${fmtNum(us.qqq_30w, 2)} (${fmtPct(us.qqq_dist_30w_pct)})</strong></div>
          <div class="ptms-kv-row"><span>标普500 (SPY) 收盘 / 13周均线 / 30周均线</span><strong>$${fmtNum(us.spy_close, 2)} / $${fmtNum(us.spy_13w, 2)} / $${fmtNum(us.spy_30w, 2)}</strong></div>
          <div class="ptms-kv-row"><span>风险预警状态</span><strong>${us.us_bear ? '跌破长期均线（熊市防御）' : '中长期趋势未破坏'} · ${us.us_shock ? '近期出现单周急跌冲击' : '无急跌冲击'} · ${us.us_warning ? '科技板块退潮预警' : '板块宽度正常'}</strong></div>
        </div>
      </article>
    ` : '<div class="panel"><p class="empty">暂无美股完整周频指标</p></div>';

    root.innerHTML = `
      ${accountSwitcherBar(bundle)}
      ${bundle.account.market === 'CN' ? cnSection + usSection : usSection + cnSection}
      <article class="panel full">
        <div class="panel-top"><div><span class="micro">INTRADAY STOP BREAKDOWN</span><h3>当前账户盘中防守线参数拆解</h3></div></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>标的</th><th>前收盘</th><th>20日高点</th><th>10日均线</th><th>日波动率</th><th>趋势防守线</th><th>3%急跌线</th><th>当前激活防线</th><th>距防线空间</th></tr></thead>
            <tbody>
              ${stopRows.map((s) => `
                <tr>
                  <td><strong>${esc(s.name)}</strong> (${esc(s.symbol)})</td>
                  <td>${fmtNum(s.prior_close, 3)}</td>
                  <td>${fmtNum(s.peak, 3)}</td>
                  <td>${fmtNum(s.ma, 3)}</td>
                  <td>${s.daily_sigma != null ? fmtPct(s.daily_sigma * 100, 2, false) : '—'}</td>
                  <td>${fmtNum(s.raw_trend_line, 3)}</td>
                  <td>${fmtNum(s.shock_line, 3)}</td>
                  <td><strong>${fmtNum(s.active_line, 3)}</strong></td>
                  <td class="${s.distance_pct != null && s.distance_pct < 0 ? 'text-red' : 'text-green'}">${fmtPct(s.distance_pct, 2)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </article>
    `;
  }

  function drawReviewChart(twrCurve, benchCurve) {
    const host = $('ptms-review-chart');
    if (!host) return;
    if (!twrCurve || !twrCurve.length) {
      host.innerHTML = '<p class="empty">暂无实盘净值记录，完成持仓初始化后将自动生成与策略基准的对照曲线。</p>';
      return;
    }
    const W = Math.max(460, host.clientWidth || 780);
    const H = host.clientHeight || 290;
    const m = { l: 46, r: 16, t: 16, b: 28 };
    const dates = twrCurve.map((x) => x.d);
    const yTwr = twrCurve.map((x) => Number(x.nav) * 100);

    const yBench = dates.map((d) => {
      let best = 100;
      for (const b of (benchCurve || [])) {
        if (b.d <= d) best = Number(b.nav) * 100;
        else break;
      }
      return best;
    });

    const allY = yTwr.concat(benchCurve && benchCurve.length ? yBench : []);
    let min = Math.min(...allY);
    let max = Math.max(...allY);
    const pad = Math.max(1.0, (max - min) * 0.14);
    min -= pad;
    max += pad;

    const n = dates.length;
    const innerW = W - m.l - m.r;
    const innerH = H - m.t - m.b;
    const x = (i) => m.l + (n < 2 ? innerW / 2 : (i / (n - 1)) * innerW);
    const y = (v) => m.t + ((max - v) / (max - min)) * innerH;

    const grid = [0, 0.25, 0.5, 0.75, 1].map((t) => {
      const v = min + (max - min) * t;
      const yy = y(v);
      return `<line x1="${m.l}" y1="${yy}" x2="${W - m.r}" y2="${yy}" stroke="#e9eef3" stroke-dasharray="3 5" /><text x="${m.l - 8}" y="${yy + 4}" fill="#a3afba" text-anchor="end" font-size="10">${fmtNum(v, 1)}</text>`;
    }).join('');

    const pathTwr = yTwr.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
    const pathBench = yBench.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
    const xTicks = Array.from(new Set([0, Math.floor((n - 1) / 2), n - 1])).map((i) => (
      `<text x="${x(i)}" y="${H - 6}" fill="#9ba9b6" font-size="10" text-anchor="${i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}">${esc(dates[i])}</text>`
    )).join('');

    host.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="账户真实净值与策略基准对照">
        ${grid}
        ${benchCurve && benchCurve.length ? `<path d="${pathBench}" fill="none" stroke="#5578e7" stroke-width="2" stroke-dasharray="6 4" />` : ''}
        <path d="${pathTwr}" fill="none" stroke="#12ad8e" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round" />
        ${n === 1 ? `<circle cx="${x(0)}" cy="${y(yTwr[0])}" r="4.5" fill="#12ad8e" />` : ''}
        ${xTicks}
      </svg>
    `;
  }

  function renderReview() {
    const root = $('ptms-review-root');
    if (!root) return;
    if (!isOnlineHttp()) {
      root.innerHTML = '<div class="panel"><p class="empty">请通过本地交易服务打开此页面，查看账户真实收益率对比与复盘日记。</p></div>';
      return;
    }
    const bundle = activeBundle();
    const rev = ptms.reviewData;
    if (!bundle || !rev || ptms.reviewAccountId !== ptms.accountId) {
      root.innerHTML = '<div class="panel"><p class="empty">正在计算账户真实收益率与执行归因...</p></div>';
      if (bundle) loadReview(ptms.accountId);
      return;
    }

    const k = rev.kpis;
    const d = rev.discipline;
    const curLabel = CURRENCY_NAMES[rev.currency] || rev.currency;
    const gapStr = k.tracking_gap_pp != null
      ? `${k.tracking_gap_pp >= 0 ? '领先 ' : '落后 '}${fmtNum(Math.abs(k.tracking_gap_pp), 2)} 个百分点`
      : '—';
    const kpis = [
      { name: '账户真实收益率', value: k.twr_return_pct != null ? fmtPct(k.twr_return_pct) : '未初始化', hint: `已剔除出入金影响 · 周期 ${k.start_date || '—'} 至 ${k.as_of_date}`, accent: '#14a989', icon: '↗' },
      { name: '同期策略参考收益', value: k.model_return_pct != null ? fmtPct(k.model_return_pct) : '—', hint: `相对策略：${gapStr}`, accent: '#5378e5', icon: '◫' },
      { name: '最大回撤对比', value: k.real_max_dd_pct != null ? fmtPct(k.real_max_dd_pct) : '—', hint: `策略同期最大回撤 ${k.model_max_dd_pct != null ? fmtPct(k.model_max_dd_pct) : '—'}`, accent: '#e4a64d', icon: '⌁' },
      { name: '纪律遵守率 / 成交偏差', value: `${fmtNum(d.trade_discipline_rate_pct, 1)}%`, hint: `平均成交偏差 ${d.avg_slippage_bp != null ? fmtNum(d.avg_slippage_bp, 1) + ' 基点' : '暂无样本'} · 计划执行率 ${fmtNum(d.ticket_follow_rate_pct, 1)}%`, accent: '#7e83e5', icon: '✦' },
    ];

    root.innerHTML = `
      ${accountSwitcherBar(bundle)}
      <div class="kpi-grid">${kpis.map((x) => `
        <div class="kpi-card" style="--accent:${x.accent}">
          <div class="kpi-caption"><span>${esc(x.name)}</span><span class="kpi-icon">${x.icon}</span></div>
          <strong class="kpi-value ${String(x.value).startsWith('+') ? 'positive' : String(x.value).startsWith('-') ? 'negative' : ''}">${esc(x.value)}</strong>
          <div class="kpi-foot">${esc(x.hint)}</div>
        </div>
      `).join('')}</div>

      <article class="panel full">
        <div class="panel-top">
          <div><span class="micro">PERFORMANCE COMPARISON</span><h3>账户真实净值 vs 策略理论基准（起点 = 100）</h3></div>
          <div class="ptms-actions-row">
            <a class="ghost-button" style="text-decoration:none" href="/api/ptms/export?account_id=${encodeURIComponent(ptms.accountId)}&format=markdown">⇩ 导出复盘报告 (.md)</a>
            <a class="ghost-button" style="text-decoration:none" href="/api/ptms/export?account_id=${encodeURIComponent(ptms.accountId)}&format=json">⇩ 备份账本数据 (.json)</a>
          </div>
        </div>
        <div class="chart-legend">
          <span><i class="swatch" style="background:#12ad8e"></i>账户真实净值（剔除出入金）</span>
          <span><i class="swatch" style="background:#5578e7"></i>同期 ${esc(VERSION_LABELS[rev.strategy_version] || rev.strategy_version)} 理论净值</span>
        </div>
        <div id="ptms-review-chart" class="chart-stage"></div>
        <div class="chart-footer"><span>真实收益率采用份额法自动剥离资金进出干扰</span><span>累计手续费 ${fmtNum(k.total_fees)} ${esc(curLabel)} · 累计分红 +${fmtNum(k.total_dividends)}</span></div>
      </article>

      <div class="ptms-grid-2">
        <article class="panel">
          <div class="panel-top"><div><span class="micro">TRADING JOURNAL</span><h3>周期复盘日记</h3></div></div>
          <form id="ptms-note-form" class="ptms-form-grid">
            <div class="ptms-field">
              <label for="ptms-note-type">复盘周期</label>
              <select id="ptms-note-type">
                <option value="WEEKLY">周度复盘</option>
                <option value="DAILY">每日复盘</option>
                <option value="MONTHLY">月度复盘</option>
              </select>
            </div>
            <div class="ptms-field">
              <label for="ptms-note-key">周期标识</label>
              <input id="ptms-note-key" value="${esc(currentIsoWeek())}" required />
            </div>
            <div class="ptms-field full">
              <label for="ptms-note-text">复盘总结（记录执行得失、偏离原因与下阶段纪律要求）</label>
              <textarea id="ptms-note-text" placeholder="例如：本周严格遵守换手阈值未盲目追涨，重点盯紧红利和创业板 30 分钟防守线。" required></textarea>
            </div>
            <div class="ptms-field full">
              <button type="submit" class="solid-button">保存复盘笔记</button>
            </div>
          </form>
          <div style="display:grid;gap:10px;margin-top:16px">
            ${(rev.notes || []).length ? rev.notes.map((n) => `
              <div class="ptms-note-item">
                <header><strong>${esc(PERIOD_LABELS[n.period_type] || n.period_type)} · ${esc(n.period_key)}</strong><small style="color:#7c8c9c">${esc(String(n.created_at).slice(0, 16).replace('T', ' '))}</small></header>
                <p>${esc(n.reflection)}</p>
              </div>
            `).join('') : '<p class="empty">暂无复盘笔记，写下你的第一篇复盘总结吧。</p>'}
          </div>
        </article>

        <article class="panel">
          <div class="panel-top"><div><span class="micro">EXECUTION AUDIT</span><h3>成交偏差与纪律记录</h3></div></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>日期</th><th>方向 / 标的</th><th>成交价</th><th>参考价</th><th>成交偏差</th><th>执行方式</th></tr></thead>
              <tbody>
                ${(rev.trade_audits || []).length ? rev.trade_audits.map((t) => `
                  <tr>
                    <td>${esc(t.trade_date)}</td>
                    <td><span class="ptms-badge ${t.side === 'BUY' ? 'green' : 'red'}">${t.side === 'BUY' ? '买' : '卖'}</span> ${esc(t.name)}<br><small>${fmtNum(t.quantity, 0)}股</small></td>
                    <td>${fmtNum(t.fill_price, 4)}</td>
                    <td>${t.ref_price != null ? fmtNum(t.ref_price, 4) : '—'}</td>
                    <td class="${t.slippage_bp != null && t.slippage_bp > 15 ? 'text-red' : ''}">${t.slippage_bp != null ? `${fmtNum(t.slippage_bp, 1)} 基点` : '—'}</td>
                    <td><span class="ptms-badge ${t.discipline_tag === 'FOLLOW' || t.discipline_tag === 'RISK_CUT' ? 'green' : 'amber'}">${esc(DISCIPLINE_LABELS[t.discipline_tag] || t.discipline_tag)}</span></td>
                  </tr>
                `).join('') : '<tr><td colspan="6">暂无买卖成交记录</td></tr>'}
              </tbody>
            </table>
          </div>
        </article>
      </div>
    `;
    drawReviewChart(rev.twr_curve, rev.benchmark_curve);
  }

  function renderCurrentPtmsView(view) {
    ptms.currentView = view || ptms.currentView;
    const livePanel = $('live-panel');
    if (livePanel) {
      livePanel.hidden = ptms.currentView === 'portfolio' || ptms.currentView === 'indicators' || ptms.currentView === 'review';
    }
    if (ptms.currentView === 'desk') renderDesk();
    if (ptms.currentView === 'portfolio') renderPortfolio();
    if (ptms.currentView === 'indicators') renderIndicators();
    if (ptms.currentView === 'review') renderReview();
  }

  async function loadPtmsState() {
    if (!isOnlineHttp() || ptms.loading) return;
    ptms.loading = true;
    try {
      ptms.stateData = await apiGet('/api/ptms/state');
      ptms.error = '';
      if (!ptms.stateData.accounts[ptms.accountId]) ptms.accountId = 'CN_MAIN';
    } catch (err) {
      ptms.error = String(err.message || err);
    } finally {
      ptms.loading = false;
      renderCurrentPtmsView();
    }
  }

  async function loadReview(accountId) {
    if (!isOnlineHttp()) return;
    try {
      const data = await apiGet(`/api/ptms/review?account_id=${encodeURIComponent(accountId)}`);
      ptms.reviewData = data;
      ptms.reviewAccountId = accountId;
      if (ptms.currentView === 'review') renderReview();
    } catch (err) {
      ptms.error = String(err.message || err);
    }
  }

  function openModal(title, bodyHtml, onSubmit) {
    const host = $('ptms-modal-host');
    if (!host) return;
    host.innerHTML = `
      <div class="ptms-modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <div class="ptms-modal-head">
          <h3>${esc(title)}</h3>
          <button type="button" class="ptms-modal-close" data-ptms-close="1" aria-label="关闭">×</button>
        </div>
        <div id="ptms-modal-error" class="ptms-alert error" hidden></div>
        <form id="ptms-modal-form">${bodyHtml}</form>
      </div>
    `;
    host.hidden = false;
    const form = $('ptms-modal-form');
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const errBox = $('ptms-modal-error');
      const btn = form.querySelector('button[type="submit"]');
      if (errBox) errBox.hidden = true;
      if (btn) btn.disabled = true;
      try {
        await onSubmit(form);
        closeModal();
      } catch (err) {
        if (errBox) {
          errBox.textContent = String(err.message || err);
          errBox.hidden = false;
        }
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

  function closeModal() {
    const host = $('ptms-modal-host');
    if (!host) return;
    host.hidden = true;
    host.innerHTML = '';
  }

  function openInitModal(bundle) {
    const { account, portfolio } = bundle;
    const td = currentTodayDate(account.market);
    const curLabel = CURRENCY_NAMES[account.currency] || account.currency;
    const rows = portfolio.positions.map((p) => `
      <tr>
        <td><strong>${esc(p.name)}</strong> (${esc(p.symbol)})</td>
        <td><input type="number" step="any" min="0" name="qty_${esc(p.symbol)}" value="0" /></td>
        <td><input type="number" step="any" min="0" name="px_${esc(p.symbol)}" value="${p.mark_price != null ? esc(p.mark_price) : '1.0'}" /></td>
      </tr>
    `).join('');
    openModal(`初始化持仓 · ${accountName(account.account_id)}（${curLabel}）`, `
      <div class="ptms-form-grid">
        <div class="ptms-field"><label>建账日期</label><input type="date" name="trade_date" value="${esc(td)}" required /></div>
        <div class="ptms-field"><label>初始可用现金（${esc(curLabel)}）</label><input type="number" step="any" min="0" name="cash" value="100000" required /></div>
        <div class="ptms-field full">
          <label>已有持仓（若目前全仓现金可全部保持 0）</label>
          <div class="table-wrap"><table><thead><tr><th>标的</th><th>持有股数</th><th>持仓成本价</th></tr></thead><tbody>${rows}</tbody></table></div>
        </div>
        <div class="ptms-field full"><label>备注</label><input name="note" value="账户初始建账" /></div>
      </div>
      <div class="ptms-modal-foot">
        <button type="button" class="ghost-button" data-ptms-close="1">取消</button>
        <button type="submit" class="solid-button">确认初始化</button>
      </div>
    `, async (form) => {
      const fd = new FormData(form);
      const positions = portfolio.positions.map((p) => ({
        symbol: p.symbol,
        quantity: Number(fd.get(`qty_${p.symbol}`) || 0),
        price: Number(fd.get(`px_${p.symbol}`) || 0),
      })).filter((x) => x.quantity > 0);
      const res = await apiPost('/api/ptms/ledger/init', {
        account_id: account.account_id,
        trade_date: fd.get('trade_date'),
        cash: Number(fd.get('cash') || 0),
        positions,
        note: fd.get('note'),
      });
      ptms.stateData = res.state;
      ptms.reviewAccountId = null;
      renderCurrentPtmsView();
    });
  }

  function openEventModal(bundle) {
    const { account, portfolio } = bundle;
    const td = currentTodayDate(account.market);
    const symOpts = portfolio.positions.map((p) => `<option value="${esc(p.symbol)}">${esc(p.name)} (${esc(p.symbol)})</option>`).join('');
    openModal(`记一笔交易 / 资金变动 · ${accountName(account.account_id)}`, `
      <div class="ptms-form-grid">
        <div class="ptms-field">
          <label>记录类型</label>
          <select name="event_type">
            <option value="BUY">买入成交</option>
            <option value="SELL">卖出成交</option>
            <option value="CASH_IN">转入资金</option>
            <option value="CASH_OUT">转出资金</option>
            <option value="DIVIDEND">分红派息</option>
          </select>
        </div>
        <div class="ptms-field"><label>发生日期</label><input type="date" name="trade_date" value="${esc(td)}" required /></div>
        <div class="ptms-field"><label>标的（资金转入/转出请留空）</label><select name="symbol"><option value="">— 无 / 仅资金变动 —</option>${symOpts}</select></div>
        <div class="ptms-field"><label>执行方式</label><select name="discipline_tag">${Object.entries(DISCIPLINE_LABELS).map(([k, v]) => `<option value="${k}" ${k === 'MANUAL_OVERRIDE' ? 'selected' : ''}>${v}</option>`).join('')}</select></div>
        <div class="ptms-field"><label>成交股数（买卖时填写）</label><input type="number" step="any" min="0" name="quantity" value="0" /></div>
        <div class="ptms-field"><label>成交单价（买卖时填写）</label><input type="number" step="any" min="0" name="price" value="0" /></div>
        <div class="ptms-field"><label>手续费</label><input type="number" step="any" min="0" name="fee" value="0" /></div>
        <div class="ptms-field"><label>金额（出入金 / 分红时填写）</label><input type="number" step="any" min="0" name="amount" value="0" /></div>
        <div class="ptms-field full"><label>备注</label><input name="note" placeholder="手动交易或资金划转说明" /></div>
      </div>
      <div class="ptms-modal-foot">
        <button type="button" class="ghost-button" data-ptms-close="1">取消</button>
        <button type="submit" class="solid-button">保存记录</button>
      </div>
    `, async (form) => {
      const fd = new FormData(form);
      const et = fd.get('event_type');
      const res = await apiPost('/api/ptms/ledger/event', {
        account_id: account.account_id,
        event_type: et,
        trade_date: fd.get('trade_date'),
        symbol: fd.get('symbol') || null,
        quantity: Number(fd.get('quantity') || 0),
        price: Number(fd.get('price') || 0),
        fee: Number(fd.get('fee') || 0),
        amount: Number(fd.get('amount') || 0),
        discipline_tag: fd.get('discipline_tag'),
        note: fd.get('note'),
      });
      ptms.stateData = res.state;
      ptms.reviewAccountId = null;
      renderCurrentPtmsView();
    });
  }

  function openReconcileModal(bundle) {
    const { account, portfolio } = bundle;
    const td = currentTodayDate(account.market);
    const curLabel = CURRENCY_NAMES[account.currency] || account.currency;
    const rows = portfolio.positions.map((p) => `
      <tr>
        <td><strong>${esc(p.name)}</strong> (${esc(p.symbol)})</td>
        <td>${fmtNum(p.shares, 2)}</td>
        <td><input type="number" step="any" min="0" name="sh_${esc(p.symbol)}" value="${p.shares}" required /></td>
      </tr>
    `).join('');
    openModal(`核对券商真实持仓 · ${accountName(account.account_id)}`, `
      <div class="ptms-alert warn">直接填写券商软件里的真实可用现金和实际持仓股数，系统会自动补平差额且不扭曲历史收益率。</div>
      <div class="ptms-form-grid">
        <div class="ptms-field"><label>核对日期</label><input type="date" name="trade_date" value="${esc(td)}" required /></div>
        <div class="ptms-field"><label>券商真实可用现金（${esc(curLabel)}）</label><input type="number" step="any" min="0" name="target_cash" value="${portfolio.cash}" required /></div>
        <div class="ptms-field full"><div class="table-wrap"><table><thead><tr><th>标的</th><th>当前账面股数</th><th>券商实际股数</th></tr></thead><tbody>${rows}</tbody></table></div></div>
        <div class="ptms-field full"><label>校准说明</label><input name="note" value="与券商实际持仓核对一致" /></div>
      </div>
      <div class="ptms-modal-foot">
        <button type="button" class="ghost-button" data-ptms-close="1">取消</button>
        <button type="submit" class="solid-button">确认对齐</button>
      </div>
    `, async (form) => {
      const fd = new FormData(form);
      const targetShares = {};
      for (const p of portfolio.positions) targetShares[p.symbol] = Number(fd.get(`sh_${p.symbol}`) || 0);
      const res = await apiPost('/api/ptms/ledger/reconcile', {
        account_id: account.account_id,
        trade_date: fd.get('trade_date'),
        target_cash: Number(fd.get('target_cash') || 0),
        target_shares: targetShares,
        note: fd.get('note'),
      });
      ptms.stateData = res.state;
      ptms.reviewAccountId = null;
      renderCurrentPtmsView();
    });
  }

  function openConfigModal(bundle) {
    const { account, portfolio } = bundle;
    const floorRows = portfolio.positions.filter((p) => !p.is_cash_etf).map((p) => `
      <div class="ptms-field">
        <label>${esc(p.name)} (${esc(p.symbol)}) 长期底仓下限 (%)</label>
        <input type="number" step="1" min="0" max="100" name="floor_${esc(p.symbol)}" value="${fmtNum((account.core_floors[p.symbol] || 0) * 100, 0)}" />
      </div>
    `).join('');
    openModal(`账户策略与风控设置 · ${accountName(account.account_id)}`, `
      <div class="ptms-form-grid">
        <div class="ptms-field">
          <label>主执行策略</label>
          <select name="strategy_version">
            <option value="baseline" ${account.strategy_version === 'baseline' ? 'selected' : ''}>V18 稳健基准</option>
            <option value="advisory" ${account.strategy_version === 'advisory' ? 'selected' : ''}>V20 双重风控</option>
            <option value="blend25" ${account.strategy_version === 'blend25' ? 'selected' : ''}>25% 混合增强</option>
          </select>
        </div>
        <div class="ptms-field"><label>周频调仓偏离阈值 (%)</label><input type="number" step="1" min="0" max="40" name="deadband_pct" value="${fmtNum(account.rebalance_deadband * 100, 0)}" required /></div>
        ${floorRows}
      </div>
      <div class="ptms-modal-foot">
        <button type="button" class="ghost-button" data-ptms-close="1">取消</button>
        <button type="submit" class="solid-button">保存设置</button>
      </div>
    `, async (form) => {
      const fd = new FormData(form);
      const floors = {};
      for (const p of portfolio.positions) {
        if (p.is_cash_etf) continue;
        const v = Number(fd.get(`floor_${p.symbol}`) || 0) / 100;
        if (v > 0) floors[p.symbol] = v;
      }
      const res = await apiPost('/api/ptms/account/config', {
        account_id: account.account_id,
        strategy_version: fd.get('strategy_version'),
        rebalance_deadband: Number(fd.get('deadband_pct') || 0) / 100,
        core_floors: floors,
      });
      ptms.stateData = res.state;
      renderCurrentPtmsView();
    });
  }

  function openExecuteTicketModal(bundle, ticket) {
    const { account } = bundle;
    const td = currentTodayDate(account.market);
    const orders = (ticket.snapshot || {}).orders || [];
    if (!orders.length) {
      openModal(`计划 #${ticket.decision_id} 无需下单`, '<p>该调仓计划没有具体的买卖笔数，可直接标记为暂不执行。</p><div class="ptms-modal-foot"><button type="button" class="ghost-button" data-ptms-close="1">关闭</button></div>', async () => {});
      return;
    }
    const defaultTag = ticket.source === 'INTRADAY_STOP' ? 'RISK_CUT' : 'FOLLOW';
    const rows = orders.map((o, idx) => `
      <tr>
        <td><span class="ptms-badge ${o.side === 'BUY' ? 'green' : 'red'}">${o.side === 'BUY' ? '买' : '卖'}</span> ${esc(o.name)}</td>
        <td><input type="number" step="any" min="0" name="qty_${idx}" value="${o.quantity}" /></td>
        <td><input type="number" step="any" min="0" name="px_${idx}" value="${o.ref_price}" /></td>
        <td><input type="number" step="any" min="0" name="fee_${idx}" value="${o.est_fee}" /></td>
      </tr>
    `).join('');
    openModal(`确认成交并记入账本 · 计划 #${ticket.decision_id}`, `
      <div class="ptms-alert ok">${esc(ticket.reason)}</div>
      <div class="ptms-form-grid">
        <div class="ptms-field"><label>实际成交日期</label><input type="date" name="trade_date" value="${esc(td)}" required /></div>
        <div class="ptms-field"><label>执行方式标记</label><select name="discipline_tag">${Object.entries(DISCIPLINE_LABELS).map(([k, v]) => `<option value="${k}" ${k === defaultTag ? 'selected' : ''}>${v}</option>`).join('')}</select></div>
        <div class="ptms-field full"><div class="table-wrap"><table><thead><tr><th>方向 / 标的</th><th>实际成交股数</th><th>实际成交均价</th><th>实际费用</th></tr></thead><tbody>${rows}</tbody></table></div></div>
        <div class="ptms-field full"><label>执行备注</label><input name="review_note" value="按计划 #${ticket.decision_id} 确认成交" /></div>
      </div>
      <div class="ptms-modal-foot">
        <button type="button" class="ghost-button" data-ptms-close="1">取消</button>
        <button type="submit" class="solid-button">确认成交入账</button>
      </div>
    `, async (form) => {
      const fd = new FormData(form);
      const fills = orders.map((o, idx) => ({
        symbol: o.symbol,
        side: o.side,
        quantity: Number(fd.get(`qty_${idx}`) || 0),
        price: Number(fd.get(`px_${idx}`) || 0),
        fee: Number(fd.get(`fee_${idx}`) || 0),
      })).filter((f) => f.quantity > 0);
      const res = await apiPost('/api/ptms/decisions/execute', {
        account_id: account.account_id,
        decision_id: ticket.decision_id,
        trade_date: fd.get('trade_date'),
        fills,
        discipline_tag: fd.get('discipline_tag'),
        review_note: fd.get('review_note'),
      });
      ptms.stateData = res.state;
      ptms.reviewAccountId = null;
      renderCurrentPtmsView();
    });
  }

  function openSkipTicketModal(bundle, ticket) {
    openModal(`暂不执行计划 #${ticket.decision_id}`, `
      <div class="ptms-form-grid">
        <div class="ptms-field full"><label>跳过理由（便于后续复盘核查）</label><textarea name="review_note" required placeholder="例如：盘中流动性异常 / 决定推迟到收盘前再观察"></textarea></div>
      </div>
      <div class="ptms-modal-foot">
        <button type="button" class="ghost-button" data-ptms-close="1">取消</button>
        <button type="submit" class="ptms-danger-btn">确认暂不执行</button>
      </div>
    `, async (form) => {
      const fd = new FormData(form);
      const res = await apiPost('/api/ptms/decisions/skip', {
        account_id: bundle.account.account_id,
        decision_id: ticket.decision_id,
        review_note: fd.get('review_note'),
      });
      ptms.stateData = res.state;
      renderCurrentPtmsView();
    });
  }

  function openResetStopModal(bundle, symbol) {
    openModal(`重置盘中防守状态 · ${symbol}`, `
      <div class="ptms-alert warn">提示：重置后将清空该标的当前冻结的移动止损高水位线及减仓进度，请填写原因留档。</div>
      <div class="ptms-form-grid">
        <div class="ptms-field full"><label>重置原因</label><input name="reason" required placeholder="例如：已人工确认重新评估趋势，重置追踪防线" /></div>
        <div class="ptms-field full"><label><input type="checkbox" name="clear_cooldown" checked style="width:auto;margin-right:6px" />同时解除 3 个交易日加仓冷却限制</label></div>
      </div>
      <div class="ptms-modal-foot">
        <button type="button" class="ghost-button" data-ptms-close="1">取消</button>
        <button type="submit" class="ptms-danger-btn">确认重置</button>
      </div>
    `, async (form) => {
      const fd = new FormData(form);
      const res = await apiPost('/api/ptms/stops/reset', {
        account_id: bundle.account.account_id,
        symbol,
        reason: fd.get('reason'),
        clear_cooldown: fd.get('clear_cooldown') === 'on',
      });
      ptms.stateData = res.state;
      renderCurrentPtmsView();
    });
  }

  async function handleAction(target) {
    const accountTab = target.closest('[data-ptms-account]');
    if (accountTab) {
      ptms.accountId = accountTab.dataset.ptmsAccount;
      writeStorage(STORAGE_KEY, ptms.accountId);
      renderCurrentPtmsView();
      if (ptms.currentView === 'review') loadReview(ptms.accountId);
      return;
    }
    if (target.closest('[data-ptms-close]')) {
      closeModal();
      return;
    }
    const btn = target.closest('[data-ptms-action]');
    if (!btn) return;
    const action = btn.dataset.ptmsAction;
    const bundle = activeBundle();
    if (action === 'reload-state') {
      await loadPtmsState();
      if (ptms.currentView === 'review') await loadReview(ptms.accountId);
      return;
    }
    if (!bundle) return;
    if (action === 'open-init') openInitModal(bundle);
    else if (action === 'open-event') openEventModal(bundle);
    else if (action === 'open-reconcile') openReconcileModal(bundle);
    else if (action === 'open-config') openConfigModal(bundle);
    else if (action === 'open-reset-stop') openResetStopModal(bundle, btn.dataset.symbol);
    else if (action === 'open-execute-ticket') {
      const did = Number(btn.dataset.decisionId);
      const t = (bundle.tickets || []).find((x) => x.decision_id === did);
      if (t) openExecuteTicketModal(bundle, t);
    } else if (action === 'open-skip-ticket') {
      const did = Number(btn.dataset.decisionId);
      const t = (bundle.tickets || []).find((x) => x.decision_id === did);
      if (t) openSkipTicketModal(bundle, t);
    } else if (action === 'create-weekly-ticket') {
      const res = await apiPost('/api/ptms/decisions/create', {
        account_id: bundle.account.account_id,
        source: 'WEEKLY_REBALANCE',
      });
      ptms.stateData = res.state;
      renderCurrentPtmsView();
    } else if (action === 'execute-current-plan') {
      let ticket = (bundle.tickets || []).find(
        (t) => t.status === 'PENDING' && t.source === 'WEEKLY_REBALANCE' && t.signal_cutoff === bundle.plan.signal_cutoff
      );
      if (!ticket) {
        const res = await apiPost('/api/ptms/decisions/create', {
          account_id: bundle.account.account_id,
          source: 'WEEKLY_REBALANCE',
        });
        ptms.stateData = res.state;
        ticket = res.ticket;
        renderCurrentPtmsView();
      }
      openExecuteTicketModal(activeBundle(), ticket);
    }
  }

  document.addEventListener('click', (ev) => {
    handleAction(ev.target).catch((err) => {
      ptms.error = String(err.message || err);
      renderCurrentPtmsView();
    });
  });

  document.addEventListener('change', (ev) => {
    if (ev.target && ev.target.id === 'ptms-ledger-filter') {
      ptms.ledgerFilter = ev.target.value;
      renderPortfolio();
    }
    if (ev.target && ev.target.id === 'ptms-note-type') {
      const keyEl = $('ptms-note-key');
      if (!keyEl) return;
      const v = ev.target.value;
      if (v === 'WEEKLY') keyEl.value = currentIsoWeek();
      else if (v === 'DAILY') keyEl.value = currentTodayDate(activeBundle()?.account?.market || 'CN');
      else keyEl.value = currentTodayDate('CN').slice(0, 7);
    }
  });

  document.addEventListener('submit', async (ev) => {
    if (ev.target && ev.target.id === 'ptms-note-form') {
      ev.preventDefault();
      try {
        const res = await apiPost('/api/ptms/review/note', {
          account_id: ptms.accountId,
          period_type: $('ptms-note-type').value,
          period_key: $('ptms-note-key').value.trim(),
          reflection: $('ptms-note-text').value.trim(),
        });
        ptms.reviewData = res.review;
        ptms.reviewAccountId = ptms.accountId;
        renderReview();
      } catch (err) {
        ptms.error = String(err.message || err);
        renderReview();
      }
    }
  });

  window.__ptmsRender = function (view) {
    renderCurrentPtmsView(view);
  };

  window.__ptmsOnLive = function () {
    loadPtmsState();
  };

  if (isOnlineHttp() && typeof window.__quantlabNavTo === 'function' && !ptms.switchedDefault) {
    ptms.switchedDefault = true;
    window.__quantlabNavTo('desk');
  }
  loadPtmsState();
})();
