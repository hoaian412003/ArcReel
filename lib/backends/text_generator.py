"""TextGenerator — 文本生成 + 记账包装层。

类似 MediaGenerator，组合 TextBackend + Ledger，
调用方无需关心记账细节。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from lib.backends.providers import CallPurpose, require_provider_pair
from lib.backends.text_backends.base import (
    TextGenerationRequest,
    TextGenerationResult,
    TextTaskType,
)
from lib.backends.text_backends.factory import create_text_backend_for_task
from lib.billing.ledger import Ledger
from lib.config.registry import PROVIDER_REGISTRY
from lib.db.base import DEFAULT_USER_ID

if TYPE_CHECKING:
    from lib.backends.text_backends.base import TextBackend

logger = logging.getLogger(__name__)


class TextGenerator:
    """组合 TextBackend + Ledger，统一封装文本生成 + 记账。"""

    def __init__(
        self,
        backend: TextBackend,
        ledger: Ledger,
        provider_id: str,
        *,
        purpose: CallPurpose,
        user_id: str = DEFAULT_USER_ID,
    ):
        require_provider_pair("text", backend, provider_id)
        self.backend = backend
        self.ledger = ledger
        self._provider_id = provider_id
        self._purpose = purpose
        self._user_id = user_id

    @property
    def model(self) -> str:
        """当前 backend 的模型名称。"""
        return self.backend.model

    @classmethod
    async def create(
        cls,
        task_type: TextTaskType,
        project_name: str | None = None,
        *,
        purpose: CallPurpose,
        user_id: str = DEFAULT_USER_ID,
    ) -> TextGenerator:
        """工厂方法：根据任务类型创建对应的 backend + ledger。

        ``purpose`` 是必填的：文本调用没有任务可回指，来源只能由调用点声明，
        同一个 ``task_type`` 会服务多种来源（剧本生成与分集规划都走 SCRIPT）。
        """
        backend, provider_id = await create_text_backend_for_task(task_type, project_name)
        return cls(backend, Ledger(), provider_id, purpose=purpose, user_id=user_id)

    def _apply_model_output_limit(self, request: TextGenerationRequest) -> TextGenerationRequest:
        """registry 为当前模型声明了输出上限时，替换调用方传入的通用上限。"""
        provider = PROVIDER_REGISTRY.get(self._provider_id)
        info = provider.models.get(self.backend.model) if provider else None
        if info is None or info.max_output_tokens is None or request.max_output_tokens is None:
            return request
        return replace(request, max_output_tokens=info.max_output_tokens)

    async def generate(
        self,
        request: TextGenerationRequest,
        project_name: str | None = None,
    ) -> TextGenerationResult:
        """生成文本并自动记录用量。"""
        request = self._apply_model_output_limit(request)
        async with self.ledger.record(
            project_name=project_name or "",
            call_type="text",
            model=self.backend.model,
            prompt=request.prompt,
            provider=self._provider_id,
            user_id=self._user_id,
            purpose=self._purpose,
        ) as call:
            result = await self.backend.generate(request)
            call.success(result)
            return result
