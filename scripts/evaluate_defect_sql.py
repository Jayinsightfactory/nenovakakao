"""Offline evaluation; independent gold links must precede prediction inspection."""
import json
import argparse
from pathlib import Path
from decimal import Decimal

BASE=Path(__file__).resolve().parents[1]/'data/defect_evaluation/20260916'

def unit(value):
    return {'대':'송이','스팀':'송이','스팀(대)':'송이','BOX':'박스','box':'박스'}.get(value,value)

def evaluate(week,links,prediction_name=None,result_name=None):
    predictions=json.loads((BASE/(prediction_name or f'predictions{week}.json')).read_text(encoding='utf-8'))
    actual={r['EstimateKey']:r for r in json.loads((BASE/'db_defects30_37.json').read_text(encoding='utf-8'))['rows']}
    links=[(x[0],0,x[1]) if len(x)==2 else x for x in links]
    assert len({(i,j) for i,j,k in links})==len(links)
    assert len({k for i,j,k in links})==len(links)
    results=[]
    for i,j,k in links:
        row=predictions[i]; item=row['items'][j]; truth=actual[k]
        product=item.get('product') or {}; customer=row.get('customer') or {}
        quantity_ok=Decimal(item['quantity_raw'])==abs(Decimal(str(truth['Quantity'])))
        unit_ok=unit(item['unit_raw'])==unit(truth['Unit'])
        assert quantity_ok and unit_ok,(i,k,'gold quantity/unit inconsistent')
        correct=product.get('nenova_key')==truth['ProdKey'] and customer.get('nenova_key')==truth['CustKey'] and quantity_ok and unit_ok
        results.append({'source_index':i,'item_index':j,'estimate_key':k,'raw':row['event']['content'],
          'actual_customer':truth['CustName'],'actual_product':truth['ProdName'],
          'predicted_customer':customer.get('name'),'predicted_product':product.get('name'),
          'correct':correct,'abstained':not product or not customer,'status':row['status']})
    n=len(results); correct=sum(r['correct'] for r in results); abstain=sum(bool(r['abstained']) for r in results)
    summary={'source_week':week,'source_messages':len(predictions),'source_items':sum(len(r['items']) for r in predictions),
      'gold_items':n,'correct':correct,'abstained':abstain,'wrong_selected':n-correct-abstain,
      'complete_match_pct':round(correct/n*100,2),'selected_precision_pct':round(correct/(n-abstain)*100,2),
      'linkage_coverage_pct':round(n/sum(len(r['items']) for r in predictions)*100,2)}
    (BASE/(result_name or f'holdout{week}_sql_results.json')).write_text(json.dumps({'summary':summary,'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))
    for r in results:
        if not r['correct']: print(json.dumps(r,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--week',type=int,required=True)
    parser.add_argument('--base',type=Path,default=BASE)
    parser.add_argument('--predictions')
    parser.add_argument('--result')
    args=parser.parse_args()
    BASE=args.base
    gold=json.loads((BASE/f'gold_links{args.week}.json').read_text(encoding='utf-8'))
    assert gold['week']==args.week
    evaluate(args.week,gold['links'],args.predictions,args.result)
