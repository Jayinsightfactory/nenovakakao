import pytest
from core.defect_scope import upload_scope


@pytest.mark.parametrize('year,sequence,target',[
    (2026,'37-1',{'year':2026,'week':38}),
    (2026,'37-2',{'year':2026,'week':38}),
    (2026,'53-1',{'year':2027,'week':1}),
    (2025,'52-1',{'year':2026,'week':1}),
])
def test_post_in_next_week(year,sequence,target):
    row={'event':{'timestamp':f'{year}년 9월 16일'},'extracted':{'sequence':sequence}}
    assert upload_scope(row)==target
    assert row['extracted']['sequence']==sequence
