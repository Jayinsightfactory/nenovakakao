from core import keyword_approval as a


def test_split_answers_keep_unanswered_pending_and_ignore_other_people():
    row = dict(id='ABC', choice_format='per_item', events=[{}, {}, {}],
               request_event_id='prompt', baseline=[])
    history = [dict(event_id='prompt', sender_name='봇', content='[전달 승인 요청 ABC]')]
    def reply(key, content, sender=a.APPROVER):
        history.append(dict(event_id=key, sender_name=sender, content=content))
    reply('1', '가 보내')
    assert a.decision(history, row, True) == {0: 'approve'}
    reply('2', '나 안보내')
    reply('3', '다 보내', '다른사람')
    assert a.decision(history, row, True) == {0: 'approve', 1: 'reject'}
    reply('4', '다 보내')
    assert a.decision(history, row, True) == {0: 'approve', 1: 'reject', 2: 'approve'}


def test_ambiguous_or_late_bare_answers_never_approve():
    row = dict(id='ABC', choice_format='per_item', events=[{}, {}, {}],
               request_event_id='prompt', baseline=[])
    prompt = dict(event_id='prompt', sender_name='봇', content='[전달 승인 요청 ABC]')
    for text in ('가', '가 보내지마', '가 보내 나', '라 보내', '가 보내 가 안보내'):
        answer = dict(event_id='1', sender_name=a.APPROVER, content=text)
        assert a.decision([prompt, answer], row, True) is None
    answer = dict(event_id='2', sender_name=a.APPROVER, content='가 보내')
    assert a.decision([prompt, answer], row, False) is None
    answer['content'] = 'ABC 가 보내 나 안보내'
    assert a.decision([prompt, answer], row, False) == {0: 'approve', 1: 'reject'}
