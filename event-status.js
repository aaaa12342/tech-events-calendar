(function (root) {
  function date(value, endOfDay) {
    if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    var result = new Date(value + (endOfDay ? 'T23:59:59.999+08:00' : 'T00:00:00+08:00'));
    return isNaN(result.getTime()) ? null : result;
  }
  function evaluate(e, now) {
    now = now || new Date();
    var start = date(e.start), end = date(e.end, true), deadline = date(e.reg_deadline, true);
    var activity = end && end < now ? 'done'
      : start && start > now ? 'todo'
      : start && start <= now ? 'ongoing' : 'unknown';
    // ended 只表示本届报名结束，不代替活动结束日期。
    var registration = e.ended || activity === 'done' || (deadline && deadline < now) ? 'closed'
      : deadline ? ((deadline - now) / 86400000 <= 3 ? 'closing' : 'open') : 'unknown';
    return { activity: activity, registration: registration };
  }
  var api = { evaluate: evaluate };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EventStatus = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
