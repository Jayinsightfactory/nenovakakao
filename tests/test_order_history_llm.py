from core.order_llm import apply_candidate_review


def item():
    return {'matched':False,'quantity':3,'unit':'박스','history_status':'available',
            'candidate_options':[{'product_key':22,'label':'Momentum 50cm',
                                  'order_history':{'ProdKey':22,'orderFrequency':4,'totalBunch':90}}]}


def test_history_supported_candidate_preserves_requested_units():
    row=item()
    apply_candidate_review(row,{'product_key':22,'confidence':.98})
    assert row['matched'] and row['product_key']==22
    assert row['quantity']==3 and row['unit']=='박스'


def test_unknown_key_or_unavailable_history_cannot_match():
    for key,history in [(999,'available'),(22,'unavailable')]:
        row=item();row['history_status']=history
        apply_candidate_review(row,{'product_key':key,'confidence':1})
        assert not row['matched']


def test_low_confidence_cannot_match():
    row=item()
    apply_candidate_review(row,{'product_key':22,'confidence':.8})
    assert not row['matched']
