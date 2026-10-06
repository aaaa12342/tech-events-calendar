const assert = require('node:assert/strict');
const { filter, health } = require('../event-view.js');
const now = new Date('2026-10-06T12:00:00+08:00');
const events = [
  { id: 'open', name: 'AI 开发挑战', org: '社区', city: '上海', category: '竞赛', online: true, reg_deadline: '2026-11-01' },
  { id: 'closing', name: '机器人展会', org: '科技协会', city: '北京', venue: '会议中心', category: '展会', reg_deadline: '2026-10-07' },
  { id: 'closed', name: '长期 AI 比赛', city: '上海', category: '竞赛', start: '2026-08-01', end: '2026-12-01', reg_deadline: '2026-08-01' },
  { id: 'done', name: '历史活动', end: '2026-10-01' },
  { id: 'unknown', name: '日期待定活动' }
];
const ids = state => filter(events, state, now).map(e => e.id);
assert.deepEqual(ids({}), ['open', 'closing', 'closed', 'done', 'unknown']);
assert.deepEqual(ids({ query: '  aI   上海  ', cat: '竞赛', onlineOnly: true, registration: 'available', hideEnded: true }), ['open']);
assert.deepEqual(ids({ query: '科技协会 会议中心' }), ['closing']);
assert.deepEqual(ids({ registration: 'closing' }), ['closing']);
assert.deepEqual(ids({ registration: 'closed', hideEnded: true }), ['closed']);
assert.deepEqual(ids({ registration: 'unknown' }), ['unknown']);
assert.deepEqual(ids({ query: '<img onerror=alert(1)>' }), []);
assert.deepEqual(ids({ cat: '展会', onlineOnly: true }), []);
assert.deepEqual(ids({ hideEnded: true }), ['open', 'closing', 'closed', 'unknown']);
assert.equal(health({ updated_at: '2026-10-06 08:00' }, now).warning, false);
assert.equal(health({ updated_at: '2026-10-04 12:00' }, now).warning, true);
assert.equal(health({}, now).warning, true);
assert.equal(health({ updated_at: 'invalid' }, now).warning, true);
const partial = health({ updated_at: '2026-10-06 08:00', errors: ['ai-pick: timeout'],
  source_status: { 'ai-pick': { status: 'cached', count: 5 }, saikr: { status: 'ok', last_success_at: '2026-10-06 08:00', count: 10 } } }, now);
assert.equal(partial.warning, true);
assert.match(partial.title, /此前保留/);
assert.equal(partial.sources[0].lastSuccess, '未知');
assert.equal(partial.sources[1].lastSuccess, '2026-10-06 08:00');
assert.match(health({ updated_at: '2026-10-06 08:00', errors: ['ai-pick: error'] }, now).title, /异常/);
console.log('Combined filters, unknown dates, and update health cases passed');
