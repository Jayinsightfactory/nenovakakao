# -*- coding: utf-8 -*-
"""최신 분석 데이터로 구글 슬라이드 보고서 업데이트"""
import sys, json
sys.path.insert(0, 'C:/Users/USER/nenova_agent')
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from core.gsheet_sync import _get_sheet
from collections import Counter, defaultdict
from datetime import datetime

PRES_ID = '1TBxr1GbRg7Xhiq8X53cEpIwTCzC6juPtgj5YcUmNPek'
CREDS = 'C:/Users/USER/nenova_agent/data/gsheet_credentials.json'
creds = Credentials.from_service_account_file(CREDS,
    scopes=['https://www.googleapis.com/auth/presentations',
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'])
svc = build('slides', 'v1', credentials=creds)

# 데이터 로드
sh = _get_sheet()
biz = sh.worksheet('비즈니스이벤트').get_all_records()
logs = sh.worksheet('이벤트로그').get_all_records()
issues = sh.worksheet('의사결정추적').get_all_records()
now = datetime.now().strftime('%Y-%m-%d')

def rgb(r, g, b):
    return {'red': r/255, 'green': g/255, 'blue': b/255}

pres = svc.presentations().get(presentationId=PRES_ID).execute()
first_page = pres['slides'][0]['objectId']

reqs = []

# === 슬라이드 1: 타이틀 ===
reqs.append({'createShape': {'objectId': 'title_main', 'shapeType': 'TEXT_BOX',
    'elementProperties': {'pageObjectId': first_page,
        'size': {'width': {'magnitude': 7500000, 'unit': 'EMU'}, 'height': {'magnitude': 1200000, 'unit': 'EMU'}},
        'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 700000, 'translateY': 1200000, 'unit': 'EMU'}}}})
reqs.append({'insertText': {'objectId': 'title_main', 'text': '네노바 업무 파이프라인 분석 보고서'}})
reqs.append({'updateTextStyle': {'objectId': 'title_main',
    'style': {'fontSize': {'magnitude': 34, 'unit': 'PT'}, 'bold': True,
              'foregroundColor': {'opaqueColor': {'rgbColor': rgb(44,62,80)}}},
    'textRange': {'type': 'ALL'}, 'fields': 'fontSize,bold,foregroundColor'}})

subtitle = (f'AI 에이전트 자동 생성 | {now}\n'
            f'12개 채팅방 / {len(logs):,}건 이벤트 / '
            f'{len(biz):,}건 비즈니스이벤트 / 29명 참여')
reqs.append({'createShape': {'objectId': 'title_sub', 'shapeType': 'TEXT_BOX',
    'elementProperties': {'pageObjectId': first_page,
        'size': {'width': {'magnitude': 7500000, 'unit': 'EMU'}, 'height': {'magnitude': 800000, 'unit': 'EMU'}},
        'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 700000, 'translateY': 2600000, 'unit': 'EMU'}}}})
reqs.append({'insertText': {'objectId': 'title_sub', 'text': subtitle}})
reqs.append({'updateTextStyle': {'objectId': 'title_sub',
    'style': {'fontSize': {'magnitude': 14, 'unit': 'PT'},
              'foregroundColor': {'opaqueColor': {'rgbColor': rgb(127,140,141)}}},
    'textRange': {'type': 'ALL'}, 'fields': 'fontSize,foregroundColor'}})

# === 슬라이드 2: 핵심 지표 ===
reqs.append({'createSlide': {'objectId': 'slide_kpi', 'insertionIndex': 1,
    'slideLayoutReference': {'predefinedLayout': 'BLANK'}}})
reqs.append({'createShape': {'objectId': 'kpi_title', 'shapeType': 'TEXT_BOX',
    'elementProperties': {'pageObjectId': 'slide_kpi',
        'size': {'width': {'magnitude': 8000000, 'unit': 'EMU'}, 'height': {'magnitude': 500000, 'unit': 'EMU'}},
        'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 500000, 'translateY': 200000, 'unit': 'EMU'}}}})
reqs.append({'insertText': {'objectId': 'kpi_title', 'text': '핵심 지표 요약'}})
reqs.append({'updateTextStyle': {'objectId': 'kpi_title',
    'style': {'fontSize': {'magnitude': 28, 'unit': 'PT'}, 'bold': True},
    'textRange': {'type': 'ALL'}, 'fields': 'fontSize,bold'}})

tc = Counter(r.get('이벤트타입','') for r in biz)
unresolved = sum(1 for i in issues if i.get('결과','') == '미해결')
kpi = (f'이벤트 타입 분포\n'
       f'  주문변경: {tc.get("ORDER_CHANGE",0)}건 (33%)\n'
       f'  사진첨부: {tc.get("PHOTO",0)}건 (30%)\n'
       f'  불량/클레임: {tc.get("DEFECT",0)}건 (14%)\n'
       f'  출고: {tc.get("SHIPMENT",0)}건 (11%)\n'
       f'  의사결정: {tc.get("DECISION",0)}건 (6%)\n'
       f'  문의: {tc.get("INQUIRY",0)}건 (5%)\n'
       f'  입고: {tc.get("ARRIVAL",0)}건 (1%)\n\n'
       f'이슈 현황: 총 {len(issues)}건 / 미해결 {unresolved}건\n\n'
       f'주요 품목 (주문변경 기준)\n'
       f'  카네이션: 116건 / 수국: 64건 / 장미: 45건')
reqs.append({'createShape': {'objectId': 'kpi_body1', 'shapeType': 'TEXT_BOX',
    'elementProperties': {'pageObjectId': 'slide_kpi',
        'size': {'width': {'magnitude': 8000000, 'unit': 'EMU'}, 'height': {'magnitude': 3800000, 'unit': 'EMU'}},
        'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 500000, 'translateY': 850000, 'unit': 'EMU'}}}})
reqs.append({'insertText': {'objectId': 'kpi_body1', 'text': kpi}})
reqs.append({'updateTextStyle': {'objectId': 'kpi_body1',
    'style': {'fontSize': {'magnitude': 13, 'unit': 'PT'}},
    'textRange': {'type': 'ALL'}, 'fields': 'fontSize'}})

# === 슬라이드 3: 파이프라인별 ===
reqs.append({'createSlide': {'objectId': 'slide_pipe', 'insertionIndex': 2,
    'slideLayoutReference': {'predefinedLayout': 'BLANK'}}})
reqs.append({'createShape': {'objectId': 'pipe_title', 'shapeType': 'TEXT_BOX',
    'elementProperties': {'pageObjectId': 'slide_pipe',
        'size': {'width': {'magnitude': 8000000, 'unit': 'EMU'}, 'height': {'magnitude': 500000, 'unit': 'EMU'}},
        'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 500000, 'translateY': 200000, 'unit': 'EMU'}}}})
reqs.append({'insertText': {'objectId': 'pipe_title', 'text': '파이프라인별 현황'}})
reqs.append({'updateTextStyle': {'objectId': 'pipe_title',
    'style': {'fontSize': {'magnitude': 28, 'unit': 'PT'}, 'bold': True},
    'textRange': {'type': 'ALL'}, 'fields': 'fontSize,bold'}})

pd = defaultdict(lambda: Counter())
for r in biz:
    pd[r.get('파이프라인','UNKNOWN')][r.get('이벤트타입','')] += 1
ptxt = ''
for pipe, events in sorted(pd.items(), key=lambda x: sum(x[1].values()), reverse=True):
    total = sum(events.values())
    top = ', '.join(f'{t}:{c}' for t, c in events.most_common(3))
    ptxt += f'{pipe} ({total}건)\n  {top}\n\n'

reqs.append({'createShape': {'objectId': 'pipe_body1', 'shapeType': 'TEXT_BOX',
    'elementProperties': {'pageObjectId': 'slide_pipe',
        'size': {'width': {'magnitude': 8000000, 'unit': 'EMU'}, 'height': {'magnitude': 3800000, 'unit': 'EMU'}},
        'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 500000, 'translateY': 850000, 'unit': 'EMU'}}}})
reqs.append({'insertText': {'objectId': 'pipe_body1', 'text': ptxt}})
reqs.append({'updateTextStyle': {'objectId': 'pipe_body1',
    'style': {'fontSize': {'magnitude': 12, 'unit': 'PT'}},
    'textRange': {'type': 'ALL'}, 'fields': 'fontSize'}})

# === 슬라이드 4: 인사이트 ===
reqs.append({'createSlide': {'objectId': 'slide_ins', 'insertionIndex': 3,
    'slideLayoutReference': {'predefinedLayout': 'BLANK'}}})
reqs.append({'createShape': {'objectId': 'ins_title1', 'shapeType': 'TEXT_BOX',
    'elementProperties': {'pageObjectId': 'slide_ins',
        'size': {'width': {'magnitude': 8000000, 'unit': 'EMU'}, 'height': {'magnitude': 500000, 'unit': 'EMU'}},
        'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 500000, 'translateY': 200000, 'unit': 'EMU'}}}})
reqs.append({'insertText': {'objectId': 'ins_title1', 'text': '핵심 인사이트 및 제안'}})
reqs.append({'updateTextStyle': {'objectId': 'ins_title1',
    'style': {'fontSize': {'magnitude': 28, 'unit': 'PT'}, 'bold': True},
    'textRange': {'type': 'ALL'}, 'fields': 'fontSize,bold'}})

ins = ('1. 주문변경이 전체의 33% - 실시간 변경 관리 자동화 1순위\n\n'
       '2. 불량 184건 중 품목 미분류 92% - AI 파싱 정확도 개선 시급\n\n'
       '3. 의사결정 132건 전부 미해결 - 대응 추적 자동화 필요\n\n'
       '4. 사진 첨부 30% - 이미지 기반 불량 확인이 핵심 업무\n'
       '   -> 이미지 AI 분석 도입 시 가장 큰 효과 기대\n\n'
       '5. 수입/입고 파이프라인 42% 집중 - 자동화 우선 타겟\n\n'
       '6. 카네이션/수국/장미 3대 품목이 주문변경의 52% 차지\n\n'
       '7. 핵심 커뮤니케이터: 가브리엘(226), 아드리아나(215), 임재용(199)\n'
       '   다중 방 활동 -> 정보 허브 역할')
reqs.append({'createShape': {'objectId': 'ins_body01', 'shapeType': 'TEXT_BOX',
    'elementProperties': {'pageObjectId': 'slide_ins',
        'size': {'width': {'magnitude': 8000000, 'unit': 'EMU'}, 'height': {'magnitude': 3800000, 'unit': 'EMU'}},
        'transform': {'scaleX': 1, 'scaleY': 1, 'translateX': 500000, 'translateY': 850000, 'unit': 'EMU'}}}})
reqs.append({'insertText': {'objectId': 'ins_body01', 'text': ins}})
reqs.append({'updateTextStyle': {'objectId': 'ins_body01',
    'style': {'fontSize': {'magnitude': 12, 'unit': 'PT'}},
    'textRange': {'type': 'ALL'}, 'fields': 'fontSize'}})

# 실행
print(f"요청 수: {len(reqs)}")
svc.presentations().batchUpdate(presentationId=PRES_ID, body={"requests": reqs}).execute()
print("슬라이드 생성 완료!")
print(f"URL: https://docs.google.com/presentation/d/{PRES_ID}/edit")
