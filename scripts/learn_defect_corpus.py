"""Offline corpus analysis only. No network, approval messages or ERP writes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.moyi_inbound import parse_export
from core.defect_deduction_parser import extract


def analyze(source, output):
    raw = source.read_bytes()
    events = parse_export(raw.decode('utf-8-sig'), 'defect-learning-only')
    rows = [extract(event) for event in events]
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'extractions.jsonl').open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    review = [row for row in rows if row['status'] == 'review']
    (output / 'needs_review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
    candidates = [row for row in rows if row['status'] == 'matching_candidate']
    vocabulary = {'verified_against_erp': False,
        'product_mentions': Counter(item['product_raw'] for row in candidates for item in row['items']),
        'customer_mentions': Counter(row['customer'] for row in candidates)}
    (output / 'unverified_vocabulary.json').write_text(json.dumps(vocabulary, ensure_ascii=False, indent=2), encoding='utf-8')
    stats = {'source_sha256': hashlib.sha256(raw).hexdigest(), 'messages': len(rows),
             'senders': Counter(e['sender_name'] for e in events),
             'status': Counter(row['status'] for row in rows),
             'issues': Counter(issue for row in review for issue in row['issues']),
             'units': Counter(item['unit_raw'] for row in rows for item in row['items']),
             'candidate_items': sum(len(row['items']) for row in candidates),
             'approval_or_write_performed': False}
    (output / 'summary.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return stats


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'defect_learning')
    args = parser.parse_args()
    analyze(args.input, args.output)
