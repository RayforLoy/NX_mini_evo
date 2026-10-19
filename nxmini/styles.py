"""Display-referred sRGB reference renderer, separate from the camera ISP."""
import json
from pathlib import Path

from .util import sha256


def load_profile(path):
    import numpy as np
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    if profile.get("schema_version") != 1 or profile.get("input_space") != "sRGB" or profile.get("output_space") != "sRGB":
        raise ValueError("Only explicitly declared sRGB -> sRGB profiles are supported")
    if profile.get("matrix_domain", "linear-sRGB") != "linear-sRGB":
        raise ValueError("Reference matrices must use linear-sRGB")
    matrix = np.asarray(profile.get("matrix", np.eye(3)), dtype=np.float32)
    curve = np.asarray(profile.get("curve", [[0, 0], [1, 1]]), dtype=np.float32)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("Invalid profile matrix")
    if curve.ndim != 2 or curve.shape[1] != 2 or len(curve) < 2 or not np.isfinite(curve).all():
        raise ValueError("Invalid profile curve")
    if list(curve[[0, -1], 0]) != [0, 1] or not (np.diff(curve[:, 0]) > 0).all():
        raise ValueError("Curve domain must increase from 0 to 1")
    if (curve[:, 1] < 0).any() or (curve[:, 1] > 1).any() or (np.diff(curve[:, 1]) < 0).any():
        raise ValueError("Tone curve must be monotonic within 0..1")
    for key in ("shadow_tint", "highlight_tint"):
        value = np.asarray(profile.get(key, [0, 0, 0]), dtype=np.float32)
        if value.shape != (3,) or not np.isfinite(value).all():
            raise ValueError(f"Invalid {key}")
    saturation = float(profile.get("saturation", 1))
    if not 0 <= saturation <= 3:
        raise ValueError("Invalid saturation")
    return profile


def transform(rgb, profile):
    import numpy as np
    encoded = np.asarray(rgb, dtype=np.float32)
    linear = np.where(encoded <= 0.04045, encoded / 12.92, ((encoded + 0.055) / 1.055) ** 2.4)
    matrix = np.asarray(profile.get("matrix", np.eye(3)), dtype=np.float32)
    linear = linear @ matrix.T
    luma = linear @ np.asarray([0.2126, 0.7152, 0.0722], dtype=np.float32)
    linear = luma[..., None] + (linear - luma[..., None]) * profile.get("saturation", 1)
    shadow = np.asarray(profile.get("shadow_tint", [0, 0, 0]), dtype=np.float32)
    highlight = np.asarray(profile.get("highlight_tint", [0, 0, 0]), dtype=np.float32)
    linear += (1 - np.clip(luma, 0, 1))[..., None] ** 2 * shadow
    linear += np.clip(luma, 0, 1)[..., None] ** 2 * highlight
    clipped = int(np.count_nonzero((linear < 0) | (linear > 1)))
    linear = np.clip(linear, 0, 1)
    encoded = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)
    curve = np.asarray(profile.get("curve", [[0, 0], [1, 1]]), dtype=np.float32)
    result = np.interp(encoded, curve[:, 0], curve[:, 1]).astype(np.float32)
    return np.clip(result, 0, 1), clipped


def export_cube(profile_path, output, size=17):
    import numpy as np
    if not 2 <= size <= 65:
        raise ValueError("Cube size must be 2..65")
    profile = load_profile(profile_path)
    with Path(output).open("x", encoding="ascii") as stream:
        stream.write(f'# NX mini evo OFFLINE REFERENCE; not calibrated for camera ISP\nTITLE "{profile["id"]}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        for blue in range(size):
            for green in range(size):
                row = np.asarray([[red, green, blue] for red in range(size)], dtype=np.float32) / (size - 1)
                converted, _ = transform(row, profile)
                for red, green_out, blue_out in converted:
                    stream.write(f"{red:.8f} {green_out:.8f} {blue_out:.8f}\n")
    return {"output": str(Path(output).resolve()), "size": size, "camera_installed": False}


def render(source: Path, profile_path: Path, output: Path):
    import io
    import numpy as np
    from PIL import Image, ImageCms, ImageOps
    if source.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        raise ValueError("Reference renderer accepts JPEG/PNG; RAW is never modified")
    if output.suffix.lower() not in (".jpg", ".jpeg", ".png") or output.resolve() == source.resolve():
        raise ValueError("Choose a new JPEG/PNG output")
    profile = load_profile(profile_path)
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)
        exif = image.getexif()
        exif_ifd = exif.get_ifd(34665)
        colour_space = exif_ifd.get(40961, exif.get(40961))
        icc = original.info.get("icc_profile")
        if icc:
            image = ImageCms.profileToProfile(image.convert("RGB"), ImageCms.ImageCmsProfile(io.BytesIO(icc)), ImageCms.createProfile("sRGB"), outputMode="RGB")
        elif colour_space == 2:
            raise ValueError("Adobe RGB input requires its ICC profile")
        else:
            image = image.convert("RGB")
        result = Image.new("RGB", image.size)
        clipping = 0
        for y in range(0, image.height, 128):
            rectangle = (0, y, image.width, min(y + 128, image.height))
            rgb = np.asarray(image.crop(rectangle), dtype=np.float32) / 255
            transformed, count = transform(rgb, profile)
            clipping += count
            result.paste(Image.fromarray(np.rint(transformed * 255).astype(np.uint8)), (0, y))
        # ColorSpace belongs to the nested Exif IFD, not IFD0.
        exif_ifd[40961] = 1
        exif_ifd[40962] = result.width
        exif_ifd[40963] = result.height
        exif[34665] = exif_ifd
        if 40961 in exif:
            del exif[40961]
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            result.save(stream, format="PNG" if output.suffix.lower() == ".png" else "JPEG",
                        exif=exif.tobytes(), icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes(),
                        quality=95, subsampling=0)
    return {"output": str(output.resolve()), "profile": profile["id"],
            "source_sha256": sha256(source.read_bytes()), "clipped_components_before_curve": clipping,
            "camera_installed": False, "calibrated_to_target_brand": False}
