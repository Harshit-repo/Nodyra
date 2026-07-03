"""Image processing nodes using Pillow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nodyra.sdk import node


def _pil():
    try:
        from PIL import Image, ImageFilter
    except ImportError as exc:
        raise RuntimeError(
            "Image processing requires Pillow. Install: uv pip install Pillow"
        ) from exc
    return Image, ImageFilter


def _save_image(img: Any, output_path: str, quality: int = 85) -> None:
    ext = Path(output_path).suffix.lower()
    if ext in (".jpg", ".jpeg") and img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    kwargs: dict[str, Any] = {}
    if ext in (".jpg", ".jpeg", ".webp"):
        kwargs["quality"] = max(1, min(100, quality))
    img.save(output_path, **kwargs)


@node(
    name="Image Resize",
    id="image_resize",
    category="Files",
    icon="image",
    requirements=["Pillow"],
    params={
        "path": {
            "description": "Path to the source image.",
            "placeholder": "/path/to/image.jpg",
        },
        "output_path": {
            "description": "Path to save the resized image.",
            "placeholder": "/path/to/output.jpg",
        },
        "width": {
            "group": "Options",
            "type": "integer",
            "description": "Target width in pixels (0=auto from height).",
        },
        "height": {
            "group": "Options",
            "type": "integer",
            "description": "Target height in pixels (0=auto from width).",
        },
        "maintain_aspect": {
            "group": "Options",
            "type": "boolean",
            "description": "Maintain aspect ratio.",
        },
        "quality": {
            "group": "Options",
            "type": "integer",
            "description": "JPEG output quality (1-100).",
        },
    },
)
def image_resize(
    input: Any = None,
    path: str = "",
    output_path: str = "",
    width: int = 0,
    height: int = 0,
    maintain_aspect: bool = True,
    quality: int = 85,
) -> dict[str, Any]:
    """Resize an image to specified dimensions."""
    if not path:
        raise ValueError("image_resize: path is required")
    if not output_path:
        raise ValueError("image_resize: output_path is required")
    if width < 0 or height < 0:
        raise ValueError("image_resize: width and height must be non-negative")
    if width == 0 and height == 0:
        raise ValueError("image_resize: width and height cannot both be 0")

    Image, _ = _pil()
    with Image.open(path) as img:
        original_size = img.size

        if maintain_aspect:
            orig_w, orig_h = original_size
            if width and height:
                ratio = min(width / orig_w, height / orig_h)
                new_w = int(orig_w * ratio)
                new_h = int(orig_h * ratio)
            elif width:
                ratio = width / orig_w
                new_w = width
                new_h = int(orig_h * ratio)
            elif height:
                ratio = height / orig_h
                new_h = height
                new_w = int(orig_w * ratio)
            else:
                new_w, new_h = orig_w, orig_h
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        else:
            new_w = width or img.width
            new_h = height or img.height
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        _save_image(img, output_path, quality)

    return {
        "output_path": output_path,
        "original_size": {"width": original_size[0], "height": original_size[1]},
        "new_size": {"width": new_w, "height": new_h},
    }


@node(
    name="Image Convert",
    id="image_convert",
    category="Files",
    icon="image",
    requirements=["Pillow"],
    params={
        "path": {
            "description": "Path to source image.",
            "placeholder": "/path/to/image.png",
        },
        "output_path": {
            "description": "Output path with target extension.",
            "placeholder": "/path/to/image.jpg",
        },
        "quality": {
            "group": "Options",
            "type": "integer",
            "description": "Output quality (for JPEG/WebP).",
        },
    },
)
def image_convert(
    input: Any = None,
    path: str = "",
    output_path: str = "",
    quality: int = 85,
) -> dict[str, Any]:
    """Convert an image between formats."""
    if not path:
        raise ValueError("image_convert: path is required")
    if not output_path:
        raise ValueError("image_convert: output_path is required")

    Image, _ = _pil()
    with Image.open(path) as img:
        out_format = Path(output_path).suffix.lstrip(".").upper()
        if img.mode in ("RGBA", "LA") and out_format in ("JPEG",):
            img = img.convert("RGB")
        _save_image(img, output_path, quality)
        size = {"width": img.width, "height": img.height}

    return {
        "output_path": output_path,
        "format": out_format,
        "size": size,
    }


@node(
    name="Image Get Info",
    id="image_get_info",
    category="Files",
    icon="image",
    requirements=["Pillow"],
    tool_side_effecting=False,
    params={
        "path": {
            "description": "Path to the image.",
            "placeholder": "/path/to/image.jpg",
        },
    },
)
def image_get_info(input: Any = None, path: str = "") -> dict[str, Any]:
    """Get metadata from an image file."""
    if not path:
        raise ValueError("image_get_info: path is required")

    Image, _ = _pil()
    with Image.open(path) as img:
        file_size = Path(path).stat().st_size

        exif_data = None
        try:
            raw_exif = img._getexif()
            if raw_exif:
                from PIL.ExifTags import TAGS

                exif_data = {}
                for tag_id, value in raw_exif.items():
                    tag_name = TAGS.get(tag_id, str(tag_id))
                    if isinstance(value, bytes):
                        try:
                            value = value.decode("utf-8", errors="replace")
                        except Exception:
                            value = str(value)
                    exif_data[tag_name] = value
        except Exception:
            exif_data = None

        return {
            "width": img.width,
            "height": img.height,
            "format": img.format or "",
            "mode": img.mode,
            "file_size_bytes": file_size,
            "exif": exif_data,
        }


@node(
    name="Image Crop",
    id="image_crop",
    category="Files",
    icon="image",
    requirements=["Pillow"],
    params={
        "path": {
            "description": "Path to source image.",
        },
        "output_path": {
            "description": "Output path.",
        },
        "left": {
            "type": "integer",
            "description": "Left edge X coordinate.",
        },
        "top": {
            "type": "integer",
            "description": "Top edge Y coordinate.",
        },
        "right": {
            "type": "integer",
            "description": "Right edge X coordinate (0=image width).",
        },
        "bottom": {
            "type": "integer",
            "description": "Bottom edge Y coordinate (0=image height).",
        },
    },
)
def image_crop(
    input: Any = None,
    path: str = "",
    output_path: str = "",
    left: int = 0,
    top: int = 0,
    right: int = 0,
    bottom: int = 0,
) -> dict[str, Any]:
    """Crop an image to a specified region."""
    if not path:
        raise ValueError("image_crop: path is required")
    if not output_path:
        raise ValueError("image_crop: output_path is required")

    Image, _ = _pil()
    with Image.open(path) as img:
        right = right or img.width
        bottom = bottom or img.height

        if left >= right or top >= bottom:
            raise ValueError(
                "image_crop: invalid crop box (left must be < right, top must be < bottom)"
            )

        cropped = img.crop((left, top, right, bottom))
        _save_image(cropped, output_path)

        return {
            "output_path": output_path,
            "crop_box": [left, top, right, bottom],
            "size": {"width": cropped.width, "height": cropped.height},
        }


@node(
    name="Image Apply Filter",
    id="image_apply_filter",
    category="Files",
    icon="image",
    requirements=["Pillow"],
    params={
        "path": {
            "description": "Path to source image.",
        },
        "output_path": {
            "description": "Output path.",
        },
        "filter": {
            "choices": [
                "BLUR",
                "CONTOUR",
                "DETAIL",
                "EDGE_ENHANCE",
                "EDGE_ENHANCE_MORE",
                "EMBOSS",
                "FIND_EDGES",
                "SHARPEN",
                "SMOOTH",
                "SMOOTH_MORE",
            ],
            "description": "Filter to apply.",
        },
        "radius": {
            "group": "Options",
            "type": "integer",
            "description": "Blur radius (only used for BLUR filter).",
        },
    },
)
def image_apply_filter(
    input: Any = None,
    path: str = "",
    output_path: str = "",
    filter: str = "",
    radius: int = 2,
) -> dict[str, Any]:
    """Apply a predefined Pillow filter to an image."""
    if not path:
        raise ValueError("image_apply_filter: path is required")
    if not output_path:
        raise ValueError("image_apply_filter: output_path is required")
    if not filter:
        raise ValueError("image_apply_filter: filter is required")

    Image, ImageFilter = _pil()
    with Image.open(path) as img:
        if filter == "BLUR":
            img = img.filter(ImageFilter.GaussianBlur(radius=radius))
        else:
            try:
                pil_filter = getattr(ImageFilter, filter)
            except AttributeError as exc:
                raise ValueError(f"image_apply_filter: unknown filter '{filter}'") from exc
            img = img.filter(pil_filter)

        _save_image(img, output_path)

    return {
        "output_path": output_path,
        "filter_applied": filter,
        "size": {"width": img.width, "height": img.height},
    }


@node(
    name="Image Rotate",
    id="image_rotate",
    category="Files",
    icon="image",
    requirements=["Pillow"],
    params={
        "path": {
            "description": "Path to source image.",
        },
        "output_path": {
            "description": "Output path.",
        },
        "degrees": {
            "type": "integer",
            "default": 90,
            "description": "Rotation angle in degrees (counter-clockwise).",
        },
        "expand": {
            "group": "Options",
            "type": "boolean",
            "default": True,
            "description": "Expand canvas to fit rotated image.",
        },
        "background_color": {
            "group": "Options",
            "default": "black",
            "description": "Background color for exposed areas.",
        },
    },
)
def image_rotate(
    input: Any = None,
    path: str = "",
    output_path: str = "",
    degrees: int = 90,
    expand: bool = True,
    background_color: str = "black",
) -> dict[str, Any]:
    """Rotate an image by a given angle."""
    if not path:
        raise ValueError("image_rotate: path is required")
    if not output_path:
        raise ValueError("image_rotate: output_path is required")

    Image, _ = _pil()
    with Image.open(path) as img:
        rotated = img.rotate(
            float(degrees),
            expand=bool(expand),
            fillcolor=background_color,
            resample=Image.Resampling.BICUBIC,
        )
        _save_image(rotated, output_path)
        original_size = {"width": img.width, "height": img.height}

    return {
        "output_path": output_path,
        "original_size": original_size,
        "new_size": {"width": rotated.width, "height": rotated.height},
        "degrees": degrees,
    }


@node(
    name="Image Flip",
    id="image_flip",
    category="Files",
    icon="image",
    requirements=["Pillow"],
    params={
        "path": {
            "description": "Path to source image.",
        },
        "output_path": {
            "description": "Output path.",
        },
        "direction": {
            "choices": ["horizontal", "vertical", "both"],
            "default": "horizontal",
            "description": "Flip direction.",
        },
    },
)
def image_flip(
    input: Any = None,
    path: str = "",
    output_path: str = "",
    direction: str = "horizontal",
) -> dict[str, Any]:
    """Flip an image horizontally, vertically, or both."""
    if not path:
        raise ValueError("image_flip: path is required")
    if not output_path:
        raise ValueError("image_flip: output_path is required")

    Image, _ = _pil()
    with Image.open(path) as img:
        if direction == "horizontal":
            flipped = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        elif direction == "vertical":
            flipped = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        elif direction == "both":
            flipped = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            flipped = flipped.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        else:
            raise ValueError(f"image_flip: unknown direction '{direction}'")
        _save_image(flipped, output_path)

    return {
        "output_path": output_path,
        "size": {"width": flipped.width, "height": flipped.height},
        "direction": direction,
    }


@node(
    name="Image Thumbnail",
    id="image_thumbnail",
    category="Files",
    icon="image",
    requirements=["Pillow"],
    params={
        "path": {
            "description": "Path to source image.",
        },
        "output_path": {
            "description": "Output path.",
        },
        "size": {
            "description": "Max dimensions as 'width,height' (e.g. '200,200').",
            "placeholder": "200,200",
        },
        "quality": {
            "group": "Options",
            "type": "integer",
            "default": 85,
            "description": "JPEG output quality (1-100).",
        },
    },
)
def image_thumbnail(
    input: Any = None,
    path: str = "",
    output_path: str = "",
    size: str = "200,200",
    quality: int = 85,
) -> dict[str, Any]:
    """Create a thumbnail of an image."""
    if not path:
        raise ValueError("image_thumbnail: path is required")
    if not output_path:
        raise ValueError("image_thumbnail: output_path is required")

    try:
        parts = size.split(",")
        thumb_w = max(1, int(parts[0].strip()))
        thumb_h = max(1, int(parts[1].strip())) if len(parts) > 1 else thumb_w
    except (ValueError, IndexError):
        raise ValueError("image_thumbnail: size must be in format 'width,height'")

    Image, _ = _pil()
    with Image.open(path) as img:
        original_size = {"width": img.width, "height": img.height}
        img.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        _save_image(img, output_path, quality)

    return {
        "output_path": output_path,
        "original_size": original_size,
        "thumb_size": {"width": img.width, "height": img.height},
    }


__all__ = [
    "image_resize",
    "image_convert",
    "image_get_info",
    "image_crop",
    "image_apply_filter",
    "image_rotate",
    "image_flip",
    "image_thumbnail",
]
