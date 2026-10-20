const test = require('node:test');
const assert = require('node:assert/strict');
const A = require('../static/apriori_view.js');

test('sentence formatter includes confidence, counts, lift and strength', () => {
  const sentence = A.ruleSentence({
    antecedent: ['shared_ip'],
    consequent: ['device_reuse'],
    confidence: 1,
    count: 6,
    lift: 4.2,
  }, 42);
  assert.equal(sentence,
    'When Shared IP happens, Same device, many accounts also happens in 100% of cases (seen in 6 of 42 events, 4.2× more often than chance).');
  assert.deepEqual(A.strengthFor(4.2), {key: 'strong', label: 'Strong', icon: '↑', tone: 'strong'});
  assert.equal(A.strengthFor(1.5).label, 'Moderate');
  assert.equal(A.strengthFor(1).label, 'No real link');
});

test('sentence formatter handles zero, null and derived support counts', () => {
  assert.equal(A.formatPercent(0), '0%');
  assert.equal(A.formatPercent(null), '—');
  assert.equal(A.formatLift(1), '1×');
  assert.equal(A.formatLift(null), '—');
  const sentence = A.ruleSentence({
    antecedent: ['a', 'b'],
    consequent: ['c'],
    confidence: 0,
    support: 0.25,
    lift: null,
  }, 8);
  assert.match(sentence, /^When A and B happen, C also happens in 0% of cases/);
  assert.match(sentence, /seen in 2 of 8 events/);
  assert.match(sentence, /an unknown amount more often than chance/);
  assert.equal(A.togetherSentence({items: ['a'], count: 0}, 10), 'A appears in 0 of 10 events.');
  assert.match(A.togetherSentence({items: ['a', 'b', 'c'], support: 0.2}, 10), /^A, B and C appear together in 2 of 10 events\.$/);
});

test('signal lookup uses friendly names and a prettified fallback', () => {
  assert.equal(A.signalLabel('device_reuse'), 'Same device, many accounts');
  assert.equal(A.signalLabel('repeat_trial'), 'Repeated free trial');
  assert.equal(A.signalLabel('temporary_email'), 'Throwaway email');
  assert.equal(A.signalLabel('some_new_signal'), 'Some New Signal');
  assert.equal(A.signalLabel('label:abuse'), 'Abuse label');
  assert.equal(A.signalTone('shared_ip'), A.signalTone('shared_ip'));
  assert.notEqual(A.signalTone('shared_ip'), A.signalTone('failed_payment'));
});

test('presets map to the documented support and confidence values', () => {
  assert.deepEqual(
    {support: A.PRESETS.broad.minSupport, confidence: A.PRESETS.broad.minConfidence},
    {support: 0.06, confidence: 0.10});
  assert.deepEqual(
    {support: A.PRESETS.balanced.minSupport, confidence: A.PRESETS.balanced.minConfidence},
    {support: 0.12, confidence: 0.26});
  assert.deepEqual(
    {support: A.PRESETS.strict.minSupport, confidence: A.PRESETS.strict.minConfidence},
    {support: 0.20, confidence: 0.45});
  assert.equal(A.presetForConfig(0.12, 0.26), 'balanced');
  assert.equal(A.presetForConfig(0.13, 0.26), null);
});

test('verdicts cover holds, decline, chance and not enough data', () => {
  assert.equal(A.verdictFor({train_confidence: 1, test_confidence: 0.9, test_antecedent_count: 6, lift: 3}).key, 'holds');
  assert.equal(A.verdictFor({train_confidence: 1, test_confidence: 0, test_antecedent_count: 3, lift: 3}).key, 'does_not_hold');
  assert.equal(A.verdictFor({train_confidence: 1, test_confidence: 0.9, test_antecedent_count: 6, lift: 1}).key, 'does_not_hold');
  assert.equal(A.verdictFor({train_confidence: 1, test_confidence: null, test_antecedent_count: 0, lift: 3}).key, 'not_enough');
  assert.equal(A.verdictFor({train_confidence: 1, test_confidence: 0, test_antecedent_count: 0, lift: 3}).key, 'not_enough');
  assert.equal(A.verdictFor({train_confidence: 1, test_confidence: 0, test_antecedent_count: 0, lift: 3}).label, 'Not enough data');
});

test('label-containing rules are hidden unless evaluation mode is on', () => {
  const rules = [
    {antecedent: ['failed_payment'], consequent: ['label:abuse'], confidence: 1, lift: 5, count: 8},
    {antecedent: ['shared_ip'], consequent: ['device_reuse'], confidence: 1, lift: 4, count: 6},
  ];
  const off = A.buildPatternModel([], rules, 42, {includeLabels: false});
  assert.equal(off.length, 1);
  assert.equal(off[0].items.includes('label:abuse'), false);
  const on = A.buildPatternModel([], rules, 42, {includeLabels: true});
  assert.equal(on.length, 2);
  const split = {
    train_n: 21,
    test_n: 21,
    checked: [
      {antecedent: ['failed_payment'], consequent: ['label:abuse'], train_confidence: 1, test_confidence: 0.9, test_antecedent_count: 5, lift: 5},
      {antecedent: ['failed_payment'], consequent: ['country_mismatch'], train_confidence: 1, test_confidence: 0.2, test_antecedent_count: 5, lift: 3},
    ],
  };
  assert.equal(A.timeSplitView({time_split: split}, false).rows.length, 1);
  assert.equal(A.timeSplitView({time_split: split}, false).hidden, 1);
  assert.equal(A.timeSplitView({time_split: split}, true).rows.length, 2);
});
