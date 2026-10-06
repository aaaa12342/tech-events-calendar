const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
const code = scripts.at(-1)[1];
function page(data, ok = true) {
  const elements = {};
  const pills = ['全部', '竞赛'].map(cat => ({ dataset: { cat }, classList: { toggle() {} }, setAttribute() {} }));
  const document = { addEventListener() {}, getElementById(id) {
    return elements[id] ||= { innerHTML: '', textContent: '', value: '', checked: false,
      handlers: {}, attrs: {}, addEventListener(type, fn) { this.handlers[type] = fn; },
      setAttribute(key, value) { this.attrs[key] = value; }, querySelectorAll() { return pills; } };
  } };
  vm.runInNewContext(code, { document, EventStatus: require('../event-status.js'), EventView: require('../event-view.js'),
    localStorage: { getItem: () => '' }, fetch: async (_url, options) => {
      assert.equal(options.cache, 'no-cache');
      return { ok, status: ok ? 200 : 503, json: async () => data };
    }, Date, console });
  return elements;
}
function trigger(el, type, value, checked) {
  el.value = value; el.checked = checked;
  el.handlers[type].call(el);
}
async function main() {
  const data = { updated_at: '2020-01-01 08:00', errors: ['ai-pick: timeout'], events: [
    { id: '1', name: 'AI 北京活动', city: '北京', category: '竞赛', online: true, reg_deadline: '2099-01-01' },
    { id: '2', name: '开发者线下活动', city: '上海', category: '会议', online: false },
    { id: '3', name: '历史活动', city: '北京', category: '会议', end: '2020-01-01' }
  ] };
  const p = page(data);
  await new Promise(setImmediate);
  assert.match(p.count.textContent, /3 \/ 3/);
  assert.match(p['update-status'].innerHTML, /超过两天/);
  assert.match(p['update-status'].innerHTML, /AI 活动雷达/);
  trigger(p.search, 'input', 'ai 北京');
  assert.match(p.count.textContent, /1 \/ 3/);
  trigger(p['online-only'], 'change', '', true);
  trigger(p.registration, 'change', 'available');
  assert.match(p.list.innerHTML, /AI 北京活动/);
  trigger(p.search, 'input', '没有匹配');
  assert.match(p.list.innerHTML, /清除筛选/);
  p['reset-filters'].handlers.click();
  assert.match(p.count.textContent, /3 \/ 3/);
  assert.equal(p.search.value, '');
  assert.equal(p['online-only'].checked, false);
  trigger(p['hide-ended'], 'change', '', true);
  assert.match(p.count.textContent, /2 \/ 3/);
  const failure = page(data, false);
  await new Promise(setImmediate);
  assert.equal(failure.list.attrs['aria-busy'], 'false');
  assert.match(failure['update-status'].textContent, /加载失败/);
  assert.match(failure.count.textContent, /加载失败/);
  const invalid = page({});
  await new Promise(setImmediate);
  assert.match(invalid.count.textContent, /加载失败/);
  console.log('Page search, combined controls, reset, stale notices, and load errors passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
