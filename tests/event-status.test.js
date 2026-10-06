const assert = require('node:assert/strict');
const { evaluate } = require('../event-status.js');
const now = new Date('2026-10-06T12:00:00+08:00');
assert.deepEqual(evaluate({ start: '2026-10-06', end: '2026-12-01', reg_deadline: '2026-08-01' }, now),
  { activity: 'ongoing', registration: 'closed' });
assert.deepEqual(evaluate({ start: '2026-10-10', reg_deadline: '2026-10-01' }, now),
  { activity: 'todo', registration: 'closed' });
assert.deepEqual(evaluate({}, now), { activity: 'unknown', registration: 'unknown' });
assert.equal(evaluate({ start: '2026-10-06' }, new Date('2026-10-06T00:00:00+08:00')).activity, 'ongoing');
assert.equal(evaluate({ end: '2026-10-06' }, new Date('2026-10-06T23:59:59.999+08:00')).activity, 'unknown');
assert.equal(evaluate({ end: '2026-10-06' }, new Date('2026-10-07T00:00:00+08:00')).activity, 'done');
assert.equal(evaluate({ reg_deadline: '2026-10-06' }, now).registration, 'closing');
assert.equal(evaluate({ reg_deadline: '2026-10-06' }, new Date('2026-10-07T00:00:00+08:00')).registration, 'closed');
assert.equal(evaluate({ start: '2026-10-10', ended: true, reg_deadline: '2026-10-09' }, now).activity, 'todo');
assert.equal(evaluate({ ended: true, reg_deadline: '2026-10-09' }, now).registration, 'closed');
assert.equal(evaluate({ reg_deadline: '2026-11-01' }, now).registration, 'open');
assert.equal(evaluate({ start: 'invalid', end: 'invalid', reg_deadline: 'invalid' }, now).registration, 'unknown');

// Execute the actual page with a small DOM stub to catch integration errors.
const fs = require('node:fs');
const vm = require('node:vm');
const elements = {};
const document = { getElementById(id) {
  return elements[id] ||= { innerHTML: '', textContent: '', addEventListener() {}, setAttribute() {} };
}, addEventListener() {} };
const data = JSON.parse(fs.readFileSync(require('node:path').join(__dirname, '../data/hackathons.json'), 'utf8'));
const html = fs.readFileSync(require('node:path').join(__dirname, '../index.html'), 'utf8');
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
vm.runInNewContext(scripts[scripts.length - 1][1], {
  document, EventStatus: { evaluate }, EventView: require('../event-view.js'), localStorage: { getItem: () => '' },
  fetch: async () => ({ ok: true, json: async () => data }), Date, console
});
setImmediate(() => {
  assert.match(elements.list.innerHTML, /class="row/);
  assert.doesNotMatch(elements.count.textContent, /失败/);
  console.log('Status boundary cases and page rendering passed');
});
