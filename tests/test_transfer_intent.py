import pytest
from core.keyword_forward import classify_transfer

@pytest.mark.parametrize('text',[
 '장미 10단 추가입고 안내', '취소완료', '*문라이트 오늘 추가건 우선 취소예정입니다',
 '록산느만 추가가능', '* 신라에서 블루 수국 15 박스 정도 추가 예정입니다.',
 '취소 가능 업체 확인 부탁합니다.', '항공편이 취소되었습니다. 도착이 목요일로 연기됩니다.',
 '재고 있으면 장미 추가 부탁드립니다', '장미 10단 추가하지말아주세요',
 '장미 10단 추가\n국화 2단 취소 가능할까요?',
])
def test_nonfinal_or_mixed_message_needs_review(text):
 assert classify_transfer(text)['decision']=='review'

@pytest.mark.parametrize('text',[
 '32-2차 수국 변경사항\n라움\n화이트 2대 취소 (라움잔량분이어서 검역표시하지말아주세요)\n초이문\n화이트 2대 추가\n화요일 출고입니다.',
 '프리덤 10단 전산에서 우선 취소 부탁드립니다. 확인 부탁드립니다',
 '동산 장미 10단 추가', '동산 출고일 9/10에서 9/11로 변경',
 '33-1차 유카리체리 1 추가입고\n아데나 미입고로\n아데나 취소필요\n수연,미정,꽃길,경부청화',
])
def test_operational_instruction_still_requires_approval(text):
 assert classify_transfer(text)['decision']=='forward'


def test_unsent_mixed_batch_preserves_review_originals():
 from core.keyword_approval import filter_queued_requests
 events=[{'content':'장미 10단 추가'},{'content':'장미 추가 입고 안내'}]
 rows={'x':{'status':'queued','events':events.copy(),'event':events[0]}}
 assert filter_queued_requests(rows)
 assert rows['x']['status']=='classification_review'
 assert rows['x']['events']==events


def test_manual_warehouse_done_claim_is_not_new_forward_instruction():
 assert classify_transfer('수국 출고일 변경\n주광\n화이트 5박스\n견적서 작업 (현장 지시 완료)')['decision']=='review'
 assert classify_transfer('수국 출고일 변경\n주광\n화이트 5박스\n현장 지시 미완료')['decision']=='forward'
