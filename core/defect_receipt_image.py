"""Render a clearly labelled receipt from freshly verified ERP ledger rows."""
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def create(row, target, adapter):
    adapter.request_id = row['id']
    entries = adapter.lookup(row)
    if not entries or not adapter.matches(row, entries):
        raise ValueError('처리 확인서 생성 보류: 실제 원장과 등록 내용 불일치')
    font_path = Path('C:/Windows/Fonts/malgun.ttf')
    font = ImageFont.truetype(str(font_path), 29)
    title_font = ImageFont.truetype(str(font_path), 40)
    width, margin = 1120, 50
    canvas = Image.new('RGB', (width, 100), 'white')
    pen = ImageDraw.Draw(canvas)
    lines = []
    def add(text):
        line = ''
        for char in str(text):
            if pen.textlength(line + char, font=font) > width - margin * 2:
                lines.append(line); line = ''
            line += char
        lines.append(line)
    add('원장 재조회로 확인한 영업 입력 저장 결과')
    add('')
    for entry in entries:
        add(f"{entry['orderYear']}년 {entry['orderWeek']}차 · {entry['managerName']}")
        add(entry['customerName'])
        add(entry.get('matchedProductName') or entry.get('productName', ''))
        add(f"수량: {entry['quantity']}{entry['sourceUnit']}    저장번호: {entry['deductionKey']}")
        add('')
    add('요청번호: ' + row['id'])
    add('원장 확인: ' + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    image = Image.new('RGB', (width, 150 + len(lines) * 47), '#f5f7fa')
    pen = ImageDraw.Draw(image)
    pen.rectangle((0, 0, width, 105), fill='#176443')
    pen.text((margin, 24), '네노바 불량 처리 확인서', font=title_font, fill='white')
    y = 130
    for line in lines:
        pen.text((margin, y), line, font=font, fill='#17212b')
        y += 47
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    image.save(target, 'PNG')
    return entries
