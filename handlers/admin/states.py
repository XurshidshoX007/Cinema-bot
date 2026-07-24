"""FSM states for admin."""

from aiogram.fsm.state import State, StatesGroup


class AddMovieState(StatesGroup):
    waiting_for_serial_pick = State()
    waiting_for_serial_continue = State()
    waiting_for_code = State()
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_video = State()


class AdState(StatesGroup):
    waiting_for_content = State()
    waiting_for_duration = State()


class DeleteMovieState(StatesGroup):
    waiting_for_code = State()


class EditMovieState(StatesGroup):
    waiting_for_code = State()
    waiting_for_action = State()
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_media = State()


class HelperAdminState(StatesGroup):
    waiting_for_user = State()


class RefreshMediaState(StatesGroup):
    waiting_for_code = State()
    waiting_for_media = State()
