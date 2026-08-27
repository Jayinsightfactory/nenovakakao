"""Constrained LLM extraction for import orders; never invents master keys."""
import json, os, re

SYSTEM = '''Extract an import flower order from Korean Kakao text. Return JSON only:
{"staff":"", "customer":"", "week":"", "items":[{"category":"", "product":"", "quantity":1, "unit":"박스"}], "questions":[]}
Do not invent missing values. Use empty strings/null and add a short Korean question. Never output database keys.'''


def parse(text):
    key = os.getenv('ANTHROPIC_API_KEY', '').strip()
    if not key:
        return {'staff': '', 'customer': '', 'week': '', 'items': [],
                'questions': ['LLM API 키가 없어 주문 내용을 구조화하지 못했습니다.']}
    from anthropic import Anthropic
    response = Anthropic(api_key=key).messages.create(
        model=os.getenv('ORDER_LLM_MODEL', 'claude-sonnet-4-5'), max_tokens=1800,
        temperature=0, system=SYSTEM, messages=[{'role': 'user', 'content': text[:12000]}])
    raw = ''.join(block.text for block in response.content if getattr(block, 'type', '') == 'text')
    match = re.search(r'\{[\s\S]*\}', raw)
    if not match: raise RuntimeError('LLM JSON 응답 없음')
    value = json.loads(match.group())
    if not isinstance(value.get('items'), list): raise RuntimeError('LLM items 형식 오류')
    return value

