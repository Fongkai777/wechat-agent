"""Strict JSON output helpers shared by model-facing modules."""
import json


STRING = {"type": "string"}


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def parse_json_completion(payload):
    choices = payload.get("choices") or []
    if not choices or choices[0].get("finish_reason") != "stop":
        raise ValueError("模型输出未完整生成，请重试")
    message = choices[0].get("message") or {}
    if message.get("refusal"):
        raise ValueError("模型拒绝了本次请求")
    data = json.loads(message.get("content") or "")
    if not isinstance(data, dict):
        raise ValueError("模型输出格式无效")
    return data
