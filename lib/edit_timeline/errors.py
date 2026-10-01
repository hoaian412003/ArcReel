"""剪辑时间线的领域错误（``ApiError`` 子类，由 app 级 handler 统一翻译）。"""

from __future__ import annotations

from lib.infra.api_errors import ApiError


class EditTimelineError(ApiError):
    """剪辑时间线操作失败；``key`` 为 i18n 消息键。"""

    def __init__(self, message: str, *, key: str = "edit_timeline_invalid", status_code: int = 422, **params: object):
        super().__init__(key, status_code=status_code, **params)
        self.detail_message = message

    def __str__(self) -> str:
        return self.detail_message


class TimelineNotFoundError(EditTimelineError):
    def __init__(self, timeline_id: str) -> None:
        super().__init__(
            f"timeline not found: {timeline_id}",
            key="edit_timeline_not_found",
            status_code=404,
            timeline_id=timeline_id,
        )


class RevisionConflictError(EditTimelineError):
    """写入声明的父修订已不是最新。"""

    def __init__(self, *, expected: int, latest: int) -> None:
        super().__init__(
            f"parent revision {expected} is not latest ({latest})",
            key="edit_timeline_revision_conflict",
            status_code=409,
            expected=expected,
            latest=latest,
        )


class InvalidTimelineError(EditTimelineError):
    def __init__(self, message: str, **params: object) -> None:
        super().__init__(message, key="edit_timeline_invalid", status_code=400, **params)
