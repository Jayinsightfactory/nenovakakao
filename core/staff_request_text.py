"""Original Kakao context precedes staff clarification and approval requests."""
def original_first(row, request):
    event = row.get('event') or {}
    original = event.get('content') or row.get('raw') or ''
    lines = ['[카톡 원문]']
    context = ' · '.join(str(v) for v in (event.get('sender_name'), event.get('timestamp')) if v)
    if context:
        lines.append(context)
    lines.append(str(original).strip() or '(저장된 원문 없음)')
    lines.extend(['', '[확인할 내용]', request])
    return '\n'.join(lines)
