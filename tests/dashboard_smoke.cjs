// Execute the shipped client with a small in-memory DOM. No browser or network.
// Covers all seven render paths, API failure, successful update, and mode switch.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'web/template.html'), 'utf8');
const snapshot = JSON.parse(fs.readFileSync(path.join(root, 'research_outputs/V22_dashboard_snapshot.json'), 'utf8'));
class Element {
  constructor(id = '') {
    this.id = id; this.dataset = {}; this.style = {}; this.listeners = {};
    this.innerHTML = ''; this.textContent = ''; this.value = ''; this.children = [];
    this.clientWidth = 800; this.clientHeight = 290; this.offsetWidth = 100; this.offsetHeight = 80;
    const classes = new Set();
    this.classList = { contains: x => classes.has(x), toggle: (x, yes) => yes ? classes.add(x) : classes.delete(x) };
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelector() { return this.body || (this.body = new Element('tbody')); }
  getBoundingClientRect() { return { left: 0, top: 0, width: 800, height: 290 }; }
}
const elements = new Map([...template.matchAll(/\bid="([^"]+)"/g)].map(x => [x[1], new Element(x[1])]));
const get = id => { assert(elements.has(id), 'Missing actual template id: ' + id); return elements.get(id); };
get('v21-data').textContent = JSON.stringify(snapshot);
for (const [id, value] of Object.entries({ 'data-mode': 'live', 'guard-symbol': '515180.SH', 'guard-weight': '44', 'guard-floor-mode': 'keep' })) get(id).value = value;
get('view-overview').classList.toggle('active', true);
const nav = Object.keys({ overview: 1, cn: 1, us: 1, cross: 1, intraday: 1, risk: 1, trades: 1 }).map(view => {
  const el = new Element(); el.dataset.view = view; return el;
});
const views = [...elements.values()].filter(x => x.id.startsWith('view-'));
const document = {
  hidden: false, getElementById: get, addEventListener() {},
  createElementNS: () => new Element(),
  querySelector: selector => get(selector.slice(1)),
  querySelectorAll: selector => selector === '.view' ? views : selector === '.nav-item' ? nav : []
};
let response = null;
const context = {
  document, location: { protocol: 'http:' },
  window: { scrollTo() {}, addEventListener() {}, innerWidth: 1200, innerHeight: 900 },
  requestAnimationFrame: callback => callback(),
  setTimeout: () => 1, clearTimeout() {}, setInterval() {},
  AbortController, Blob, URL, console,
  fetch: async (url, options) => {
    if (!response) throw new Error('offline fixture');
    return { ok: true, json: async () => structuredClone(response) };
  }
};
vm.runInNewContext(fs.readFileSync(path.join(root, 'web/v21.js'), 'utf8'), context);
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await settle();
  assert.match(get('live-message').textContent, /连接失败/);
  assert.match(get('overview-weights').innerHTML, /44%/);
  for (const button of nav) button.listeners.click(); // every real render branch
  const models = structuredClone(snapshot.models);
  models.CN.baseline.target = { tech: .5, red: 0, bond: .3, gold: 0, money: .2 };
  models.US.baseline.target = { qqq: .7, bil: .3 };
  for (const region of ['CN', 'US']) for (const model of Object.values(models[region])) {
    model.origin = 'online_recomputed_simulation'; model.cutoff = '2026-11-27';
  }
  response = { schema: 1, status: { state: 'connected', provider: 'fixture', errors: {}, quote_interval_seconds: 30, history_interval_seconds: 900 },
    market_status: {}, live: { models, signals: snapshot.signals, quotes: {}, intraday: {}, strategy_status: {} } };
  get('live-refresh').listeners.click();
  await settle();
  nav[0].listeners.click();
  assert.match(get('overview-weights').innerHTML, /50%/);
  assert.match(get('overview-feed').innerHTML, /2026-11-27/);
  assert.match(get('bt-reference').innerHTML, /requirements-open-source/);
  get('data-mode').value = 'research';
  get('data-mode').listeners.change();
  assert.match(get('overview-weights').innerHTML, /44%/);
  get('live-pause').listeners.click();
  assert.equal(get('live-pause').textContent, '恢复刷新');
  console.log('Dashboard smoke passed: 7 views, offline, live data update, historical mode, pause.');
})().catch(error => { console.error(error); process.exitCode = 1; });
