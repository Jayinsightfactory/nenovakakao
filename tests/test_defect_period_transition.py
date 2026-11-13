import pytest
from core.defect_period_transition import resolve

BASE = {'year': 2026, 'week': 40}
H = {'ActionType': 'REGISTER_ESTIMATE', 'HistoryKey': '1', 'ChangedAt': '2026-10-06T14:40:00Z', 'AfterJson': '{"appliedOrderYear":2026,"appliedOrderWeek":"40","deductionType":"불량차감","estimateKey":1}'}
def fetch(p):
    return {'rows': [], 'history': [H] if p['week']==40 else []}

@pytest.mark.parametrize('time,week', [('2026년 10월 5일 오후 2:00',40),('2026년 10월 7일 오전 9:00',41)])
def test_source_time_not_processing_time(time, week):
    p,e = resolve({'event':{'timestamp':time}},BASE,fetch)
    assert p['week']==week
    assert BASE['week']==40

def test_timezone_boundary_holds():
    with pytest.raises(ValueError, match='경계'):
        resolve({'event':{'timestamp':'2026년 10월 6일 오후 4:00'}},BASE,fetch)

def test_truncated_history_holds():
    with pytest.raises(ValueError, match='한도'):
        resolve({'event':{'timestamp':'2026년 10월 7일 오전 9:00'}},BASE,lambda p:{'rows':[],'history':[H]*1000})

def test_registered_without_history_holds():
    with pytest.raises(ValueError, match='이력 누락'):
        resolve({'event':{'timestamp':'2026년 10월 7일 오전 9:00'}},BASE,lambda p:{'history':[],'rows':[{'estimateKey':1,'appliedOrderYear':2026,'appliedOrderWeek':'40','deductionType':'불량차감'}]})

def test_confirmed_database_wall_time_resolves_false_nine_hour_boundary():
    p,_ = resolve({'event':{'timestamp':'2026년 10월 6일 오후 4:00'}},BASE,fetch,
                  history_timestamp_mode='database_kst_wall')
    assert p['week']==41
    with pytest.raises(ValueError, match='경계'):
        resolve({'event':{'timestamp':'2026년 10월 6일 오후 2:40'}},BASE,fetch,
                history_timestamp_mode='database_kst_wall')


def test_database_snapshot_field_case_preserves_registration_event():
    import json
    h={**H,'AfterJson':json.dumps({'AppliedOrderYear':2026,'AppliedOrderWeek':'40',
                                  'DeductionType':'불량차감','EstimateKey':1})}
    p,_=resolve({'event':{'timestamp':'2026년 10월 7일 오전 9:00'}},BASE,
                lambda p:{'rows':[],'history':[h] if p['week']==40 else []})
    assert p['week']==41


def partition_fixture():
    first=[{'HistoryKey':str(i),'DeductionKey':1} for i in range(500)]
    second=[{'HistoryKey':str(i),'DeductionKey':2} for i in range(500,1000)]
    support={'rows':[{'deductionKey':1,'orderYear':2026,'orderWeek':'41'},
                     {'deductionKey':2,'orderYear':2026,'orderWeek':'40'}],
             'history':first+second}
    parts={41:{'rows':[{'deductionKey':1}], 'history':first+[{'HistoryKey':'1000','DeductionKey':1}]},
           40:{'rows':[{'deductionKey':2}], 'history':second}}
    return support,parts


def test_partition_history_requires_full_original_and_ledger_coverage():
    from core.defect_period_transition import complete_support_history,VerifiedPartitionHistory
    support,parts=partition_fixture()
    def request(method,params):
        assert method=='GET'
        return support if params.get('view')=='support' else parts[params['week']]
    result=complete_support_history({'year':2026,'week':41},request)
    assert isinstance(result['history'],VerifiedPartitionHistory)
    assert len(result['history'])==1001
    parts[40]['rows']=[]
    with pytest.raises(ValueError,match='범위 불완전'):
        complete_support_history({'year':2026,'week':41},request)


def test_partition_does_not_bypass_cap_or_concurrent_history_change():
    from core.defect_period_transition import complete_support_history
    support,parts=partition_fixture()
    def request(method,params):return support if params.get('view')=='support' else parts[params['week']]
    parts[40]['history']*=2
    with pytest.raises(ValueError,match='한도'):
        complete_support_history({'year':2026,'week':41},request)
    parts[40]['history']=parts[40]['history'][:500]
    parts[41]['history'][0]={**parts[41]['history'][0],'ChangedAt':'changed'}
    with pytest.raises(ValueError,match='변경'):
        complete_support_history({'year':2026,'week':41},request)


def test_archived_estimate_history_retained_without_active_ledger_row():
    from core.defect_period_transition import complete_support_history
    support, parts = partition_fixture()
    archived = {**H, 'HistoryKey': '2000', 'DeductionKey': 99,
                'OrderYear': 2026, 'OrderWeek': '39',
                'AfterJson': H['AfterJson'].replace('"40"', '"41"')}
    support['history'] = support['history'][:999] + [archived]
    parts[39] = {'rows': [], 'history': [archived]}
    calls = []
    def request(method, params):
        assert method == 'GET'
        calls.append(params)
        return support if params.get('view') == 'support' else parts[params['week']]
    result = complete_support_history({'year': 2026, 'week': 41}, request)
    assert archived in result['history']
    assert any(p.get('week') == 39 for p in calls)
    period, _ = resolve({'event': {'timestamp': '2026년 10월 7일 오전 9:00'}},
                        {'year': 2026, 'week': 41},
                        lambda p: complete_support_history(p, request) if p['week'] == 41
                        else {'rows': [], 'history': []})
    assert period['week'] == 42
    parts[39]['history'] = []
    with pytest.raises(ValueError, match='범위 불완전'):
        complete_support_history({'year': 2026, 'week': 41}, request)
