import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from nxmini.styles import export_cube, load_profile, render, transform


def test_identity_and_monochrome():
    rgb = np.random.default_rng(3).random((12, 17, 3)).astype(np.float32)
    out, _ = transform(rgb, {})
    assert np.max(np.abs(out - rgb)) < 0.000001
    mono, _ = transform(rgb, load_profile("profiles/leica-monochrome.json"))
    assert np.max(np.abs(mono[..., 0] - mono[..., 2])) == 0


def test_all_profiles_and_cube_order(tmp_path):
    for path in Path("profiles").glob("*.json"):
        profile = load_profile(path)
        assert not profile["camera_installed"]
        destination = tmp_path / (path.stem + ".cube")
        export_cube(path, destination, size=3)
        rows = [line for line in destination.read_text().splitlines() if line and line[0].isdigit()]
        assert len(rows) == 27
        expected, _ = transform(np.asarray([[0, 0, 0], [0.5, 0, 0], [1, 0, 0]], dtype=np.float32), profile)
        assert np.allclose(np.asarray([list(map(float, s.split())) for s in rows[:3]]), expected)


def test_preserve_input_and_reject_log_profile(tmp_path):
    source = tmp_path / "photo.jpg"
    Image.new("RGB", (32, 40), (130, 80, 30)).save(source)
    original = source.read_bytes()
    result = render(source, Path("profiles/ricoh-positive.json"), tmp_path / "out.jpg")
    assert source.read_bytes() == original and not result["camera_installed"]
    with pytest.raises(ValueError):
        render(source, Path("profiles/ricoh-positive.json"), source)
    invalid = tmp_path / "log.json"
    invalid.write_text(json.dumps({"schema_version": 1, "input_space": "V-Log", "output_space": "sRGB"}))
    with pytest.raises(ValueError):
        load_profile(invalid)


def test_nested_adobe_rgb_and_output_colour_tag(tmp_path):
    source = tmp_path / "adobe.jpg"
    exif = Image.Exif()
    exif[34665] = {40961: 2}
    Image.new("RGB", (8, 8), (100, 70, 40)).save(source, exif=exif)
    with pytest.raises(ValueError, match="ICC"):
        render(source, Path("profiles/ricoh-positive.json"), tmp_path / "bad.jpg")
    exif[34665] = {40961: 1}
    Image.new("RGB", (8, 8), (100, 70, 40)).save(source, exif=exif)
    output = tmp_path / "out.jpg"
    render(source, Path("profiles/ricoh-positive.json"), output)
    with Image.open(output) as result:
        assert result.getexif().get_ifd(34665)[40961] == 1
        assert result.info["icc_profile"]
