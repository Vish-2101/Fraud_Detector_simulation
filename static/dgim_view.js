/* Plain-language DGIM presentation helpers. No third-party JS. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.DgimView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const STATUS = Object.freeze({
    burst: Object.freeze({key: 'burst', label: 'BURST', icon: '▲', tone: 'burst'}),
    watch: Object.freeze({key: 'watch', label: 'WATCH', icon: '◐', tone: 'watch'}),
    normal: Object.freeze({key: 'normal', label: 'NORMAL', icon: '✓', tone: 'normal'}),
  });

  const ALL_NORMAL_MESSAGE =
    'Nothing abnormal so far. Run Card-testing or Multi-account to see a burst.';

  function statusFor(estimate, threshold) {
    estimate = Number(estimate) || 0;
    threshold = Number(threshold) || 0;
    if (estimate >= threshold) return STATUS.burst;
    return estimate > 0 ? STATUS.watch : STATUS.normal;
  }

  function kindLabel(kind) {
    return kind === 'card' ? 'card' : 'device';
  }

  function rankEntities(entities, threshold) {
    return (entities || [])
      .map(row => ({...row, status: statusFor(row.estimate, threshold)}))
      .sort((a, b) =>
        (Number(b.estimate) || 0) - (Number(a.estimate) || 0)
        || (Number(b.events) || 0) - (Number(a.events) || 0)
        || String(a.name || '').localeCompare(String(b.name || ''))
        || String(a.kind || '').localeCompare(String(b.kind || '')));
  }

  function defaultEntity(entities, threshold) {
    const ranked = rankEntities(entities, threshold);
    if (!ranked.length) return null;
    const abnormal = ranked.filter(row => row.status.key !== 'normal');
    if (abnormal.length) return abnormal[0];
    return ranked.slice().sort((a, b) =>
      (Number(b.events) || 0) - (Number(a.events) || 0)
      || String(a.name || '').localeCompare(String(b.name || ''))
      || String(a.kind || '').localeCompare(String(b.kind || '')))[0];
  }

  function bannerFor(view) {
    const kind = kindLabel(view && view.kind);
    const estimate = Number(view && view.estimate) || 0;
    const threshold = Number(view && view.threshold) || 0;
    const windowSize = Number(view && view.window_size) || 0;
    const status = statusFor(estimate, threshold);
    let title;
    if (status.key === 'burst') {
      title = `Abnormal: ${estimate} suspicious events among this ${kind}'s last `
        + `${windowSize} events (threshold ${threshold}).`;
    } else if (status.key === 'watch') {
      title = `Some suspicious activity: ${estimate} in the last ${windowSize} events, `
        + `below the threshold of ${threshold}.`;
    } else {
      title = `Normal: no suspicious events in this entity's last ${windowSize} events.`;
    }
    return {...status, title};
  }

  function resultFor(view) {
    const latest = view && view.latest;
    const kind = kindLabel(view && view.kind);
    if (!latest) {
      return {tone: 'normal', fired: false,
              text: 'No events for this entity yet.'};
    }
    if (latest.fired) {
      const after = latest.score_after_dgim ?? latest.score ?? 0;
      const before = latest.score_before_dgim ?? Math.max(0, after - 10);
      return {tone: 'burst', fired: true,
              text: `DGIM added +10 to the latest event (${latest.id}, score ${before} -> ${after}).`};
    }
    const reasons = [];
    if (latest.cancel) {
      reasons.push('the latest event is a cancel');
    } else {
      const estimate = latest.device_estimate ?? latest.estimate ?? 0;
      const threshold = latest.device_threshold ?? (view && view.threshold) ?? 0;
      if (estimate < threshold) {
        reasons.push(kind === 'card'
          ? "the device's estimate is below the threshold"
          : 'the estimate is below the threshold');
      }
      if (!latest.bit) reasons.push('the latest event itself was not suspicious');
      if (!reasons.length) reasons.push('the DGIM firing conditions were not met');
    }
    const note = kind === 'card' ? ' DGIM scores the device stream.' : '';
    return {tone: 'normal', fired: false,
            text: `DGIM added nothing to the latest event because ${reasons.join(' and ')}.${note}`};
  }

  function bucketSpans(buckets, startPos, endPos) {
    return (buckets || []).map((bucket, index) => {
      const low = Math.max(Number(startPos) || 1,
                           Number(bucket.pos) - Number(bucket.size) + 1);
      const high = Math.min(Number(endPos) || 0, Number(bucket.pos));
      if (!(high >= low)) return null;
      return {
        index, pos: Number(bucket.pos), size: Number(bucket.size),
        startPos: low, endPos: high,
        left: low - startPos, width: high - low + 1,
        clipped: low !== Number(bucket.pos) - Number(bucket.size) + 1
          || high !== Number(bucket.pos),
      };
    }).filter(Boolean);
  }

  function burstSpans(rows, threshold) {
    const spans = [];
    let start = null;
    (rows || []).forEach((row, index) => {
      const hot = (Number(row.estimate) || 0) >= (Number(threshold) || 0);
      if (hot && start === null) start = index;
      if (!hot && start !== null) { spans.push({start, end: index - 1}); start = null; }
    });
    if (start !== null) spans.push({start, end: (rows || []).length - 1});
    return spans;
  }

  function firstFired(rows) {
    return (rows || []).find(row => row.fired) || null;
  }

  function exactFromRows(rows) {
    return (rows || []).reduce((sum, row) => sum + (Number(row.bit) ? 1 : 0), 0);
  }

  return {
    STATUS, ALL_NORMAL_MESSAGE,
    statusFor, kindLabel, rankEntities, defaultEntity, bannerFor, resultFor,
    bucketSpans, burstSpans, firstFired, exactFromRows,
  };
});
