"""Prepare IEEE-CIS Fraud Detection data for Radar Lab.

Requires the Kaggle files (not committed):
  data/raw/ieee_cis/train_transaction.csv
  data/raw/ieee_cis/train_identity.csv

Install tooling first:
  pip install -r requirements-tools.txt
  kaggle competitions download -c ieee-fraud-detection
  # accept the competition rules on Kaggle first

Run:
  python tools/prepare_ieee_cis.py --raw data/raw/ieee_cis \
      --out data/processed/ieee_cis_events.jsonl --sample 50000

`--sample N` takes the FIRST N rows after sorting by TransactionDT: a
time-contiguous slice, never a random sample. Use --sample 0 for all rows.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.ieee_cis import (COUNTRY_MISMATCH_RULE, DISPOSABLE_DOMAINS,
                               active_rules, map_transaction, stable_hash)

TX_COLUMNS = ['TransactionID', 'isFraud', 'TransactionDT', 'TransactionAmt',
              'card1', 'card2', 'card3', 'card5', 'addr1', 'addr2', 'dist1',
              'P_emaildomain']
ID_COLUMNS = ['TransactionID', 'DeviceInfo', 'id_30', 'id_31', 'id_33']


def missing_rate(series):
    return round(float(series.isna().mean()), 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--raw', default='data/raw/ieee_cis')
    parser.add_argument('--out', default='data/processed/ieee_cis_events.jsonl')
    parser.add_argument('--report', default='data/processed/ieee_cis_report.json')
    parser.add_argument('--sample', type=int, default=50000,
                        help='time-contiguous prefix size; 0 = all rows')
    args = parser.parse_args()

    try:
        import pandas as pd
    except ImportError:
        raise SystemExit('pandas is required for preparation. pip install -r requirements-tools.txt')

    raw = Path(args.raw)
    tx_path = raw / 'train_transaction.csv'
    id_path = raw / 'train_identity.csv'
    if not tx_path.exists():
        raise SystemExit(f'Missing {tx_path}. Download with: kaggle competitions download -c ieee-fraud-detection')
    transaction = pd.read_csv(tx_path, usecols=TX_COLUMNS)
    identity = pd.read_csv(id_path, usecols=ID_COLUMNS) if id_path.exists() else pd.DataFrame(
        columns=ID_COLUMNS)
    joined = transaction.merge(identity, on='TransactionID', how='left')
    joined = joined.sort_values('TransactionDT', kind='mergesort')
    if args.sample and args.sample > 0:
        joined = joined.head(args.sample)

    rows = joined.to_dict('records')
    events = []
    for row in rows:
        transaction_row = {k: row.get(k) for k in TX_COLUMNS}
        identity_row = {k: row.get(k) for k in ID_COLUMNS}
        events.append(map_transaction(transaction_row, identity_row))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8', newline='\n') as fh:
        for event in events:
            fh.write(json.dumps(event, sort_keys=True, separators=(',', ':')) + '\n')

    abuse = sum(e['label'] == 'abuse' for e in events)
    fraud_rate = round(abuse / len(events), 4) if events else 0.0
    frame = joined
    missing = {
        'ts (TransactionDT)': missing_rate(frame['TransactionDT']),
        'amount (TransactionAmt)': missing_rate(frame['TransactionAmt']),
        'card (card1|card2|card3|card5)': round(float(
            frame[['card1', 'card2', 'card3', 'card5']].isna().all(axis=1).mean()), 4),
        'device (DeviceInfo|id_30|id_31|id_33)': round(float(
            frame[['DeviceInfo', 'id_30', 'id_31', 'id_33']].isna().all(axis=1).mean()), 4),
        'email_domain (P_emaildomain)': missing_rate(frame['P_emaildomain']),
        'country_mismatch (addr2/dist1 proxy)': round(float(
            (frame['addr2'].isna() | frame['dist1'].isna()).mean()), 4),
        'failed (not present; set 0)': 1.0,
        'label (isFraud)': missing_rate(frame['isFraud']),
        'ip (addr1 proxy)': missing_rate(frame['addr1']),
    }
    rules = active_rules(events)
    report = {
        'dataset': 'IEEE-CIS Fraud Detection (Vesta)',
        'source_files': [str(tx_path), str(id_path)],
        'rows_written': len(events),
        'sample_requested': args.sample,
        'sample_method': 'first N rows after sorting by TransactionDT (time-contiguous)',
        'fraud_rate': fraud_rate,
        'fraud_rows': abuse,
        'missing_rates': missing,
        'proxy_rules': {
            'card': 'SHA-256(card1|card2|card3|card5), 16 hex chars',
            'device': 'SHA-256(DeviceInfo|id_30|id_31|id_33); null when all missing',
            'disposable_email': 'PROXY: domain in a short documented throwaway list',
            'disposable_domains': sorted(DISPOSABLE_DOMAINS),
            'country_mismatch': COUNTRY_MISMATCH_RULE,
            'ip': 'PROXY: SHA-256(addr1) billing-region alias; dataset has no IP',
            'failed': 'Not present in IEEE-CIS; every event has failed=false',
        },
        'engine_rules': rules,
        'inactive_rules': sorted(k for k, v in rules.items() if not v),
        'inactive_reason': {
            'failed_payment': 'No issuer-failure / decline signal in the dataset.',
            'device_reuse': 'All rows are payments; lifecycle rules need signup/trial/usage events.',
            'repeat_trial': 'No signup/trial lifecycle in the dataset.',
            'shared_ip': 'No true IP field; addr1 proxy is not a network identifier.',
            'temporary_email': 'Engine gates the proxy to lifecycle events; all rows are payments.',
            'free_forwarding_email': 'Engine gates the proxy to lifecycle events; all rows are payments.',
            'heavy_usage': 'No trial usage signal.',
            'trial_after_cancel': 'No trial/cancel lifecycle in the dataset.',
            'bloom_device_seen': 'Device is missing for a large share of rows; max score contribution is +10 and needs lifecycle corroboration.',
        },
        'label_policy': 'isFraud is written as label for temporal evaluation only; never read by scoring, Bloom, DGIM or Apriori baskets.',
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'wrote {out} rows={len(events)} fraud_rate={fraud_rate:.2%}')
    print(f'wrote {report_path}')


if __name__ == '__main__':
    main()
