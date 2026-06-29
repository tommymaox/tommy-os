from __future__ import annotations

from zuyu_app.food import food_log_macros, recipe_totals


def test_recipe_totals_and_per_serving_are_rounded() -> None:
    totals, per_serving = recipe_totals(
        [
            {"quantity": 2, "item_kj": 100, "item_protein": 10, "item_carbs": 5, "item_fat": 1, "item_fibre": 2},
            {"quantity": 1, "kj_override": 80, "protein_override": 4, "carbs_override": 12, "fat_override": 3, "fibre_override": 1},
        ],
        servings=3,
    )

    assert totals == {"kj": 280.0, "protein": 24.0, "carbs": 22.0, "fat": 5.0, "fibre": 5.0}
    assert per_serving == {"kj": 93.33, "protein": 8.0, "carbs": 7.33, "fat": 1.67, "fibre": 1.67}


def test_food_log_macros_supports_recipe_overrides() -> None:
    macros = food_log_macros(
        {
            "recipe_id": "recipe-1",
            "servings": 1.5,
            "recipe_kj": 400,
            "recipe_protein": 30,
            "recipe_carbs": 25,
            "recipe_fat": 10,
            "recipe_fibre": 8,
            "protein_override": 35,
        }
    )

    assert macros == {"kj": 600.0, "protein": 52.5, "carbs": 37.5, "fat": 15.0, "fibre": 12.0}
