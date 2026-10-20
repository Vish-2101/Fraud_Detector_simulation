"""Generate the committed synthetic demo fixture.

Run: python tools/make_fixtures.py

Outputs (deterministic):
  fixtures/synthetic_events.jsonl
  fixtures/synthetic_manifest.json

The generator is seeded and uses only the standard library. It authors a
6-12 hour, bursty event stream with positive examples and negative controls for
the exact rules, Bloom filter, DGIM and Apriori, plus labels for the optional
shadow baseline. Labels are never used by the scoring code.

Every scenario instance appears in both temporal halves. The manifest records
event ids, entities, expected algorithms and expected minimum decisions; the
engine never reads it.
"""
import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'fixtures'
sys.path.insert(0, str(ROOT))

SEED = 20261020
DURATION = 8.5 * 3600  # 8.5 simulated hours
TRAIN_FRACTION = 0.7

NORMAL_DOMAINS = ['gmail.com', 'outlook.com', 'yahoo.com', 'proton.me',
                  'icloud.com', 'campus.edu']
DISPOSABLE = ['mailinator.com', 'guerrillamail.com', '10minutemail.com',
              'yopmail.com', 'temp-mail.org']
FORWARDING = ['duck.com', 'mozmail.com', 'simplelogin.com', 'anonaddy.com',
              'relay.firefox.com']

SCENARIO_NOTES = {
    'everyday_payments': 'Everyday legitimate payments and account activity.',
    'card_testing': 'Six small failed payments on one card inside 60 seconds; exact card velocity decides.',
    'multi_account': 'Four accounts on one device inside 60 seconds; exact device reuse decides.',
    'repeated_trial': 'Four signup/trial/usage/cancel cycles on one device with throwaway signups.',
    'slow_drip_device': 'Ten to fifteen suspicious device events spaced 70-200s apart; exact window never accumulates, DGIM does.',
    'slow_drip_card': 'Ten failed card payments spaced 90-240s apart; exact card velocity never fires, DGIM and Bloom do.',
    'mixed_stream': 'One suspicious event in three on the same device, so DGIM buckets merge across sizes.',
    'dgim_forgetting': 'A device bursts, then produces 61 clean events; its DGIM estimate returns to zero.',
    'recidivist': 'A flagged device is inserted into Bloom, stays quiet for 61 events, then returns hours later with a throwaway signup.',
    'free_forwarding': 'Throwaway-free forwarding-email signups: weak +10 only, still allowed.',
    'campus_wifi': 'Eight to twelve legitimate accounts share one campus IP on distinct devices.',
    'travelers': 'Legitimate travellers with single country-mismatch payments and a few issuer failures.',
    'large_purchase': 'Legitimate purchases of at least $1,000 with no other suspicious signals.',
    'dgim_negative': 'Negative control: two scattered suspicious bits on a legitimate device, below the DGIM threshold.',
    'stealth_abuse': 'Abusive accounts with no suspicious signal: expected false negatives.',
    'bloom_collision': 'Optional Bloom collision control: an unused identifier whose bits were already set, with no corroboration and no points.',
}


def event(**changes):
    row = {
        'ts': 0.0,
        'type': 'payment',
        'amount': 0.0,
        'account': 'acct_00000',
        'device': 'dev_000000',
        'ip': 'ip_10_4_0_0',
        'card': 'card_000000',
        'email_domain': None,
        'disposable': False,
        'forwarding_email': False,
        'country_mismatch': False,
        'failed': False,
        'usage': 0,
        'label': 'legitimate',
        'source': 'synthetic',
    }
    row.update(changes)
    return row


class FixtureBuilder:
    def __init__(self, seed=SEED):
        self.rng = random.Random(seed)
        self.rows = []
        self.instances = []
        self.used_ids = set()
        self.next_source = 1
        self.collision_targets = []

    # ---- neutral identifiers ----
    def _new_id(self, kind):
        while True:
            if kind == 'device':
                value = f'dev_{self.rng.getrandbits(24):06x}'
            elif kind == 'card':
                value = f'card_{self.rng.getrandbits(24):06x}'
            elif kind == 'account':
                value = f'acct_{self.rng.randrange(100000):05d}'
            else:
                value = f'ip_10_4_{self.rng.randint(1, 254)}_{self.rng.randint(1, 254)}'
            if value not in self.used_ids:
                self.used_ids.add(value)
                return value

    def _entities(self):
        return {
            'account': self._new_id('account'),
            'device': self._new_id('device'),
            'ip': self._new_id('ip'),
            'card': self._new_id('card'),
        }

    # ---- event construction ----
    def _amount(self, flavor='normal'):
        if flavor == 'small':
            return round(self.rng.uniform(0.5, 4.0), 2)
        if flavor == 'large':
            return round(self.rng.uniform(1000, 4200), 2)
        if flavor == 'travel':
            return round(self.rng.uniform(80, 900), 2)
        value = math.exp(self.rng.gauss(math.log(25.0), 0.9))
        if self.rng.random() < 0.002:  # rare large everyday purchase
            value *= self.rng.uniform(45, 180)
        return round(max(1.0, min(1000000.0, value)), 2)

    def _email(self, flavor='normal'):
        if flavor == 'disposable':
            return self.rng.choice(DISPOSABLE)
        if flavor == 'forwarding':
            return self.rng.choice(FORWARDING)
        return self.rng.choice(NORMAL_DOMAINS)

    def _row(self, ts, kind='payment', entities=None, amount=0.0, label='legitimate',
             email=None, disposable=False, forwarding=False, mismatch=False,
             failed=False, usage=0):
        entities = entities or self._entities()
        source_id = f'src_{self.next_source:06d}'
        self.next_source += 1
        return event(
            ts=round(ts, 3), type=kind, amount=round(float(amount), 2),
            account=entities['account'], device=entities['device'],
            ip=entities['ip'], card=entities['card'],
            email_domain=self._email(email) if email else None,
            disposable=bool(disposable), forwarding_email=bool(forwarding),
            country_mismatch=bool(mismatch), failed=bool(failed),
            usage=usage, label=label, source='synthetic',
            source_id=source_id,
        )

    def _add(self, scenario, rows, expected, min_decision, description=None,
             start=None, entities=None):
        instance = {
            'scenario': scenario,
            'event_rows': rows,
            'expected_algorithms': expected,
            'expected_min_decision': min_decision,
            'description': description or SCENARIO_NOTES[scenario],
            'start': start,
            'entities': entities or {},
        }
        self.instances.append(instance)
        self.rows.extend(rows)
        return instance

    @staticmethod
    def _collect_entities(rows):
        result = {'accounts': [], 'devices': [], 'cards': [], 'ips': []}
        for key, bucket in [('account', 'accounts'), ('device', 'devices'),
                            ('card', 'cards'), ('ip', 'ips')]:
            result[bucket] = sorted({row[key] for row in rows})
        return result

    # ---- scenario 1: everyday traffic ----
    def _everyday(self, start, size):
        rows = []
        t = start
        kinds = ['payment'] * 84 + ['signup'] * 6 + ['trial'] * 5 + ['usage'] * 3 + ['cancel'] * 2
        for _ in range(size):
            t += self.rng.expovariate(1.0 / 7.0)  # bursty arrivals inside a cluster
            kind = self.rng.choice(kinds)
            amount = 0 if kind in {'signup', 'trial', 'cancel'} else self._amount()
            disposable = kind == 'signup' and self.rng.random() < 0.015
            failed = kind == 'payment' and self.rng.random() < 0.012
            mismatch = kind == 'payment' and self.rng.random() < 0.008
            forwarding = kind == 'signup' and self.rng.random() < 0.006
            rows.append(self._row(
                t, kind=kind, amount=amount, label='legitimate',
                email='disposable' if disposable else 'forwarding' if forwarding else 'normal',
                disposable=disposable, forwarding=forwarding, mismatch=mismatch,
                failed=failed, usage=95 if kind == 'usage' and self.rng.random() < 0.15 else 0,
            ))
        return self._add('everyday_payments', rows, ['exact', 'baseline'], 'allow',
                         description='Bursty everyday traffic: mostly payments, a few normal signups and trials.',
                         start=start)

    def _rhythm(self, t):
        # One busy-period curve across the simulated day (quiet edges, midafternoon peak).
        phase = 2 * math.pi * (t / DURATION)
        return 0.35 + 0.65 * (0.5 + 0.5 * math.sin(phase - math.pi / 2))

    # ---- scenarios 2-16 ----
    def _card_testing(self, start):
        card = self._new_id('card')
        rows = []
        t = start
        for _ in range(6):
            t += self.rng.uniform(3, 8)
            rows.append(self._row(
                t, kind='payment', entities={
                    'account': self._new_id('account'),
                    'device': self._new_id('device'),
                    'ip': self._new_id('ip'),
                    'card': card,
                },
                amount=self._amount('small'), label='abuse', failed=True, mismatch=True,
            ))
        return self._add('card_testing', rows, ['exact', 'bloom', 'apriori', 'baseline'],
                         'review', start=start,
                         entities={'cards': [card]})

    def _multi_account(self, start):
        device = self._new_id('device')
        ip = self._new_id('ip')
        rows = []
        t = start
        for kind in ['signup', 'trial', 'trial', 'usage']:
            t += self.rng.uniform(2, 6)
            rows.append(self._row(
                t, kind=kind, entities={
                    'account': self._new_id('account'),
                    'device': device,
                    'ip': ip,
                    'card': self._new_id('card'),
                },
                amount=0, label='abuse', email='disposable', disposable=True,
                usage=95 if kind == 'usage' else 0,
            ))
        return self._add('multi_account', rows, ['exact', 'bloom', 'apriori', 'baseline'],
                         'review', start=start, entities={'devices': [device], 'ips': [ip]})

    def _repeated_trial(self, start):
        device = self._new_id('device')
        ip = self._new_id('ip')
        card = self._new_id('card')
        rows = []
        t = start
        for _ in range(4):
            account = self._new_id('account')
            t += self.rng.uniform(15, 25)
            rows.append(self._row(
                t, kind='signup', entities={'account': account, 'device': device,
                                            'ip': ip, 'card': card},
                amount=0, label='abuse', email='disposable', disposable=True,
            ))
            t += self.rng.uniform(3, 6)
            rows.append(self._row(
                t, kind='trial', entities={'account': account, 'device': device,
                                           'ip': ip, 'card': card},
                amount=0, label='abuse', email='normal',
            ))
            t += self.rng.uniform(3, 6)
            rows.append(self._row(
                t, kind='usage', entities={'account': account, 'device': device,
                                           'ip': ip, 'card': card},
                amount=0, label='abuse', email='normal', usage=95,
            ))
            t += self.rng.uniform(3, 6)
            rows.append(self._row(
                t, kind='cancel', entities={'account': account, 'device': device,
                                            'ip': ip, 'card': card},
                amount=0, label='unknown', email='normal',
            ))
        return self._add('repeated_trial', rows, ['exact', 'bloom', 'apriori', 'baseline'],
                         'block', start=start, entities={'devices': [device], 'ips': [ip]})

    def _slow_drip_device(self, start, use_email):
        device = self._new_id('device')
        rows = []
        t = start
        for _ in range(12):
            t += self.rng.uniform(70, 200)
            if use_email:
                rows.append(self._row(
                    t, kind='signup', entities={
                        'account': self._new_id('account'),
                        'device': device,
                        'ip': self._new_id('ip'),
                        'card': self._new_id('card'),
                    },
                    amount=0, label='abuse', email='disposable', disposable=True,
                ))
            else:
                card = self._new_id('card')
                rows.append(self._row(
                    t, kind='payment', entities={
                        'account': self._new_id('account'),
                        'device': device,
                        'ip': self._new_id('ip'),
                        'card': card,
                    },
                    amount=self._amount('small'), label='abuse', failed=True,
                ))
        return self._add('slow_drip_device', rows, ['exact', 'bloom', 'dgim', 'baseline'],
                         'review', start=start, entities={'devices': [device]})

    def _slow_drip_card(self, start):
        device = self._new_id('device')
        card = self._new_id('card')
        rows = []
        t = start
        for _ in range(10):
            t += self.rng.uniform(90, 240)
            rows.append(self._row(
                t, kind='payment', entities={
                    'account': self._new_id('account'),
                    'device': device,
                    'ip': self._new_id('ip'),
                    'card': card,
                },
                amount=self._amount('small'), label='abuse', failed=True,
            ))
        return self._add('slow_drip_card', rows, ['exact', 'bloom', 'dgim', 'baseline'],
                         'review', start=start,
                         entities={'devices': [device], 'cards': [card]})

    def _mixed_stream(self, start):
        device = self._new_id('device')
        account = self._new_id('account')
        ip = self._new_id('ip')
        rows = []
        t = start
        for _ in range(12):
            t += self.rng.uniform(35, 70)
            rows.append(self._row(
                t, kind='signup', entities={'account': account, 'device': device,
                                            'ip': ip, 'card': self._new_id('card')},
                amount=0, label='abuse', email='disposable', disposable=True,
            ))
            for _ in range(2):
                t += self.rng.uniform(35, 70)
                rows.append(self._row(
                    t, kind='payment', entities={'account': account, 'device': device,
                                                 'ip': ip, 'card': self._new_id('card')},
                    amount=self._amount(), label='legitimate', email='normal',
                ))
        return self._add('mixed_stream', rows, ['exact', 'bloom', 'dgim', 'baseline'],
                         'review', start=start, entities={'devices': [device]})

    def _dgim_forgetting(self, start):
        device = self._new_id('device')
        account = self._new_id('account')
        ip = self._new_id('ip')
        rows = []
        t = start
        for _ in range(6):
            t += self.rng.uniform(3, 8)
            rows.append(self._row(
                t, kind='signup', entities={'account': account, 'device': device,
                                            'ip': ip, 'card': self._new_id('card')},
                amount=0, label='abuse', email='disposable', disposable=True,
            ))
        for _ in range(61):
            t += self.rng.uniform(25, 45)
            rows.append(self._row(
                t, kind='payment', entities={'account': account, 'device': device,
                                             'ip': ip, 'card': self._new_id('card')},
                amount=self._amount(), label='legitimate', email='normal',
            ))
        return self._add('dgim_forgetting', rows, ['exact', 'bloom', 'dgim', 'baseline'],
                         'review', start=start, entities={'devices': [device]})

    def _recidivist(self, start, return_time):
        device = self._new_id('device')
        account = self._new_id('account')
        ip = self._new_id('ip')
        rows = [self._row(
            start, kind='signup', entities={'account': account, 'device': device,
                                            'ip': ip, 'card': self._new_id('card')},
            amount=0, label='abuse', email='disposable', disposable=True,
        )]
        t = start
        for _ in range(61):
            t += self.rng.uniform(25, 45)
            rows.append(self._row(
                t, kind='payment', entities={'account': account, 'device': device,
                                             'ip': ip, 'card': self._new_id('card')},
                amount=self._amount(), label='legitimate', email='normal',
            ))
        rows.append(self._row(
            return_time, kind='signup', entities={
                'account': self._new_id('account'), 'device': device,
                'ip': self._new_id('ip'), 'card': self._new_id('card'),
            },
            amount=0, label='abuse', email='disposable', disposable=True,
        ))
        return self._add('recidivist', rows, ['exact', 'bloom', 'baseline'],
                         'allow', start=start, entities={'devices': [device]})

    def _free_forwarding(self, start):
        rows = []
        t = start
        for _ in range(3):
            t += self.rng.uniform(120, 300)
            rows.append(self._row(
                t, kind='signup', amount=0, label='abuse', email='forwarding',
                forwarding=True,
            ))
        return self._add('free_forwarding', rows, ['exact', 'apriori', 'baseline'],
                         'allow', start=start)

    def _campus_wifi(self, start):
        ip = self._new_id('ip')
        rows = []
        t = start
        for _ in range(10):
            t += self.rng.uniform(2, 5)
            rows.append(self._row(
                t, kind='signup', entities={
                    'account': self._new_id('account'),
                    'device': self._new_id('device'),
                    'ip': ip,
                    'card': self._new_id('card'),
                },
                amount=0, label='legitimate', email='normal',
            ))
        return self._add('campus_wifi', rows, ['exact', 'baseline'], 'allow',
                         start=start, entities={'ips': [ip]})

    def _travelers(self, start):
        rows = []
        t = start
        for index in range(8):
            t += self.rng.uniform(90, 400)
            rows.append(self._row(
                t, kind='payment', amount=self._amount('travel'),
                label='legitimate', email='normal', mismatch=True,
                failed=index in (2, 6),
            ))
        return self._add('travelers', rows, ['exact', 'apriori', 'baseline'], 'allow',
                         start=start)

    def _large_purchase(self, start):
        rows = []
        t = start
        for _ in range(5):
            t += self.rng.uniform(120, 500)
            rows.append(self._row(
                t, kind='payment', amount=self._amount('large'),
                label='legitimate', email='normal',
            ))
        return self._add('large_purchase', rows, ['exact', 'baseline'], 'allow', start=start)

    def _dgim_negative(self, start):
        device = self._new_id('device')
        account = self._new_id('account')
        ip = self._new_id('ip')
        rows = []
        t = start
        for index in range(20):
            t += self.rng.uniform(80, 160)
            if index in (5, 15):
                rows.append(self._row(
                    t, kind='signup', entities={'account': account, 'device': device,
                                                'ip': ip, 'card': self._new_id('card')},
                    amount=0, label='legitimate', email='disposable', disposable=True,
                ))
            else:
                rows.append(self._row(
                    t, kind='payment', entities={'account': account, 'device': device,
                                                 'ip': ip, 'card': self._new_id('card')},
                    amount=self._amount(), label='legitimate', email='normal',
                ))
        return self._add('dgim_negative', rows, ['dgim'], 'allow', start=start,
                         description=SCENARIO_NOTES['dgim_negative'],
                         entities={'devices': [device]})

    def _stealth_abuse(self, start):
        rows = []
        t = start
        for _ in range(5):
            t += self.rng.uniform(60, 200)
            rows.append(self._row(
                t, kind='payment', amount=self._amount(), label='abuse', email='normal',
            ))
        return self._add('stealth_abuse', rows, ['baseline'], 'allow', start=start)

    def _bloom_collision(self, start):
        instance = {
            'scenario': 'bloom_collision',
            'event_rows': [],
            'expected_algorithms': ['bloom'],
            'expected_min_decision': 'allow',
            'description': SCENARIO_NOTES['bloom_collision'],
            'start': start,
            'entities': {},
        }
        self.instances.append(instance)
        self.collision_targets.append((start, instance))

    # ---- build the timeline ----
    def build(self):
        # Everyday clusters follow a bursty cluster process with a daily rhythm.
        t = 180.0
        while t < DURATION - 240:
            t += self.rng.expovariate(1.0 / 520.0)
            if t >= DURATION - 240:
                break
            if self.rng.random() <= self._rhythm(t):
                self._everyday(t, self.rng.randint(22, 58))

        # Train half: every scenario type has at least one instance.
        self._card_testing(1200)
        self._multi_account(2500)
        self._repeated_trial(3500)
        self._card_testing(5000)
        self._multi_account(6000)
        self._repeated_trial(7000)
        self._slow_drip_device(1500, use_email=True)
        self._slow_drip_device(6000, use_email=False)
        self._slow_drip_device(13000, use_email=True)
        self._slow_drip_card(4500)
        self._slow_drip_card(15000)
        self._mixed_stream(8000)
        self._dgim_forgetting(2000)
        self._recidivist(4000, 16000)
        self._free_forwarding(3000)
        self._campus_wifi(2200)
        self._campus_wifi(12000)
        self._travelers(1800)
        self._travelers(9500)
        self._travelers(16000)
        self._large_purchase(3200)
        self._large_purchase(14500)
        self._dgim_negative(7000)
        self._stealth_abuse(5000)
        self._stealth_abuse(13500)
        self._card_testing(9000)
        self._multi_account(11000)
        self._repeated_trial(14000)
        self._card_testing(13500)
        self._multi_account(17000)
        self._repeated_trial(18000)
        self._card_testing(17500)
        self._multi_account(18500)
        self._repeated_trial(19000)
        self._free_forwarding(17000)
        self._bloom_collision(18000)

        # Test half: every scenario type appears again, interleaved across the tail.
        self._card_testing(22000)
        self._card_testing(27000)
        self._multi_account(22000)
        self._multi_account(27500)
        self._repeated_trial(21500)
        self._repeated_trial(26500)
        self._card_testing(24500)
        self._multi_account(25000)
        self._repeated_trial(24000)
        self._card_testing(28500)
        self._multi_account(29000)
        self._repeated_trial(28500)
        self._card_testing(29500)
        self._multi_account(29900)
        self._repeated_trial(29700)
        self._slow_drip_device(21500, use_email=True)
        self._slow_drip_device(24500, use_email=False)
        self._slow_drip_device(27000, use_email=True)
        self._slow_drip_card(22500)
        self._slow_drip_card(26000)
        self._mixed_stream(23000)
        self._dgim_forgetting(24000)
        self._recidivist(22000, 29200)
        self._free_forwarding(21500)
        self._free_forwarding(27500)
        self._campus_wifi(22000)
        self._campus_wifi(28000)
        self._travelers(21500)
        self._travelers(25500)
        self._travelers(28500)
        self._large_purchase(22500)
        self._large_purchase(28000)
        self._dgim_negative(24500)
        self._stealth_abuse(22500)
        self._stealth_abuse(28500)
        self._bloom_collision(28000)

        self._fill_everyday_gap()
        return self

    def _fill_everyday_gap(self):
        # Keep the fixture inside the requested 2,000-3,000 event band even if
        # the sampled cluster process is a little light or heavy.
        while len(self.rows) < 2100:
            self._everyday(self.rng.uniform(300, DURATION - 600),
                           self.rng.randint(24, 46))

    def _find_collision(self, bloom, prefix):
        for index in range(5_000_000):
            candidate = f'{prefix}_{index:06x}'
            if candidate in self.used_ids:
                continue
            if bloom.contains_bits(candidate):
                return candidate
        raise RuntimeError(f'Could not find a {prefix} collision')

    def attach_collision_controls(self):
        """Find ids already covered by the default m=512, k=3 filter bits."""
        from engine import Detector
        detector = Detector()
        ordered = sorted(self.rows, key=lambda row: (row['ts'], row['source_id']))
        index = 0
        for target, instance in sorted(self.collision_targets, key=lambda item: item[0]):
            while index < len(ordered) and ordered[index]['ts'] < target:
                detector.process(ordered[index])
                index += 1
            device = self._find_collision(detector.bloom_device, 'dev')
            card = self._find_collision(detector.bloom_card, 'card')
            self.used_ids.add(device)
            self.used_ids.add(card)
            positions = {
                'device': detector.bloom_device.positions(device),
                'card': detector.bloom_card.positions(card),
            }
            row = self._row(
                target, kind='payment', entities={
                    'account': self._new_id('account'),
                    'device': device,
                    'ip': self._new_id('ip'),
                    'card': card,
                },
                amount=self._amount(), label='legitimate', email='normal',
            )
            instance['event_rows'] = [row]
            instance['entities'] = {'devices': [device], 'cards': [card]}
            instance['collision_positions'] = positions
            self.rows.append(row)

    def _set_unknown_labels(self):
        target = int(round(0.03 * len(self.rows)))
        current = sum(1 for row in self.rows if row['label'] == 'unknown')
        candidates = [row for row in self.rows
                      if row['label'] == 'legitimate'
                      and row['_scenario'] == 'everyday_payments']
        self.rng.shuffle(candidates)
        for row in candidates[:max(0, target - current)]:
            row['label'] = 'unknown'

    def _tag_rows(self):
        for instance in self.instances:
            for row in instance['event_rows']:
                row['_scenario'] = instance['scenario']

    def finalize(self):
        self._tag_rows()
        self._set_unknown_labels()
        ordered = sorted(self.rows, key=lambda row: (row['ts'], row['source_id']))
        event_id = {row['source_id']: f'evt_{index:05d}'
                    for index, row in enumerate(ordered, 1)}
        labeled = [row for row in ordered if row['label'] in ('abuse', 'legitimate')]
        cut = int(len(labeled) * TRAIN_FRACTION)
        train_sources = {row['source_id'] for row in labeled[:cut]}
        test_sources = {row['source_id'] for row in labeled[cut:]}

        instances = []
        scenario_counts = {}
        scenario_abuse = {}
        for instance in self.instances:
            rows = instance['event_rows']
            ids = [event_id[row['source_id']] for row in rows]
            halves = {('train' if row['source_id'] in train_sources else 'test')
                      for row in rows}
            item = {
                'scenario_id': '',
                'scenario': instance['scenario'],
                'event_ids': ids,
                'source_ids': [row['source_id'] for row in rows],
                'entities': self._collect_entities(rows) if rows else instance['entities'],
                'expected_algorithms': list(instance['expected_algorithms']),
                'expected_min_decision': instance['expected_min_decision'],
                'description': instance['description'],
                'temporal_half': next(iter(halves)) if len(halves) == 1 else 'mixed',
                'event_count': len(rows),
            }
            if 'collision_positions' in instance:
                item['collision_positions'] = instance['collision_positions']
            instances.append(item)
            scenario_counts[instance['scenario']] = scenario_counts.get(instance['scenario'], 0) + len(rows)
            scenario_abuse[instance['scenario']] = scenario_abuse.get(instance['scenario'], 0) + sum(
                row['label'] == 'abuse' for row in rows)

        instances.sort(key=lambda item: (min((row['ts'] for row in self.rows
                                              if row['source_id'] in set(item['source_ids'])),
                                             default=0), item['scenario']))
        for index, item in enumerate(instances, 1):
            item['scenario_id'] = f'{item["scenario"]}-{index:02d}'

        abuse = sum(row['label'] == 'abuse' for row in ordered)
        legitimate = sum(row['label'] == 'legitimate' for row in ordered)
        unknown = sum(row['label'] == 'unknown' for row in ordered)
        train_abuse = sum(row['label'] == 'abuse' for row in labeled[:cut])
        test_abuse = sum(row['label'] == 'abuse' for row in labeled[cut:])
        manifest = {
            'generated_by': 'tools/make_fixtures.py',
            'seed': SEED,
            'time': {
                'start_ts': ordered[0]['ts'] if ordered else 0,
                'end_ts': ordered[-1]['ts'] if ordered else 0,
                'duration_seconds': round(ordered[-1]['ts'] - ordered[0]['ts'], 3) if ordered else 0,
                'arrival_model': 'seeded bursty cluster process with a daily rhythm curve',
            },
            'counts': {
                'events': len(ordered),
                'abuse': abuse,
                'legitimate': legitimate,
                'unknown': unknown,
                'abuse_rate': round(abuse / len(ordered), 4) if ordered else 0,
            },
            'temporal_split': {
                'train_fraction': TRAIN_FRACTION,
                'train_labeled': cut,
                'test_labeled': len(labeled) - cut,
                'train_abuse': train_abuse,
                'test_abuse': test_abuse,
            },
            'scenario_counts': dict(sorted(scenario_counts.items())),
            'scenario_abuse': dict(sorted(scenario_abuse.items())),
            'collision_method': ('Brute-force over unused neutral identifiers; pick the first '
                                 'device and card whose three default-filter positions '
                                 '(m=512, k=3) were already set when the control event '
                                 'was replayed. The control is not inserted because it is '
                                 'clean and allowed.'),
            'instances': instances,
        }
        return ordered, manifest


def build_fixture(seed=SEED):
    builder = FixtureBuilder(seed).build()
    builder.attach_collision_controls()
    ordered, manifest = builder.finalize()
    return ordered, manifest


def replay_summary(rows):
    """Replay through the unchanged engine and report algorithm contributions."""
    from engine import Detector
    detector = Detector()
    for row in rows:
        detector.process(dict(row))
    exact = sum(1 for e in detector.events if e['shadow']['base_score'] > 0)
    bloom = sum(1 for e in detector.events
                if any('Bloom' in reason['reason'] for reason in e['reasons']))
    dgim = sum(1 for e in detector.events
               if any('DGIM' in reason['reason'] for reason in e['reasons']))
    basket = sum(1 for e in detector.events if set(e.get('items', [])) - {
        'bloom_device_seen', 'bloom_card_seen', 'dgim_device_burst'})
    baseline = sum(1 for e in detector.events
                   if e.get('baseline') and e['baseline']['score'] >= detector.review_threshold)
    patterns = detector.apriori()
    return {
        'exact': exact, 'bloom': bloom, 'dgim': dgim, 'apriori_events': basket,
        'apriori_analyzed': patterns['analyzed'],
        'apriori_min_support': patterns['min_support'],
        'apriori_patterns': len(patterns['itemsets']),
        'apriori_rules': len(patterns['rules']), 'baseline_flagged': baseline,
    }


def write_outputs(rows, manifest, out_path, manifest_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open('w', encoding='utf-8', newline='\n') as fh:
        for row in rows:
            clean = {key: value for key, value in row.items() if not key.startswith('_')}
            fh.write(json.dumps(clean, sort_keys=True, separators=(',', ':')) + '\n')
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


def print_summary(rows, manifest, algorithm_counts):
    counts = manifest['counts']
    print(f"events={counts['events']} abuse={counts['abuse']} "
          f"({counts['abuse_rate'] * 100:.1f}%) legitimate={counts['legitimate']} "
          f"unknown={counts['unknown']}")
    split = manifest['temporal_split']
    print(f"temporal split: train={split['train_labeled']} labeled "
          f"({split['train_abuse']} abuse), test={split['test_labeled']} labeled "
          f"({split['test_abuse']} abuse)")
    print('per scenario: ' + ', '.join(
        f'{name}={count}' for name, count in manifest['scenario_counts'].items()))
    print('algorithm contributions: '
          f"exact={algorithm_counts['exact']} events, "
          f"bloom=+10 on {algorithm_counts['bloom']}, "
          f"dgim=+10 on {algorithm_counts['dgim']}, "
          f"apriori={algorithm_counts['apriori_patterns']} patterns/"
          f"{algorithm_counts['apriori_rules']} rules at "
          f"{algorithm_counts['apriori_min_support']:.0%} support over "
          f"{algorithm_counts['apriori_analyzed']} events "
          f"({algorithm_counts['apriori_events']} with signal items), "
          f"baseline flagged={algorithm_counts['baseline_flagged']}")
    if not algorithm_counts['apriori_patterns']:
        print('note: the default 12% support is above every seeded itemset; '
              'lower min_support to about 1% to surface the documented combinations.')


def main():
    parser = argparse.ArgumentParser(description='Generate the deterministic synthetic fixture.')
    parser.add_argument('--out', default=str(FIXTURES / 'synthetic_events.jsonl'))
    parser.add_argument('--manifest-out', default=str(FIXTURES / 'synthetic_manifest.json'))
    args = parser.parse_args()
    rows, manifest = build_fixture()
    write_outputs(rows, manifest, Path(args.out), Path(args.manifest_out))
    print_summary(rows, manifest, replay_summary(rows))


if __name__ == '__main__':
    main()
