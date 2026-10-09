"""画布小图：大图按宽度缩成 webp 并缓存，小图原样返回。"""

from __future__ import annotations

from PIL import Image

from server.routers.files import _thumbnail_for


def test_a_wide_image_is_scaled_down_and_cached(tmp_path):
    source = tmp_path / "wide.png"
    Image.new("RGB", (1600, 900), "red").save(source)

    thumb = _thumbnail_for("demo", source, 480)

    assert thumb.suffix == ".webp"
    with Image.open(thumb) as image:
        assert image.width == 480
    assert _thumbnail_for("demo", source, 480) == thumb


def test_an_image_already_narrow_enough_is_returned_unchanged(tmp_path):
    source = tmp_path / "small.png"
    Image.new("RGB", (200, 100), "blue").save(source)

    assert _thumbnail_for("demo", source, 480) == source
