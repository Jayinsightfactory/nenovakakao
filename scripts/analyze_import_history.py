"""Offline, evidence-preserving reconciliation. No worker, network, or ERP writes.

This is retrospective reconciliation, not model training or measured prediction
accuracy. Ambiguous context is retained rather than invented.
"""
from __future__ import annotations
import argparse, ast, hashlib, json, re
from collections import Counter, defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = {'박스':'box','박':'box','box':'box','boxes':'box','bx':'box',
        '단':'bunch','bunch':'bunch','bunches':'bunch',
        '송이':'stem','스팀':'stem','스템':'stem','스탬':'stem','줄기':'stem',
        'stem':'stem','stems':'stem','st':'stem','st.':'stem'}
QTY = re.compile(r'(?<![\d.,])(?P<n>\d[\d,]*(?:\.\d+)?)\s*(?P<u>박스|스팀|스템|스탬|송이|줄기|bunches|bunch|boxes|box|stems|stem|단|bx|st\.?|박)(?![a-z가-힣])', re.I)
ROUND = re.compile(r'(?<![\d/])(\d{1,2})\s*[-－]\s*(\d{1,2})(?![\d/])|(?<!\d)(\d{1,2})\s*차')
CL = re.compile(r'(?<![a-z0-9])CL\s*(\d{1,3})(?!\d)', re.I)
COUNTRIES = {'네덜란드':'네덜란드','콜롬비아':'콜롬비아','콜':'콜롬비아', '중국':'중국',
             '에콰도르':'에콰도르','태국':'태국','호주':'호주','이스라엘':'이스라엘',
             '뉴질랜드':'뉴질랜드','일본':'일본','케냐':'케냐','에티오피아':'에티오피아'}
CATEGORY_EN = {'카네이션':'carnation','미니카네이션':'minicarnation','장미':'rose','수국':'hydrangea',
               '아스틸베':'astilbe','튤립':'tulip','백합':'lily','안시리움':'anthurium','거베라':'gerbera',
               '루스커스':'ruscus','리시안셔스':'lisianthus','작약':'peony'}
GENERIC = {'화이트','레드','블루','핑크','그린','연핑크','진핑크','진그린','피치','white','red','blue','pink','green'}

def norm(s): return re.sub(r'[^a-z0-9가-힣]', '', str(s).lower())
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def save(p, value): p.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def dictionaries():
    result = {}
    # Read dictionary literals only; legacy scripts have file/network side effects.
    for file, variable, reverse in [('product_matcher.py','MANUAL_TRANSLITERATION',False),
                                    ('build_matching_table.py','EN_KO_TRANSLITERATION',True)]:
        tree = ast.parse((ROOT/'scripts'/file).read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t,ast.Name) and t.id==variable for t in node.targets):
                for k,v in ast.literal_eval(node.value).items():
                    result[norm(v if reverse else k)] = norm(k if reverse else v)
    result.update({'워싱턴화이트':'washingtonwhite','유로파핑크':'europalpink'})
    return result

def customer_index(customers):
    index = defaultdict(set)
    for c in customers:
        for value in [c.get('CustName'),c.get('OrderCode'),c.get('CustCode'),*str(c.get('Descr') or '').split('/')]:
            if value and len(norm(value))>=2: index[norm(value)].add(c['CustKey'])
    return index

def extract(events, customers):
    cindex = customer_index(customers)
    items, coverage = [], []
    for event in events:
        content = event['content']; week=None; country=''; category=''; cust=None; cust_text=''
        global_flags=[]
        if re.search(r'안될|안 될|가능할|가능한지|가능 여부|못 구|넘겨|대체|미컨펌|원본이|다른것|다른 것|\?|문의',content):
            global_flags.append('조건·문의·차수이동 확인 필요')
        if re.search(r'공유|원본|정리|총 발주|전체 발주',content): global_flags.append('요약·재공유 가능')
        default_action='additional' if '추가' in content else 'change' if '변경' in content else 'order'
        count=0
        for lineno,line in enumerate(content.splitlines(),1):
            line=line.strip()
            if not line: continue
            rounds=list(ROUND.finditer(line))
            # Date/size strings are not rounds unless the line is an order header.
            if rounds and (re.search(r'차|발주|추가|취소|변경|ETA',line,re.I) or re.match(r'^\d{1,2}\s*-\s*\d',line)):
                m=rounds[0]; a=int(m[1] or m[3]); b=int(m[2]) if m[2] else None
                if 1<=a<=53 and (b is None or 1<=b<=5): week=f'{a:02d}-{b:02d}' if b else f'{a:02d}'
            for token,canonical in COUNTRIES.items():
                if re.search(r'(?<![가-힣])'+re.escape(token)+r'(?![가-힣])',line): country=canonical; break
            for token in sorted(CATEGORY_EN,key=len,reverse=True):
                if token in line: category=token; break
            codes=CL.findall(line)
            if len(codes)==1:
                cust_text='CL'+str(int(codes[0])); found=cindex.get(norm(cust_text),set())
                cust=next(iter(found)) if len(found)==1 else None
            elif len(codes)>1: cust=None; cust_text=' / '.join('CL'+c for c in codes)
            else:
                clean=re.sub(r'^[*★\s]+|[\s:：]+$','',line)
                found=cindex.get(norm(clean),set())
                if len(found)==1: cust=next(iter(found)); cust_text=clean
            quantities=list(QTY.finditer(line))
            if not quantities: continue
            first=quantities[0]
            term=line[:first.start()].strip(' -*★•:：→')
            if not term or not re.search(r'[a-z가-힣]',term,re.I): continue
            # Exclude prose/packaging notes; keep the original message regardless.
            if len(term)>90 or re.search(r'포장|담으|경우|부족|수량은|수량이|현재 |가능하면|박스당|^총|^합계|^사진|^파일:',term): continue
            flags=list(global_flags)
            if re.search(r'\d\s*[~~∼-]\s*$',term): flags.append('수량 범위')
            if len(quantities)>1: flags.append('복수 수량·단위 표기')
            if norm(term) in GENERIC: flags.append('색상만 표기·품종 확인 필요')
            if re.search(r'->|→|에서|으로 변경',line): flags.append('변경 전후 수량 확인 필요')
            if len(codes)>1: flags.append('복수 거래처 한 줄')
            if not week: flags.append('차수 없음')
            elif '-' not in week: flags.append('세부 차수 없음')
            if cust is None: flags.append('거래처 미확정')
            action='cancel' if '취소' in line else 'change' if '변경' in line else 'additional' if '추가' in line else default_action
            items.append({'item_id':f"{event['ordinal']}:{lineno}",'ordinal':event['ordinal'],
                'event_id':event['event_id'],'line':lineno,'sender':event['sender_name'],
                'timestamp':event['timestamp'],'year':int(event['timestamp'][:4]),'week':week,
                'customer_key':cust,'customer_text':cust_text,'country':country,'category':category,
                'raw_product':term,'raw_line':line,'quantity':float(first['n'].replace(',','')),
                'unit':UNIT[first['u'].lower()],'raw_unit':first['u'],'action':action,'flags':flags,
                'quantity_expressions':[{'quantity':float(m['n'].replace(',','')),'unit':UNIT[m['u'].lower()]} for m in quantities]})
            count+=1
        coverage.append({'ordinal':event['ordinal'],'event_id':event['event_id'],
            'timestamp':event['timestamp'],'sender':event['sender_name'],'extracted_lines':count,
            'file_reference':'파일:' in content,'photo_reference':bool(re.search(r'사진',content)),
            'order_language':bool(re.search(r'발주|주문|추가|취소|변경',content))})
    return items,coverage

class Matcher:
    def __init__(self, products):
        self.products={p['ProdKey']:p for p in products}; self.trans=dictionaries(); self.cache={}
        for p in products:
            for kr,en in re.findall(r'([가-힣]+)\s*\(([A-Za-z][A-Za-z ]+)\)',p.get('ProdName') or ''):
                self.trans.setdefault(norm(kr),norm(en))
        self.trans=sorted(self.trans.items(),key=lambda x:len(x[0]),reverse=True)
        self.names={}
        for key,p in self.products.items():
            name=p.get('ProdName') or ''; clean=re.sub(r'\[[^]]+\]','',name)
            pieces=[name,clean,clean.split('/')[-1],*re.findall(r'\(([^)]+)\)',clean)]
            cat=CATEGORY_EN.get(p.get('FlowerName'),'')
            pieces.extend([re.sub(r'^(?:'+re.escape(cat)+r')\s*','',clean,flags=re.I)] if cat else [])
            self.names[key]={norm(s) for s in pieces if len(norm(s))>=3}
    def candidates(self, item):
        cache_key=(item['raw_product'],item['category'],item['country'])
        if cache_key in self.cache: return self.cache[cache_key]
        raw=re.sub(r'^[*\-\d.\s]+','',item['raw_product']); raw=CL.sub('',raw).strip()
        n=norm(raw); translated=n
        for k,v in self.trans: translated=translated.replace(k,v)
        forms={n,translated}
        if item['category']:
            stripped=n.removeprefix(norm(item['category']))
            forms.add(stripped)
            for k,v in self.trans: stripped=stripped.replace(k,v)
            forms.add(stripped)
            forms.add(translated.removeprefix(CATEGORY_EN.get(item['category'],'')))
        forms={x for x in forms if len(x)>=3}
        scored=[]
        for key,p in self.products.items():
            if item['country'] and p.get('CounName') and p['CounName']!=item['country']: continue
            if item['category'] and p.get('FlowerName') and p['FlowerName']!=item['category']: continue
            names=self.names[key]; score=0
            if forms & names: score=1
            elif any(t in v for t in forms for v in names): score=.94
            else:
                # Expensive fuzzy scoring only where a 3-character anchor exists.
                for t in forms:
                    for v in names:
                        if any(t[i:i+3] in v for i in range(max(0,len(t)-2))):
                            score=max(score,SequenceMatcher(None,t,v).ratio())
            if score>=.62: scored.append((round(score,4),key))
        scored.sort(reverse=True)
        if scored: scored=[(s,k) for s,k in scored if s>=max(.70,scored[0][0]-.08)][:15]
        self.cache[cache_key]=scored
        return scored

def reconcile(items, products, orders, history, units):
    matcher=Matcher(products); unit_map={p['ProdKey']:p for p in units}
    by_context=defaultdict(list); by_history=defaultdict(list)
    for row in orders: by_context[(int(row['OrderYear']),row['OrderWeek'],row['CustKey'])].append(row)
    for row in history: by_history[row['OrderDetailKey']].append(row)
    results=[]
    for item in items:
        scored=matcher.candidates(item)
        result=dict(item, master_candidates=[{'product_key':k,'product':matcher.products[k]['ProdName'],'score':s} for s,k in scored])
        result.update(status='unresolved',matched_product_key=None,actual_rows=[],history_evidence=[])
        if not item['week']: result['status']='missing_week'
        elif item['customer_key'] is None: result['status']='missing_customer'
        elif '-' not in item['week']: result['status']='partial_week'
        elif not scored: result['status']='product_unresolved'
        else:
            context=by_context.get((item['year'],item['week'],item['customer_key']),[])
            ids={k for s,k in scored}; rows=[r for r in context if r['ProdKey'] in ids]
            active=[r for r in rows if not r['MasterDeleted'] and not r['DetailDeleted']]
            keys={r['ProdKey'] for r in active}
            if not context: result['status']='no_order_context'
            elif not active: result['status']='not_in_current_order'
            elif len(keys)>1: result['status']='ambiguous_product'
            else:
                key=next(iter(keys)); result['matched_product_key']=key
                result['matched_product']=matcher.products[key]['ProdName']
                col={'box':'BoxQuantity','bunch':'BunchQuantity','stem':'SteamQuantity'}[item['unit']]
                total=sum(float(r.get(col) or 0) for r in active)
                result['actual_quantity']=total; result['actual_column']=col
                result['actual_rows']=[{k:r.get(k) for k in ['OrderMasterKey','OrderDetailKey','ProdKey','ProdName','BoxQuantity','BunchQuantity','SteamQuantity','OutQuantity','DetailCreateDtm','DetailLastUpdateDtm']} for r in active]
                result['status']='same_product_quantity' if abs(total-item['quantity'])<1e-6 else 'quantity_differs'
                if max(s for s,k in scored if k==key)<.90: result['status']='weak_name_candidate'
                if item['flags']: result['status']='context_review'
                if item['action'] in {'change','cancel'}: result['status']='change_cancel_review'
                # Only report numerical history evidence in the actual order-entry unit.
                out_unit=UNIT.get(str(unit_map.get(key,{}).get('OutUnit','')).lower())
                date=re.match(r'(\d+)년 (\d+)월 (\d+)일',item['timestamp'])
                msg_date=datetime(*map(int,date.groups())).date()
                if out_unit==item['unit']:
                    for r in rows:
                        if r['ProdKey']!=key: continue
                        for h in by_history[r['OrderDetailKey']]:
                            if h['ColumName']!='주문수량' or not h.get('ChangeDtm'): continue
                            hd=datetime.fromisoformat(h['ChangeDtm'].replace('Z','+00:00')).date()
                            delta=float(h.get('AfterValue') or 0)-float(h.get('BeforeValue') or 0)
                            if abs((hd-msg_date).days)<=3 and abs(delta-item['quantity'])<1e-6:
                                result['history_evidence'].append({k:h.get(k) for k in ['OrderHistoryKey','ChangeDtm','ChangeType','BeforeValue','AfterValue','OrderDetailKey']})
                result['master_ambiguous']=len(scored)>1
            if rows and not result['actual_rows']:
                result['actual_rows']=[{k:r.get(k) for k in ['OrderMasterKey','OrderDetailKey','ProdKey','ProdName','BoxQuantity','BunchQuantity','SteamQuantity','DetailDeleted','MasterDeleted']} for r in rows]
        results.append(result)
    return results

def learning_candidates(results):
    groups=defaultdict(list)
    for r in results:
        if r['status']=='same_product_quantity': groups[(norm(r['raw_product']),r['country'],r['category'])].append(r)
    learned=[]
    for (alias,country,category),rows in groups.items():
        counts=Counter(r['matched_product_key'] for r in rows)
        for key,count in counts.items():
            evidence=[r for r in rows if r['matched_product_key']==key]
            order_keys={a['OrderMasterKey'] for r in evidence for a in r['actual_rows']}
            learned.append({'alias':evidence[0]['raw_product'],'country':country,'category':category,
                'product_key':key,'product':evidence[0]['matched_product'],'message_count':len({r['event_id'] for r in evidence}),
                'distinct_orders':len(order_keys),'competing_product_keys':list(counts),
                'status':'반복 관측·검토 후보' if len(order_keys)>=3 and len(counts)==1 else '근거 부족 또는 다중 품목',
                'evidence_item_ids':[r['item_id'] for r in evidence], 'auto_apply':False})
    return sorted(learned,key=lambda r:(-r['distinct_orders'],-r['message_count']))

def main(folder):
    events=[json.loads(s) for s in (folder/'messages.jsonl').read_text(encoding='utf-8').splitlines()]
    products=read(folder/'master_products.json'); customers=read(folder/'master_customers.json')
    orders=read(folder/'nenova_orders.json'); history=read(folder/'nenova_history.json'); units=read(folder/'nenova_product_units.json')
    items,coverage=extract(events,customers)
    print('extracted',len(items),flush=True)
    results=reconcile(items,products,orders,history,units)
    candidates=learning_candidates(results)
    save(folder/'line_matches.json',results); save(folder/'message_coverage.json',coverage)
    save(folder/'learning_candidates.json',candidates)
    contexts=defaultdict(list)
    for r in results:
        if r['week'] and r['customer_key']: contexts[(r['year'],r['week'],r['customer_key'])].append(r)
    groups=[]
    for (year,week,cust),rows in contexts.items():
        current=[r for r in orders if int(r['OrderYear'])==year and r['OrderWeek']==week and r['CustKey']==cust and not r['MasterDeleted'] and not r['DetailDeleted']]
        groups.append({'year':year,'week':week,'customer_key':cust,'customer':rows[0]['customer_text'],
                       'message_count':len({r['event_id'] for r in rows}),'extracted_lines':len(rows),
                       'status_counts':dict(Counter(r['status'] for r in rows)),
                       'chat_item_ids':[r['item_id'] for r in rows],'current_order_lines':current,
                       'warning':'대화 수량을 단순 합산하지 않음: 추가/취소/재공유/첨부 원본 가능'})
    save(folder/'order_compositions.json',groups)
    stats={'messages':len(events),'first':events[0]['timestamp'],'last':events[-1]['timestamp'],
           'extracted_lines':len(results),'messages_with_lines':sum(c['extracted_lines']>0 for c in coverage),
           'order_language_messages':sum(c['order_language'] for c in coverage),
           'unparsed_order_language_messages':sum(c['order_language'] and not c['extracted_lines'] for c in coverage),
           'file_reference_messages':sum(c['file_reference'] for c in coverage),'photo_reference_messages':sum(c['photo_reference'] for c in coverage),
           'db_order_details':len(orders),'db_order_masters':len({r['OrderMasterKey'] for r in orders}),'db_history_rows':len(history),
           'status_counts':dict(Counter(r['status'] for r in results)),'actions':dict(Counter(r['action'] for r in results)),
           'units':dict(Counter(r['raw_unit'] for r in results)),'contexts':len(groups),
           'history_supported_lines':sum(bool(r['history_evidence']) for r in results),
           'learning_candidates':len(candidates),'repeated_candidates':sum(r['status']=='반복 관측·검토 후보' for r in candidates),
           'training_performed':False,'operational_rules_modified':False}
    save(folder/'summary.json',stats)
    print(json.dumps(stats,ensure_ascii=False),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('folder',type=Path)
    main(parser.parse_args().folder)
