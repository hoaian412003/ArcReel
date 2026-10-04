"""供应商轮询同步钩子。

轮询层（``lib.backends``）每收到一次供应商成功响应就触发一次，由队列侧注册实现把它记成「任务
最近一次与供应商同步」。放在 infra 层，使 backends 不必依赖任务队列；未注册时是空操作。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

SyncHook = Callable[..., Awaitable[None]]

_hook: SyncHook | None = None


def set_sync_hook(hook: SyncHook | None) -> None:
    global _hook
    _hook = hook


async def fire_provider_sync(*, label: str = "", status: str | None = None) -> None:
    hook = _hook
    if hook is None:
        return
    try:
        await hook(label=label, status=status)
    except Exception:
        logger.debug("轮询同步钩子失败", exc_info=True)
