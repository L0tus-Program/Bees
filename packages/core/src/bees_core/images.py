"""Imagens locais limitadas; OCR opcional sem rede ou instalação implícita."""

import io
import os
import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg"})
MAX_IMAGE_BYTES = 512 * 1024
MAX_IMAGE_PIXELS = 8_000_000


def validate_image(media_type: str, content: bytes) -> None:
    if media_type not in IMAGE_MEDIA_TYPES or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Imagem fora dos limites.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if (
                    image.format != {"image/png": "PNG", "image/jpeg": "JPEG"}[media_type]
                    or image.width * image.height > MAX_IMAGE_PIXELS
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError("Formato ou dimensões inválidos.")
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                image.load()
    except (
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ValueError("Imagem inválida.") from None


def extract_text(content: bytes, media_type: str) -> str:
    """Executável local opcional; apenas inglês, sempre explicitado ao modelo."""
    from bees_core.providers.errors import ProviderError

    validate_image(media_type, content)
    executable = shutil.which("tesseract")
    if executable is None:
        raise ProviderError("image_ocr_unavailable")
    try:
        # Arquivos privados temporários e saída em disco: stdout não cresce na memória.
        with tempfile.TemporaryDirectory(prefix="bees-ocr-") as directory:
            source = Path(directory) / "input"
            source.write_bytes(content)
            output = Path(directory) / "output"
            subprocess.run(
                [executable, str(source), str(output), "-l", "eng"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=True,
                env=os.environ | {"OMP_THREAD_LIMIT": "1"},
            )
            with output.with_suffix(".txt").open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ProviderError("request_too_large")
            text = raw.decode("utf-8").strip()
            if not text:
                raise ProviderError("image_ocr_empty")
            return text
    except OSError, UnicodeError, subprocess.SubprocessError:
        raise ProviderError("image_ocr_failed") from None
