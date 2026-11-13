from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from core import window_detector as w

def window(h):
    return SimpleNamespace(_hWnd=h,title='카카오톡',width=519,height=900)

def test_same_handle_not_counted_twice(monkeypatch):
    monkeypatch.setattr(w.gw,'getWindowsWithTitle',lambda _: [window(1),window(1),window(2)])
    monkeypatch.setattr(w,'_is_main_handle',lambda h:h==1)
    monkeypatch.setattr('win32gui.IsWindowVisible',lambda h:True)
    assert w._exact_main_window()._hWnd==1

def test_two_genuine_main_windows_still_block(monkeypatch):
    monkeypatch.setattr(w.gw,'getWindowsWithTitle',lambda _:[window(1),window(2)])
    monkeypatch.setattr(w,'_is_main_handle',lambda h:True)
    monkeypatch.setattr('win32gui.IsWindowVisible',lambda h:True)
    with pytest.raises(RuntimeError,match='2개'):w._exact_main_window()

def test_hidden_helper_not_selected_over_visible_main(monkeypatch):
    monkeypatch.setattr(w.gw,'getWindowsWithTitle',lambda _:[window(1),window(2)])
    monkeypatch.setattr(w,'_is_main_handle',lambda h:True)
    monkeypatch.setattr('win32gui.IsWindowVisible',lambda h:h==2)
    assert w._exact_main_window()._hWnd==2
