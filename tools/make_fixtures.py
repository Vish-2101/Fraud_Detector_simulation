"""Generate the small committed fixtures used by the offline demo and tests.

Run: python tools/make_fixtures.py

Outputs (all deterministic):
  fixtures/synthetic_events.jsonl      ~500 authored events
  fixtures/ieee_cis_sample.jsonl       ~500 rows with the IEEE-CIS mapped schema
  fixtures/stripe_events_sample.json   ~40 events in Stripe webhook JSON shape

No raw dataset is copied or committed.
"""
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'fixtures'
RNG = random.Random(20261019)

DISPOSABLE = ['mailinator.com', 'guerrillamail.com', '10minutemail.com', 'yopmail.com']
FORWARDING = ['duck.com', 'mozmail.com', 'simplelogin.com']


def base_event(ts, kind, account, device, ip, card, amount, label, source, **extra):
    event = {
        'ts': round(ts, 3),
        'type': kind,
        'amount': round(amount, 2),
        'account': account,
        'device': device,
        'ip': ip,
        'card': card,
        'email_domain': extra.pop('email_domain', None),
        'disposable': extra.pop('disposable', False),
        'forwarding_email': extra.pop('forwarding_email', False),
        'country_mismatch': extra.pop('country_mismatch', False),
        'failed': extra.pop('failed', False),
        'label': label,
        'source': source,
    }
    event.update(extra)
    return event


def synthetic_rows(n=500):
    """Block-composed fixture: ~12% abuse, with honest false-positive traps."""
    rows = []
    ts = 0.0

    def normal(count):
        nonlocal ts
        for i in range(count):
            ts += RNG.uniform(1, 30)
            rows.append(base_event(ts, 'payment', f'syn-user-{len(rows)}',
                                   f'syn-device-{len(rows)}', f'syn-ip-{len(rows)}',
                                   f'syn-card-{len(rows)}', RNG.uniform(5, 400),
                                   'legitimate', 'synthetic',
                                   email_domain=RNG.choice(['gmail.com', 'outlook.com',
                                                            'yahoo.com', 'proton.me'])))

    def campus(count=30):
        nonlocal ts
        for k in range(count):
            ts += RNG.uniform(1, 9)
            rows.append(base_event(ts, 'signup', f'syn-campus-user-{len(rows)}',
                                   f'syn-campus-device-{len(rows)}', 'syn-campus-ip',
                                   f'syn-campus-card-{len(rows)}', 0, 'legitimate',
                                   'synthetic', email_domain='campus.edu',
                                   disposable=(k == 0)))

    def travelers(count=20):
        nonlocal ts
        for _ in range(count):
            ts += RNG.uniform(10, 90)
            rows.append(base_event(ts, 'payment', f'syn-traveler-{len(rows)}',
                                   f'syn-traveler-device-{len(rows)}',
                                   f'syn-traveler-ip-{len(rows)}',
                                   f'syn-traveler-card-{len(rows)}',
                                   RNG.uniform(1100, 3000), 'legitimate', 'synthetic',
                                   country_mismatch=True))

    def card_testing(bursts=4):
        nonlocal ts
        for burst in range(bursts):
            card = f'syn-card-test-{burst}'
            device = f'syn-bot-{burst}'
            for k in range(5):
                ts += RNG.uniform(2, 8)
                rows.append(base_event(ts, 'payment', f'syn-bot-user-{burst}-{k}', device,
                                       f'syn-bot-ip-{burst}', card, RNG.uniform(0.5, 3),
                                       'abuse', 'synthetic', failed=True,
                                       country_mismatch=True))

    def trial_cycles(cycles=6):
        nonlocal ts
        for cycle in range(cycles):
            device = f'syn-trial-device-{cycle}'
            for k, kind in enumerate(['signup', 'trial', 'usage', 'cancel']):
                ts += RNG.uniform(20, 70)
                rows.append(base_event(ts, kind, f'syn-trial-user-{cycle}-{k}', device,
                                       f'syn-trial-ip-{cycle}', f'syn-trial-card-{cycle}',
                                       0, 'unknown' if kind == 'cancel' else 'abuse',
                                       'synthetic', disposable=True,
                                       email_domain=RNG.choice(DISPOSABLE),
                                       usage=95 if kind == 'usage' else 0))

    def low_slow(groups=2):
        nonlocal ts
        for group in range(groups):
            device = f'syn-slow-device-{group}'
            for k in range(6):
                ts += RNG.uniform(80, 110)
                rows.append(base_event(ts, 'signup', f'syn-slow-user-{group}-{k}', device,
                                       f'syn-slow-ip-{group}', f'syn-slow-card-{k}',
                                       0, 'abuse', 'synthetic', disposable=True,
                                       email_domain='mailinator.com'))

    def forwarding(count=8):
        nonlocal ts
        for _ in range(count):
            ts += RNG.uniform(10, 40)
            rows.append(base_event(ts, 'signup', f'syn-forward-user-{len(rows)}',
                                   f'syn-forward-device-{len(rows)}',
                                   f'syn-forward-ip-{len(rows)}',
                                   f'syn-forward-card-{len(rows)}', 0, 'abuse',
                                   'synthetic', forwarding_email=True,
                                   email_domain=RNG.choice(FORWARDING)))

    # Interleave blocks so both temporal splits contain positives and negatives.
    normal(40)
    campus(30)
    normal(40)
    travelers(20)
    normal(40)
    card_testing(4)
    normal(60)
    trial_cycles(6)
    normal(60)
    low_slow(2)
    normal(80)
    forwarding(8)
    normal(100)
    while len(rows) < n:
        normal(n - len(rows))
    return sorted(rows[:n], key=lambda e: (e['ts'], e['account']))


def ieee_rows(n=500):
    """Synthetic rows that obey the exact IEEE-CIS mapped schema."""
    rows = []
    ts = 86400.0
    cards = [f'card-{i:04d}' for i in range(120)]
    devices = [f'device-{i:04d}' for i in range(90)]
    for i in range(n):
        ts += RNG.uniform(20, 900)
        fraud = i % 29 == 0
        card = RNG.choice(cards[:25]) if fraud else RNG.choice(cards)
        device = None if RNG.random() < 0.28 else (RNG.choice(devices[:12]) if fraud else RNG.choice(devices))
        domain = RNG.choice(DISPOSABLE) if fraud else RNG.choice(
            ['gmail.com', 'outlook.com', 'yahoo.com', 'comcast.net', 'proton.me'])
        rows.append(base_event(
            ts, 'payment', 'acct-' + card.split('-')[-1], device,
            f'ip-{RNG.randrange(40):04d}', card,
            round(RNG.uniform(120, 900) if fraud else RNG.uniform(5, 300), 2),
            'abuse' if fraud else 'legitimate', 'ieee_cis',
            email_domain=domain, disposable=domain in DISPOSABLE,
            country_mismatch=fraud and RNG.random() < 0.6,
            failed=False, source_id=f'fixture-{i:05d}'))
    return sorted(rows, key=lambda e: e['ts'])


def stripe_customer(i):
    return f'cus_fixture_{i:03d}'


def stripe_charge(seq, customer, risk, score, blocked=False, fingerprint='fp_reused'):
    ts = 1700000000 + seq * 600
    amount = 4900 if not blocked else 1999
    return {
        'id': f'evt_fixture_charge_{seq:03d}',
        'object': 'event',
        'type': 'charge.failed' if blocked else 'charge.succeeded',
        'created': ts,
        'data': {'object': {
            'id': f'ch_fixture_{seq:03d}',
            'object': 'charge',
            'amount': amount,
            'currency': 'usd',
            'status': 'failed' if blocked else 'succeeded',
            'customer': customer,
            'payment_method_details': {'card': {'last4': '0019' if blocked else ('9235' if risk == 'elevated' else '4242'),
                                                'fingerprint': fingerprint}},
            'outcome': {'risk_level': risk, 'risk_score': score,
                        'type': 'blocked' if blocked else 'authorized'},
            'billing_details': {'email': f'user{seq}@example.com',
                                'address': {'country': 'US'}},
            'metadata': {'device_id': f'dev_{seq % 5}', 'ip': f'203.0.113.{seq % 20}',
                         'device_country': 'US'},
        }},
    }


def stripe_subscription(seq, customer, kind, status, fingerprint='fp_reused'):
    ts = 1700000000 + seq * 600
    obj = {
        'id': f'sub_fixture_{seq:03d}',
        'object': 'subscription',
        'customer': customer,
        'status': status,
        'trial_start': ts - 100 if kind != 'customer.subscription.deleted' else None,
        'trial_end': ts + 6 * 86400,
        'metadata': {'device_id': fingerprint, 'ip': f'203.0.113.{seq % 20}'},
    }
    return {
        'id': f'evt_fixture_sub_{seq:03d}',
        'object': 'event',
        'type': kind,
        'created': ts,
        'data': {'object': obj,
                 'previous_attributes': {'status': 'trialing'} if kind.endswith('updated') else None},
    }


def stripe_events():
    events = []
    seq = 0
    # Success card 4242: normal risk.
    for i in range(10):
        seq += 1
        events.append(stripe_charge(seq, stripe_customer(i % 3), 'normal', RNG.randint(5, 25),
                                    fingerprint=f'fp_normal_{i % 3}'))
    # Elevated-risk test card.
    for i in range(6):
        seq += 1
        events.append(stripe_charge(seq, stripe_customer(10 + i), 'elevated', RNG.randint(60, 78),
                                    fingerprint='fp_elevated'))
    # Always-blocked test card.
    for i in range(4):
        seq += 1
        events.append(stripe_charge(seq, stripe_customer(20 + i), 'highest', 90,
                                    blocked=True, fingerprint='fp_blocked'))
    # Subscription / test-clock trial abuse: reused fingerprint, cancel then retrial.
    for cycle in range(5):
        seq += 1
        events.append(stripe_subscription(seq, stripe_customer(30 + cycle),
                                          'customer.subscription.created', 'trialing'))
        seq += 1
        events.append(stripe_subscription(seq, stripe_customer(30 + cycle),
                                          'customer.subscription.updated', 'active'))
        seq += 1
        events.append(stripe_subscription(seq, stripe_customer(30 + cycle),
                                          'customer.subscription.deleted', 'canceled'))
        seq += 1
        events.append(stripe_subscription(seq, stripe_customer(30 + cycle),
                                          'customer.subscription.created', 'trialing'))
    return events


def main():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, rows in [('synthetic_events.jsonl', synthetic_rows()),
                       ('ieee_cis_sample.jsonl', ieee_rows())]:
        path = FIXTURES / name
        with path.open('w', encoding='utf-8', newline='\n') as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
        print(f'wrote {path.relative_to(ROOT)} rows={len(rows)} '
              f"abuse={sum(r['label'] == 'abuse' for r in rows)}")
    path = FIXTURES / 'stripe_events_sample.json'
    with path.open('w', encoding='utf-8', newline='\n') as fh:
        json.dump(stripe_events(), fh, indent=2, sort_keys=True)
        fh.write('\n')
    print(f'wrote {path.relative_to(ROOT)} events={len(stripe_events())}')


if __name__ == '__main__':
    main()
