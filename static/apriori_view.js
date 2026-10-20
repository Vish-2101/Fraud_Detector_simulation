/* Plain-language Apriori presentation helpers. No third-party JS. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AprioriView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const SIGNAL_LABELS = Object.freeze({
    device_reuse: 'Same device, many accounts',
    repeat_trial: 'Repeated free trial',
    temporary_email: 'Throwaway email',
    country_mismatch: 'Country mismatch',
    failed_payment: 'Failed payment',
    card_velocity: 'Many card attempts',
    shared_ip: 'Shared IP',
    large_amount: 'Large amount ($1,000+)',
    free_forwarding_email: 'Free-forwarding email',
    heavy_usage: 'Heavy trial usage',
    trial_after_cancel: 'Trial after cancellation',
    bloom_device_seen: 'Device seen before (Bloom)',
    bloom_card_seen: 'Card seen before (Bloom)',
    dgim_device_burst: 'Recent suspicious burst (DGIM)',
    'label:abuse': 'Abuse label',
    'label:legitimate': 'Legitimate label',
  });

  const SIGNAL_TONES = Object.freeze({
    device_reuse: 0,
    repeat_trial: 1,
    temporary_email: 2,
    country_mismatch: 3,
    failed_payment: 4,
    card_velocity: 5,
    shared_ip: 6,
    large_amount: 7,
    free_forwarding_email: 8,
    heavy_usage: 9,
    trial_after_cancel: 0,
    'label:abuse': 4,
    'label:legitimate': 2,
  });

  const PRESETS = Object.freeze({
    broad: Object.freeze({
      key: 'broad', label: 'Broad',
      minSupport: 0.06, minConfidence: 0.10,
      description: 'A pattern must appear in at least 6% of events and be right at least 10% of the time. Finds more, noisier patterns.',
    }),
    balanced: Object.freeze({
      key: 'balanced', label: 'Balanced',
      minSupport: 0.12, minConfidence: 0.26,
      description: 'A pattern must appear in at least 12% of events and be right at least 26% of the time.',
    }),
    strict: Object.freeze({
      key: 'strict', label: 'Strict',
      minSupport: 0.20, minConfidence: 0.45,
      description: 'A pattern must appear in at least 20% of events and be right at least 45% of the time. Keeps fewer, stronger patterns.',
    }),
  });
  const PRESET_ORDER = Object.freeze(['broad', 'balanced', 'strict']);
  const DEFAULT_PRESET = 'balanced';

  const TERM_TIPS = Object.freeze({
    event: 'One event, plus all the signals found in it.',
    signal: 'One simple observation about an event.',
    pattern: 'A set of signals that appear together often enough.',
    support: 'The share of events that contain the pattern.',
    confidence: 'How often the conclusion appears when the premise appears.',
    lift: 'How many times more often the signals co-occur than chance.',
    prevalence: 'The share of events labelled abuse.',
    candidate: 'A combination Apriori checks against the event baskets.',
    pruning: 'Skipping combinations that contain a rare signal.',
  });

  function prettySignal(raw) {
    return String(raw ?? '')
      .replace(/^label:/, '')
      .replace(/[_-]+/g, ' ')
      .trim()
      .replace(/\b\w/g, ch => ch.toUpperCase()) || 'Unknown signal';
  }

  function signalLabel(raw) {
    const key = String(raw ?? '');
    return SIGNAL_LABELS[key] || prettySignal(key);
  }

  function signalTone(raw) {
    const key = String(raw ?? '');
    if (Object.prototype.hasOwnProperty.call(SIGNAL_TONES, key)) return SIGNAL_TONES[key];
    let hash = 0;
    for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
    return hash % 10;
  }

  function isLabelSignal(item) {
    const text = String(item ?? '').toLowerCase();
    return text.startsWith('label:') || text === 'abuse' || text === 'legitimate';
  }

  function hasLabel(items) {
    return (items || []).some(isLabelSignal);
  }

  function filterLabelRules(rules, includeLabels) {
    if (includeLabels) return (rules || []).slice();
    return (rules || []).filter(r =>
      !hasLabel(r.antecedent) && !hasLabel(r.consequent));
  }

  function formatPercent(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    const percent = Number(value) * 100;
    const rounded = Math.round(percent * 10) / 10;
    if (Math.abs(rounded - Math.round(rounded)) < 1e-9) return `${Math.round(rounded)}%`;
    return `${rounded.toFixed(Math.min(1, Math.max(0, digits)))}%`;
  }

  function formatLift(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    const text = Number(value).toFixed(2).replace(/\.?0+$/, '');
    return `${text}×`;
  }

  function countOf(pattern, total) {
    if (pattern && pattern.count !== null && pattern.count !== undefined) return Number(pattern.count);
    if (pattern && pattern.support !== null && pattern.support !== undefined) {
      return Math.round(Number(pattern.support) * Number(total || 0));
    }
    return null;
  }

  function joinSignals(items, labels = signalLabel) {
    const values = (items || []).map(labels);
    if (!values.length) return 'no signals';
    if (values.length === 1) return values[0];
    if (values.length === 2) return `${values[0]} and ${values[1]}`;
    return `${values.slice(0, -1).join(', ')} and ${values[values.length - 1]}`;
  }

  function ruleSentence(rule, total) {
    const antecedent = rule.antecedent || [];
    const consequent = rule.consequent || [];
    const premise = joinSignals(antecedent);
    const conclusion = joinSignals(consequent);
    const premiseVerb = antecedent.length === 1 ? 'happens' : 'happen';
    const conclusionVerb = consequent.length === 1 ? 'also happens' : 'also happen';
    const confidence = rule.confidence === null || rule.confidence === undefined
      ? 'an unknown percentage of cases'
      : `${formatPercent(rule.confidence)} of cases`;
    const count = countOf(rule, total);
    const seen = count === null ? `seen in an unknown number of ${total} events` : `seen in ${count} of ${total} events`;
    const lift = rule.lift === null || rule.lift === undefined
      ? 'an unknown amount more often than chance'
      : `${formatLift(rule.lift)} more often than chance`;
    return `When ${premise} ${premiseVerb}, ${conclusion} ${conclusionVerb} in ${confidence} (${seen}, ${lift}).`;
  }

  function togetherSentence(pattern, total) {
    const items = pattern.items || [];
    const labels = items.map(signalLabel);
    const count = countOf(pattern, total);
    if (labels.length === 1) {
      return `${labels[0]} appears in ${count === null ? 'an unknown number' : count} of ${total} events.`;
    }
    return `${joinSignals(items)} appear together in ${count === null ? 'an unknown number' : count} of ${total} events.`;
  }

  function strengthFor(lift) {
    if (lift === null || lift === undefined || Number.isNaN(Number(lift))) {
      return {key: 'unknown', label: 'Unknown strength', icon: '?', tone: 'unknown'};
    }
    if (Number(lift) >= 2) return {key: 'strong', label: 'Strong', icon: '↑', tone: 'strong'};
    if (Number(lift) >= 1.2) return {key: 'moderate', label: 'Moderate', icon: '→', tone: 'moderate'};
    return {key: 'none', label: 'No real link', icon: '≈', tone: 'none'};
  }

  function cooccurrenceLift(items, support, singletonSupport) {
    if (!items || items.length < 2) return null;
    if (support === null || support === undefined) return null;
    let chance = 1;
    for (const item of items) {
      const single = singletonSupport && singletonSupport.get
        ? singletonSupport.get(item)
        : singletonSupport && singletonSupport[item];
      if (single === null || single === undefined || Number(single) <= 0) return null;
      chance *= Number(single);
    }
    if (!chance) return null;
    return Number(support) / chance;
  }

  function presetForConfig(minSupport, minConfidence) {
    const support = Number(minSupport);
    const confidence = Number(minConfidence);
    return PRESET_ORDER.find(key => {
      const preset = PRESETS[key];
      return Math.abs(preset.minSupport - support) < 1e-6
        && Math.abs(preset.minConfidence - confidence) < 1e-6;
    }) || null;
  }

  function verdictFor(check, tolerance = 0.75) {
    if (!check || check.train_confidence === null || check.train_confidence === undefined
        || check.test_confidence === null || check.test_confidence === undefined
        || check.test_antecedent_count === 0) {
      return {
        key: 'not_enough', label: 'Not enough data', icon: '?', tone: 'unknown',
        detail: 'The premise did not appear in the second half, so its later confidence is undefined.',
      };
    }
    const test = Number(check.test_confidence);
    const train = Number(check.train_confidence);
    const lift = check.lift === null || check.lift === undefined ? null : Number(check.lift);
    if (test <= 0.05 || (lift !== null && lift <= 1.1) || test < train * tolerance) {
      return {
        key: 'does_not_hold', label: 'Does not hold up', icon: '↘', tone: 'bad',
        detail: 'The later confidence is near zero, much weaker, or the association is near chance.',
      };
    }
    return {
      key: 'holds', label: 'Holds up', icon: '✓', tone: 'good',
      detail: 'The later confidence stays reasonably close to the first half.',
    };
  }

  function scopeData(state, scope) {
    const source = state && state.apriori_current
      ? (scope === 'abuse' ? state.apriori_current.abuse_only : state.apriori_current.all_events)
      : null;
    const fallback = state && state.apriori_class
      ? (scope === 'abuse' ? state.apriori_class.abuse_only : state.apriori_class.all_events)
      : null;
    if (scope === 'abuse') {
      const data = source || fallback || {};
      return {
        scope, analyzed: Number(data.analyzed || 0), itemsets: data.itemsets || [],
        rules: data.rules || [], levels: data.levels || [], min_support: data.min_support,
      };
    }
    const data = source || fallback || {};
    return {
      scope, analyzed: Number((state && state.analyzed) ?? data.analyzed ?? 0),
      itemsets: (state && state.patterns) || data.itemsets || [],
      rules: (state && state.rules) || data.rules || [],
      levels: (state && state.apriori_levels) || data.levels || [],
      min_support: data.min_support ?? state?.apriori_config?.min_support,
    };
  }

  function buildPatternModel(patterns, rules, total, options = {}) {
    const includeLabels = Boolean(options.includeLabels);
    const patternList = patterns || [];
    const singletonSupport = new Map();
    for (const pattern of patternList) {
      if ((pattern.items || []).length === 1) singletonSupport.set(pattern.items[0], pattern.support);
    }
    const visibleRules = filterLabelRules(rules || [], includeLabels);
    const represented = new Set();
    const models = [];
    for (const rule of visibleRules) {
      if (!(rule.antecedent || []).length || !(rule.consequent || []).length) continue;
      const items = [...rule.antecedent, ...rule.consequent];
      represented.add(items.slice().sort().join('\u0001'));
      models.push({
        kind: 'rule',
        key: `rule:${rule.antecedent.join('|')}=>${rule.consequent.join('|')}`,
        items, antecedent: rule.antecedent, consequent: rule.consequent,
        count: countOf(rule, total), total, support: rule.support,
        confidence: rule.confidence, lift: rule.lift,
        strength: strengthFor(rule.lift),
        sentence: ruleSentence(rule, total),
      });
    }
    const seen = new Set();
    for (const pattern of patternList) {
      const items = (pattern.items || []).filter(item => includeLabels || !isLabelSignal(item));
      if (!items.length) continue;
      if (!includeLabels && hasLabel(items)) continue;
      const setKey = items.slice().sort().join('\u0001');
      if (represented.has(setKey) || seen.has(setKey)) continue;
      seen.add(setKey);
      const lift = cooccurrenceLift(items, pattern.support, singletonSupport);
      models.push({
        kind: 'together',
        key: `together:${setKey}`,
        items, antecedent: items, consequent: [], count: countOf(pattern, total),
        total, support: pattern.support, confidence: null, lift,
        strength: strengthFor(lift),
        sentence: togetherSentence({...pattern, items}, total),
      });
    }
    return models.sort((a, b) =>
      (b.lift ?? 1) - (a.lift ?? 1)
      || (b.confidence ?? 0) - (a.confidence ?? 0)
      || (b.count ?? 0) - (a.count ?? 0)
      || a.sentence.localeCompare(b.sentence));
  }

  function timeSplitView(state, includeLabels = false) {
    const split = (state && state.time_split) || {};
    const all = split.checked || [];
    const rows = filterLabelRules(all, includeLabels).map(check => ({
      ...check,
      verdict: verdictFor(check),
      sentence: ruleSentence({
        antecedent: check.antecedent,
        consequent: check.consequent,
        confidence: check.train_confidence,
        count: null,
        lift: check.lift,
      }, split.train_n || 0).replace(/\s*\(seen in.*$/, ''),
    }));
    return {
      train_n: split.train_n || 0,
      test_n: split.test_n || 0,
      note: split.note || '',
      hidden: all.length - rows.length,
      rows,
    };
  }

  return {
    SIGNAL_LABELS, PRESETS, PRESET_ORDER, DEFAULT_PRESET, TERM_TIPS,
    prettySignal, signalLabel, signalTone, isLabelSignal, hasLabel, filterLabelRules,
    formatPercent, formatLift, countOf, joinSignals, ruleSentence, togetherSentence,
    strengthFor, cooccurrenceLift, presetForConfig, verdictFor, scopeData,
    buildPatternModel, timeSplitView,
  };
});
