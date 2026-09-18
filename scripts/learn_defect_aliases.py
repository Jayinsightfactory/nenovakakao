"""Learn defect product aliases from Kakao history joined to posted ERP rows.

Read-only with respect to Kakao and the ERP. A Kakao defect item is linked to
an ERP deduction only when customer, unit, quantity and week (source week or
the next one) agree and exactly one ERP product remains. Those links are the
ground truth for both learning and the holdout evaluation.

    python scripts/learn_defect_aliases.py            # evaluate + write aliases
    python scripts/learn_defect_aliases.py --dry-run  # evaluate only
"""
import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import defect_approval as approval  # noqa: E402
from core.defect_matching import norm  # noqa: E402

CORPUS = ROOT / 'data' / 'defect_learning' / '20260914' / 'extractions.jsonl'
ERP = ROOT / 'data' / 'defect_evaluation' / '20260916' / 'db_defects30_37.json'
MASTER = ROOT / 'data' / 'defect_master.json'
OUTPUT = ROOT / 'data' / 'defect_learned_aliases.json'
UNIT = {'BOX': '박스', 'box': '박스', '개': '송이'}


def week_of(sequence):
    head = str(sequence or '').split('-')[0]
    return int(head) if head.isdecimal() else None


def links(master, rows, erp):
    """Yield (extraction, item_index, erp_product_name, week) ground-truth links.

    Quantity/unit must be unique on both sides within one customer and week,
    otherwise sibling items of a multi-line message link to the wrong product.
    """
    index = collections.defaultdict(list)
    for row in erp:
        index[(row['CustName'].strip(), int(row['OrderWeek'][:2]))].append(row)
    groups = collections.defaultdict(list)
    for source in rows:
        week = week_of(source.get('sequence'))
        if week is None or not source.get('items'):
            continue
        matched = approval.rematch({'extracted': dict(source, issues=list(source.get('issues') or []))},
                                   master, learned={})
        customer = (matched.get('customer') or {}).get('name', '').strip()
        if not customer:
            continue
        for position, item in enumerate(source['items']):
            try:
                quantity = abs(float(item['quantity_raw']))
            except (TypeError, ValueError):
                continue
            unit = UNIT.get(item.get('unit_raw'), item.get('unit_raw'))
            groups[(customer, week)].append((quantity, unit, source, position))
    for (customer, week), entries in groups.items():
        pool = index.get((customer, week), []) + index.get((customer, week + 1), [])
        kakao_count = collections.Counter((q, u) for q, u, _, _ in entries)
        erp_count = collections.Counter((abs(float(r['Quantity'])), r['Unit']) for r in pool)
        for quantity, unit, source, position in entries:
            if kakao_count[(quantity, unit)] != 1 or erp_count[(quantity, unit)] != 1:
                continue
            target = next(r['ProdName'].strip() for r in pool
                          if r['Unit'] == unit and abs(float(r['Quantity'])) == quantity)
            yield source, position, target, week


def learn(pairs, min_share=0.8, min_support=2):
    votes = collections.defaultdict(collections.Counter)
    for source, position, target, _ in pairs:
        term = norm(source['items'][position]['product_raw'])
        if len(term) >= 2 and not term.isdecimal():
            votes[(source.get('category') or '', term)][target] += 1
    table = collections.defaultdict(dict)
    for (category, term), counter in votes.items():
        target, support = counter.most_common(1)[0]
        # One quantity-based link can be a coincidence (substituted product,
        # sibling line). Two agreeing links are required before trusting it.
        if support >= min_support and support / sum(counter.values()) >= min_share:
            table[category or '*'][term] = {'target': target, 'support': support}
    return dict(table)


def evaluate(master, pairs, learned, phonetic=True):
    from core import defect_phonetic
    original = defect_phonetic.resolve
    if not phonetic:
        defect_phonetic.resolve = lambda term, rows, **_: (None, [])
    try:
        result = collections.Counter()
        misses = []
        for source, position, target, _ in pairs:
            row = approval.rematch({'extracted': dict(source, issues=list(source.get('issues') or []))},
                                   master, learned=learned)
            item = row['items'][position]
            predicted = (item.get('product') or {}).get('name', '').strip()
            key = 'correct' if predicted == target else 'abstain' if not predicted else 'wrong'
            result[key] += 1
            result['by:' + str(item.get('matched_by'))] += predicted == target
            if key != 'correct':
                misses.append((key, source.get('category'), item['product_raw'], predicted, target))
        return result, misses
    finally:
        defect_phonetic.resolve = original


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--holdout-from', type=int, default=36)
    options = parser.parse_args()
    master = json.loads(MASTER.read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in CORPUS.read_text(encoding='utf-8').splitlines() if line.strip()]
    erp = json.loads(ERP.read_text(encoding='utf-8'))['rows']
    pairs = list(links(master, rows, erp))
    train = [p for p in pairs if p[3] < options.holdout_from]
    test = [p for p in pairs if p[3] >= options.holdout_from]
    print(f'links total={len(pairs)} train(<{options.holdout_from})={len(train)} holdout={len(test)}')
    frozen = learn(train)
    for label, table, phonetic in (('baseline', {}, False), ('+phonetic', {}, True),
                                   ('+learned', frozen, False), ('+both', frozen, True)):
        result, misses = evaluate(master, test, table, phonetic)
        total = sum(result[k] for k in ('correct', 'abstain', 'wrong'))
        print(f"{label:10s} correct={result['correct']}/{total} ({100*result['correct']/max(total,1):.1f}%) "
              f"abstain={result['abstain']} wrong={result['wrong']} "
              f"{ {k: v for k, v in result.items() if k.startswith('by:')} }")
    for miss in misses[:40]:
        print('  miss', miss)
    if options.dry_run:
        return
    final = learn(pairs)
    # Staff-approved corrections outrank corpus statistics and are never dropped.
    try:
        existing = json.loads(OUTPUT.read_text(encoding='utf-8')).get('aliases', {})
    except (OSError, ValueError):
        existing = {}
    for category, bucket in existing.items():
        for alias, entry in bucket.items():
            if entry.get('source') == 'staff_correction':
                final.setdefault(category, {})[alias] = entry
    # Where every posted deduction of a category comes from one origin, a
    # catalog name that exists under several origins resolves to that origin.
    by_category = collections.defaultdict(collections.Counter)
    for row in erp:
        by_category[row['FlowerName']][row['CounName']] += 1
    priors = {category: counter.most_common(1)[0][0] for category, counter in by_category.items()
              if sum(counter.values()) >= 5 and counter.most_common(1)[0][1] == sum(counter.values())}
    OUTPUT.write_text(json.dumps({
        'source': 'kakao defect room x ERP deductions weeks 30-37',
        'links': len(pairs), 'origin_priors': priors, 'aliases': final},
        ensure_ascii=False, indent=1), encoding='utf-8')
    print('origin priors', priors)
    print('wrote', OUTPUT, sum(len(v) for v in final.values()), 'aliases')


if __name__ == '__main__':
    main()
