const test = require('node:test');
const assert = require('node:assert/strict');
const D = require('../static/dgim_view.js');

test('status badges switch exactly at the burst threshold', () => {
  assert.equal(D.statusFor(5, 5).label, 'BURST');
  assert.equal(D.statusFor(6, 5).label, 'BURST');
  assert.equal(D.statusFor(4, 5).label, 'WATCH');
  assert.equal(D.statusFor(1, 5).label, 'WATCH');
  assert.equal(D.statusFor(0, 5).label, 'NORMAL');
  assert.equal(D.statusFor(0, 1).label, 'NORMAL');
});

test('ranking is highest estimate first and defaults to the busiest when all normal', () => {
  const rows = [
    {name: 'quiet-2', kind: 'device', estimate: 0, events: 9},
    {name: 'watch-dev', kind: 'device', estimate: 2, events: 30},
    {name: 'burst-card', kind: 'card', estimate: 7, events: 4},
    {name: 'quiet-1', kind: 'card', estimate: 0, events: 2},
  ];
  const ranked = D.rankEntities(rows, 5);
  assert.deepEqual(ranked.map(row => row.name), ['burst-card', 'watch-dev', 'quiet-2', 'quiet-1']);
  assert.equal(ranked[0].status.key, 'burst');
  assert.equal(ranked[1].status.key, 'watch');
  assert.equal(ranked[2].status.key, 'normal');
  assert.equal(D.defaultEntity(rows, 5).name, 'burst-card');
  const allQuiet = rows.filter(row => row.estimate === 0);
  assert.equal(D.defaultEntity(allQuiet, 5).name, 'quiet-2');
  assert.equal(D.defaultEntity([], 5), null);
});

test('banner copy names the entity type, estimate, window and threshold', () => {
  const burst = D.bannerFor({kind: 'device', estimate: 6, window_size: 60, threshold: 5});
  assert.equal(burst.title,
    "Abnormal: 6 suspicious events among this device's last 60 events (threshold 5).");
  const watch = D.bannerFor({kind: 'card', estimate: 3, window_size: 40, threshold: 5});
  assert.equal(watch.title,
    'Some suspicious activity: 3 in the last 40 events, below the threshold of 5.');
  const normal = D.bannerFor({kind: 'card', estimate: 0, window_size: 40, threshold: 5});
  assert.equal(normal.title,
    "Normal: no suspicious events in this entity's last 40 events.");
});

test('result line explains fired, below threshold, clean, cancel and card cases', () => {
  const fired = D.resultFor({
    kind: 'device', threshold: 5,
    latest: {id: 'evt_00006', fired: true, score_before_dgim: 90, score_after_dgim: 100},
  });
  assert.equal(fired.text,
    'DGIM added +10 to the latest event (evt_00006, score 90 -> 100).');

  const low = D.resultFor({
    kind: 'device', threshold: 5,
    latest: {id: 'evt_00001', fired: false, cancel: false, bit: 1,
             device_estimate: 2, device_threshold: 5},
  });
  assert.match(low.text, /because the estimate is below the threshold\./);

  const clean = D.resultFor({
    kind: 'device', threshold: 5,
    latest: {id: 'evt_00002', fired: false, cancel: false, bit: 0,
             device_estimate: 9, device_threshold: 5},
  });
  assert.match(clean.text, /because the latest event itself was not suspicious\./);

  const cancel = D.resultFor({
    kind: 'device', threshold: 5,
    latest: {id: 'evt_00003', fired: false, cancel: true, bit: 1,
             device_estimate: 9, device_threshold: 5},
  });
  assert.match(cancel.text, /because the latest event is a cancel\./);

  const card = D.resultFor({
    kind: 'card', threshold: 5,
    latest: {id: 'evt_00004', fired: false, cancel: false, bit: 1,
             device_estimate: 2, device_threshold: 5},
  });
  assert.match(card.text, /because the device's estimate is below the threshold\./);
  assert.match(card.text, /DGIM scores the device stream\./);

  assert.equal(D.resultFor({kind: 'device', latest: null}).text,
    'No events for this entity yet.');
});

test('bucket boxes span exactly the cells a bucket summarises', () => {
  const spans = D.bucketSpans([
    {pos: 4, size: 4},
    {pos: 6, size: 2},
    {pos: 8, size: 1},
  ], 1, 8);
  assert.deepEqual(spans.map(span => [span.startPos, span.endPos, span.width]), [
    [1, 4, 4], [5, 6, 2], [8, 8, 1],
  ]);
  const clipped = D.bucketSpans([{pos: 4, size: 4}], 3, 8)[0];
  assert.equal(clipped.width, 2);
  assert.equal(clipped.clipped, true);
});

test('burst shading marks only positions at or above the threshold', () => {
  const rows = [
    {estimate: 1}, {estimate: 5}, {estimate: 6}, {estimate: 4}, {estimate: 5},
  ];
  assert.deepEqual(D.burstSpans(rows, 5), [{start: 1, end: 2}, {start: 4, end: 4}]);
  assert.equal(D.exactFromRows([{bit: 1}, {bit: 0}, {bit: 1}]), 2);
  assert.equal(D.firstFired([{fired: false}, {fired: true, id: 'x'}]).id, 'x');
});
