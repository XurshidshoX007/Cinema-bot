"""Keyboard layout tests."""

from keyboards import (
    ADMIN_ACTIONS,
    USER_ACTIONS,
    SEARCH_BUTTON,
    ADMIN_PANEL_BUTTON,
    main_menu,
    admin_menu,
    movie_menu,
)


def test_user_actions_is_tuple():
    assert isinstance(USER_ACTIONS, tuple)
    assert len(USER_ACTIONS) > 0


def test_admin_actions_is_tuple():
    assert isinstance(ADMIN_ACTIONS, tuple)
    assert len(ADMIN_ACTIONS) > 0


def test_no_overlap_user_admin():
    """User va admin tugmalari bir-biriga to'g'ri kelmaydi."""
    overlap = set(USER_ACTIONS) & set(ADMIN_ACTIONS)
    assert len(overlap) == 0, f"Overlap: {overlap}"


def test_main_menu_has_buttons():
    markup = main_menu(123)
    assert markup is not None
    assert len(markup.keyboard) > 0


def test_main_menu_admin_panel():
    markup = main_menu(123, show_admin_panel=True)
    all_texts = [btn.text for row in markup.keyboard for btn in row]
    assert ADMIN_PANEL_BUTTON in all_texts


def test_main_menu_no_admin():
    markup = main_menu(123, show_admin_panel=False)
    all_texts = [btn.text for row in markup.keyboard for btn in row]
    assert ADMIN_PANEL_BUTTON not in all_texts


def test_admin_menu_owner():
    markup = admin_menu(permissions=set(), is_owner=True)
    assert len(markup.keyboard) > 0


def test_movie_menu():
    markup = movie_menu()
    assert len(markup.keyboard) > 0
