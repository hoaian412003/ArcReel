from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.speech.asr_runner import (
    DEFAULT_MODEL,
    REPO_ROOT,
    AsrConfig,
    AsrError,
    asr_subprocess_env,
    build_command,
    parse_output,
    resolve_workdir,
    transcribe_files,
)

A = Path("/p/a.mp4")
B = Path("/p/b.mp4")


def _config(tmp_path: Path) -> AsrConfig:
    (tmp_path / "detect.py").write_text("")
    return AsrConfig(workdir=str(tmp_path))


class TestConfig:
    def test_blank_settings_point_at_the_tool_inside_the_project(self):
        config = AsrConfig.from_settings({})

        assert config.workdir == str(REPO_ROOT / "tools" / "asr")
        assert config.model == DEFAULT_MODEL
        assert config.device == "auto"

    def test_values_are_validated_and_bad_ones_fall_back(self):
        config = AsrConfig.from_settings({"asr_model": " medium ", "asr_device": "tpu", "asr_timeout_seconds": "1"})

        assert config.model == "medium"
        assert config.device == "auto"
        assert config.timeout_seconds == 1800

    def test_workdir_resolution(self):
        assert resolve_workdir("/opt/asr") == "/opt/asr"
        assert resolve_workdir("x/y") == str(REPO_ROOT / "x" / "y")


def test_command_batches_all_files_into_one_process(tmp_path: Path):
    command = build_command(_config(tmp_path), [A, B], language="zh")

    assert command[:4] == ["uv", "run", "--project", str(tmp_path)]
    assert command[-2:] == [str(A), str(B)]
    assert command[command.index("--model") + 1] == DEFAULT_MODEL
    assert command[command.index("--language") + 1] == "zh"


class TestParseOutput:
    def test_segments_and_per_file_errors(self):
        stdout = json.dumps(
            {
                str(A): {
                    "duration": 5,
                    "segments": [
                        {
                            "start": 1.0,
                            "end": 2.5,
                            "text": "你好",
                            "words": [
                                {"start": 1.0, "end": 1.4, "word": "你"},
                                {"start": 1.4, "end": 2.5, "word": "好"},
                            ],
                        }
                    ],
                },
                str(B): {"error": "RuntimeError: boom"},
            }
        ).encode()

        parsed = parse_output(stdout, [A, B])

        segments = parsed[A]
        assert isinstance(segments, list)
        assert (segments[0].start, segments[0].end, segments[0].text) == (1.0, 2.5, "你好")
        assert [(w.text, w.start) for w in segments[0].words] == [("你", 1.0), ("好", 1.4)]
        assert isinstance(parsed[B], AsrError)
        assert "boom" in str(parsed[B])

    def test_missing_file_entry_is_a_per_file_error(self):
        parsed = parse_output(b"{}", [A])

        assert isinstance(parsed[A], AsrError)

    @pytest.mark.parametrize("stdout", [b"not json", b"[]"])
    def test_unparseable_output_fails_the_whole_batch(self, stdout: bytes):
        with pytest.raises(AsrError):
            parse_output(stdout, [A])


class TestTranscribe:
    async def test_runs_once_and_returns_segments(self, tmp_path: Path):
        calls: list[tuple[list[str], float]] = []

        async def run(args, deadline_seconds):
            calls.append((list(args), deadline_seconds))
            return 0, json.dumps({str(A): {"segments": [{"start": 0.5, "end": 1.0, "text": "好"}]}}).encode(), b""

        result = await transcribe_files([A], config=_config(tmp_path), run=run)

        assert len(calls) == 1
        segments = result[A]
        assert isinstance(segments, list)
        assert segments[0].start == 0.5

    async def test_non_zero_exit_reports_the_stderr_tail(self, tmp_path: Path):
        async def run(args, deadline_seconds):
            return 3, b"", b"Traceback ... model download failed"

        with pytest.raises(AsrError, match="model download failed"):
            await transcribe_files([A], config=_config(tmp_path), run=run)

    async def test_timeout_is_an_asr_error(self, tmp_path: Path):
        async def run(args, deadline_seconds):
            raise TimeoutError

        with pytest.raises(AsrError, match="未完成"):
            await transcribe_files([A], config=_config(tmp_path), run=run)

    async def test_no_files_means_no_process(self, tmp_path: Path):
        async def run(args, deadline_seconds):
            raise AssertionError("must not spawn")

        assert await transcribe_files([], config=_config(tmp_path), run=run) == {}


class TestSubprocessEnv:
    def test_uv_variables_that_block_the_tool_from_setting_itself_up_are_dropped(self):
        env = asr_subprocess_env(
            {"PATH": "/usr/bin", "UV_NO_SYNC": "1", "UV_FROZEN": "1", "VIRTUAL_ENV": "/app/.venv", "HOME": "/root"}
        )

        assert env == {"PATH": "/usr/bin", "HOME": "/root"}

    def test_other_variables_are_kept(self):
        assert asr_subprocess_env({"HF_HOME": "/cache", "UV_CACHE_DIR": "/c"}) == {
            "HF_HOME": "/cache",
            "UV_CACHE_DIR": "/c",
        }
