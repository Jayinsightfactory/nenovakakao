"""Sync only A:R of a dedicated order-review tab; S:V belongs to the reviewer."""
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.order_review import TITLE, HEADERS, review_rows, plan_updates
from core.atomic_json import save


def sync(sh):
    state = json.loads((ROOT / 'data' / 'import_order_state.json').read_text(encoding='utf-8'))
    meta = sh.fetch_sheet_metadata()
    tab = next((x for x in meta['sheets'] if x['properties']['title'] == TITLE), None)
    if tab is None:
        allocated = sum(x['properties'].get('gridProperties', {}).get('rowCount', 0) *
                        x['properties'].get('gridProperties', {}).get('columnCount', 0)
                        for x in meta['sheets'])
        if allocated + 22000 > 10000000: raise RuntimeError('검토 시트 생성 공간 부족')
        ws = sh.add_worksheet(TITLE, rows=1000, cols=22)
        ws.update(range_name='A1:V1', values=[HEADERS], value_input_option='RAW')
        sid = ws.id
        sh.batch_update({'requests': [
            {'setBasicFilter': {'filter': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1000, 'startColumnIndex': 0, 'endColumnIndex': 22}}}},
            {'updateSheetProperties': {'properties': {'sheetId': sid, 'gridProperties': {'frozenRowCount': 1, 'frozenColumnCount': 2}}, 'fields': 'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'}},
            {'repeatCell': {'range': {'sheetId': sid}, 'cell': {'userEnteredFormat': {'wrapStrategy': 'WRAP', 'verticalAlignment': 'TOP', 'textFormat': {'fontSize': 10}}}, 'fields': 'userEnteredFormat'}},
            {'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1}, 'cell': {'userEnteredFormat': {'backgroundColor': {'red': 0.12, 'green': 0.22, 'blue': 0.35}, 'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}}}}, 'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat'}},
            {'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 1, 'startColumnIndex': 18, 'endColumnIndex': 22}, 'cell': {'userEnteredFormat': {'backgroundColor': {'red': 1, 'green': 0.97, 'blue': 0.82}}}, 'fields': 'userEnteredFormat.backgroundColor'}},
            {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 22}, 'properties': {'pixelSize': 140}, 'fields': 'pixelSize'}},
            {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 17, 'endIndex': 18}, 'properties': {'pixelSize': 420}, 'fields': 'pixelSize'}},
            {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 21, 'endIndex': 22}, 'properties': {'pixelSize': 280}, 'fields': 'pixelSize'}}]})
    else:
        ws = sh.worksheet(TITLE)
    existing = []
    for start in range(1, ws.row_count + 1, 2000):
        block = ws.get(f'A{start}:V{min(ws.row_count, start+1999)}', value_render_option='UNFORMATTED_VALUE')
        if block:
            existing += [[]] * (start - 1 - len(existing))
            existing.extend(block)
    if not existing or existing[0] != HEADERS: raise RuntimeError('검토 시트 헤더 불일치')
    updates, last = plan_updates(existing, review_rows(state))
    if last > ws.row_count: ws.resize(rows=last + 100)
    for start in range(0, len(updates), 20):
        batch = updates[start:start+20]
        ws.batch_update([dict(item) for item in batch], value_input_option='RAW')
        actual = ws.batch_get([item['range'] for item in batch], value_render_option='UNFORMATTED_VALUE')
        if len(actual) != len(batch): raise RuntimeError('검토 시트 재조회 범위 누락')
        for expected, got in zip(batch, actual):
            value = list(got[0]) if got else []
            value += [''] * (18 - len(value))
            if value != expected['values'][0]: raise RuntimeError('검토 시트 저장 재조회 불일치')
    result = {'at': time.time(), 'sheet_id': ws.id, 'url': sh.url + '#gid=' + str(ws.id),
              'updated_rows': len(updates), 'review_rows': len(review_rows(state)), 'status': 'ok'}
    save(ROOT / 'data' / 'order_review_sync_status.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    import msvcrt
    with (ROOT / 'data' / 'order_review_sync.lock').open('a+b') as lock:
        lock.seek(0)
        try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError: raise SystemExit(0)
        from scripts.incremental_sync import _get_gspread_client
        try:
            sync(_get_gspread_client())
        except Exception as exc:
            path = ROOT / 'data' / 'order_review_sync_status.json'
            previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            previous.update(at=time.time(), status='error', error_type=type(exc).__name__)
            save(path, previous)
            raise
