"""Распознавание речи (контракт 1.6): голосовые в Telegram, микрофон в чатах Пульса, расшифровки по просьбе агентов.

Манифест: право `speech:provide` и `"speech": {"title": "OpenAI, Groq"}`. Плагин описывает распознавание функцией
и подключает её:

    from pulse_plugin import Plugin
    from pulse_plugin.speech import NotConfigured

    plugin = Plugin()

    @plugin.provide_speech
    async def transcribe(path, *, language, source, filename, mime):
        # path — временный файл с записью (аудио или видео; удаляется после ответа)
        # language — «ru», «en» или None (определить); source — telegram | chat | agent
        return {"text": "…", "language": "ru", "duration_sec": 7.4, "model": "whisper-1"}

Пульс шлёт записи на POST /speech/transcribe процесса плагина (только из своей сети). Ошибки — исключения ниже:
BadAudio (400 — формат не понят), TooLong (413), NotConfigured (503 — нет ключей), Upstream (502 — модель
не ответила).
"""

import inspect
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

__all__ = ["SpeechError", "BadAudio", "TooLong", "NotConfigured", "Upstream", "mount"]

CHUNK = 1024 * 1024


class SpeechError(Exception):
    status = 400

    def __init__(self, message: str = ""):
        super().__init__(message or self.__class__.__doc__ or "ошибка распознавания")


class BadAudio(SpeechError):
    """Формат записи не понят"""
    status = 400


class TooLong(SpeechError):
    """Запись слишком длинная"""
    status = 413


class NotConfigured(SpeechError):
    """Распознавание не настроено"""
    status = 503


class Upstream(SpeechError):
    """Модель не ответила"""
    status = 502


def mount(app: FastAPI, handler) -> None:
    """Маршрут /speech/transcribe для Пульса: запись — во временный файл, дальше — handler."""

    @app.post("/speech/transcribe")
    async def speech_transcribe(file: UploadFile = File(...), language: str | None = Form(None),
                                source: str = Form("chat")) -> Any:
        suffix = Path(file.filename or "").suffix[:10]
        fd, name = tempfile.mkstemp(prefix="pulse-speech-", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := await file.read(CHUNK):
                    out.write(chunk)
            kwargs = {"language": language or None, "source": source, "filename": file.filename,
                      "mime": file.content_type}
            if inspect.iscoroutinefunction(handler):
                result = await handler(Path(name), **kwargs)
            else:
                result = await run_in_threadpool(lambda: handler(Path(name), **kwargs))
        except SpeechError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)
        finally:
            Path(name).unlink(missing_ok=True)
        if isinstance(result, str):
            result = {"text": result}
        return result
