"""Auditable defect vocabulary and conservative name matching.

Vocabulary reviewed against the 2026 week-36 development corpus/catalog.
No customer quantities, evaluation answers, or approvals are inferred here.
"""
import re
from difflib import SequenceMatcher


def norm(value):
    return re.sub(r'[^a-z0-9가-힣]', '', str(value or '').lower())


PRODUCT_NAMES = {
    '모멘텀': 'ROSE / Momentum 50cm',
    '롤리팝화이트블루': 'ROSE / Lollipop White Blue 50cm',
    '하츠': 'ROSE / Hearts 50cm', '아가판글리스터': 'Agapanthus / Gletsjer',
    '문라이트': 'CARNATION Moon Light', '몬디알화이트': 'ROSE / Mondial White 50cm',
    '플라야블랑카': 'ROSE / Playa Blanca 50cm', '프라도민트': 'CARNATION Prado Mint',
    '비스위트': 'ROSE / Be Sweet 50cm', '비스윗': 'ROSE / Be Sweet 50cm',
    '시네신스': 'CHINA / 리모늄 시네신스 화이트 (Sinensis white) 500g',
    '만달라': 'ROSE / Mandala 50cm', '헤르모사': 'ROSE / Hermosa(Ossimo) 50cm',
    '헤르메스오렌지': 'CARNATION Hermes Orange', '유카리체리': 'CARNATION Yukari Cherry',
    '돈셀': 'CARNATION Doncel', '알테어': 'CARNATION Altair',
    '사가': 'ROSE / Saga 50cm', '로다스': 'CARNATION rodas',
    '지오지아': 'CARNATION Giogia', '노비아': 'CARNATION Novia',
    '크리미아': 'CARNATION Crimea', '마리포사': 'CARNATION Mariposa',
    '캐롤라인골드': 'CARNATION Caroline Gold', '유카리오스쿠로': 'CARNATION Yukari Oscuro',
    '틴티드블루': 'ROSE / Tinted Blue 50cm', '오션송': 'ROSE / Ocean song 50cm',
    '폴림니아': 'CARNATION Polimnia', '클리어워터': 'CARNATION Clear Water',
    '캔들라이트': 'ROSE / Candlelight 50cm', '스위트마마': 'ROSE / Sweet Mama 50cm',
    '맘마미아': 'ROSE / Mama Mia 50cm', '베로나핑크': 'CARNATION Verona Pink',
    '퀵샌드': 'ROSE / Quick Sand 50cm', '카라멜': 'CARNATION Caramel',
    '핑크플로이드': 'ROSE / Pink Floyd (Hot Pink) 50cm',
    '몬디일화이트': 'ROSE / Mondial White 50cm',
    '네스': 'CARNATION Ness', '카오리': 'CARNATION Kaori', '체리오': 'CARNATION Cherrio',
    '쥬리고': 'CARNATION Zurigo', '이케바나': 'CARNATION Ikebana',
    '오스쿠로': 'CARNATION Yukari Oscuro',
    '마이라화이트': 'ROSE / Garden mayra white 50cm',
    '프라우드': 'ROSE CHINA / 프라우드(White proud)',
    '케서린': 'ROSE CHINA / 안슬레이(Annesley/Katherin)',
    '캐서린': 'ROSE CHINA / 안슬레이(Annesley/Katherin)',
    '스프레이아틱': 'MiniCarnation Artic/Ibis(화이트)',
    'sp아틱': 'MiniCarnation Artic/Ibis(화이트)',
    '마루치': 'CARNATION Maruchi', '헤르메스': 'CARNATION Hermes',
    '코랄리프': 'ROSE / Coral Reef 50cm',
    '미스티블루': 'CHINA / 리모늄 미스티 블루(Limonium Misty blue) 500g',
    '버터컵': 'ROSE CHINA / 버터컵(Butter cup)',
    '레드니스': 'ROSE CHINA / 레드니스(Redness)',
    '아프리콧테라짜': 'SPRAY ROSE CHINA / 아프리콧 테라짜 (Spray-rose Apricot Terrazza)',
    '블랙잭': 'Eucalyptus CHINA / 블랙잭 (베이비 블루) (대) 1Kg',
}

ORIGIN_NAMES = {'중국': {
    '킴벌리': 'ROSE CHINA / 킴벌리(Kimberly)',
    '로맨틱비치': 'ROSE CHINA / 로맨틱 비치(Romantic Beach)',
    '나오미': 'ROSE CHINA / 나오미(Naomi)',
    '아발란체': 'ROSE CHINA / 아바란체(Avalanche)',
    '아바란체': 'ROSE CHINA / 아바란체(Avalanche)',
    '나이팅게일': 'ROSE CHINA / 나이팅게일(Nightingale, Purple Fairy)',
    '만달라': 'ROSE CHINA / 만달라(Mandala) MQ',
}}
CUSTOMER_NAMES = {
    '그린': '그린화원', '미우': '아이엠（미우）', '대구희경': '희경(Hee Kyoung)',
    '꽃동산': '동산(꽃동산)', '라움': '주식회사 트라움에스앤씨 (라움)',
    '일신': '일신원예', '서부꽃집': '부산 서부꽃집', '영남소재': '(주)영남꽃소재',
    '은성꽃도매': '은성아트제단（은성꽃도매）', '미정': '미정화훼유통',
    '부산화월': '주식회사 화월', '중앙화훼': '중앙화훼유통',
    '청지': '(주)청지화원', '신초원': '초원', '미카엘': '(주)미카엘플라워',
    '상희': '상희꽃상사', '수경': '수경원예', '수연': '수연원예',
    '나래꽃': '나래꽃', '대한': '대한꽃집', '알파': '알파플라워',
    '주광': '주광농원', '유오디아': '유오디아 꽃마을', '영남가빈': '인터넷공판장 (영남가빈)',
    '광주천사': '월드천사', '양주태광': '태광플라워 양주지점',
    '남대문청화': '청화꽃집', '꿀벌': '주식회사 꿀벌원예',
    '매일': '날마다, 꽃（매일）', '경향': '경향농원', '성남': '성남원예',
    '원협가빈': '인터넷공판장 (영남가빈)', '경남꽃도매': '경남꽃도매(영남4호)',
    '초이문': '초이문(센스앤센서빌러티)', '레바논': '레바논 꽃방',
    '대지': '대지원예2', '자연': '자연플라워 (중매 1493)',
    '플로르아름': '에프에스오2025(fso2025)',
}

CATEGORY_NAMES = {
    '수국': {'화이트':'Hydrangea White (화이트)', '블루':'Hydrangea Blue (블루）',
           '라벤더':'Hydrangea Lavender (라벤더)', '라벤다':'Hydrangea Lavender (라벤더)',
           '피치':'Hydrangea Peach (Florentina) 피치', '버건디':'Hydrangea Burgundy (버건디)',
           '진그린':'Hydrangea G/ Esmeral (진그린)', '진핑크':'Hydrangea Dark Pink (진핑크)'},
}


def match(term, rows, vocabulary=None):
    term = str(term or '').strip()
    if not term:
        return None, []
    if term.isdecimal():
        found = [r for r in rows if str(r.get('nenova_key')) == term]
        return (found[0] if len(found)==1 else None), found
    # Explicit lengths must not silently reuse the 50cm vocabulary entry.
    bare = re.sub(r'\d+\s*cm', '', term, flags=re.I).strip()
    target = (vocabulary or {}).get(norm(bare))
    if target:
        size = re.search(r'(\d+)\s*cm', term, re.I)
        if size: target = target.replace('50cm', size[1]+'cm')
        found = [r for r in rows if str(r.get('name', '')).strip() == target]
        if len(found)==1: return found[0], found
        # The curated target is unavailable in this origin/category/size pool.
        # Do not silently substitute another origin bearing the same alias.
        return None, found
    key = norm(term)
    ranked = []
    for row in rows:
        aliases = row.get('name_alias') or []
        if isinstance(aliases, str): aliases = [aliases]
        names = [row.get('name'), row.get('name_en'), *aliases]
        keys = [norm(n) for n in names if n and not norm(n).isdecimal()]
        score = max([1 if key == n else 0.92 if len(key)>=2 and key in n
                     else SequenceMatcher(None,key,n).ratio() for n in keys] or [0])
        ranked.append((score,row))
    ranked.sort(key=lambda p:p[0],reverse=True)
    candidates = [r for score,r in ranked if score>=0.55][:3]
    accepted = ranked[0][1] if ranked and ranked[0][0]>=0.92 and (
        len(ranked)==1 or ranked[0][0]-ranked[1][0]>=0.04) else None
    return accepted,candidates


def customer(source, rows):
    def exact_customer(term):
        chosen, options = match(term, rows, CUSTOMER_NAMES)
        if chosen and norm(term) not in CUSTOMER_NAMES:
            aliases = chosen.get('name_alias') or []
            if isinstance(aliases,str): aliases=[aliases]
            names = [chosen.get('name',''),*aliases]
            if norm(term) not in {norm(n) for n in names if n and not norm(n).isdecimal()}:
                chosen = None
        return chosen, options
    chosen, options = exact_customer(source.get('customer'))
    if chosen: return chosen, options
    found = {}
    for term in source.get('customer_candidates', []):
        candidate, _ = exact_customer(term)
        if candidate: found[str(candidate['nenova_key'])] = candidate
    if len(found)==1:
        chosen = next(iter(found.values()))
        source['customer'] = chosen['name']
        source['issues'] = [i for i in source['issues'] if i not in (
            '거래처 없음','거래처/농장/품종 후보 복수; 확인 필요')]
        return chosen,[chosen]
    return None, options
