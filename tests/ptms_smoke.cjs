// Smoke test for v21.js + ptms.js across T1-T4 visibility, live-panel hiding, and clean product copy.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'web/template.html'), 'utf8');
const snapshot = JSON.parse(fs.readFileSync(path.join(root, 'research_outputs/V22_dashboard_snapshot.json'), 'utf8'));

class Element {
  constructor(id = '') {
    this.id = id;
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.innerHTML = '';
    this.textContent = '';
    this.value = '';
    this.hidden = false;
    this.children = [];
    this.clientWidth = 860;
    this.clientHeight = 290;
    this.offsetWidth = 100;
    this.offsetHeight = 80;
    const classes = new Set();
    this.classList = {
      contains: (x) => classes.has(x),
      toggle: (x, yes) => (yes ? classes.add(x) : classes.delete(x)),
    };
  }
  addEventListener(name, cb) { this.listeners[name] = cb; }
  setAttribute(name, val) { this[name] = val; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelector() { return this.body || (this.body = new Element('tbody')); }
  getBoundingClientRect() { return { left: 0, top: 0, width: 860, height: 290 }; }
  closest() { return null; }
}

const elements = new Map([...template.matchAll(/\bid="([^"]+)"/g)].map((x) => [x[1], new Element(x[1])]));
const get = (id) => {
  if (!elements.has(id) && id.startsWith('ptms-')) elements.set(id, new Element(id));
  assert(elements.has(id), 'Missing template id: ' + id);
  return elements.get(id);
};
get('v21-data').textContent = JSON.stringify(snapshot);
for (const [id, val] of Object.entries({ 'data-mode': 'live', 'guard-symbol': '515180.SH', 'guard-weight': '44', 'guard-floor-mode': 'keep' })) {
  get(id).value = val;
}
get('view-overview').classList.toggle('active', true);

const views = [...elements.values()].filter((x) => x.id.startsWith('view-'));
const allNavViews = ['desk', 'portfolio', 'indicators', 'review', 'overview', 'cn', 'us', 'cross', 'intraday', 'risk', 'trades'];
const nav = allNavViews.map((v) => { const el = new Element(); el.dataset.view = v; return el; });

const document = {
  hidden: false,
  getElementById: get,
  addEventListener() {},
  createElementNS: () => new Element(),
  querySelector: (sel) => get(sel.slice(1)),
  querySelectorAll: (sel) => (sel === '.view' ? views : sel === '.nav-item' ? nav : []),
};

const samplePtmsState = {
  schema: 1,
  server_time: '2026-11-22T03:00:00Z',
  indicators: {
    CN: {
      source: 'research_fallback',
      asof_week_start: '2026-11-16',
      tech: { close: 2.23, temperature: '冻', stage: 0, ma_fast: 2.51, ma_slow: 3.43, mom_fast: -0.27, mom_slow: -0.43, momentum: -0.34, vs_red_6w: -0.26, vol: 0.29, dd: -0.47, reentry_state: 'off', probe_signal: false, confirm_signal: false },
      red: { close: 1.368, temperature: '温', stage: 4, ma_fast: 1.366, ma_slow: 1.348, mom_fast: -0.01, mom_slow: 0.029, vol: 0.123 },
      defensive: { bond_eligible: true, bond_mom_fast: 0.011, bond_target: 0.35, gold_eligible: false, gold_mom_fast: -0.22, gold_target: 0 },
      portfolio: { estimated_vol: 0.07, risk_multiplier: 1.0, market_liquidity_ratio: 0.94, market_turn_100m: 13508, tech_share_pct: 5.2, red_share_pct: 3.1, us_risk_state_lagged: '熊市趋势', cross_market_reason: 'US熊市：限制新增创业板仓', v10_reason: '国债/黄金不合格', v14_warning: true },
    },
    US: {
      source: 'research_fallback',
      asof_week_start: '2026-11-16',
      risk_state: '严重风险',
      trend_decision: '多周期趋势防守',
      trend_score: 0,
      realized_vol: 0.2788,
      vol_multiplier: 0.86,
      qqq_close: 608.45,
      qqq_10w: 701.5,
      qqq_20w: 705.0,
      qqq_30w: 708.0,
      qqq_dist_10w_pct: -13.2,
      qqq_dist_20w_pct: -13.7,
      qqq_dist_30w_pct: -14.1,
      spy_close: 689.81,
      spy_13w: 756.7,
      spy_30w: 749.7,
      smh_relative_4w: -0.12,
      rsp_relative_4w: 0.055,
      us_bear: true,
      us_shock: true,
      us_warning: true,
    },
  },
  accounts: {
    CN_MAIN: {
      account: { account_id: 'CN_MAIN', market: 'CN', currency: 'CNY', strategy_version: 'baseline', rebalance_deadband: 0.15, core_floors: { '515180.SH': 0.2 } },
      portfolio: {
        initialized: true, event_count: 2, cash: 21000, cash_weight: 0.21, holdings_value: 79000, total_equity: 100000,
        unrealized_pnl: 1200, realized_pnl: 300, total_pnl: 1500, total_fees: 25, total_dividends: 0, net_capital_in: 98500,
        positions: [
          { symbol: '159915.SZ', bucket: 'tech', name: '创业板ETF', lot_size: 100, t_plus: 1, is_cash_etf: false, shares: 0, sellable_shares: 0, today_bought_shares: 0, avg_cost: 0, mark_price: 2.23, price_source: 'cost_fallback', market_value: 0, actual_weight: 0, core_floor: 0, unrealized_pnl: 0, realized_pnl: 0 },
          { symbol: '515180.SH', bucket: 'red', name: '红利ETF', lot_size: 100, t_plus: 1, is_cash_etf: false, shares: 30000, sellable_shares: 30000, today_bought_shares: 0, avg_cost: 1.35, mark_price: 1.40, price_source: 'quote', market_value: 42000, actual_weight: 0.42, core_floor: 0.2, unrealized_pnl: 1500, realized_pnl: 0 },
          { symbol: '511260.SH', bucket: 'bond', name: '10年国债ETF', lot_size: 100, t_plus: 0, is_cash_etf: false, shares: 270, sellable_shares: 270, today_bought_shares: 0, avg_cost: 136, mark_price: 137, price_source: 'quote', market_value: 37000, actual_weight: 0.37, core_floor: 0, unrealized_pnl: 270, realized_pnl: 0 },
          { symbol: '518880.SH', bucket: 'gold', name: '黄金ETF', lot_size: 100, t_plus: 0, is_cash_etf: false, shares: 0, sellable_shares: 0, today_bought_shares: 0, avg_cost: 0, mark_price: 7.28, price_source: 'quote', market_value: 0, actual_weight: 0, core_floor: 0, unrealized_pnl: 0, realized_pnl: 0 },
          { symbol: '511880.SH', bucket: 'money', name: '货币ETF', lot_size: 100, t_plus: 0, is_cash_etf: true, shares: 0, sellable_shares: 0, today_bought_shares: 0, avg_cost: 0, mark_price: 100.9, price_source: 'quote', market_value: 0, actual_weight: 0, core_floor: 0, unrealized_pnl: 0, realized_pnl: 0 },
        ],
      },
      stop_states: {
        '515180.SH': { symbol: '515180.SH', name: '红利ETF', session_date: '2026-11-20', status_note: '正在监控战术仓位', active_line: 1.36, candidate_line: 1.36, raw_trend_line: 1.35, shock_line: 1.36, prior_close: 1.40, daily_sigma: 0.008, peak: 1.42, ma: 1.38, last_price: 1.40, distance_pct: 2.94, below_count: 0, stage: 0, pending_stage: 0, cooldown_active: false, cooldown_remaining_days: 0, actual_weight: 0.42, core_floor: 0.2 },
      },
      plan: {
        verdict: 'ACTION_REBALANCE', verdict_label: '建议按周频调仓', verdict_reason: '当前仓位偏离已达到调仓阈值', should_execute: true,
        signal_cutoff: '2026-11-20', signal_origin: 'online_recomputed_simulation', turnover: 0.16, deadband: 0.15, total_est_fee: 2.1, projected_cash_after: 800, cooldown_clips: [],
        weights_comparison: [{ symbol: '515180.SH', bucket: 'red', name: '红利ETF', actual_weight: 0.42, effective_actual_weight: 0.42, weekly_target: 0.44, effective_target: 0.44, drift: 0.02, core_floor: 0.2, cooldown_active: false, pending_stage: 0 }],
        orders: [{ symbol: '511880.SH', name: '货币ETF', side: 'BUY', quantity: 200, lot_size: 100, sellable_shares: 0, ref_price: 100.9, est_amount: 20180, est_fee: 2.02, t_plus: 0, reason: '配置货币ETF' }],
      },
      tickets: [],
      events: [{ event_id: 1, trade_date: '2026-11-20', event_time: '2026-11-20T15:00:00+08:00', event_type: 'INIT_POSITION', symbol: '515180.SH', quantity: 30000, price: 1.35, fee: 0, cash_delta: 0, discipline_tag: 'INIT', note: '建账' }],
    },
  },
};

const sampleReview = {
  account_id: 'CN_MAIN',
  market: 'CN',
  currency: 'CNY',
  strategy_version: 'baseline',
  kpis: { start_date: '2026-11-20', as_of_date: '2026-11-22', twr_return_pct: 1.5, model_return_pct: 1.2, tracking_gap_pp: 0.3, real_max_dd_pct: -0.4, model_max_dd_pct: -0.6, total_fees: 25, total_dividends: 0 },
  discipline: { trade_discipline_rate_pct: 100.0, avg_slippage_bp: 2.5, ticket_follow_rate_pct: 100.0 },
  twr_curve: [{ d: '2026-11-20', nav: 1.0 }, { d: '2026-11-22', nav: 1.015 }],
  benchmark_curve: [{ d: '2026-11-20', nav: 1.0 }, { d: '2026-11-22', nav: 1.012 }],
  trade_audits: [],
  notes: [{ review_id: 1, period_type: 'WEEKLY', period_key: '2026-W47', reflection: '烟雾测试笔记', created_at: '2026-11-22T11:00:00Z' }],
};

const storage = new Map();
const context = {
  document,
  location: { protocol: 'http:' },
  window: {
    location: { protocol: 'http:' },
    localStorage: { getItem: (k) => storage.get(k) || null, setItem: (k, v) => storage.set(k, String(v)) },
    scrollTo() {},
    addEventListener() {},
    innerWidth: 1200,
    innerHeight: 900,
  },
  requestAnimationFrame: (cb) => cb(),
  setTimeout: () => 1,
  clearTimeout() {},
  setInterval() {},
  AbortController,
  Blob,
  URL,
  console,
  fetch: async (url) => {
    if (url.startsWith('/api/ptms/state')) return { ok: true, json: async () => structuredClone(samplePtmsState) };
    if (url.startsWith('/api/ptms/review')) return { ok: true, json: async () => structuredClone(sampleReview) };
    if (url.startsWith('/api/dashboard')) {
      return { ok: true, json: async () => ({ schema: 1, status: { state: 'connected', provider: 'stub', errors: {}, quote_interval_seconds: 30, history_interval_seconds: 900 }, market_status: {}, live: { models: snapshot.models, signals: snapshot.signals, quotes: {}, intraday: {}, strategy_status: {} } }) };
    }
    throw new Error('unexpected url: ' + url);
  },
};
context.window.document = document;
context.localStorage = context.window.localStorage;

vm.runInNewContext(fs.readFileSync(path.join(root, 'web/v21.js'), 'utf8'), context);
vm.runInNewContext(fs.readFileSync(path.join(root, 'web/ptms.js'), 'utf8'), context);

const stripTagsAndAttrs = (html) => html.replace(/<[^>]+>/g, ' ');
const settle = () => new Promise((resolve) => setImmediate(resolve));

(async () => {
  await settle();
  await settle();
  // T1 shows live-panel and renders product copy
  assert.equal(get('live-panel').hidden, false);
  assert.match(get('ptms-desk-root').innerHTML, /建议按周频调仓/);
  assert.match(get('ptms-desk-root').innerHTML, /货币ETF/);

  // T2 hides live-panel
  nav[1].listeners.click();
  await settle();
  assert.equal(get('live-panel').hidden, true);
  assert.match(get('ptms-portfolio-root').innerHTML, /红利ETF/);
  assert.match(get('ptms-portfolio-root').innerHTML, /按成本估值/);

  // T3 hides live-panel
  nav[2].listeners.click();
  await settle();
  assert.equal(get('live-panel').hidden, true);
  assert.match(get('ptms-indicators-root').innerHTML, /创业板趋势温度与动量/);

  // T4 hides live-panel
  nav[3].listeners.click();
  await settle();
  await settle();
  assert.equal(get('live-panel').hidden, true);
  assert.match(get('ptms-review-root').innerHTML, /烟雾测试笔记/);
  assert.match(get('ptms-review-root').innerHTML, /周度复盘/);
  assert.match(get('ptms-review-chart').innerHTML, /<svg/);

  // Switching back to T1 or research view restores live-panel
  nav[0].listeners.click();
  assert.equal(get('live-panel').hidden, false);
  nav[4].listeners.click();
  assert.equal(get('live-panel').hidden, false);

  // Verify no raw engineering enums/jargon leak into visible T1-T4 text
  const visibleAll = [
    get('ptms-desk-root').innerHTML,
    get('ptms-portfolio-root').innerHTML,
    get('ptms-indicators-root').innerHTML,
    get('ptms-review-root').innerHTML,
  ].map(stripTagsAndAttrs).join('\n');

  for (const forbidden of [
    'ACTION_REBALANCE',
    'HOLD_WITHIN_DEADBAND',
    'cost_fallback',
    '(CASH_IN)',
    '[WEEKLY]',
    'Stage 0',
    'RECONCILE',
    'CN_MAIN',
    'US_MAIN',
    '日σ',
    '未熊',
  ]) {
    assert.ok(!visibleAll.includes(forbidden), 'Forbidden jargon leaked to UI text: ' + forbidden);
  }

  console.log('PTMS smoke passed: T1 shows live-panel, T2-T4 hide live-panel, product copy verified.');
})().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
