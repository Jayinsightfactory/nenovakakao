"""
구글 슬라이드 보고서 생성 + 카카오워크 전송

네노바 업무 파이프라인 현황을 슬라이드로 시각화하고
관리자전용톡방에 링크를 전송한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

CREDS_FILE = Path(__file__).parent.parent / "data" / "gsheet_credentials.json"
PIPELINE_CONFIG = Path(__file__).parent.parent / "data" / "pipeline_config.json"

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]


def _get_services():
    creds = Credentials.from_service_account_file(str(CREDS_FILE), scopes=SCOPES)
    slides = build("slides", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    return slides, drive


def _load_pipeline() -> dict:
    with open(PIPELINE_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def _rgb(r, g, b):
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


STAGE_COLORS = {
    "IMPORT":    _rgb(52, 152, 219),   # 파랑
    "QC":        _rgb(231, 76, 60),    # 빨강
    "INVENTORY": _rgb(243, 156, 18),   # 주황
    "ORDER":     _rgb(46, 204, 113),   # 초록
    "DISTRIBUTE":_rgb(155, 89, 182),   # 보라
    "FIELD":     _rgb(52, 73, 94),     # 남색
    "SYSTEM":    _rgb(149, 165, 166),  # 회색
}


def create_report() -> str:
    """
    파이프라인 보고서 슬라이드를 생성한다.

    Returns:
        슬라이드 URL
    """
    slides_svc, drive_svc = _get_services()
    config = _load_pipeline()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── 1. 프레젠테이션 생성 ──
    pres = slides_svc.presentations().create(body={
        "title": f"네노바 업무 파이프라인 보고서 ({now})"
    }).execute()
    pres_id = pres["presentationId"]

    # 관리자 계정에 편집 권한 부여
    admin_email = os.getenv("ADMIN_GOOGLE_EMAIL", "dlaww584@gmail.com")
    try:
        drive_svc.permissions().create(
            fileId=pres_id,
            body={"type": "user", "role": "writer", "emailAddress": admin_email},
            fields="id",
        ).execute()
    except Exception:
        # 권한 부여 실패 시 anyone으로 폴백
        drive_svc.permissions().create(
            fileId=pres_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()

    requests = []

    # ── 2. 타이틀 슬라이드 (기본 첫 페이지) ──
    title_page = pres["slides"][0]
    title_id = title_page["objectId"]

    # 타이틀 텍스트 찾기
    for elem in title_page.get("pageElements", []):
        shape = elem.get("shape", {})
        ph = shape.get("placeholder", {})
        if ph.get("type") == "CENTERED_TITLE" or ph.get("type") == "TITLE":
            requests.append({
                "insertText": {
                    "objectId": elem["objectId"],
                    "text": "네노바 업무 파이프라인 보고서",
                }
            })
        elif ph.get("type") == "SUBTITLE":
            requests.append({
                "insertText": {
                    "objectId": elem["objectId"],
                    "text": f"AI 에이전트 자동 생성 | {now}\n화훼 수입/유통 업무 흐름 분석",
                }
            })

    # ── 3. 파이프라인 개요 슬라이드 ──
    overview_id = "pipeline_overview"
    requests.append({
        "createSlide": {
            "objectId": overview_id,
            "insertionIndex": 1,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }
    })

    # 제목
    requests.append({
        "createShape": {
            "objectId": "overview_title",
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": overview_id,
                "size": {"width": {"magnitude": 8000000, "unit": "EMU"},
                         "height": {"magnitude": 600000, "unit": "EMU"}},
                "transform": {"scaleX": 1, "scaleY": 1,
                              "translateX": 500000, "translateY": 200000,
                              "unit": "EMU"},
            },
        }
    })
    requests.append({
        "insertText": {"objectId": "overview_title",
                       "text": "업무 파이프라인 전체 구조"}
    })
    requests.append({
        "updateTextStyle": {
            "objectId": "overview_title",
            "style": {"fontSize": {"magnitude": 24, "unit": "PT"},
                      "bold": True},
            "textRange": {"type": "ALL"},
            "fields": "fontSize,bold",
        }
    })

    # 파이프라인 흐름도 (좌→우 박스)
    stages = config.get("pipeline_stages", {})
    stage_keys = ["IMPORT", "QC", "INVENTORY", "ORDER", "DISTRIBUTE", "FIELD", "SYSTEM"]
    box_w = 1100000
    box_h = 700000
    gap = 150000
    start_x = 300000
    start_y = 1000000

    for i, key in enumerate(stage_keys):
        if key not in stages:
            continue
        info = stages[key]
        color = STAGE_COLORS.get(key, _rgb(100, 100, 100))
        box_id = f"stage_box_{key}"
        label_id = f"stage_label_{key}"

        col = i % 4
        row = i // 4
        x = start_x + col * (box_w + gap)
        y = start_y + row * (box_h + gap + 400000)

        # 박스
        requests.append({
            "createShape": {
                "objectId": box_id,
                "shapeType": "ROUND_RECTANGLE",
                "elementProperties": {
                    "pageObjectId": overview_id,
                    "size": {"width": {"magnitude": box_w, "unit": "EMU"},
                             "height": {"magnitude": box_h, "unit": "EMU"}},
                    "transform": {"scaleX": 1, "scaleY": 1,
                                  "translateX": x, "translateY": y,
                                  "unit": "EMU"},
                },
            }
        })
        requests.append({
            "updateShapeProperties": {
                "objectId": box_id,
                "shapeProperties": {
                    "shapeBackgroundFill": {
                        "solidFill": {"color": {"rgbColor": color}, "alpha": 0.9}
                    }
                },
                "fields": "shapeBackgroundFill",
            }
        })

        # 텍스트: 단계명 + 방 목록
        rooms_text = "\n".join(info.get("rooms", []))
        text = f"{info['name']}\n\n{rooms_text}"
        requests.append({"insertText": {"objectId": box_id, "text": text}})
        requests.append({
            "updateTextStyle": {
                "objectId": box_id,
                "style": {"fontSize": {"magnitude": 10, "unit": "PT"},
                          "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(255, 255, 255)}},
                          "bold": True},
                "textRange": {"type": "FIXED_RANGE", "startIndex": 0,
                              "endIndex": len(info["name"])},
                "fields": "fontSize,foregroundColor,bold",
            }
        })
        requests.append({
            "updateTextStyle": {
                "objectId": box_id,
                "style": {"fontSize": {"magnitude": 8, "unit": "PT"},
                          "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(236, 240, 241)}}},
                "textRange": {"type": "FIXED_RANGE",
                              "startIndex": len(info["name"]) + 1,
                              "endIndex": len(text)},
                "fields": "fontSize,foregroundColor",
            }
        })

    # 화살표 (단계 간 연결)
    arrow_pairs = [(0, 1), (1, 2), (2, 3), (3, 4)]
    for idx, (f, t) in enumerate(arrow_pairs):
        if f >= len(stage_keys) or t >= len(stage_keys):
            continue
        f_col = f % 4
        t_col = t % 4
        f_row = f // 4
        t_row = t // 4
        ax1 = start_x + f_col * (box_w + gap) + box_w
        ay1 = start_y + f_row * (box_h + gap + 400000) + box_h // 2
        ax2 = start_x + t_col * (box_w + gap)
        ay2 = start_y + t_row * (box_h + gap + 400000) + box_h // 2

        requests.append({
            "createLine": {
                "objectId": f"arrow_{idx}",
                "lineCategory": "STRAIGHT",
                "elementProperties": {
                    "pageObjectId": overview_id,
                    "size": {"width": {"magnitude": ax2 - ax1, "unit": "EMU"},
                             "height": {"magnitude": 1, "unit": "EMU"}},
                    "transform": {"scaleX": 1, "scaleY": 1,
                                  "translateX": ax1, "translateY": ay1,
                                  "unit": "EMU"},
                },
            }
        })

    # ── 4. 인력 구조 슬라이드 ──
    personnel_id = "personnel_slide"
    requests.append({
        "createSlide": {
            "objectId": personnel_id,
            "insertionIndex": 2,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }
    })
    requests.append({
        "createShape": {
            "objectId": "personnel_title",
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": personnel_id,
                "size": {"width": {"magnitude": 8000000, "unit": "EMU"},
                         "height": {"magnitude": 600000, "unit": "EMU"}},
                "transform": {"scaleX": 1, "scaleY": 1,
                              "translateX": 500000, "translateY": 200000,
                              "unit": "EMU"},
            },
        }
    })
    requests.append({
        "insertText": {"objectId": "personnel_title",
                       "text": "주요 인력 및 역할"}
    })
    requests.append({
        "updateTextStyle": {
            "objectId": "personnel_title",
            "style": {"fontSize": {"magnitude": 24, "unit": "PT"}, "bold": True},
            "textRange": {"type": "ALL"},
            "fields": "fontSize,bold",
        }
    })

    # 인력 테이블 텍스트
    personnel = config.get("key_personnel", {})
    personnel_text = "이름 | 역할 | 파이프라인 단계\n"
    personnel_text += "─" * 40 + "\n"
    for name, info in personnel.items():
        stage_name = _get_stage_name_from_config(config, info.get("stage", ""))
        personnel_text += f"{name} | {info.get('role', '')} | {stage_name}\n"

    requests.append({
        "createShape": {
            "objectId": "personnel_body",
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": personnel_id,
                "size": {"width": {"magnitude": 8000000, "unit": "EMU"},
                         "height": {"magnitude": 3500000, "unit": "EMU"}},
                "transform": {"scaleX": 1, "scaleY": 1,
                              "translateX": 500000, "translateY": 1000000,
                              "unit": "EMU"},
            },
        }
    })
    requests.append({"insertText": {"objectId": "personnel_body", "text": personnel_text}})
    requests.append({
        "updateTextStyle": {
            "objectId": "personnel_body",
            "style": {"fontSize": {"magnitude": 12, "unit": "PT"},
                      "fontFamily": "Noto Sans KR"},
            "textRange": {"type": "ALL"},
            "fields": "fontSize,fontFamily",
        }
    })

    # ── 5. 품목/거래처 슬라이드 ──
    products_id = "products_slide"
    requests.append({
        "createSlide": {
            "objectId": products_id,
            "insertionIndex": 3,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }
    })
    requests.append({
        "createShape": {
            "objectId": "products_title",
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": products_id,
                "size": {"width": {"magnitude": 8000000, "unit": "EMU"},
                         "height": {"magnitude": 600000, "unit": "EMU"}},
                "transform": {"scaleX": 1, "scaleY": 1,
                              "translateX": 500000, "translateY": 200000,
                              "unit": "EMU"},
            },
        }
    })
    requests.append({"insertText": {"objectId": "products_title", "text": "취급 품목 및 거래처"}})
    requests.append({
        "updateTextStyle": {
            "objectId": "products_title",
            "style": {"fontSize": {"magnitude": 24, "unit": "PT"}, "bold": True},
            "textRange": {"type": "ALL"},
            "fields": "fontSize,bold",
        }
    })

    products = config.get("product_categories", {})
    suppliers = config.get("suppliers", [])

    prod_text = "[ 품목 카테고리 ]\n\n"
    for cat, varieties in products.items():
        prod_text += f"{cat}: {', '.join(varieties[:8])}"
        if len(varieties) > 8:
            prod_text += f" 외 {len(varieties)-8}종"
        prod_text += "\n"

    prod_text += f"\n[ 거래처 {len(suppliers)}개 ]\n"
    prod_text += ", ".join(suppliers[:15])
    if len(suppliers) > 15:
        prod_text += f" 외 {len(suppliers)-15}곳"

    requests.append({
        "createShape": {
            "objectId": "products_body",
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": products_id,
                "size": {"width": {"magnitude": 8000000, "unit": "EMU"},
                         "height": {"magnitude": 3500000, "unit": "EMU"}},
                "transform": {"scaleX": 1, "scaleY": 1,
                              "translateX": 500000, "translateY": 1000000,
                              "unit": "EMU"},
            },
        }
    })
    requests.append({"insertText": {"objectId": "products_body", "text": prod_text}})
    requests.append({
        "updateTextStyle": {
            "objectId": "products_body",
            "style": {"fontSize": {"magnitude": 11, "unit": "PT"},
                      "fontFamily": "Noto Sans KR"},
            "textRange": {"type": "ALL"},
            "fields": "fontSize,fontFamily",
        }
    })

    # ── 6. 요청 실행 ──
    if requests:
        slides_svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={"requests": requests},
        ).execute()

    url = f"https://docs.google.com/presentation/d/{pres_id}/edit"
    print(f"[SLIDE] 보고서 생성 완료: {url}")
    return url


def _get_stage_name_from_config(config: dict, stage_key: str) -> str:
    return config.get("pipeline_stages", {}).get(stage_key, {}).get("name", stage_key)


if __name__ == "__main__":
    url = create_report()
    print(f"\nURL: {url}")
