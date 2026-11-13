"""Local Korean Windows OCR. No cloud image transmission or chat export."""
import asyncio
from pathlib import Path

async def read_image(path):
    from winrt.windows.storage import StorageFile, FileAccessMode
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    engine = OcrEngine.try_create_from_language(Language('ko'))
    if engine is None:
        raise RuntimeError('Windows Korean OCR language unavailable')
    file = await StorageFile.get_file_from_path_async(str(Path(path).resolve()))
    stream = await file.open_async(FileAccessMode.READ)
    try:
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        try:
            result = await engine.recognize_async(bitmap)
            return [line.text for line in result.lines]
        finally:
            bitmap.close()
    finally:
        stream.close()


def read(path):
    return asyncio.run(read_image(path))
