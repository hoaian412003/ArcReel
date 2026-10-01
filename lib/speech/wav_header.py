"""流式合成的 WAV 文件头修复。

流式 TTS 服务边合成边输出，WAV 头里的 RIFF 与 data 长度写成未知值（``0xFFFFFFFF``）。ffprobe /
ffmpeg 能容忍，但浏览器的 ``<audio>`` 与 Python ``wave`` 会把时长读成荒谬值，落盘后需按实际文件大小
回填这两处长度。
"""

from __future__ import annotations

import struct
from pathlib import Path

_UNKNOWN = 0xFFFFFFFF
_RIFF_HEADER_BYTES = 12
_CHUNK_HEADER_BYTES = 8


def finalize_streaming_wav(path: Path) -> bool:
    """把 ``path`` 里长度未知的 WAV 头回填为实际大小；返回是否改动了文件。

    不是 RIFF/WAVE、没有 ``data`` 块，或长度本就合法时原样保留并返回 ``False``。
    """
    data = path.read_bytes()
    if len(data) < _RIFF_HEADER_BYTES + _CHUNK_HEADER_BYTES or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False

    riff_size = struct.unpack_from("<I", data, 4)[0]
    offset = _RIFF_HEADER_BYTES
    data_size_offset: int | None = None
    data_size = 0
    while offset + _CHUNK_HEADER_BYTES <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        body = offset + _CHUNK_HEADER_BYTES
        if chunk_id == b"data":
            data_size_offset = offset + 4
            data_size = chunk_size
            break
        offset = body + chunk_size + (chunk_size & 1)
    if data_size_offset is None:
        return False

    actual_data = len(data) - (data_size_offset + 4)
    if riff_size != _UNKNOWN and data_size != _UNKNOWN:
        return False

    patched = bytearray(data)
    struct.pack_into("<I", patched, 4, len(data) - 8)
    struct.pack_into("<I", patched, data_size_offset, actual_data)
    temp = path.with_name(f".{path.name}.fix")
    temp.write_bytes(bytes(patched))
    temp.replace(path)
    return True
