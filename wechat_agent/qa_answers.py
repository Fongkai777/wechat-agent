"""Structured Q&A answers reference a frozen, server-owned source list."""
from __future__ import annotations

import json
from typing import Any


# 回答只输出段落及数字引用，来源详情由服务端冻结的证据列表提供。
QA_ANSWER_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "grounded_chat_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["paragraphs"],
            "properties": {
                "paragraphs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "text", "source_refs"],
                        "properties": {
                            "kind": {"type": "string", "enum": ["answer", "limitation"]},
                            "text": {"type": "string"},
                            "source_refs": {"type": "array", "items": {"type": "integer"}},
                        },
                    },
                },
            },
        },
    },
}


def validate_qa_answer(data: Any, source_count: int) -> dict[str, Any]:
    # 严格检查顶层与段落字段，拒绝空回答及模型夹带的额外结构。
    if not isinstance(data, dict) or set(data) != {"paragraphs"}:
        raise ValueError("Expected an object containing only paragraphs")
    paragraphs = data["paragraphs"]
    if not isinstance(paragraphs, list) or not paragraphs:
        raise ValueError("paragraphs must be a nonempty array")
    clean = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict) or set(paragraph) != {"kind", "text", "source_refs"}:
            raise ValueError("Each paragraph must contain only kind, text and source_refs")
        kind, text, refs = (paragraph[key] for key in ("kind", "text", "source_refs"))
        if kind not in ("answer", "limitation") or not isinstance(text, str) or not text.strip():
            raise ValueError("Invalid paragraph kind or empty text")
        # 引用是最终证据列表的 1 起始序号；拒绝越界、布尔值及伪造的来源编号。
        if not isinstance(refs, list) or any(type(ref) is not int or not 1 <= ref <= source_count for ref in refs):
            raise ValueError(f"source_refs must contain integers from 1 to {source_count}")
        # 实质回答必须有引用，限制说明可无引用；此处校验结构，不判定原文是否支持结论。
        if kind == "answer" and not refs:
            raise ValueError("An answer paragraph must cite evidence; use limitation only for missing evidence")
        clean.append({"kind": kind, "text": text.strip(), "source_refs": list(dict.fromkeys(refs))})
    return {"paragraphs": clean}


def parse_qa_answer(payload: dict[str, Any], source_count: int) -> dict[str, Any]:
    # 拒绝拒答、截断及非正常结束的响应，只有完整 JSON 才能进入引用校验。
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("模型没有返回回答")
    choice = choices[0]
    message = choice.get("message") or {}
    if message.get("refusal"):
        raise RuntimeError("模型拒绝了本次回答")
    if choice.get("finish_reason") != "stop":
        raise RuntimeError("模型回答未完整生成，请重试；未保存为有效回答")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Missing structured answer")
    return validate_qa_answer(json.loads(content), source_count)


def qa_answer_text(data: dict[str, Any]) -> str:
    # 将已验证段落渲染为正文与方括号引用，编号沿用冻结证据顺序。
    return "\n\n".join(
        paragraph["text"] + (" " + "".join(f"[{ref}]" for ref in paragraph["source_refs"])
                             if paragraph["source_refs"] else "")
        for paragraph in data["paragraphs"]
    )


def qa_source_kind(source: dict[str, Any]) -> str:
    return {"private": "私聊", "group": "群聊", "index_summary": "索引汇总"}.get(
        source.get("chat_type"), "会话类型未知")
