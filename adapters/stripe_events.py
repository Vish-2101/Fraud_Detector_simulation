"""Map Stripe test-mode webhook/event JSON into Radar Lab events.

Stripe sandbox data is deterministic and is NOT real fraud. `risk_level` and
`risk_score` from Stripe Radar are copied to `stripe_risk_level` /
`stripe_risk_score` for side-by-side comparison only; they are never scoring,
Bloom, DGIM or Apriori inputs. Ground-truth `label` stays `unknown` unless a
hand-built fixture explicitly supplies `metadata.radar_lab_label` for tests.
"""
import hashlib
import math
from datetime import datetime, timezone

from adapters.ieee_cis import FORWARDING_DOMAINS, is_disposable

CHARGE_TYPES = {'charge.succeeded', 'charge.failed', 'charge.refunded',
                'charge.captured', 'charge.expired', 'charge.pending'}
SUBSCRIPTION_TYPES = {
    'customer.subscription.created', 'customer.subscription.updated',
    'customer.subscription.deleted',
}


def _hash(parts, prefix):
    joined = '|'.join('' if p is None else str(p) for p in parts)
    return f'{prefix}-{hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]}'


def _amount(obj):
    amount = obj.get('amount')
    if amount is None and obj.get('amount_total') is not None:
        amount = obj.get('amount_total')
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        return 0.0
    currency = (obj.get('currency') or 'usd').lower()
    if currency != 'usd':
        amount = amount / 100.0
    return round(amount / 100.0, 2) if currency == 'usd' else round(amount, 2)


def _timestamp(event, obj):
    value = event.get('created', obj.get('created', 0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _email(obj, extra=None):
    candidates = [
        obj.get('receipt_email'),
        obj.get('customer_email'),
        (obj.get('billing_details') or {}).get('email'),
        extra,
    ]
    return next((v for v in candidates if isinstance(v, str) and '@' in v), None)


def _domain(email):
    return email.lower().split('@')[-1] if email else None


def _metadata(obj):
    return obj.get('metadata') or {}


def _card_token(obj):
    details = obj.get('payment_method_details') or {}
    card = details.get('card') or {}
    fingerprint = card.get('fingerprint')
    last4 = card.get('last4')
    payment_method = obj.get('payment_method')
    if fingerprint:
        return 'stripe-card-' + fingerprint
    if last4:
        return 'stripe-card-' + last4
    if payment_method:
        return 'stripe-card-' + str(payment_method)
    return 'stripe-card-unknown'


def _device_token(obj, customer):
    meta = _metadata(obj)
    value = meta.get('device_id') or meta.get('fingerprint') or obj.get('fingerprint')
    if value:
        return 'stripe-device-' + str(value)
    # No device in Stripe JSON: use the customer as the closest identifier and
    # document that this under-counts multi-account-on-one-device abuse.
    return 'stripe-device-' + str(customer or 'unknown')


def _country_mismatch(obj):
    meta = _metadata(obj)
    device_country = meta.get('device_country') or meta.get('device_country_code')
    billing = ((obj.get('billing_details') or {}).get('address') or {}).get('country')
    if device_country and billing:
        return str(device_country).upper() != str(billing).upper()
    # Fixture can set an explicit proxy flag; sandbox data normally lacks it.
    return str(meta.get('country_mismatch', '')).lower() in ('1', 'true', 'yes')


def map_event(stripe_event):
    """Map one Stripe event. Returns None for unhandled event types."""
    if not isinstance(stripe_event, dict):
        return None
    kind = stripe_event.get('type')
    obj = ((stripe_event.get('data') or {}).get('object') or {})
    if kind not in CHARGE_TYPES | SUBSCRIPTION_TYPES:
        return None
    customer = obj.get('customer')
    if isinstance(customer, dict):
        customer = customer.get('id')
    email = _email(obj, _metadata(obj).get('email'))
    domain = _domain(email)
    meta = _metadata(obj)
    ts = _timestamp(stripe_event, obj)
    failed = kind == 'charge.failed'
    if kind in CHARGE_TYPES:
        event_type = 'payment'
        amount = _amount(obj)
        outcome = obj.get('outcome') or {}
        if outcome.get('type') in ('issuer_declined', 'blocked', 'invalid'):
            failed = True
    else:
        amount = 0.0
        status = obj.get('status')
        if kind == 'customer.subscription.created':
            event_type = 'trial' if (obj.get('trial_start') or obj.get('trial_end')) else 'signup'
        elif kind == 'customer.subscription.deleted' or status in ('canceled', 'unpaid', 'incomplete_expired'):
            event_type = 'cancel'
        elif obj.get('trial_start') or obj.get('trial_end'):
            event_type = 'usage'
        else:
            event_type = 'usage'
    outcome = obj.get('outcome') or {}
    risk_level = outcome.get('risk_level')
    risk_score = outcome.get('risk_score')
    label = meta.get('radar_lab_label')
    if label not in ('abuse', 'legitimate'):
        label = 'unknown'
    event = {
        'ts': ts,
        'type': event_type,
        'amount': amount,
        'account': 'stripe-customer-' + str(customer or 'unknown'),
        'card': _card_token(obj),
        'device': _device_token(obj, customer),
        'ip': 'stripe-ip-' + str(meta.get('ip') or 'unknown'),
        'email_domain': domain,
        'disposable': is_disposable(domain),
        'forwarding_email': bool(domain) and domain in FORWARDING_DOMAINS,
        'country_mismatch': _country_mismatch(obj),
        'failed': failed,
        'label': label,
        'source': 'stripe',
        'source_id': stripe_event.get('id') or obj.get('id'),
        'stripe_event_type': kind,
        'stripe_risk_level': risk_level,
        'stripe_risk_score': risk_score,
    }
    return event


def map_events(stripe_events):
    """Map, drop unhandled events and sort by event time (Stripe order is not guaranteed)."""
    mapped = [map_event(e) for e in stripe_events]
    mapped = [e for e in mapped if e is not None]
    return sorted(mapped, key=lambda e: (e['ts'], str(e.get('source_id') or '')))


def schema_valid(event):
    required = {'ts', 'type', 'amount', 'account', 'card', 'device', 'ip',
                'disposable', 'country_mismatch', 'failed', 'label',
                'stripe_risk_level', 'stripe_risk_score'}
    return required <= set(event) and event['type'] in {'payment', 'signup', 'trial', 'usage', 'cancel'}
