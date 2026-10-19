"""Generate the committed synthetic demo fixture.

Run: python tools/make_fixtures.py

Output (deterministic):
  fixtures/synthetic_events.jsonl   ~500 authored events

No external or raw dataset is read or copied.
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
        for _ in range(count):
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


def main():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    rows = synthetic_rows()
    path = FIXTURES / 'synthetic_events.jsonl'
    with path.open('w', encoding='utf-8', newline='\n') as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
    print(f'wrote {path.relative_to(ROOT)} rows={len(rows)} '
          f"abuse={sum(r['label'] == 'abuse' for r in rows)}")


if __name__ == '__main__':
    main()
