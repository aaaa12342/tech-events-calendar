(function (root) {
  var phases = typeof module !== 'undefined' && module.exports ? require('./event-status.js') : root.EventStatus;
  var sourceNames = { 'ai-pick': 'AI 活动雷达', huodongxing: '活动行', modelscope: '魔搭社区',
    segmentfault: '思否', saikr: '赛氪', rosedu: 'ROS 教育基金会' };
  function filter(events, state, now) {
    var words = (state.query || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    return events.filter(function (e) {
      var p = phases.evaluate(e, now);
      var text = [e.name, e.org, e.city, e.venue, e.category].join(' ').toLowerCase();
      return (state.cat === '全部' || !state.cat || e.category === state.cat)
        && (!state.onlineOnly || e.online === true)
        && (!state.hideEnded || p.activity !== 'done')
        && (!state.registration || (state.registration === 'available'
          ? p.registration === 'open' || p.registration === 'closing' : p.registration === state.registration))
        && words.every(function (word) { return text.indexOf(word) !== -1; });
    });
  }
  function updatedTime(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(value)) return null;
    var t = Date.parse(value.replace(' ', 'T') + ':00+08:00');
    return isNaN(t) ? null : t;
  }
  function health(data, now) {
    var timestamp = updatedTime(data.updated_at);
    var stale = timestamp !== null && (now || new Date()).getTime() - timestamp >= 48 * 3600000;
    var errors = Array.isArray(data.errors) ? data.errors : [];
    var sources = Object.keys(data.source_status || {}).map(function (name) {
      var s = data.source_status[name];
      return { name: sourceNames[name] || name, status: s.status, lastSuccess: s.last_success_at || '未知', count: s.count || 0 };
    });
    var partial = errors.length > 0 || sources.some(function (s) { return s.status !== 'ok'; });
    var notes = [];
    if (timestamp === null) notes.push('更新时间未知，请以活动官网为准。');
    else if (stale) notes.push('数据已超过两天未更新，报名信息请以活动官网为准。');
    if (partial) notes.push('部分来源更新异常' + (sources.some(function (s) { return s.status === 'cached'; }) ? '，相关活动使用此前保留的数据。' : '，部分信息可能尚未更新。'));
    return { warning: timestamp === null || stale || partial,
      title: notes.length ? notes.join(' ') : '最近一次数据更新正常。',
      sources: sources, failedNames: errors.map(function (e) { var key = String(e).split(':')[0]; return sourceNames[key] || key; }) };
  }
  var api = { filter: filter, health: health };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EventView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
