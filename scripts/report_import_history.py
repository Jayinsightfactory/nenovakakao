"""Local report and review tables; never modifies operational matching rules."""
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path


def main(folder):
    def read(name):
        return json.loads((folder/name).read_text(encoding='utf-8'))
    summary = read('summary.json')
    manifest = read('manifest.json')
    matches = read('line_matches.json')
    candidates = read('learning_candidates.json')
    orders = read('nenova_orders.json')
    units = {r['ProdKey']: r for r in read('nenova_product_units.json')}
    assert hashlib.sha256((folder/'source_export.txt').read_bytes()).hexdigest() == manifest['sha256']
    assert len(matches) == sum(summary['status_counts'].values())
    assert len({r['item_id'] for r in matches}) == len(matches)
    assert all(not r['auto_apply'] for r in candidates)
    labels = dict(missing_week='차수 없음', missing_customer='거래처 미확정',
                  partial_week='세부 차수 없음', product_unresolved='품목 후보 미해결',
                  quantity_differs='품목 연결·현재 수량 다름', not_in_current_order='현재 주문에 후보 없음',
                  same_product_quantity='품목·해당 단위 수량 일치', change_cancel_review='변경·취소 검토',
                  context_review='조건·색상·복수 단위 등 검토', ambiguous_product='주문 내 복수 품목 후보',
                  weak_name_candidate='이름 유사도 낮음', no_order_context='해당 주문 없음')
    def table(name, headers, rows):
        with (folder/name).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in rows:
                writer.writerow([("'"+v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v) for v in row])
    table('품목별_대조.csv', ['근거ID','작성자','대화시각','차수','거래처','원문','대화수량','단위','판정','ProdKey','등록품목','현재수량','주의사항'],
          ([r['item_id'],r['sender'],r['timestamp'],r['week'],r['customer_text'],r['raw_line'],r['quantity'],r['unit'],labels[r['status']],r['matched_product_key'],r.get('matched_product',''),r.get('actual_quantity',''),' / '.join(r['flags'])] for r in matches))
    table('별칭_검토후보.csv', ['별칭','국가','분류','ProdKey','등록품목','서로다른주문수','메시지수','판정','근거ID'],
          ([r['alias'],r['country'],r['category'],r['product_key'],r['product'],r['distinct_orders'],r['message_count'],r['status'],' / '.join(r['evidence_item_ids'])] for r in candidates))
    grouped = defaultdict(list)
    for r in orders:
        if not r['MasterDeleted'] and not r['DetailDeleted']:
            grouped[(r['CustKey'],r['ProdKey'])].append(r)
    profiles=[]
    for (cust,prod),rows in grouped.items():
        profiles.append(dict(customer_key=cust, product_key=prod, product=rows[0]['ProdName'],
                             distinct_orders=len({r['OrderMasterKey'] for r in rows}),
                             order_master_keys=sorted({r['OrderMasterKey'] for r in rows}),
                             unit_metadata=units.get(prod,{}), auto_apply=False))
    profiles.sort(key=lambda r:(r['customer_key'],-r['distinct_orders'],r['product_key']))
    (folder/'customer_product_profiles.json').write_text(json.dumps(profiles,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 수입방 대화와 네노바 발주 대조', '',
           '## 저장 및 분석 범위', '',
           f"- 원문: {manifest['first']} ~ {manifest['last']}, {summary['messages']:,}개 메시지. 현재 PC가 내보낸 범위 전체이며 그 이전 대화는 포함 여부를 확인할 수 없습니다.",
           '- 원문 파일과 메시지별 JSONL을 모두 보관했습니다. 원문 해시 일치 및 추출 항목 ID 중복 여부를 검증했습니다.',
           f"- 실제 읽기 API의 품목·거래처 마스터와 같은 네노바 DB의 SELECT 전용 스냅샷을 사용했습니다. 2026년 주문 {summary['db_order_masters']:,}건, 상세 {summary['db_order_details']:,}행, 변경 이력 {summary['db_history_rows']:,}행(삭제 이력 포함).",
           f"- 수량 표현이 있는 후보 {summary['extracted_lines']:,}행을 추출했고, 차수·거래처 조합 {summary['contexts']}개에 기존 전체 품목 구성을 연결했습니다. 일부 조합은 세부 차수가 없어 미해결입니다.",
           '', '## 대조 결과', '', '| 판정 | 후보 행 수 |', '|---|---:|']
    lines += [f'| {labels[k]} | {v:,} |' for k,v in summary['status_counts'].items()]
    lines += ['', '**이 수치는 예측 정확도가 아닙니다.** 이미 등록된 주문을 이용한 사후 대조입니다. 수량 차이는 추가·취소·후속 변경일 수 있으므로 오등록으로 단정하지 않습니다.',
              f"변경 이력에서 대화일 ±3일·동일 주문 단위의 증가량이 일치한 후보는 {summary['history_supported_lines']}행입니다. 시점과 숫자 참고 근거이며 해당 대화가 원인임을 증명하지 않습니다.",
              '', '## 학습에 활용할 수 있는 근거', '',
              f"별칭 검토 후보 {summary['learning_candidates']}개 중 서로 다른 주문 3건 이상에서 반복된 후보는 {summary['repeated_candidates']}개입니다. 일치 사례 중심으로 선정했으므로 반례 검토 후에만 적용해야 합니다.",
              '', '| 별칭 | 국가 | ProdKey | 서로 다른 주문 |', '|---|---|---:|---:|']
    lines += [f"| {r['alias']} | {r['country']} | {r['product_key']} | {r['distinct_orders']} |" for r in candidates if r['status']=='반복 관측·검토 후보']
    lines += ['', '- 거래처별 자주 주문한 품목 목록을 `customer_product_profiles.json`에 별도로 만들었습니다. 2026년 현재 살아 있는 주문의 서로 다른 주문 건수를 기준으로 정렬했습니다. 미래 시점 정보가 섞인 사후 집계이므로 과거 예측 성능 평가에는 그대로 쓰면 안 됩니다.',
              '- 단위는 박스·단·스팀을 별도 비교했습니다. 품목별 환산 메타데이터를 보관했으며, 모든 품목을 1단=10스팀으로 환산하지 않았습니다.',
              '- 색상만 적힌 표현, 복수 단위, 조건부 취소·대체·차수 이동은 확인 대상으로 남겼습니다. 담당자 확인을 학습 정답으로 쌓는 방식이 필요합니다.',
              '', '## 정재훈 CL73 / 35-02 확인', '',
              '원문 메시지 9800의 3·4행: 워싱턴 화이트 50스팀 추가, 유로파 핑크 50스팀 추가. 기존 주문 6243의 ProdKey 230·228과 각각 일치합니다. 두 품목 모두 DB에 50스팀·5단으로 저장돼 있습니다. 대화와 현재 등록 결과가 같으며 중복 등록하지 않았습니다.',
              '', '## 남은 한계와 후속 검토', '',
              f"- 파일 참조 메시지 {summary['file_reference_messages']}개, ‘사진’ 표현을 포함한 메시지 {summary['photo_reference_messages']:,}개가 있습니다. 고유 첨부 개수가 아니며 첨부파일·사진 내용 자체는 분석하지 못했습니다.",
              f"- 발주·추가·취소 등의 표현은 있으나 품목 수량 행을 추출하지 못한 메시지는 {summary['unparsed_order_language_messages']:,}개입니다. 전체 발주를 빠짐없이 재구성한 결과는 아닙니다.",
              '- 거래처가 없는 대화는 전체 수입 물량·농장 발주일 수 있습니다. 개별 고객 주문으로 임의 배정하지 않았습니다. 후속 메시지의 생략된 맥락도 자동 상속하지 않았습니다.',
              '- 추가·수정·취소·재공유 숫자를 단순 합산하지 않았습니다. `order_compositions.json`에서 각 조합의 기존 전체 품목과 원문 참조를 함께 볼 수 있습니다.',
              '- 이번 작업은 근거 데이터와 검토 후보를 만든 것입니다. 모델 학습, 운영 매칭 규칙 반영, 카카오 발송, 주문등록은 실행하지 않았습니다.',
              '', '## 파일 안내', '',
              '- `source_export.txt`: 전체 내보내기 원문 / `messages.jsonl`: 메시지별 원문',
              '- `품목별_대조.csv`: 모든 후보 행과 판정 / `별칭_검토후보.csv`: 별칭 근거',
              '- `order_compositions.json`: 차수·거래처별 전체 기존 주문 구성',
              '- `customer_product_profiles.json`: 거래처별 품목 빈도 및 단위 메타데이터',
              '- `line_matches.json`: 후보·주문·변경 이력 근거 / `message_coverage.json`: 누락 검토 대상',
              '', f"원문 SHA-256: `{manifest['sha256']}`"]
    (folder/'대조결과.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'verified':True,'report':str(folder/'대조결과.md'),'customer_product_profiles':len(profiles)},ensure_ascii=False))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
