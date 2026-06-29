from __future__ import annotations


def test_health_and_status_endpoints(client) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert "x-request-id" in health.headers

    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["db"]["reachable"] is True
    assert status.json()["integrations"]["ai_food_assist"]["configured"] is False


def test_event_crud_and_validation(client) -> None:
    bad = client.post(
        "/api/events",
        json={
            "title": "Broken",
            "date": "2026-04-04",
            "start_time": "16:00",
            "end_time": "15:00",
        },
    )
    assert bad.status_code == 422

    created = client.post(
        "/api/events",
        json={
            "title": "Lift session",
            "date": "2026-04-04",
            "type": "gym",
            "start_time": "17:00",
            "end_time": "18:00",
        },
    )
    assert created.status_code == 201
    event_id = created.json()["id"]

    listed = client.get("/api/events", params={"start": "2026-04-01", "end": "2026-04-30"})
    assert listed.status_code == 200
    assert any(event["id"] == event_id for event in listed.json())

    updated = client.put(f"/api/events/{event_id}", json={"title": "Updated lift session"})
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated lift session"

    deleted = client.delete(f"/api/events/{event_id}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True


def test_food_flow_and_summary(client) -> None:
    item = client.post(
        "/api/food/items",
        json={
            "name": "Chicken Breast",
            "serving_size": 100,
            "serving_unit": "g",
            "kj": 550,
            "protein": 31,
            "carbs": 0,
            "fat": 3.6,
            "fibre": 0,
        },
    )
    assert item.status_code == 201
    item_id = item.json()["id"]

    recipe = client.post(
        "/api/food/recipes",
        json={
            "name": "Chicken Bowl",
            "servings": 2,
            "ingredients": [
                {"food_item_id": item_id, "quantity": 2},
                {"custom_name": "Rice", "quantity": 1, "kj_override": 700, "protein_override": 5, "carbs_override": 35, "fat_override": 1, "fibre_override": 1},
            ],
        },
    )
    assert recipe.status_code == 201

    log = client.post(
        "/api/food/log",
        json={
            "date": "2026-04-04",
            "meal_slot": "dinner",
            "recipe_id": recipe.json()["id"],
            "servings": 1,
        },
    )
    assert log.status_code == 201

    summary = client.get("/api/food/summary", params={"start": "2026-04-01", "end": "2026-04-07"})
    assert summary.status_code == 200
    body = summary.json()
    assert body["active_days"] == 1
    assert body["totals"]["protein"] > 0


def test_client_log_endpoint_accepts_payload(client) -> None:
    response = client.post(
        "/api/client-logs",
        json={"entries": [{"event": "ui.test", "level": "info", "message": "hello", "data": {"tab": "calendar"}}]},
    )
    assert response.status_code == 202
    assert response.json()["accepted"] == 0


def test_ai_food_parse_requires_configuration(client) -> None:
    response = client.post("/api/food/ai/parse", json={"text": "200g chicken breast and 150g rice", "meal_slot": "lunch"})
    assert response.status_code == 503
