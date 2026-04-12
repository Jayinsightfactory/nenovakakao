"""슬라이드 보고서 생성 스크립트 (1회성)"""
import sys, json
sys.path.insert(0, 'C:/Users/USER/nenova_agent')

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

PRES_ID = '1TBxr1GbRg7Xhiq8X53cEpIwTCzC6juPtgj5YcUmNPek'
CREDS = 'C:/Users/USER/nenova_agent/data/gsheet_credentials.json'

creds = Credentials.from_service_account_file(CREDS,
    scopes=['https://www.googleapis.com/auth/presentations'])
svc = build('slides', 'v1', credentials=creds)

with open('C:/Users/USER/nenova_agent/data/pipeline_config.json', encoding='utf-8') as f:
    config = json.load(f)

def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

COLORS = {
    "IMPORT": rgb(52,152,219), "QC": rgb(231,76,60), "INVENTORY": rgb(243,156,18),
    "ORDER": rgb(46,204,113), "DISTRIBUTE": rgb(155,89,182),
    "FIELD": rgb(52,73,94), "SYSTEM": rgb(149,165,166),
}

reqs = []
pres = svc.presentations().get(presentationId=PRES_ID).execute()
first_page = pres['slides'][0]['objectId']
stages = config.get("pipeline_stages", {})

# ── 1. 타이틀 ──
reqs.append({"createShape": {"objectId": "t1", "shapeType": "TEXT_BOX",
    "elementProperties": {"pageObjectId": first_page,
        "size": {"width": {"magnitude": 7500000, "unit": "EMU"}, "height": {"magnitude": 1200000, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 700000, "translateY": 1500000, "unit": "EMU"}}}})
reqs.append({"insertText": {"objectId": "t1", "text": "네노바 업무 파이프라인 보고서"}})
reqs.append({"updateTextStyle": {"objectId": "t1",
    "style": {"fontSize": {"magnitude": 36, "unit": "PT"}, "bold": True,
              "foregroundColor": {"opaqueColor": {"rgbColor": rgb(44,62,80)}}},
    "textRange": {"type": "ALL"}, "fields": "fontSize,bold,foregroundColor"}})

reqs.append({"createShape": {"objectId": "t2", "shapeType": "TEXT_BOX",
    "elementProperties": {"pageObjectId": first_page,
        "size": {"width": {"magnitude": 7500000, "unit": "EMU"}, "height": {"magnitude": 800000, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 700000, "translateY": 2800000, "unit": "EMU"}}}})
reqs.append({"insertText": {"objectId": "t2",
    "text": "AI \uc5d0\uc774\uc804\ud2b8 \uc790\ub3d9 \uc0dd\uc131 | 2026-04-11\n\ud654\ud6fc \uc218\uc785/\uc720\ud1b5 \uc804\uccb4 \uc5c5\ubb34 \ud750\ub984 \ubd84\uc11d\n7\ub2e8\uacc4 \ud30c\uc774\ud504\ub77c\uc778 \xb7 15\uac1c \uc5c5\ubb34\ubc29 \xb7 9\uba85 \ud575\uc2ec \uc778\ub825"}})
reqs.append({"updateTextStyle": {"objectId": "t2",
    "style": {"fontSize": {"magnitude": 16, "unit": "PT"},
              "foregroundColor": {"opaqueColor": {"rgbColor": rgb(127,140,141)}}},
    "textRange": {"type": "ALL"}, "fields": "fontSize,foregroundColor"}})

# ── 2. 파이프라인 흐름도 ──
reqs.append({"createSlide": {"objectId": "s2", "insertionIndex": 1,
    "slideLayoutReference": {"predefinedLayout": "BLANK"}}})
reqs.append({"createShape": {"objectId": "s2t", "shapeType": "TEXT_BOX",
    "elementProperties": {"pageObjectId": "s2",
        "size": {"width": {"magnitude": 8000000, "unit": "EMU"}, "height": {"magnitude": 500000, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 200000, "unit": "EMU"}}}})
reqs.append({"insertText": {"objectId": "s2t", "text": "\uc5c5\ubb34 \ud30c\uc774\ud504\ub77c\uc778 \uc804\uccb4 \uad6c\uc870"}})
reqs.append({"updateTextStyle": {"objectId": "s2t",
    "style": {"fontSize": {"magnitude": 28, "unit": "PT"}, "bold": True},
    "textRange": {"type": "ALL"}, "fields": "fontSize,bold"}})

stage_keys = ["IMPORT", "QC", "INVENTORY", "ORDER", "DISTRIBUTE", "FIELD", "SYSTEM"]
for i, key in enumerate(stage_keys):
    if key not in stages:
        continue
    info = stages[key]
    color = COLORS.get(key, rgb(100,100,100))
    bid = f"b{i}"
    col, row = i % 4, i // 4
    bw, bh = 1900000, 1600000
    x = 300000 + col * (bw + 200000)
    y = 900000 + row * (bh + 200000)

    reqs.append({"createShape": {"objectId": bid, "shapeType": "ROUND_RECTANGLE",
        "elementProperties": {"pageObjectId": "s2",
            "size": {"width": {"magnitude": bw, "unit": "EMU"}, "height": {"magnitude": bh, "unit": "EMU"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "EMU"}}}})
    reqs.append({"updateShapeProperties": {"objectId": bid,
        "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": color}, "alpha": 0.85}}},
        "fields": "shapeBackgroundFill"}})
    rooms_list = "\n".join(info.get("rooms", []))
    text = f"{info['name']}\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n{rooms_list}"
    reqs.append({"insertText": {"objectId": bid, "text": text}})
    nl = len(info['name'])
    reqs.append({"updateTextStyle": {"objectId": bid,
        "style": {"fontSize": {"magnitude": 14, "unit": "PT"}, "bold": True,
                  "foregroundColor": {"opaqueColor": {"rgbColor": rgb(255,255,255)}}},
        "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": nl},
        "fields": "fontSize,bold,foregroundColor"}})
    reqs.append({"updateTextStyle": {"objectId": bid,
        "style": {"fontSize": {"magnitude": 9, "unit": "PT"},
                  "foregroundColor": {"opaqueColor": {"rgbColor": rgb(236,240,241)}}},
        "textRange": {"type": "FIXED_RANGE", "startIndex": nl, "endIndex": len(text)},
        "fields": "fontSize,foregroundColor"}})

# ── 3. 인력 구조 ──
reqs.append({"createSlide": {"objectId": "s3", "insertionIndex": 2,
    "slideLayoutReference": {"predefinedLayout": "BLANK"}}})
reqs.append({"createShape": {"objectId": "s3t", "shapeType": "TEXT_BOX",
    "elementProperties": {"pageObjectId": "s3",
        "size": {"width": {"magnitude": 8000000, "unit": "EMU"}, "height": {"magnitude": 500000, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 200000, "unit": "EMU"}}}})
reqs.append({"insertText": {"objectId": "s3t", "text": "\uc8fc\uc694 \uc778\ub825 \ubc0f \ud30c\uc774\ud504\ub77c\uc778 \ubc30\uce58"}})
reqs.append({"updateTextStyle": {"objectId": "s3t",
    "style": {"fontSize": {"magnitude": 28, "unit": "PT"}, "bold": True},
    "textRange": {"type": "ALL"}, "fields": "fontSize,bold"}})

ptxt = "\uc774\ub984                    |  \uc5ed\ud560                |  \ud30c\uc774\ud504\ub77c\uc778\n"
ptxt += "\u2501" * 50 + "\n"
for name, info in config.get("key_personnel", {}).items():
    sn = stages.get(info.get("stage",""), {}).get("name", "")
    ptxt += f"{name:20s}  |  {info.get('role',''):15s}  |  {sn}\n"

reqs.append({"createShape": {"objectId": "s3b", "shapeType": "TEXT_BOX",
    "elementProperties": {"pageObjectId": "s3",
        "size": {"width": {"magnitude": 8000000, "unit": "EMU"}, "height": {"magnitude": 3800000, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 900000, "unit": "EMU"}}}})
reqs.append({"insertText": {"objectId": "s3b", "text": ptxt}})
reqs.append({"updateTextStyle": {"objectId": "s3b",
    "style": {"fontSize": {"magnitude": 13, "unit": "PT"}},
    "textRange": {"type": "ALL"}, "fields": "fontSize"}})

# ── 4. 품목/거래처 ──
reqs.append({"createSlide": {"objectId": "s4", "insertionIndex": 3,
    "slideLayoutReference": {"predefinedLayout": "BLANK"}}})
reqs.append({"createShape": {"objectId": "s4t", "shapeType": "TEXT_BOX",
    "elementProperties": {"pageObjectId": "s4",
        "size": {"width": {"magnitude": 8000000, "unit": "EMU"}, "height": {"magnitude": 500000, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 200000, "unit": "EMU"}}}})
reqs.append({"insertText": {"objectId": "s4t", "text": "\ucde8\uae09 \ud488\ubaa9 \ubc0f \uac70\ub798\ucc98 \ud604\ud669"}})
reqs.append({"updateTextStyle": {"objectId": "s4t",
    "style": {"fontSize": {"magnitude": 28, "unit": "PT"}, "bold": True},
    "textRange": {"type": "ALL"}, "fields": "fontSize,bold"}})

products = config.get("product_categories", {})
suppliers = config.get("suppliers", [])
ptxt2 = "[ \ud488\ubaa9 \uce74\ud14c\uace0\ub9ac ]\n\n"
for cat, vs in products.items():
    ptxt2 += f"  {cat}: {', '.join(vs[:10])}\n"
ptxt2 += f"\n[ \uac70\ub798\ucc98 {len(suppliers)}\uacf3 ]\n  "
ptxt2 += ", ".join(suppliers)

reqs.append({"createShape": {"objectId": "s4b", "shapeType": "TEXT_BOX",
    "elementProperties": {"pageObjectId": "s4",
        "size": {"width": {"magnitude": 8000000, "unit": "EMU"}, "height": {"magnitude": 3800000, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 900000, "unit": "EMU"}}}})
reqs.append({"insertText": {"objectId": "s4b", "text": ptxt2}})
reqs.append({"updateTextStyle": {"objectId": "s4b",
    "style": {"fontSize": {"magnitude": 11, "unit": "PT"}},
    "textRange": {"type": "ALL"}, "fields": "fontSize"}})

# ── 5. 3계층 구조 ──
reqs.append({"createSlide": {"objectId": "s5", "insertionIndex": 4,
    "slideLayoutReference": {"predefinedLayout": "BLANK"}}})
reqs.append({"createShape": {"objectId": "s5t", "shapeType": "TEXT_BOX",
    "elementProperties": {"pageObjectId": "s5",
        "size": {"width": {"magnitude": 8000000, "unit": "EMU"}, "height": {"magnitude": 500000, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 200000, "unit": "EMU"}}}})
reqs.append({"insertText": {"objectId": "s5t", "text": "\ub370\uc774\ud130 3\uacc4\uce35 \uad6c\uc870"}})
reqs.append({"updateTextStyle": {"objectId": "s5t",
    "style": {"fontSize": {"magnitude": 28, "unit": "PT"}, "bold": True},
    "textRange": {"type": "ALL"}, "fields": "fontSize,bold"}})

l3txt = (
    "Layer 1: \uc774\ubca4\ud2b8\ub85c\uadf8 (\uc6d0\ubcf8)\n"
    "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
    "\uc2dc\uac01 | \ubc29\uc774\ub984 | \ud30c\uc774\ud504\ub77c\uc778 | \ubc1c\uc2e0\uc790 | \uc6d0\ubb38 | \uba54\uc2dc\uc9c0ID\n\n"
    "Layer 2: \ube44\uc988\ub2c8\uc2a4\uc774\ubca4\ud2b8 (\ud30c\uc2f1)\n"
    "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
    "\uc774\ubca4\ud2b8ID | \uc774\ubca4\ud2b8\ud0c0\uc785 | \ucc28\uc218 | \ud488\ubaa9 | \uc218\ub7c9 | \uac70\ub798\ucc98 | \uc5f0\uad00ID\n"
    "\uc774\ubca4\ud2b8\ud0c0\uc785: DEFECT, ORDER_CHANGE, SHIPMENT, ARRIVAL, DECISION, INQUIRY, PHOTO\n\n"
    "Layer 3: \uc758\uc0ac\uacb0\uc815\ucd94\uc801 (\uc774\uc288\u2192\ub300\uc751\u2192\uacb0\uacfc)\n"
    "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
    "\uc774\uc288ID | \uc774\uc288\ub0b4\uc6a9 | \ub300\uc751\uc790 | \ub300\uc751\ub0b4\uc6a9 | \uc18c\uc694\uc2dc\uac04 | \uacb0\uacfc"
)
reqs.append({"createShape": {"objectId": "s5b", "shapeType": "TEXT_BOX",
    "elementProperties": {"pageObjectId": "s5",
        "size": {"width": {"magnitude": 8000000, "unit": "EMU"}, "height": {"magnitude": 3800000, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 900000, "unit": "EMU"}}}})
reqs.append({"insertText": {"objectId": "s5b", "text": l3txt}})
reqs.append({"updateTextStyle": {"objectId": "s5b",
    "style": {"fontSize": {"magnitude": 12, "unit": "PT"}},
    "textRange": {"type": "ALL"}, "fields": "fontSize"}})

# ── 실행 ──
print(f"요청 수: {len(reqs)}")
svc.presentations().batchUpdate(presentationId=PRES_ID, body={"requests": reqs}).execute()
print("슬라이드 생성 완료!")
print(f"URL: https://docs.google.com/presentation/d/{PRES_ID}/edit")
