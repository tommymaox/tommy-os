from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from .config import Settings
from .schemas import FoodAiParseInput, FoodAiParseResult


def _library_context(items: list[dict[str, Any]], limit: int = 120) -> list[dict[str, Any]]:
    context: list[dict[str, Any]] = []
    for item in items[:limit]:
        context.append(
            {
                "id": item["id"],
                "name": item["name"],
                "brand": item.get("brand"),
                "serving_size": item.get("serving_size"),
                "serving_unit": item.get("serving_unit"),
                "kj": item.get("kj"),
                "protein": item.get("protein"),
                "carbs": item.get("carbs"),
                "fat": item.get("fat"),
                "fibre": item.get("fibre"),
            }
        )
    return context


def parse_food_with_ai(
    settings: Settings,
    payload: FoodAiParseInput,
    library_items: list[dict[str, Any]],
    *,
    request_id: str | None = None,
) -> FoodAiParseResult:
    if not settings.openai_api_key:
        raise RuntimeError("AI food assist is not configured")

    client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_ms / 1000)
    library_json = json.dumps(_library_context(library_items), ensure_ascii=False)
    system_prompt = (
        "You are a careful nutrition parser for a personal food tracker. "
        "Turn the user's meal description into structured food entries with estimated macros and kJ. "
        "If a provided saved library item is clearly the same food, use that exact item by returning its food_item_id "
        "and source_type 'library'. When you use a library item, servings must be relative to that saved serving size/unit. "
        "If there is not a clear match, return source_type 'estimate' with food_item_id null and estimate the macros for the described amount. "
        "Split multiple foods into separate entries. Keep names concise and readable. "
        "Confidence should be between 0 and 1. Use lower confidence for vague or uncertain foods. "
        "Use notes to mention uncertainty, assumptions, or preparation details when helpful. "
        "kJ should be broadly consistent with protein/carbs/fat unless you know a better real-world estimate. "
        "Do not include any prose outside the structured response."
    )
    user_prompt = (
        f"Meal slot: {payload.meal_slot or 'unknown'}\n"
        f"User description: {payload.text}\n\n"
        "Saved food library you may reuse when the match is clear:\n"
        f"{library_json}"
    )

    response = client.responses.parse(
        model=settings.openai_model,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
        ],
        text_format=FoodAiParseResult,
        reasoning={"effort": "none"},
        max_output_tokens=1600,
        extra_headers={k: v for k, v in {"X-Client-Request-Id": request_id or ""}.items() if v},
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise RuntimeError("AI food assist returned no structured result")
    parsed.model = settings.openai_model
    return parsed
