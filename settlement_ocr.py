import io
import logging
import re

from travian_discord_integration import extract_all_coordinates


OCR_CONFIG = "--psm 6 -c tessedit_char_whitelist=0123456789-+|()/[]{}<> "
LOGGER = logging.getLogger(__name__)

_warned_missing_dependency = False
_warned_missing_tesseract = False

OCR_WRAPPED_PAIR_FALLBACK_PATTERN = re.compile(
    r"(?<!\d)[\(\[\{<]\s*(?P<x>[+\-]?\d{1,3})\s*[^0-9+\-]\s*(?P<y>[+\-]?\d{1,3})\s*[\)\]\}>](?!\d)"
)
OCR_MISSING_SEPARATOR_FALLBACK_PATTERN = re.compile(
    r"(?<!\d)[\(\[\{<]\s*(?P<x>[+\-]?\d{1,3})(?P<y>[+\-]\d{1,3})\s*[\)\]\}>](?!\d)"
)


def extract_coordinates_from_image_bytes(image_bytes: bytes) -> list[tuple[int, int]]:
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        _warn_missing_dependency("Pillow")
        return []

    try:
        import pytesseract
    except ImportError:
        _warn_missing_dependency("pytesseract")
        return []

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            text = pytesseract.image_to_string(
                _prepare_image_for_ocr(image, Image, ImageOps),
                config=OCR_CONFIG,
            )
    except pytesseract.TesseractNotFoundError:
        _warn_missing_tesseract()
        return []
    except pytesseract.TesseractError:
        LOGGER.exception("Tesseract konnte einen Siedel-Screenshot nicht auswerten.")
        return []
    except UnidentifiedImageError:
        LOGGER.debug("Discord-Anhang ist kein lesbares Bild fuer OCR.")
        return []
    except OSError:
        LOGGER.exception("Discord-Bild konnte fuer OCR nicht gelesen werden.")
        return []

    return extract_coordinates_from_ocr_text(text)


def extract_coordinates_from_ocr_text(text: str | None) -> list[tuple[int, int]]:
    if not text:
        return []

    coordinates = extract_all_coordinates(text)
    seen = set(coordinates)
    for pattern in (
        OCR_WRAPPED_PAIR_FALLBACK_PATTERN,
        OCR_MISSING_SEPARATOR_FALLBACK_PATTERN,
    ):
        for match in pattern.finditer(text):
            coordinate = (int(match.group("x")), int(match.group("y")))
            if coordinate in seen:
                continue
            seen.add(coordinate)
            coordinates.append(coordinate)
    return coordinates


def _prepare_image_for_ocr(image, image_module, image_ops):
    grayscale = image.convert("L")
    width, height = grayscale.size
    if max(width, height) <= 1200:
        grayscale = grayscale.resize(
            (width * 3, height * 3),
            resample=_resampling_lanczos(image_module),
        )
    grayscale = image_ops.autocontrast(grayscale)
    return grayscale.point(lambda value: 0 if value < 170 else 255)


def _resampling_lanczos(image_module):
    resampling = getattr(image_module, "Resampling", None)
    if resampling is not None:
        return resampling.LANCZOS
    return getattr(image_module, "LANCZOS", 1)


def _warn_missing_dependency(package_name: str) -> None:
    global _warned_missing_dependency
    if _warned_missing_dependency:
        return
    LOGGER.warning(
        "OCR fuer Siedel-Screenshots ist deaktiviert: Python-Paket %s fehlt.",
        package_name,
    )
    _warned_missing_dependency = True


def _warn_missing_tesseract() -> None:
    global _warned_missing_tesseract
    if _warned_missing_tesseract:
        return
    LOGGER.warning(
        "OCR fuer Siedel-Screenshots ist deaktiviert: Tesseract ist nicht installiert."
    )
    _warned_missing_tesseract = True
