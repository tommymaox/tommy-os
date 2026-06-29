from __future__ import annotations

import sqlite3
from typing import Any


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def recipe_items_for(conn: sqlite3.Connection, recipe_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT fri.*, fi.name as item_name, fi.brand, fi.serving_size, fi.serving_unit,
                  fi.kj as item_kj, fi.protein as item_protein, fi.carbs as item_carbs,
                  fi.fat as item_fat, fi.fibre as item_fibre
           FROM food_recipe_items fri
           LEFT JOIN food_items fi ON fri.food_item_id = fi.id
           WHERE fri.recipe_id = ?
           ORDER BY fri.created_at ASC, fri.id ASC""",
        (recipe_id,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def recipe_totals(items: list[dict[str, Any]], servings: float | None) -> tuple[dict[str, float], dict[str, float]]:
    totals = {"kj": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0, "fibre": 0.0}
    for item in items:
        qty = _to_float(item.get("quantity") or 1)
        totals["kj"] += _to_float(item.get("kj_override") if item.get("kj_override") is not None else item.get("item_kj")) * qty
        totals["protein"] += _to_float(item.get("protein_override") if item.get("protein_override") is not None else item.get("item_protein")) * qty
        totals["carbs"] += _to_float(item.get("carbs_override") if item.get("carbs_override") is not None else item.get("item_carbs")) * qty
        totals["fat"] += _to_float(item.get("fat_override") if item.get("fat_override") is not None else item.get("item_fat")) * qty
        totals["fibre"] += _to_float(item.get("fibre_override") if item.get("fibre_override") is not None else item.get("item_fibre")) * qty
    yield_count = float(servings or 1) or 1
    per_serving = {key: round(value / yield_count, 2) for key, value in totals.items()}
    return {key: round(value, 2) for key, value in totals.items()}, per_serving


def recipe_payload(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    recipe = row_to_dict(row)
    items = recipe_items_for(conn, recipe["id"])
    totals, per_serving = recipe_totals(items, recipe.get("servings"))
    recipe["ingredients"] = items
    recipe["totals"] = totals
    recipe["per_serving"] = per_serving
    recipe["ingredient_count"] = len(items)
    return recipe


def enrich_food_log_item(conn: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any]:
    if item.get("recipe_id"):
        recipe_row = conn.execute("SELECT * FROM food_recipes WHERE id = ?", (item["recipe_id"],)).fetchone()
        recipe = recipe_payload(conn, recipe_row) if recipe_row else None
        if recipe:
            item["recipe_kj"] = recipe["per_serving"]["kj"]
            item["recipe_protein"] = recipe["per_serving"]["protein"]
            item["recipe_carbs"] = recipe["per_serving"]["carbs"]
            item["recipe_fat"] = recipe["per_serving"]["fat"]
            item["recipe_fibre"] = recipe["per_serving"]["fibre"]
    return item


def food_log_source_type(item: dict[str, Any]) -> str:
    if item.get("recipe_id"):
        return "recipe"
    if item.get("food_item_id"):
        return "food"
    return "custom"


def food_log_display_name(item: dict[str, Any]) -> str:
    return item.get("custom_name") or item.get("recipe_name") or item.get("item_name") or "Unknown"


def food_log_source_macros(item: dict[str, Any]) -> dict[str, float]:
    if item.get("recipe_id"):
        return {
            "kj": round(_to_float(item.get("kj_override") if item.get("kj_override") is not None else item.get("recipe_kj")), 2),
            "protein": round(_to_float(item.get("protein_override") if item.get("protein_override") is not None else item.get("recipe_protein")), 2),
            "carbs": round(_to_float(item.get("carbs_override") if item.get("carbs_override") is not None else item.get("recipe_carbs")), 2),
            "fat": round(_to_float(item.get("fat_override") if item.get("fat_override") is not None else item.get("recipe_fat")), 2),
            "fibre": round(_to_float(item.get("fibre_override") if item.get("fibre_override") is not None else item.get("recipe_fibre")), 2),
        }
    if item.get("food_item_id"):
        return {
            "kj": round(_to_float(item.get("kj_override") if item.get("kj_override") is not None else item.get("item_kj")), 2),
            "protein": round(_to_float(item.get("protein_override") if item.get("protein_override") is not None else item.get("item_protein")), 2),
            "carbs": round(_to_float(item.get("carbs_override") if item.get("carbs_override") is not None else item.get("item_carbs")), 2),
            "fat": round(_to_float(item.get("fat_override") if item.get("fat_override") is not None else item.get("item_fat")), 2),
            "fibre": round(_to_float(item.get("fibre_override") if item.get("fibre_override") is not None else item.get("item_fibre")), 2),
        }
    return {
        "kj": round(_to_float(item.get("kj_override")), 2),
        "protein": round(_to_float(item.get("protein_override")), 2),
        "carbs": round(_to_float(item.get("carbs_override")), 2),
        "fat": round(_to_float(item.get("fat_override")), 2),
        "fibre": round(_to_float(item.get("fibre_override")), 2),
    }


def food_log_history_signature(item: dict[str, Any]) -> str:
    macros = food_log_source_macros(item)
    source_type = food_log_source_type(item)
    return "|".join(
        [
            source_type,
            str(item.get("food_item_id") or ""),
            str(item.get("recipe_id") or ""),
            food_log_display_name(item).strip().lower(),
            str(round(_to_float(item.get("servings") or 1), 4)),
            str(macros["kj"]),
            str(macros["protein"]),
            str(macros["carbs"]),
            str(macros["fat"]),
            str(macros["fibre"]),
        ]
    )


def food_log_macros(item: dict[str, Any]) -> dict[str, float]:
    servings = _to_float(item.get("servings") or 1)
    if item.get("recipe_id"):
        return {
            "kj": round(_to_float(item.get("kj_override") if item.get("kj_override") is not None else item.get("recipe_kj")) * servings, 2),
            "protein": round(_to_float(item.get("protein_override") if item.get("protein_override") is not None else item.get("recipe_protein")) * servings, 2),
            "carbs": round(_to_float(item.get("carbs_override") if item.get("carbs_override") is not None else item.get("recipe_carbs")) * servings, 2),
            "fat": round(_to_float(item.get("fat_override") if item.get("fat_override") is not None else item.get("recipe_fat")) * servings, 2),
            "fibre": round(_to_float(item.get("fibre_override") if item.get("fibre_override") is not None else item.get("recipe_fibre")) * servings, 2),
        }
    if item.get("food_item_id"):
        return {
            "kj": round(_to_float(item.get("kj_override") if item.get("kj_override") is not None else item.get("item_kj")) * servings, 2),
            "protein": round(_to_float(item.get("protein_override") if item.get("protein_override") is not None else item.get("item_protein")) * servings, 2),
            "carbs": round(_to_float(item.get("carbs_override") if item.get("carbs_override") is not None else item.get("item_carbs")) * servings, 2),
            "fat": round(_to_float(item.get("fat_override") if item.get("fat_override") is not None else item.get("item_fat")) * servings, 2),
            "fibre": round(_to_float(item.get("fibre_override") if item.get("fibre_override") is not None else item.get("item_fibre")) * servings, 2),
        }
    return {
        "kj": round(_to_float(item.get("kj_override")) * servings, 2),
        "protein": round(_to_float(item.get("protein_override")) * servings, 2),
        "carbs": round(_to_float(item.get("carbs_override")) * servings, 2),
        "fat": round(_to_float(item.get("fat_override")) * servings, 2),
        "fibre": round(_to_float(item.get("fibre_override")) * servings, 2),
    }
