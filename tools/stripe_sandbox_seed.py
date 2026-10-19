"""Seed a Stripe sandbox/test-mode account with deterministic Radar-style events.

OPTIONAL tooling. Requires `pip install -r requirements-tools.txt` and a test
secret key (`export STRIPE_SECRET_KEY=sk_test_...`). The committed fixture
`fixtures/stripe_events_sample.json` lets the demo run without an account.

This script uses Stripe test cards:
  4242424242424242  success
  4000000000009235  elevated risk
  4100000000000019  always blocked
and a test clock for 7-day trials, cancellation and retrial on a reused card
fingerprint. Sandbox data is deterministic and is NOT real fraud.

Stripe list APIs omit some test-clock results, so the script retrieves objects
by id and writes hand-wrapped event JSON itself:
  data/processed/stripe_events_live.json

Run:
  python tools/stripe_sandbox_seed.py
  python tools/stripe_webhook_replay.py --file data/processed/stripe_events_live.json
"""
import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CARDS = [
    ('4242424242424242', 'success'),
    ('4000000000009235', 'elevated_risk'),
    ('4100000000000019', 'always_blocked'),
]


def event(type_name, obj):
    return {
        'id': f"evt_seed_{obj.get('id', 'unknown')}",
        'object': 'event',
        'type': type_name,
        'created': obj.get('created') or obj.get('trial_start') or 0,
        'data': {'object': obj},
    }


def payment_method(stripe, number):
    return stripe.PaymentMethod.create(type='card', card={
        'number': number, 'exp_month': 12, 'exp_year': 2034, 'cvc': '123'})


def seed(stripe):
    events = []
    # 1) Charges with the three documented test cards.
    for index, (number, label) in enumerate(CARDS):
        pm = payment_method(stripe, number)
        customer = stripe.Customer.create(email=f'radar-lab-{index}@example.com',
                                          payment_method=pm.id,
                                          metadata={'device_id': f'seed-device-{index}',
                                                    'ip': f'203.0.113.{10 + index}',
                                                    'device_country': 'US'})
        try:
            intent = stripe.PaymentIntent.create(
                amount=4900 if label != 'always_blocked' else 1999,
                currency='usd', customer=customer.id, payment_method=pm.id,
                confirm=True, automatic_payment_methods={'enabled': True, 'allow_redirects': 'never'},
                metadata={'device_id': f'seed-device-{index}'})
            events.append(event('charge.succeeded', intent))
            if intent.get('status') != 'succeeded':
                events.append(event('charge.failed', intent))
        except Exception as exc:  # blocked test card may raise a card error
            events.append({'id': f'evt_seed_error_{index}', 'object': 'event',
                           'type': 'charge.failed', 'created': 0,
                           'data': {'object': {'id': f'failed_{index}', 'customer': customer.id,
                                               'amount': 1999, 'currency': 'usd',
                                               'status': 'failed',
                                               'metadata': {'device_id': f'seed-device-{index}'},
                                               'outcome': {'risk_level': 'highest', 'risk_score': 90},
                                               'error': str(exc)}}})

    # 2) Test-clock trial abuse: create, advance 8 days, cancel, retry on the
    #    same customer/card fingerprint (Stripe keeps the fingerprint stable).
    clock = stripe.test_helpers.TestClock.create(frozen_time=1700000000)
    pm = payment_method(stripe, '4242424242424242')
    for cycle in range(3):
        customer = stripe.Customer.create(
            email=f'radar-lab-trial-{cycle}@example.com', payment_method=pm.id,
            test_clock=clock.id,
            metadata={'device_id': 'seed-reused-laptop', 'ip': '203.0.113.50',
                      'device_country': 'US', 'cycle': str(cycle)})
        subscription = stripe.Subscription.create(
            customer=customer.id, items=[{'price_data': {
                'currency': 'usd', 'product_data': {'name': 'Radar Lab trial'},
                'unit_amount': 999, 'recurring': {'interval': 'month'}}}],
            trial_period_days=7,
            metadata={'device_id': 'seed-reused-laptop', 'cycle': str(cycle)})
        events.append(event('customer.subscription.created', subscription))
        stripe.test_helpers.TestClock.advance(
            clock.id, frozen_time=int(subscription['trial_end']) + 86400)
        updated = stripe.Subscription.retrieve(subscription.id)
        events.append(event('customer.subscription.updated', updated))
        canceled = stripe.Subscription.cancel(subscription.id)
        events.append(event('customer.subscription.deleted', canceled))
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='data/processed/stripe_events_live.json')
    parser.add_argument('--dry-run', action='store_true',
                        help='just validate that the stripe package/key is available')
    args = parser.parse_args()
    key = os.environ.get('STRIPE_SECRET_KEY')
    if not key:
        raise SystemExit('Set STRIPE_SECRET_KEY to a test-mode key (sk_test_...).')
    try:
        import stripe
    except ImportError:
        raise SystemExit('stripe is required: pip install -r requirements-tools.txt')
    stripe.api_key = key
    if args.dry_run:
        print('Stripe SDK import and key present; no objects created.')
        return
    events = seed(stripe)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(events, indent=2) + '\n', encoding='utf-8')
    print(f'wrote {out} events={len(events)} (test mode, deterministic, not real fraud)')


if __name__ == '__main__':
    main()
