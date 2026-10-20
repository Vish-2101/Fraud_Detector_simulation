const test = require('node:test');
const assert = require('node:assert/strict');

const view = require('../static/bloom_view.js');

const base = {kind: 'device', key: 'device-1', positions: [101, 202, 303]};

test('every valid outcome renders without an Unknown fallback', () => {
  const cases = [
    ['definitely_absent', /Definitely absent\./],
    ['true_positive', /Possibly present, and it really was inserted by/],
    ['false_positive', /Possibly present, but it was never inserted\. This is a hash collision \(false positive\)\./],
  ];
  for (const [outcome, heading] of cases) {
    const html = view.inspectionHtml({...base, outcome});
    assert.match(html, heading);
    assert.doesNotMatch(html, /Unknown result/i);
  }
});

test('true positive names the inserting event, action and score', () => {
  const html = view.inspectionHtml({
    ...base,
    outcome: 'true_positive',
    inserted_event: {event_id: 'evt_00001', action: 'review', score: 35},
  });
  assert.match(html, /Possibly present, and it really was inserted by <code>evt_00001<\/code>\./);
  assert.match(html, /review/);
  assert.match(html, /35/);
});

test('false positive lists the source identifiers for every bit', () => {
  const html = view.inspectionHtml({
    ...base,
    outcome: 'false_positive',
    bit_sources: [
      {position: 101, identifiers: ['inserted-a', 'inserted-b']},
      {position: 202, identifiers: ['inserted-c']},
      {position: 303, identifiers: []},
    ],
  });
  assert.match(html, /bit 101: inserted-a, inserted-b/);
  assert.match(html, /bit 202: inserted-c/);
  assert.match(html, /bit 303: no recorded source/);
});

test('an unexpected outcome is explicit, never Unknown', () => {
  const html = view.inspectionHtml({...base, outcome: 'possibly_present'});
  assert.match(html, /Unexpected response from server\./);
  assert.match(html, /<details class="bloom-raw"><summary>Raw outcome<\/summary>/);
  assert.match(html, /possibly_present/);
  assert.doesNotMatch(html, /Unknown result/i);
});

test('a missing outcome is also explicit and shown in the details panel', () => {
  const html = view.inspectionHtml({...base});
  assert.match(html, /Unexpected response from server\./);
  assert.match(html, /<pre>missing<\/pre>/);
  assert.doesNotMatch(html, /Unknown result/i);
});

test('account hints offer one-click device and card checks', () => {
  const html = view.inspectionHtml({
    ...base,
    outcome: 'definitely_absent',
    hint: {
      kind: 'account',
      message: 'This looks like an account ID. Bloom stores device and card IDs.',
      choices: [
        {kind: 'device', key: 'dev-42', label: 'Check device dev-42'},
        {kind: 'card', key: 'card-42', label: 'Check card card-42'},
      ],
    },
  });
  assert.match(html, /This looks like an account ID\./);
  assert.match(html, /data-bloom-kind="device"/);
  assert.match(html, /data-bloom-key="dev-42"/);
  assert.match(html, /data-bloom-kind="card"/);
  assert.match(html, /data-bloom-key="card-42"/);
});

test('hint values are escaped before entering attributes', () => {
  const html = view.hintHtml({
    message: '<script>bad</script>',
    choices: [{kind: 'card', key: 'x" onmouseover="bad', label: '<b>card</b>'}],
  });
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /onmouseover="bad"/);
});
