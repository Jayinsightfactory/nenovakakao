import pytest
from core import credential_store as store


def test_target_is_staff_scoped_and_rejects_path_characters():
    assert store._target('임재용대리') == 'nenovakakao/nenova/임재용대리'
    with pytest.raises(ValueError): store._target('../직원')


def test_save_requires_all_fields(monkeypatch):
    with pytest.raises(ValueError): store.save('임재용대리', '', 'secret')
    with pytest.raises(ValueError): store.save('임재용대리', 'user', '')
