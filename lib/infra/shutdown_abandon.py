"""关停放弃标志：关停宽限期耗尽后，被打断的在跑任务保持崩溃语义。

置位期间，各取消处理路径不把任务落 cancelled、不把 pending 调用行结算为 cancelled，任务行
保持 running、调用行保持 pending，由下次启动的孤儿扫描接续（可 resume 的视频）或判失败。
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

_abandoning = False


def is_abandoning() -> bool:
    return _abandoning


@contextmanager
def abandoning() -> Generator[None]:
    global _abandoning
    _abandoning = True
    try:
        yield
    finally:
        _abandoning = False
