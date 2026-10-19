"""Map IEEE-CIS Fraud Detection rows into Radar Lab events.

Source: Kaggle competition `ieee-fraud-detection` (Vesta), files
`train_transaction.csv` + `train_identity.csv` joined on TransactionID.

IMPORTANT: this is a proxy mapping. IEEE-CIS has no IP address, no issuer
failure flag, and no true merchant-country field. See the constants below for
the exact documented rules and the README table for inactive engine rules.
"""
import hashlib
import math

# Short, documented list of anonymous / throwaway email providers. This is a
# PROXY for disposable email: the dataset does not mark addresses as disposable.
DISPOSABLE_DOMAINS = frozenset({
    'mailinator.com', 'guerrillamail.com', '10minutemail.com', 'temp-mail.org',
    'throwawaymail.com', 'yopmail.com', 'sharklasers.com', 'getnada.com',
    'dispostable.com', 'trashmail.com',
})

# Free forwarding/aliasing domains are a separate documented proxy: they are
# not throwaway mailboxes but let one person create many addresses.
FORWARDING_DOMAINS = frozenset({'duck.com', 'mozmail.com', 'simplelogin.com',
                                'anonaddy.com', 'relay.firefox.com'})

# PROXY RULE: IEEE-CIS exposes `addr2` (card billing region) and `dist1`
# (distance from card address to merchant) but not a merchant country.
# country_mismatch = 1 only when both fields are present and dist1 > 0.
# This is a proximity proxy, not a verified country comparison.
COUNTRY_MISMATCH_RULE = 'addr2 present AND dist1 numeric > 0 (proximity proxy; no merchant-country field)'

CARD_FIELDS = ('card1', 'card2', 'card3', 'card5')
DEVICE_FIELDS = ('DeviceInfo', 'id_30', 'id_31', 'id_33')


def clean(value):
    """CSV missing values are NaN/empty; normalize to None."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    if text == '' or text.lower() in ('nan', 'none', 'null'):
        return None
    return text


def stable_hash(parts, prefix):
    joined = '|'.join('' if p is None else str(p) for p in parts)
    return f'{prefix}-{hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]}'


def email_domain(value):
    text = clean(value)
    if not text or '@' in text and not text.split('@')[-1]:
        return None
    return text.lower().lstrip('@')


def is_disposable(domain):
    return bool(domain) and domain.lower() in DISPOSABLE_DOMAINS


def _as_float(value):
    text = clean(value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def map_transaction(transaction, identity=None):
    """Convert one joined transaction/identity row to a Radar Lab event."""
    identity = identity or {}
    card = stable_hash([clean(transaction.get(k)) for k in CARD_FIELDS], 'card')
    device_parts = [clean(identity.get(k)) for k in DEVICE_FIELDS]
    device = None if all(p is None for p in device_parts) else stable_hash(device_parts, 'device')
    domain = email_domain(transaction.get('P_emaildomain'))
    addr2 = clean(transaction.get('addr2'))
    dist1 = _as_float(transaction.get('dist1'))
    mismatch = bool(addr2 is not None and dist1 is not None and dist1 > 0)
    transaction_id = clean(transaction.get('TransactionID'))
    amount = _as_float(transaction.get('TransactionAmt'))
    dt = _as_float(transaction.get('TransactionDT'))
    is_fraud = clean(transaction.get('isFraud'))
    label = 'abuse' if str(is_fraud) == '1' else 'legitimate'
    account = 'acct-' + card.split('-', 1)[1] if '-' in card else card
    # No IP field exists. Use addr1 (billing region) as a weak network-region
    # alias; this is a clearly documented proxy, never a claim about real IPs.
    ip = stable_hash([clean(transaction.get('addr1'))], 'ip') if clean(transaction.get('addr1')) else 'ip-unknown'
    return {
        'ts': dt if dt is not None else 0,
        'type': 'payment',
        'amount': amount if amount is not None else 0,
        'account': account,
        'card': card,
        'device': device,
        'ip': ip,
        'email_domain': domain,
        'disposable': is_disposable(domain),
        'forwarding_email': bool(domain) and domain in FORWARDING_DOMAINS,
        'country_mismatch': mismatch,
        'failed': False,  # no issuer-failure signal exists in this dataset
        'label': label,
        'source': 'ieee_cis',
        'source_id': transaction_id,
    }


def schema_valid(event):
    required = {'ts', 'type', 'amount', 'account', 'card', 'device', 'ip',
                'disposable', 'country_mismatch', 'failed', 'label'}
    if not required <= set(event):
        return False
    if event['type'] not in {'payment', 'signup', 'trial', 'usage', 'cancel'}:
        return False
    if event['label'] not in {'abuse', 'legitimate', 'unknown'}:
        return False
    if not isinstance(event['amount'], (int, float)) or isinstance(event['amount'], bool):
        return False
    for key in ('account', 'card', 'ip'):
        if not isinstance(event[key], str) or not event[key]:
            return False
    return event['device'] is None or isinstance(event['device'], str)


def active_rules(events):
    """Engine rules that are *scoring-active* on this mapped dataset.

    IEEE-CIS rows map to `payment` events, so lifecycle rules are reported
    inactive. This mirrors the engine's type gating (`engine.Detector.process`)
    and is a documentation aid, never a scoring input.
    """
    has_payment = any(e['type'] == 'payment' for e in events)
    lifecycle = [e for e in events if e['type'] in {'signup', 'trial', 'usage'}]
    return {
        'card_velocity': has_payment,
        'failed_payment': any(e.get('failed') for e in events),
        'large_amount': has_payment,
        'country_mismatch': has_payment,
        'device_reuse': bool(lifecycle),
        'repeat_trial': any(e['type'] == 'trial' for e in lifecycle),
        'temporary_email': bool(lifecycle) and any(e.get('disposable') for e in lifecycle),
        'free_forwarding_email': bool(lifecycle) and any(e.get('forwarding_email') for e in lifecycle),
        'shared_ip': bool(lifecycle),
        'heavy_usage': any(e['type'] == 'usage' for e in lifecycle),
        'trial_after_cancel': any(e['type'] == 'trial' for e in lifecycle),
        'bloom_device_seen': bool(lifecycle),
        'bloom_card_seen': has_payment,
        'dgim_device_burst': has_payment,
    }
