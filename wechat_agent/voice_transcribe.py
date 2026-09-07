from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import tempfile
import wave
from pathlib import Path
from typing import Any


SAMPLE_RATE = 24_000
DEFAULT_CACHE = Path("voice_transcriptions.json")


def voice_cache_path() -> Path:
    return Path(os.environ.get("WECHAT_AGENT_VOICE_CACHE") or DEFAULT_CACHE)


def voice_cache_key(rel_db: str, local_id: int, create_time: int) -> str:
    return json.dumps([rel_db, int(local_id), int(create_time)], ensure_ascii=False)


def load_voice_cache(cache_path: Path | None = None) -> dict[str, Any]:
    path = cache_path or voice_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_voice_cache(cache: dict[str, Any], cache_path: Path | None = None) -> None:
    path = cache_path or voice_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def cached_voice_transcription(rel_db: str, local_id: int, create_time: int, cache_path: Path | None = None) -> str:
    entry = load_voice_cache(cache_path).get(voice_cache_key(rel_db, local_id, create_time))
    if isinstance(entry, dict):
        return str(entry.get("text") or "").strip()
    if isinstance(entry, str):
        return entry.strip()
    return ""


def can_decode_silk() -> bool:
    return _find_silk_decoder() != ""


def can_transcribe_locally() -> bool:
    try:
        import whisper  # noqa: F401
    except Exception:
        return False
    return True


def voice_dependency_note() -> str:
    missing: list[str] = []
    if not can_decode_silk():
        missing.append("SILK 解码依赖 silk-python 或 pilk")
    if not can_transcribe_locally():
        missing.append("语音识别依赖 openai-whisper")
    if missing:
        return "未转写，缺少：" + "、".join(missing)
    return "未转写，点击转文字"


def decode_silk_to_wav(data: bytes) -> bytes | None:
    try:
        return _decode_silk_to_wav_or_raise(data)
    except Exception:
        return None


def transcribe_voice_data(
    data: bytes,
    rel_db: str,
    local_id: int,
    create_time: int,
    cache_path: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    cache = load_voice_cache(cache_path)
    key = voice_cache_key(rel_db, local_id, create_time)
    entry = cache.get(key)
    if not force and isinstance(entry, dict) and "text" in entry:
        return {"ok": True, "cached": True, **entry}

    wav_data = _decode_silk_to_wav_or_raise(data)
    result = transcribe_wav(wav_data)
    text = str(result.get("text") or "").strip() or "（无可识别文字）"
    item = {
        "text": text,
        "language": result.get("language") or "unknown",
        "backend": "openai-whisper-local",
        "model": whisper_model_name(),
        "create_time": int(create_time),
        "source_db": rel_db,
        "local_id": int(local_id),
        "audio_sha256": hashlib.sha256(data).hexdigest(),
        "empty_transcription": text == "（无可识别文字）",
    }
    cache[key] = item
    save_voice_cache(cache, cache_path)
    return {"ok": True, "cached": False, **item}


def transcribe_wav(wav_data: bytes) -> dict[str, str]:
    try:
        import whisper
    except Exception as exc:
        raise RuntimeError("缺少 openai-whisper：请先运行 .venv/bin/python -m pip install openai-whisper") from exc

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_data)
        wav_path = f.name
    try:
        model = _get_whisper_model()
        kwargs: dict[str, Any] = {"fp16": False}
        language = os.environ.get("WECHAT_AGENT_WHISPER_LANGUAGE", "zh").strip()
        if language and language.lower() not in {"auto", "detect"}:
            kwargs["language"] = language
        result = model.transcribe(wav_path, **kwargs)
        return {
            "language": str(result.get("language") or kwargs.get("language") or "unknown"),
            "text": str(result.get("text") or "").strip(),
        }
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def whisper_model_name() -> str:
    return os.environ.get("WECHAT_AGENT_WHISPER_MODEL", "base").strip() or "base"


@functools.lru_cache(maxsize=1)
def _get_whisper_model() -> Any:
    import whisper

    return whisper.load_model(whisper_model_name())


def _decode_silk_to_wav_or_raise(data: bytes) -> bytes:
    decoder = _find_silk_decoder()
    if not decoder:
        raise RuntimeError("缺少 SILK 解码依赖：请先运行 .venv/bin/python -m pip install pilk")
    silk_data = data[1:] if data[:1] == b"\x02" else data
    if not silk_data.startswith(b"#!SILK_V3"):
        raise RuntimeError("语音数据不是 SILK_V3 格式")
    pcm = _decode_with_pysilk(silk_data) if decoder == "pysilk" else _decode_with_pilk(silk_data)
    return _pcm_to_wav(pcm)


@functools.lru_cache(maxsize=1)
def _find_silk_decoder() -> str:
    try:
        import pysilk  # noqa: F401

        return "pysilk"
    except Exception:
        pass
    try:
        import pilk  # noqa: F401

        return "pilk"
    except Exception:
        return ""


def _decode_with_pysilk(silk_data: bytes) -> bytes:
    import pysilk

    pcm_out = io.BytesIO()
    pysilk.decode(io.BytesIO(silk_data), pcm_out, SAMPLE_RATE)
    return pcm_out.getvalue()


def _decode_with_pilk(silk_data: bytes) -> bytes:
    import pilk

    suffix = b"" if silk_data.endswith(b"\xff\xff") else b"\xff\xff"
    with tempfile.NamedTemporaryFile(suffix=".silk", delete=False) as silk_file:
        silk_file.write(silk_data + suffix)
        silk_path = silk_file.name
    pcm_fd, pcm_path = tempfile.mkstemp(suffix=".pcm")
    os.close(pcm_fd)
    try:
        pilk.decode(silk_path, pcm_path)
        return Path(pcm_path).read_bytes()
    finally:
        for path in (silk_path, pcm_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def _pcm_to_wav(pcm: bytes) -> bytes:
    wav_out = io.BytesIO()
    with wave.open(wav_out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return wav_out.getvalue()
