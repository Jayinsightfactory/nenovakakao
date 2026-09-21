"""Korean-transliteration matching for English catalog variety names.

Staff write varieties as Hangul sound-alikes ("피피", "보야저") while the ERP
master stores English names ("ALSTROMERIA Fifi", "Voyager"). Both sides are
reduced to a coarse consonant skeleton so p/f, b/v, l/r, g/k differences that
Hangul cannot express do not block a match. Selection stays conservative: a
clear best score and margin are required, otherwise only candidates return.
"""
import re
from difflib import SequenceMatcher

_INITIAL = ['k', 'k', 'n', 't', 't', 'r', 'm', 'p', 'p', 's', 's', '', 'j', 'j', 'j', 'k', 't', 'p', 'h']
_FINAL = ['', 'k', 'k', 'k', 'n', 'n', 'n', 't', 'r', 'k', 'm', 'p', 'r', 'r', 'p', 'r', 'm', 'p', 'p',
          's', 's', 'n', 'j', 'j', 'k', 't', 'p', 'h']
# Vowel classes: a / e(i) / o(u). ㅡ is epenthetic in loanwords and dropped.
_VOWEL = ['a', 'e', 'a', 'e', 'a', 'e', 'a', 'e', 'o', 'a', 'e', 'e', 'o', 'o', 'a', 'e', 'e', 'o', '', 'e', 'e']

_PREFIX = re.compile(
    r'^(?:spray\s+)?(?:rose|carnation|minicarnation|alstromeria|alstroemeria|hydrangea|'
    r'eucalyptus|agapanthus|limonium|ruscus|tulip|mokara|china)\b[\s/]*', re.I)


def hangul_key(text, vowels=False):
    out = []
    for ch in str(text or ''):
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(_INITIAL[code // 588])
            out.append(_VOWEL[(code % 588) // 28] or '-')
            out.append(_FINAL[code % 28])
        elif ch.isascii() and ch.isalnum():
            out.append(_latin_full(ch))
    return _finish(''.join(out), vowels)


def _latin_full(text):
    s = re.sub(r'[^a-z]', '', str(text or '').lower())
    for src, dst in (('stl', 'sl'), ('sch', 's'), ('tch', 'j'), ('ph', 'p'), ('sh', 's'), ('ch', 'j'), ('th', 't'),
                     ('ck', 'k'), ('qu', 'k'), ('gh', ''), ('kn', 'n'), ('wr', 'r')):
        s = s.replace(src, dst)
    s = re.sub(r'c(?=[eiy])', 's', s)
    s = re.sub(r'g(?=[eiy])', 'j', s)
    s = re.sub(r'e$', '', s) if len(s) > 3 else s
    # Hangul loanwords follow non-rhotic English: Voyager 보야저, Silver 실버.
    s = re.sub(r'(?<=[aeiouy])r(?![aeiouy])', '', s)
    return s.translate(str.maketrans({
        'c': 'k', 'g': 'k', 'q': 'k', 'x': 'ks', 'd': 't', 'b': 'p', 'f': 'p', 'v': 'p', 'z': 'j',
        'l': 'r', 'w': '', 'y': 'e', 'i': 'e', 'u': 'o'}))


def latin_key(text, vowels=False):
    return _finish(_latin_full(text), vowels)


def _finish(full, vowels):
    # Collapse doubled letters first (Lollipop, 롤리), then optionally drop the
    # vowels. Consonants separated by a vowel stay distinct (Fifi -> pp).
    full = re.sub(r'(.)\1+', r'\1', full)
    return full.replace('-', '') if vowels else re.sub(r'[aeo-]', '', full)


def variety_names(row):
    """English variety strings of one catalog row, without category/size."""
    names = []
    for value in (row.get('name'), row.get('name_en')):
        text = str(value or '')
        text = re.sub(r'\d+\s*cm\b|\d+\s*(?:g|kg)\b', ' ', text, flags=re.I)
        inner = re.findall(r'\(([^)]*)\)', text)
        text = re.sub(r'\([^)]*\)', ' ', text)
        for part in [text, *inner]:
            part = part.split('/')[-1] if '/' in part else part
            part = _PREFIX.sub('', part.strip())
            part = _PREFIX.sub('', part.strip())
            if re.search(r'[a-z]{2}', part, re.I):
                names.append(part.strip())
    return list(dict.fromkeys(names))


def score(term, name):
    a, b = hangul_key(term), latin_key(name)
    if len(a) < 2 or len(b) < 2:
        return 0.0
    base = SequenceMatcher(None, a, b).ratio()
    av, bv = hangul_key(term, True), latin_key(name, True)
    return 0.7 * base + 0.3 * SequenceMatcher(None, av, bv).ratio()


def match(term, rows, accept=0.86, margin=0.06):
    """Return (accepted_row_or_None, top candidates) by phonetic similarity."""
    if not re.search(r'[가-힣]', str(term or '')):
        return None, []
    ranked = []
    for row in rows:
        best = max((score(term, name) for name in variety_names(row)), default=0.0)
        if best:
            ranked.append((best, row))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    candidates = [row for value, row in ranked if value >= 0.6][:3]
    if not ranked or ranked[0][0] < accept:
        return None, candidates
    # Rows that share the winning variety text (same name, other sizes) are
    # resolved by the caller's size pool, so compare against a different name.
    top_names = {n.lower() for n in variety_names(ranked[0][1])}
    rival = next((value for value, row in ranked[1:]
                  if not top_names & {n.lower() for n in variety_names(row)}), 0.0)
    same_text = [row for value, row in ranked if value == ranked[0][0]]
    if ranked[0][0] - rival < margin or len(same_text) > 1:
        return None, candidates
    return ranked[0][1], candidates


def korean_parts(row):
    text = str(row.get('name') or '')
    text = text.split('/')[-1] if '/' in text else text
    parts = [re.sub(r'[^가-힣]', '', part) for part in re.split(r'[()\[\]]', text)]
    return [part for part in parts if len(part) >= 2]


def _variety_id(row):
    names = variety_names(row)
    latin = re.sub(r'[^a-z]', '', names[0].lower()) if names else ''
    return latin or ''.join(korean_parts(row))


def _base(rows):
    """One variety in several packings: prefer the everyday base row."""
    plain = [r for r in rows if not str(r.get('name', '')).startswith('[')] or rows
    size = lambda r: re.search(r'(\d+)\s*cm', r.get('name', ''), re.I)
    fifty = [r for r in plain if size(r) and size(r)[1] == '50' and '-' not in r['name'].split('50cm')[0][-3:]]
    unsized = [r for r in plain if not size(r)]
    pool = fifty or unsized or plain
    return min(pool, key=lambda r: (len(str(r.get('name', ''))), str(r.get('name', ''))))


def resolve(term, rows, accept=0.86, margin=0.015):
    """Pick a catalog row for a Hangul term: Korean name evidence, then sound."""
    key = re.sub(r'[^가-힣]', '', str(term or ''))
    if len(key) < 2:
        return None, []
    group = [r for r in rows if key in korean_parts(r) or key == ''.join(korean_parts(r))]
    if not group:
        # A partial Korean hit ("아프리콧" inside "아프리콧 테라짜") counts only
        # when every hit is the same variety; that is checked below.
        group = [r for r in rows if any(key in part for part in korean_parts(r))]
    if not group:
        ranked = sorted(((max((score(term, n) for n in variety_names(r)), default=0.0), r) for r in rows),
                        key=lambda pair: pair[0], reverse=True)
        if not ranked or ranked[0][0] < accept:
            return None, [r for value, r in ranked if value >= 0.6][:3]
        # Packings of one variety share the exact score; a different variety
        # must trail by a clear margin or the term stays a question.
        group = [r for value, r in ranked if ranked[0][0] - value <= margin]
    plain = [r for r in group if not str(r.get('name', '')).startswith('[')] or group
    names = [str(r.get('name', '')).strip() for r in plain]
    # Two catalog rows with the same name are a master-data ambiguity that
    # only staff can settle; several varieties likewise stay a question.
    if len(set(names)) != len(names) or len({_variety_id(r) for r in plain}) != 1:
        return None, plain[:3]
    return _base(plain), plain[:3]
