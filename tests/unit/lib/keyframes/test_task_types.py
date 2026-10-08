"""起始帧联合图任务类型：资源 id 形态、入队结构校验与 i2i 路由。"""

import pytest

from lib.generation.generation_queue import I2I_ONLY_TASK_TYPES
from lib.generation.generation_queue_client import TaskSpec, TaskSpecValidationError
from lib.keyframes.task_types import KEYFRAME_SHEET_TASK_TYPE, parse_sheet_resource_id, sheet_resource_id
from lib.project.resource_paths import (
    KEYFRAME_SHEET_RESOURCE_TYPE,
    resource_relative_path,
    version_snapshot_relative_path,
)


def test_resource_id_round_trips() -> None:
    assert sheet_resource_id(3, "S2") == "episode_3/S2"
    assert parse_sheet_resource_id("episode_3/S2") == (3, "S2")


@pytest.mark.parametrize("bad", ["S1", "episode_/S1", "episode_x/S1", "episode_1/", "ep_1/S1", ""])
def test_malformed_resource_ids_are_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="无效的起始帧联合图资源 id"):
        parse_sheet_resource_id(bad)


def test_the_sheet_and_its_versions_live_under_the_episode_keyframes_directory() -> None:
    assert resource_relative_path(KEYFRAME_SHEET_RESOURCE_TYPE, "episode_1/S1") == "keyframes/episode_1/S1.png"
    snapshot = version_snapshot_relative_path(
        KEYFRAME_SHEET_RESOURCE_TYPE, "episode_1/S1", version=2, timestamp="20261007T000000"
    )
    assert snapshot == "versions/keyframes/episode_1/S1_v2_20261007T000000.png"


def test_a_sheet_task_is_promptless_and_needs_a_two_segment_id() -> None:
    spec = TaskSpec.from_request(task_type=KEYFRAME_SHEET_TASK_TYPE, media_type="image", resource_id="episode_1/S1")

    assert spec.payload == {}
    with pytest.raises(TaskSpecValidationError) as one_segment:
        TaskSpec.from_request(task_type=KEYFRAME_SHEET_TASK_TYPE, media_type="image", resource_id="S1")
    assert one_segment.value.code == "invalid_resource_id"
    with pytest.raises(ValueError, match="take no prompt"):
        TaskSpec.from_request(
            task_type=KEYFRAME_SHEET_TASK_TYPE, media_type="image", resource_id="episode_1/S1", prompt="x"
        )


def test_a_sheet_task_always_routes_to_the_i2i_image_slot() -> None:
    assert KEYFRAME_SHEET_TASK_TYPE in I2I_ONLY_TASK_TYPES
