"""Tests for the project-level video prompt profile."""

import pytest

from lib.prompts.video_prompt_profile import (
    DEFAULT_VIDEO_PROMPT_PROFILE,
    h3_ref2va_supported,
    is_h3_ref2va,
    video_prompt_profile,
)

_SUPPORTED = {"generation_mode": "reference_video", "content_mode": "drama"}


class TestVideoPromptProfile:
    def test_no_project_reads_default(self):
        assert video_prompt_profile(None) == DEFAULT_VIDEO_PROMPT_PROFILE

    @pytest.mark.parametrize("raw", [None, "", "unknown", 3, ["h3_ref2va"]])
    def test_missing_or_invalid_value_reads_default(self, raw):
        assert video_prompt_profile({"video_prompt_profile": raw}) == "default"

    def test_declared_value_is_returned(self):
        assert video_prompt_profile({"video_prompt_profile": "h3_ref2va"}) == "h3_ref2va"


class TestH3Ref2vaSupport:
    @pytest.mark.parametrize("content_mode", ["drama", "narration"])
    def test_reference_video_drama_and_narration_are_supported(self, content_mode):
        assert h3_ref2va_supported({"generation_mode": "reference_video", "content_mode": content_mode})

    @pytest.mark.parametrize(
        "project",
        [
            {"generation_mode": "storyboard", "content_mode": "drama"},
            {"generation_mode": "reference_video", "content_mode": "ad"},
            {"content_mode": "drama"},
        ],
    )
    def test_other_mode_pairs_are_not_supported(self, project):
        assert not h3_ref2va_supported(project)

    def test_profile_is_active_only_when_declared_and_supported(self):
        assert is_h3_ref2va({**_SUPPORTED, "video_prompt_profile": "h3_ref2va"})
        assert not is_h3_ref2va(_SUPPORTED)
        assert not is_h3_ref2va({"generation_mode": "storyboard", "video_prompt_profile": "h3_ref2va"})
        assert not is_h3_ref2va(None)
