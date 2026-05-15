from . import (
    admin,
    admin_text_refined,
    admin_ui,
    channel_fix,
    chat_member,
    inline,
    kino,
    start,
    user,
    user_search_prompt,
)
from middlewares import forcesub

ROUTERS = (
    chat_member.router,
    start.router,
    user_search_prompt.router,
    user.router,
    kino.router,
    channel_fix.router,
    admin_ui.router,
    admin.router,
    inline.router,
    forcesub.router,
)
