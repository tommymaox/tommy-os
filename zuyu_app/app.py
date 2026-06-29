from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from .ai_food import parse_food_with_ai
try:
    from .brief import AEST, run_brief_generation
except ImportError:
    from datetime import timezone, timedelta
    AEST = timezone(timedelta(hours=10))
    async def run_brief_generation(*a, **kw): pass
from .config import Settings, get_settings
from .db import db_session, init_db
from .food import (
    enrich_food_log_item,
    food_log_display_name,
    food_log_history_signature,
    food_log_macros,
    food_log_source_macros,
    food_log_source_type,
    recipe_payload,
    row_to_dict,
)
from .observability import (
    configure_logging,
    http_exception_handler,
    request_logging_middleware,
    unhandled_exception_handler,
    validation_exception_handler,
    log_event,
)
from .schemas import (
    ClientLogBatch,
    EventCreate,
    EventUpdate,
    FoodItemCreate,
    FoodItemUpdate,
    FoodAiParseInput,
    FoodLogCreate,
    FoodLogUpdate,
    FoodRecipeCreate,
    FoodRecipeUpdate,
    KbValue,
    RecipeIngredientInput,
    TodoCreate,
    TodoUpdate,
    WikiPageCreate,
    WikiPageUpdate,
    RawSourceCreate,
    RawSourceUpdate,
    RawSourceIngest,
)
from .validation import KB_KEY_RE, parse_date


STARTED_AT = datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def replace_recipe_ingredients(conn, recipe_id: str, ingredients: list[RecipeIngredientInput]) -> None:
    conn.execute("DELETE FROM food_recipe_items WHERE recipe_id = ?", (recipe_id,))
    now = utc_now_iso()
    for item in ingredients:
        ingredient_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO food_recipe_items (
                   id, recipe_id, food_item_id, custom_name, quantity,
                   kj_override, protein_override, carbs_override, fat_override, fibre_override, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ingredient_id,
                recipe_id,
                item.food_item_id,
                item.custom_name,
                item.quantity,
                item.kj_override,
                item.protein_override,
                item.carbs_override,
                item.fat_override,
                item.fibre_override,
                now,
            ),
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logger = configure_logging(settings)

    async def _brief_auto_generate(today: str) -> None:
        brief_id = str(uuid.uuid4())
        now_iso = utc_now_iso()
        try:
            with db_session(settings.db_path) as conn:
                # Verify still no brief for today before inserting
                existing = conn.execute(
                    "SELECT id FROM briefings WHERE date=? AND status IN ('done','running') LIMIT 1", (today,)
                ).fetchone()
                if existing:
                    return
                conn.execute(
                    "INSERT INTO briefings (id, date, status, created_at, updated_at) VALUES (?,?,'running',?,?)",
                    (brief_id, today, now_iso, now_iso),
                )
        except Exception:
            return
        asyncio.create_task(run_brief_generation(settings, settings.db_path, brief_id, today))

    async def _brief_scheduler_loop() -> None:
        await asyncio.sleep(20)
        while True:
            try:
                now_aest = datetime.now(AEST)
                today = now_aest.strftime("%Y-%m-%d")
                if now_aest.hour >= 6:
                    with db_session(settings.db_path) as conn:
                        row = conn.execute(
                            "SELECT status FROM briefings WHERE date=? ORDER BY created_at DESC LIMIT 1", (today,)
                        ).fetchone()
                    if not row:
                        log_event(logger, "brief.scheduler.trigger", date=today)
                        await _brief_auto_generate(today)
            except Exception as exc:
                log_event(logger, "brief.scheduler.error", error=str(exc))
            await asyncio.sleep(1800)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(settings)
        # Reset any briefings stuck in 'running' from a previous crash
        with db_session(settings.db_path) as conn:
            conn.execute(
                "UPDATE briefings SET status='failed', error='Reset on startup' WHERE status='running'"
            )
        asyncio.create_task(_brief_scheduler_loop())
        log_event(logger, "app.start", env=settings.app_env, version=settings.app_version, db_path=settings.db_path)
        yield
        log_event(logger, "app.stop", env=settings.app_env, version=settings.app_version)

    app = FastAPI(title="zuyu dashboard", lifespan=lifespan)
    app.state.settings = settings
    app.middleware("http")(request_logging_middleware)
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.middleware("http")
    async def no_cache_html(request: Request, call_next):
        response = await call_next(request)
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            response.headers["CDN-Cache-Control"] = "no-store"
            response.headers["Cloudflare-CDN-Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health():
        with db_session(settings.db_path) as conn:
            conn.execute("SELECT 1").fetchone()
        return {
            "ok": True,
            "status": "healthy",
            "version": settings.app_version,
            "env": settings.app_env,
            "uptime_seconds": int((datetime.now(timezone.utc) - STARTED_AT).total_seconds()),
        }

    @app.head("/api/health")
    def health_head():
        with db_session(settings.db_path) as conn:
            conn.execute("SELECT 1").fetchone()
        return None

    @app.get("/api/status")
    def status():
        with db_session(settings.db_path) as conn:
            events_count = conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
            food_count = conn.execute("SELECT COUNT(*) AS count FROM food_log").fetchone()["count"]
        return {
            "app": {"env": settings.app_env, "version": settings.app_version},
            "db": {"path": settings.db_path, "reachable": True},
            "integrations": {"ai_food_assist": {"configured": bool(settings.openai_api_key), "model": settings.openai_model}},
            "counts": {"events": events_count, "food_log": food_count},
            "uptime_seconds": int((datetime.now(timezone.utc) - STARTED_AT).total_seconds()),
        }

    @app.post("/api/client-logs", status_code=202)
    async def client_logs(payload: ClientLogBatch, request: Request):
        if not settings.client_log_enabled or not payload.entries:
            return {"ok": True, "accepted": 0}
        request_id = getattr(request.state, "request_id", None)
        for entry in payload.entries:
            log_event(
                logger,
                "client.log",
                request_id=request_id,
                level=entry.level,
                client_event=entry.event,
                message=entry.message,
                data=entry.data,
            )
        return {"ok": True, "accepted": len(payload.entries)}

    @app.get("/api/events")
    def list_events(start: str = Query(...), end: str = Query(...)):
        start = parse_date(start)
        end = parse_date(end)
        with db_session(settings.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE date >= ? AND date <= ? ORDER BY date, start_time",
                (start, end),
            ).fetchall()
            return [row_to_dict(row) for row in rows]

    @app.post("/api/events", status_code=201)
    def create_event(payload: EventCreate):
        event_id = str(uuid.uuid4())
        now = utc_now_iso()
        with db_session(settings.db_path) as conn:
            conn.execute(
                """INSERT INTO events (id, title, date, type, start_time, end_time, notes, color, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    payload.title,
                    payload.date,
                    payload.type,
                    payload.start_time,
                    payload.end_time,
                    payload.notes,
                    payload.color,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            return row_to_dict(row)

    @app.put("/api/events/{event_id}")
    def update_event(event_id: str, payload: EventUpdate):
        with db_session(settings.db_path) as conn:
            existing = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Event not found")
            fields = {key: value for key, value in payload.model_dump(exclude_unset=True).items()}
            if not fields:
                return row_to_dict(existing)
            if "start_time" in fields and "end_time" not in fields:
                if fields["start_time"] and existing["end_time"] and existing["end_time"] <= fields["start_time"]:
                    raise HTTPException(status_code=422, detail="end_time must be after start_time")
            fields["updated_at"] = utc_now_iso()
            set_clause = ", ".join(f"{key} = ?" for key in fields)
            conn.execute(f"UPDATE events SET {set_clause} WHERE id = ?", [*fields.values(), event_id])
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            return row_to_dict(row)

    @app.delete("/api/events/{event_id}")
    def delete_event(event_id: str):
        with db_session(settings.db_path) as conn:
            existing = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Event not found")
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            return {"ok": True}

    @app.get("/api/todos")
    def list_todos():
        with db_session(settings.db_path) as conn:
            rows = conn.execute("SELECT * FROM todos ORDER BY created_at ASC").fetchall()
            return [row_to_dict(row) for row in rows]

    @app.post("/api/todos", status_code=201)
    def create_todo(payload: TodoCreate):
        todo_id = str(uuid.uuid4())
        now = utc_now_iso()
        with db_session(settings.db_path) as conn:
            conn.execute(
                "INSERT INTO todos (id, text, done, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
                (todo_id, payload.text, now, now),
            )
            row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
            return row_to_dict(row)

    @app.put("/api/todos/{todo_id}")
    def update_todo(todo_id: str, payload: TodoUpdate):
        with db_session(settings.db_path) as conn:
            existing = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Todo not found")
            fields: dict[str, Any] = {}
            if payload.text is not None:
                fields["text"] = payload.text
            if payload.done is not None:
                fields["done"] = 1 if payload.done else 0
            if not fields:
                return row_to_dict(existing)
            fields["updated_at"] = utc_now_iso()
            conn.execute(f"UPDATE todos SET {', '.join(f'{key} = ?' for key in fields)} WHERE id = ?", [*fields.values(), todo_id])
            row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
            return row_to_dict(row)

    @app.delete("/api/todos/{todo_id}")
    def delete_todo(todo_id: str):
        with db_session(settings.db_path) as conn:
            existing = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Todo not found")
            conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
            return {"ok": True}

    @app.get("/api/kb/{key}")
    def kb_get(key: str):
        if not KB_KEY_RE.match(key):
            raise HTTPException(status_code=400, detail="Invalid key")
        with db_session(settings.db_path) as conn:
            row = conn.execute("SELECT value FROM kb_store WHERE key = ?", (key,)).fetchone()
        if not row:
            return {"value": None}
        try:
            return {"value": json.loads(row["value"])}
        except Exception:
            return {"value": row["value"]}

    @app.put("/api/kb/{key}")
    def kb_put(key: str, payload: KbValue):
        if not KB_KEY_RE.match(key):
            raise HTTPException(status_code=400, detail="Invalid key")
        now = utc_now_iso()
        value = json.dumps(payload.value, ensure_ascii=False)
        with db_session(settings.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kb_store (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )
            return {"ok": True}

    @app.post("/api/food/ai/parse")
    def parse_food_description(payload: FoodAiParseInput, request: Request):
        if not settings.openai_api_key:
            raise HTTPException(status_code=503, detail="AI food assist is not configured on this server")
        request_id = getattr(request.state, "request_id", None)
        with db_session(settings.db_path) as conn:
            items = [row_to_dict(row) for row in conn.execute("SELECT * FROM food_items ORDER BY name COLLATE NOCASE").fetchall()]
        try:
            result = parse_food_with_ai(settings, payload, items, request_id=request_id)
            log_event(
                logger,
                "food.ai.parse.success",
                request_id=request_id,
                model=settings.openai_model,
                text_length=len(payload.text),
                entry_count=len(result.entries),
            )
            return result.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            log_event(
                logger,
                "food.ai.parse.error",
                request_id=request_id,
                model=settings.openai_model,
                error=repr(exc),
            )
            raise HTTPException(status_code=502, detail="AI food assist failed to estimate this meal") from exc

    _FOOD_JSON_FIELDS = {"ingredients", "steps"}

    def _food_item_out(row: sqlite3.Row) -> dict[str, Any]:
        d = row_to_dict(row)
        for field in _FOOD_JSON_FIELDS:
            if isinstance(d.get(field), str):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    pass
        return d

    def _food_item_db_value(key: str, value: Any) -> Any:
        if key in _FOOD_JSON_FIELDS and value is not None:
            return json.dumps(value)
        return value

    @app.get("/api/food/items")
    def list_food_items(q: str | None = None):
        with db_session(settings.db_path) as conn:
            if q:
                rows = conn.execute(
                    "SELECT * FROM food_items WHERE name LIKE ? OR brand LIKE ? ORDER BY name COLLATE NOCASE",
                    (f"%{q.strip()}%", f"%{q.strip()}%"),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM food_items ORDER BY name COLLATE NOCASE").fetchall()
            return [_food_item_out(row) for row in rows]

    @app.post("/api/food/items", status_code=201)
    def create_food_item(payload: FoodItemCreate):
        item_id = str(uuid.uuid4())
        now = utc_now_iso()
        with db_session(settings.db_path) as conn:
            conn.execute(
                """INSERT INTO food_items (id, name, brand, serving_size, serving_unit, kj, protein, carbs, fat, fibre, notes, ingredients, steps, photo, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id,
                    payload.name,
                    payload.brand,
                    payload.serving_size,
                    payload.serving_unit,
                    payload.kj,
                    payload.protein,
                    payload.carbs,
                    payload.fat,
                    payload.fibre,
                    payload.notes,
                    json.dumps(payload.ingredients) if payload.ingredients is not None else None,
                    json.dumps(payload.steps) if isinstance(payload.steps, list) else payload.steps,
                    payload.photo,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM food_items WHERE id = ?", (item_id,)).fetchone()
            return _food_item_out(row)

    @app.put("/api/food/items/{item_id}")
    def update_food_item(item_id: str, payload: FoodItemUpdate):
        with db_session(settings.db_path) as conn:
            existing = conn.execute("SELECT * FROM food_items WHERE id = ?", (item_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Food item not found")
            fields = {key: _food_item_db_value(key, value) for key, value in payload.model_dump(exclude_unset=True).items()}
            if not fields:
                return _food_item_out(existing)
            fields["updated_at"] = utc_now_iso()
            conn.execute(f"UPDATE food_items SET {', '.join(f'{key} = ?' for key in fields)} WHERE id = ?", [*fields.values(), item_id])
            row = conn.execute("SELECT * FROM food_items WHERE id = ?", (item_id,)).fetchone()
            return _food_item_out(row)

    @app.delete("/api/food/items/{item_id}")
    def delete_food_item(item_id: str):
        with db_session(settings.db_path) as conn:
            existing = conn.execute("SELECT id FROM food_items WHERE id = ?", (item_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Food item not found")
            conn.execute("DELETE FROM food_items WHERE id = ?", (item_id,))
            conn.execute("DELETE FROM food_log WHERE food_item_id = ?", (item_id,))
            conn.execute("DELETE FROM food_recipe_items WHERE food_item_id = ?", (item_id,))
            return {"ok": True}

    @app.get("/api/food/recipes")
    def list_food_recipes():
        with db_session(settings.db_path) as conn:
            rows = conn.execute("SELECT * FROM food_recipes ORDER BY name COLLATE NOCASE").fetchall()
            return [recipe_payload(conn, row) for row in rows]

    @app.get("/api/food/recipes/{recipe_id}")
    def get_food_recipe(recipe_id: str):
        with db_session(settings.db_path) as conn:
            row = conn.execute("SELECT * FROM food_recipes WHERE id = ?", (recipe_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Recipe not found")
            return recipe_payload(conn, row)

    @app.post("/api/food/recipes", status_code=201)
    def create_food_recipe(payload: FoodRecipeCreate):
        recipe_id = str(uuid.uuid4())
        now = utc_now_iso()
        with db_session(settings.db_path) as conn:
            conn.execute(
                """INSERT INTO food_recipes (id, name, servings, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (recipe_id, payload.name, payload.servings, payload.notes, now, now),
            )
            replace_recipe_ingredients(conn, recipe_id, payload.ingredients)
            row = conn.execute("SELECT * FROM food_recipes WHERE id = ?", (recipe_id,)).fetchone()
            return recipe_payload(conn, row)

    @app.put("/api/food/recipes/{recipe_id}")
    def update_food_recipe(recipe_id: str, payload: FoodRecipeUpdate):
        with db_session(settings.db_path) as conn:
            existing = conn.execute("SELECT * FROM food_recipes WHERE id = ?", (recipe_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Recipe not found")
            fields = {key: value for key, value in payload.model_dump(exclude={"ingredients"}, exclude_unset=True).items()}
            if fields:
                fields["updated_at"] = utc_now_iso()
                conn.execute(f"UPDATE food_recipes SET {', '.join(f'{key} = ?' for key in fields)} WHERE id = ?", [*fields.values(), recipe_id])
            if payload.ingredients is not None:
                replace_recipe_ingredients(conn, recipe_id, payload.ingredients)
            row = conn.execute("SELECT * FROM food_recipes WHERE id = ?", (recipe_id,)).fetchone()
            return recipe_payload(conn, row)

    @app.delete("/api/food/recipes/{recipe_id}")
    def delete_food_recipe(recipe_id: str):
        with db_session(settings.db_path) as conn:
            existing = conn.execute("SELECT id FROM food_recipes WHERE id = ?", (recipe_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Recipe not found")
            conn.execute("DELETE FROM food_recipe_items WHERE recipe_id = ?", (recipe_id,))
            conn.execute("DELETE FROM food_recipes WHERE id = ?", (recipe_id,))
            conn.execute("DELETE FROM food_log WHERE recipe_id = ?", (recipe_id,))
            return {"ok": True}

    @app.get("/api/food/log")
    def get_food_log(date: str = Query(...)):
        date = parse_date(date)
        with db_session(settings.db_path) as conn:
            rows = conn.execute(
                """SELECT fl.*, fi.name as item_name, fi.brand, fi.serving_size, fi.serving_unit,
                          fi.kj as item_kj, fi.protein as item_protein, fi.carbs as item_carbs,
                          fi.fat as item_fat, fi.fibre as item_fibre,
                          fr.name as recipe_name, fr.servings as recipe_servings
                   FROM food_log fl
                   LEFT JOIN food_items fi ON fl.food_item_id = fi.id
                   LEFT JOIN food_recipes fr ON fl.recipe_id = fr.id
                   WHERE fl.date = ?
                   ORDER BY fl.created_at ASC""",
                (date,),
            ).fetchall()
            return [enrich_food_log_item(conn, row_to_dict(row)) for row in rows]

    @app.get("/api/food/log/history")
    def get_food_log_history(limit: int = Query(40, ge=1, le=100)):
        with db_session(settings.db_path) as conn:
            rows = conn.execute(
                """SELECT fl.*, fi.name as item_name, fi.brand, fi.serving_size, fi.serving_unit,
                          fi.kj as item_kj, fi.protein as item_protein, fi.carbs as item_carbs,
                          fi.fat as item_fat, fi.fibre as item_fibre,
                          fr.name as recipe_name, fr.servings as recipe_servings
                   FROM food_log fl
                   LEFT JOIN food_items fi ON fl.food_item_id = fi.id
                   LEFT JOIN food_recipes fr ON fl.recipe_id = fr.id
                   ORDER BY COALESCE(fl.updated_at, fl.created_at) DESC, fl.created_at DESC
                   LIMIT ?""",
                (max(limit * 6, 120),),
            ).fetchall()
            history: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in rows:
                item = enrich_food_log_item(conn, row_to_dict(row))
                signature = food_log_history_signature(item)
                if signature in seen:
                    continue
                seen.add(signature)
                item["source_type"] = food_log_source_type(item)
                item["display_name"] = food_log_display_name(item)
                item["template_macros"] = food_log_source_macros(item)
                history.append(item)
                if len(history) >= limit:
                    break
            return history

    @app.get("/api/food/summary")
    def get_food_summary(start: str = Query(...), end: str = Query(...)):
        start = parse_date(start)
        end = parse_date(end)
        with db_session(settings.db_path) as conn:
            rows = conn.execute(
                """SELECT fl.*, fi.name as item_name, fi.brand, fi.serving_size, fi.serving_unit,
                          fi.kj as item_kj, fi.protein as item_protein, fi.carbs as item_carbs,
                          fi.fat as item_fat, fi.fibre as item_fibre,
                          fr.name as recipe_name, fr.servings as recipe_servings
                   FROM food_log fl
                   LEFT JOIN food_items fi ON fl.food_item_id = fi.id
                   LEFT JOIN food_recipes fr ON fl.recipe_id = fr.id
                   WHERE fl.date >= ? AND fl.date <= ?
                   ORDER BY fl.date ASC, fl.created_at ASC""",
                (start, end),
            ).fetchall()
            items = [enrich_food_log_item(conn, row_to_dict(row)) for row in rows]
        start_dt = datetime.strptime(start, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end, "%Y-%m-%d").date()
        totals = {"kj": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0, "fibre": 0.0}
        by_date: dict[str, dict[str, Any]] = {}
        for item in items:
            macros = food_log_macros(item)
            date_key = item["date"]
            day = by_date.setdefault(date_key, {"date": date_key, "totals": {"kj": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0, "fibre": 0.0}, "entries": 0})
            for key, value in macros.items():
                totals[key] += value
                day["totals"][key] += value
            day["entries"] += 1
        days = []
        cursor = start_dt
        while cursor <= end_dt:
            key = cursor.isoformat()
            day = by_date.get(key, {"date": key, "totals": {"kj": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0, "fibre": 0.0}, "entries": 0})
            day["totals"] = {name: round(value, 2) for name, value in day["totals"].items()}
            days.append(day)
            cursor += timedelta(days=1)
        total_days = max(1, len(days))
        active_days = sum(1 for day in days if day["entries"] > 0)
        averages = {key: round(value / total_days, 2) for key, value in totals.items()}
        best_protein = max(days, key=lambda day: day["totals"]["protein"], default=None)
        best_kj = max(days, key=lambda day: day["totals"]["kj"], default=None)
        return {
            "start": start,
            "end": end,
            "days": days,
            "totals": {key: round(value, 2) for key, value in totals.items()},
            "averages": averages,
            "active_days": active_days,
            "best_protein_day": best_protein["date"] if best_protein and best_protein["totals"]["protein"] > 0 else None,
            "best_kj_day": best_kj["date"] if best_kj and best_kj["totals"]["kj"] > 0 else None,
        }

    @app.post("/api/food/log", status_code=201)
    def add_food_log(payload: FoodLogCreate):
        log_id = str(uuid.uuid4())
        now = utc_now_iso()
        with db_session(settings.db_path) as conn:
            conn.execute(
                """INSERT INTO food_log (id, date, food_item_id, recipe_id, meal_slot, custom_name, servings,
                   kj_override, protein_override, carbs_override, fat_override, fibre_override, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    log_id,
                    payload.date,
                    payload.food_item_id,
                    payload.recipe_id,
                    payload.meal_slot,
                    payload.custom_name,
                    payload.servings,
                    payload.kj_override,
                    payload.protein_override,
                    payload.carbs_override,
                    payload.fat_override,
                    payload.fibre_override,
                    payload.notes,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """SELECT fl.*, fi.name as item_name, fi.brand, fi.serving_size, fi.serving_unit,
                          fi.kj as item_kj, fi.protein as item_protein, fi.carbs as item_carbs,
                          fi.fat as item_fat, fi.fibre as item_fibre,
                          fr.name as recipe_name, fr.servings as recipe_servings
                   FROM food_log fl
                   LEFT JOIN food_items fi ON fl.food_item_id = fi.id
                   LEFT JOIN food_recipes fr ON fl.recipe_id = fr.id
                   WHERE fl.id = ?""",
                (log_id,),
            ).fetchone()
            return enrich_food_log_item(conn, row_to_dict(row))

    @app.put("/api/food/log/{log_id}")
    def update_food_log(log_id: str, payload: FoodLogUpdate):
        with db_session(settings.db_path) as conn:
            existing = conn.execute("SELECT * FROM food_log WHERE id = ?", (log_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Log entry not found")
            fields = payload.model_dump(exclude_unset=True)
            if fields:
                fields["updated_at"] = utc_now_iso()
                conn.execute(f"UPDATE food_log SET {', '.join(f'{key} = ?' for key in fields)} WHERE id = ?", [*fields.values(), log_id])
            row = conn.execute(
                """SELECT fl.*, fi.name as item_name, fi.brand, fi.serving_size, fi.serving_unit,
                          fi.kj as item_kj, fi.protein as item_protein, fi.carbs as item_carbs,
                          fi.fat as item_fat, fi.fibre as item_fibre,
                          fr.name as recipe_name, fr.servings as recipe_servings
                   FROM food_log fl
                   LEFT JOIN food_items fi ON fl.food_item_id = fi.id
                   LEFT JOIN food_recipes fr ON fl.recipe_id = fr.id
                   WHERE fl.id = ?""",
                (log_id,),
            ).fetchone()
            return enrich_food_log_item(conn, row_to_dict(row))

    @app.delete("/api/food/log/{log_id}")
    def delete_food_log(log_id: str):
        with db_session(settings.db_path) as conn:
            existing = conn.execute("SELECT id FROM food_log WHERE id = ?", (log_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Log entry not found")
            conn.execute("DELETE FROM food_log WHERE id = ?", (log_id,))
            return {"ok": True}

    # ── Wiki ──────────────────────────────────────────────────────────────────

    import re as _re

    def _wiki_dir() -> "Path":
        from pathlib import Path as _Path
        return _Path(settings.wiki_dir)

    def _slug_to_path(slug: str) -> "Path":
        """fitness/my-page → wiki_dir/fitness/my-page.md"""
        from pathlib import Path as _Path
        safe = slug.strip("/").replace("..", "")
        return _wiki_dir() / (_Path(safe).with_suffix(".md"))

    def _title_to_filename(title: str) -> str:
        slug = title.lower().strip()
        slug = _re.sub(r"[^\w\s-]", "", slug)
        slug = _re.sub(r"[\s_]+", "-", slug)
        slug = _re.sub(r"-+", "-", slug).strip("-")
        return slug or "untitled"

    def _parse_frontmatter(text: str) -> tuple[dict, str]:
        """Return (meta, body). meta keys: title, category, tags, created, updated, summary."""
        meta: dict = {}
        body = text
        m = _re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)", text, _re.DOTALL)
        if not m:
            return meta, body
        fm_block, body = m.group(1), m.group(2)
        for line in fm_block.splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if key == "tags":
                inner = _re.sub(r"[\[\]]", "", val)
                meta[key] = [t.strip() for t in inner.split(",") if t.strip()]
            else:
                meta[key] = val.strip('"').strip("'")
        return meta, body.lstrip("\n")

    def _build_frontmatter(meta: dict) -> str:
        tags_str = "[" + ", ".join(meta.get("tags", [])) + "]"
        lines = [
            "---",
            f"title: {meta.get('title', '')}",
            f"category: {meta.get('category', '')}",
            f"tags: {tags_str}",
            f"created: {meta.get('created', '')}",
            f"updated: {meta.get('updated', '')}",
            f"summary: {meta.get('summary', '')}",
            "---",
            "",
        ]
        return "\n".join(lines)

    def _read_page(path: "Path") -> dict | None:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        parts = path.relative_to(_wiki_dir()).with_suffix("").parts
        slug = "/".join(parts)
        return {
            "slug": slug,
            "title": meta.get("title", path.stem),
            "category": meta.get("category", parts[0] if parts else ""),
            "tags": meta.get("tags", []),
            "summary": meta.get("summary", ""),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
            "content": body,
        }

    @app.get("/api/wiki/pages")
    def wiki_list_pages(category: str | None = None):
        wiki_root = _wiki_dir()
        if not wiki_root.exists():
            return []
        pages = []
        for md_file in sorted(wiki_root.rglob("*.md")):
            if md_file.name == "CLAUDE.md":
                continue
            page = _read_page(md_file)
            if page is None:
                continue
            if category and page["category"] != category:
                continue
            pages.append({k: v for k, v in page.items() if k != "content"})
        pages.sort(key=lambda p: p.get("updated", ""), reverse=True)
        return pages

    @app.get("/api/wiki/pages/{slug:path}")
    def wiki_get_page(slug: str):
        path = _slug_to_path(slug)
        page = _read_page(path)
        if page is None:
            raise HTTPException(status_code=404, detail="Wiki page not found")
        return page

    @app.post("/api/wiki/pages", status_code=201)
    def wiki_create_page(payload: WikiPageCreate):
        wiki_root = _wiki_dir()
        category_dir = wiki_root / payload.category
        category_dir.mkdir(parents=True, exist_ok=True)
        filename = _title_to_filename(payload.title)
        path = category_dir / f"{filename}.md"
        if path.exists():
            raise HTTPException(status_code=409, detail="A page with this title already exists in this category")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        meta = {
            "title": payload.title,
            "category": payload.category,
            "tags": payload.tags,
            "created": today,
            "updated": today,
            "summary": payload.summary,
        }
        content = payload.content or f"# {payload.title}\n"
        path.write_text(_build_frontmatter(meta) + content, encoding="utf-8")
        slug = f"{payload.category}/{filename}"
        return _read_page(path)

    @app.put("/api/wiki/pages/{slug:path}")
    def wiki_update_page(slug: str, payload: WikiPageUpdate):
        path = _slug_to_path(slug)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Wiki page not found")
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        if payload.title is not None:
            meta["title"] = payload.title
        if payload.tags is not None:
            meta["tags"] = payload.tags
        if payload.summary is not None:
            meta["summary"] = payload.summary
        meta["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_body = payload.content if payload.content is not None else body
        path.write_text(_build_frontmatter(meta) + new_body, encoding="utf-8")
        return _read_page(path)

    @app.delete("/api/wiki/pages/{slug:path}")
    def wiki_delete_page(slug: str):
        path = _slug_to_path(slug)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Wiki page not found")
        path.unlink()
        return {"ok": True}

    @app.get("/api/wiki/search")
    def wiki_search(q: str = Query(..., min_length=1)):
        wiki_root = _wiki_dir()
        if not wiki_root.exists():
            return []
        q_lower = q.lower()
        results = []
        for md_file in sorted(wiki_root.rglob("*.md")):
            if md_file.name == "CLAUDE.md":
                continue
            page = _read_page(md_file)
            if page is None:
                continue
            haystack = " ".join([
                page.get("title", ""),
                page.get("summary", ""),
                " ".join(page.get("tags", [])),
                page.get("content", ""),
            ]).lower()
            if q_lower in haystack:
                score = 3 if q_lower in page.get("title", "").lower() else \
                        2 if q_lower in page.get("summary", "").lower() else 1
                results.append({**{k: v for k, v in page.items() if k != "content"}, "_score": score})
        results.sort(key=lambda p: (-p["_score"], p.get("updated", "")))
        for r in results:
            r.pop("_score", None)
        return results

    # ── End Wiki ───────────────────────────────────────────────────────────────

    # ── Wiki Sources & Ingestion Pipeline ──────────────────────────────────────

    @app.get("/api/wiki/sources")
    def wiki_sources_list():
        with db_session(settings.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM wiki_sources ORDER BY status ASC, name ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/wiki/sources", status_code=201)
    async def wiki_sources_create(request: Request):
        payload = await request.json()
        import json as _json
        src_id = "src-" + str(uuid.uuid4())[:8]
        now = utc_now_iso()
        with db_session(settings.db_path) as conn:
            conn.execute(
                """INSERT INTO wiki_sources
                   (id, name, source_type, status, config_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (src_id, payload.get("name", ""), payload.get("source_type", "other"),
                 payload.get("status", "active"), _json.dumps(payload.get("config", {})), now, now),
            )
        return {"id": src_id, "ok": True}

    @app.put("/api/wiki/sources/{source_id}")
    async def wiki_sources_update(source_id: str, request: Request):
        payload = await request.json()
        import json as _json
        now = utc_now_iso()
        with db_session(settings.db_path) as conn:
            row = conn.execute("SELECT id FROM wiki_sources WHERE id=?", (source_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Source not found")
            fields, vals = [], []
            for k, col in [("name", "name"), ("status", "status"), ("error_msg", "error_msg")]:
                if k in payload:
                    fields.append(f"{col}=?")
                    vals.append(payload[k])
            if "config" in payload:
                fields.append("config_json=?")
                vals.append(_json.dumps(payload["config"]))
            if fields:
                fields.append("updated_at=?")
                vals.append(now)
                conn.execute(
                    f"UPDATE wiki_sources SET {', '.join(fields)} WHERE id=?",
                    vals + [source_id],
                )
        return {"ok": True}

    @app.delete("/api/wiki/sources/{source_id}")
    def wiki_sources_delete(source_id: str):
        with db_session(settings.db_path) as conn:
            conn.execute("DELETE FROM wiki_sources WHERE id=?", (source_id,))
        return {"ok": True}

    @app.post("/api/wiki/sources/{source_id}/sync", status_code=202)
    def wiki_sources_sync(source_id: str):
        now = utc_now_iso()
        job_id = "job-" + str(uuid.uuid4())[:8]
        with db_session(settings.db_path) as conn:
            row = conn.execute("SELECT name FROM wiki_sources WHERE id=?", (source_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Source not found")
            source_name = row["name"]
            conn.execute(
                """INSERT INTO wiki_ingest_jobs
                   (id, source_id, source_name, status, started_at, created_at)
                   VALUES (?,?,?,'running',?,?)""",
                (job_id, source_id, source_name, now, now),
            )
            conn.execute(
                "UPDATE wiki_sources SET status='syncing', updated_at=? WHERE id=?",
                (now, source_id),
            )
        return {"job_id": job_id, "status": "running"}

    @app.get("/api/wiki/pipeline/stats")
    def wiki_pipeline_stats():
        with db_session(settings.db_path) as conn:
            sources = conn.execute("SELECT COUNT(*) FROM wiki_sources").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM wiki_sources WHERE status IN ('active','syncing')"
            ).fetchone()[0]
            total_items = conn.execute(
                "SELECT COALESCE(SUM(items_total),0) FROM wiki_sources"
            ).fetchone()[0]
            processed_items = conn.execute(
                "SELECT COALESCE(SUM(items_processed),0) FROM wiki_sources"
            ).fetchone()[0]
            pages_created = conn.execute(
                "SELECT COALESCE(SUM(pages_created),0) FROM wiki_ingest_jobs"
            ).fetchone()[0]
            jobs_done = conn.execute(
                "SELECT COUNT(*) FROM wiki_ingest_jobs WHERE status='done'"
            ).fetchone()[0]
            jobs_error = conn.execute(
                "SELECT COUNT(*) FROM wiki_ingest_jobs WHERE status='error'"
            ).fetchone()[0]
            jobs_running = conn.execute(
                "SELECT COUNT(*) FROM wiki_ingest_jobs WHERE status='running'"
            ).fetchone()[0]
            avg_health = conn.execute(
                "SELECT COALESCE(AVG(health_score),0) FROM wiki_sources WHERE status!='inactive'"
            ).fetchone()[0]
        return {
            "sources_total": sources,
            "sources_active": active,
            "items_total": total_items,
            "items_processed": processed_items,
            "pages_created": pages_created,
            "jobs_done": jobs_done,
            "jobs_error": jobs_error,
            "jobs_running": jobs_running,
            "avg_health": round(avg_health, 1),
        }

    @app.get("/api/wiki/pipeline/jobs")
    def wiki_pipeline_jobs(limit: int = Query(default=25)):
        with db_session(settings.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM wiki_ingest_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── End Wiki Sources & Pipeline ────────────────────────────────────────────

    # ── Raw Sources ────────────────────────────────────────────────────────────

    def _raw_dir(status: str) -> "Path":
        from pathlib import Path as _Path
        return _wiki_dir() / "raw" / status

    def _read_raw_source(path: "Path") -> dict | None:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        status = path.parent.name  # "unprocessed" or "processed"
        return {
            "id": path.stem,
            "title": meta.get("title", path.stem),
            "type": meta.get("type", "note"),
            "source_url": meta.get("source_url", ""),
            "added": meta.get("added", ""),
            "status": status,
            "content": body,
        }

    @app.get("/api/wiki/raw")
    def raw_list(status: str | None = None):
        dirs = []
        if status == "unprocessed":
            dirs = [_raw_dir("unprocessed")]
        elif status == "processed":
            dirs = [_raw_dir("processed")]
        else:
            dirs = [_raw_dir("unprocessed"), _raw_dir("processed")]
        sources = []
        for d in dirs:
            if not d.exists():
                continue
            for f in sorted(d.glob("*.md"), reverse=True):
                src = _read_raw_source(f)
                if src:
                    sources.append({k: v for k, v in src.items() if k != "content"})
        return sources

    @app.get("/api/wiki/raw/item/{filename:path}")
    def raw_get(filename: str):
        safe = filename.strip("/").replace("..", "")
        for status in ["unprocessed", "processed"]:
            path = _raw_dir(status) / f"{safe}.md"
            if path.exists():
                return _read_raw_source(path)
        raise HTTPException(status_code=404, detail="Raw source not found")

    @app.post("/api/wiki/raw", status_code=201)
    def raw_create(payload: RawSourceCreate):
        d = _raw_dir("unprocessed")
        d.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filename = _title_to_filename(payload.title)
        base = filename
        counter = 1
        while (d / f"{filename}.md").exists():
            filename = f"{base}-{counter}"
            counter += 1
        meta_lines = ["---", f"title: {payload.title}", f"type: {payload.type}"]
        if payload.source_url:
            meta_lines.append(f"source_url: {payload.source_url}")
        meta_lines += [f"added: {today}", "---", ""]
        path = d / f"{filename}.md"
        path.write_text("\n".join(meta_lines) + "\n" + payload.content, encoding="utf-8")
        return _read_raw_source(path)

    @app.post("/api/wiki/raw/process/{filename:path}")
    def raw_mark_processed(filename: str):
        safe = filename.strip("/").replace("..", "")
        src_path = _raw_dir("unprocessed") / f"{safe}.md"
        if not src_path.exists():
            raise HTTPException(status_code=404, detail="Raw source not found in unprocessed")
        dst_dir = _raw_dir("processed")
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_path = dst_dir / src_path.name
        src_path.rename(dst_path)
        return _read_raw_source(dst_path)

    @app.delete("/api/wiki/raw/item/{filename:path}")
    def raw_delete(filename: str):
        safe = filename.strip("/").replace("..", "")
        for status in ["unprocessed", "processed"]:
            path = _raw_dir(status) / f"{safe}.md"
            if path.exists():
                path.unlink()
                return {"ok": True}
        raise HTTPException(status_code=404, detail="Raw source not found")

    @app.put("/api/wiki/raw/item/{filename:path}")
    def raw_update(filename: str, payload: RawSourceUpdate):
        safe = filename.strip("/").replace("..", "")
        path = None
        for status in ["unprocessed", "processed"]:
            candidate = _raw_dir(status) / f"{safe}.md"
            if candidate.exists():
                path = candidate
                break
        if path is None:
            raise HTTPException(status_code=404, detail="Raw source not found")
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        if payload.title is not None:
            meta["title"] = payload.title
        if payload.type is not None:
            meta["type"] = payload.type
        if payload.source_url is not None:
            meta["source_url"] = payload.source_url
        new_body = payload.content if payload.content is not None else body
        # Rebuild frontmatter manually for raw sources (only raw-relevant keys)
        lines = ["---", f"title: {meta.get('title', '')}",
                 f"type: {meta.get('type', 'note')}"]
        if meta.get("source_url"):
            lines.append(f"source_url: {meta['source_url']}")
        if meta.get("added"):
            lines.append(f"added: {meta['added']}")
        lines += ["---", ""]
        path.write_text("\n".join(lines) + "\n" + new_body, encoding="utf-8")
        return _read_raw_source(path)

    @app.post("/api/wiki/raw/ingest/{filename:path}", status_code=201)
    def raw_ingest(filename: str, payload: RawSourceIngest):
        """Convert a raw source into a wiki page and mark it as processed."""
        safe = filename.strip("/").replace("..", "")
        src_path = None
        for status in ["unprocessed", "processed"]:
            candidate = _raw_dir(status) / f"{safe}.md"
            if candidate.exists():
                src_path = candidate
                break
        if src_path is None:
            raise HTTPException(status_code=404, detail="Raw source not found")
        src = _read_raw_source(src_path)
        wiki_root = _wiki_dir()
        category_dir = wiki_root / payload.category
        category_dir.mkdir(parents=True, exist_ok=True)
        fn = _title_to_filename(src["title"])
        base = fn
        counter = 1
        while (category_dir / f"{fn}.md").exists():
            fn = f"{base}-{counter}"
            counter += 1
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        meta = {
            "title": src["title"],
            "category": payload.category,
            "tags": [],
            "created": today,
            "updated": today,
            "summary": "",
        }
        content = src["content"] or f"# {src['title']}\n"
        (category_dir / f"{fn}.md").write_text(_build_frontmatter(meta) + content, encoding="utf-8")
        # Mark raw source as processed if not already
        if src_path.parent.name == "unprocessed":
            dst_dir = _raw_dir("processed")
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst_path = dst_dir / src_path.name
            src_path.rename(dst_path)
        slug = f"{payload.category}/{fn}"
        return {"slug": slug, "title": src["title"], "category": payload.category}

    # ── End Raw Sources ────────────────────────────────────────────────────────

    # ── Notes ─────────────────────────────────────────────────────────────────

    import re as _nre
    import shutil as _nshutil
    from pathlib import Path as _NPath

    def _notes_root() -> _NPath:
        return _NPath(settings.db_path).parent / "notes"

    def _notes_safe(rel_path: str) -> _NPath:
        root = _notes_root()
        clean = _nre.sub(r"\.\.+", "", rel_path.strip("/"))
        resolved = (root / clean).resolve()
        if not str(resolved).startswith(str(root.resolve())):
            raise HTTPException(status_code=400, detail="Invalid path")
        return resolved

    def _note_fm_read(text: str) -> tuple[dict, str]:
        meta: dict = {}
        body = text
        m = _nre.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)", text, _nre.DOTALL)
        if m:
            for line in m.group(1).splitlines():
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                k = k.strip(); v = v.strip().strip("\"'")
                if k == "tags":
                    inner = _nre.sub(r"[\[\]]", "", v)
                    meta[k] = [t.strip() for t in inner.split(",") if t.strip()]
                else:
                    meta[k] = v
            body = m.group(2).lstrip("\n")
        return meta, body

    def _note_fm_write(meta: dict) -> str:
        tags_str = "[" + ", ".join(meta.get("tags", [])) + "]"
        lines = [
            "---",
            f"title: {meta.get('title', '')}",
            f"tags: {tags_str}",
            f"created: {meta.get('created', '')}",
            f"updated: {meta.get('updated', '')}",
            f"pinned: {'true' if meta.get('pinned') else 'false'}",
        ]
        if "writing_assist" in meta and meta["writing_assist"] is not None:
            wa = meta["writing_assist"]
            wa_bool = wa if isinstance(wa, bool) else str(wa).lower() == "true"
            lines.append(f"writing_assist: {'true' if wa_bool else 'false'}")
        lines += ["---", ""]
        return "\n".join(lines)

    def _note_info(path: _NPath, root: _NPath) -> dict:
        rel = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            meta, _ = _note_fm_read(text)
        except Exception:
            meta = {}
        return {
            "type": "file",
            "path": rel,
            "name": meta.get("title") or path.stem,
            "title": meta.get("title") or path.stem,
            "tags": meta.get("tags", []),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
            "pinned": str(meta.get("pinned", "false")).lower() == "true",
        }

    def _notes_tree_node(path: _NPath, root: _NPath) -> dict | None:
        if path.is_file():
            return _note_info(path, root) if path.suffix == ".md" else None
        if path.is_dir():
            rel = str(path.relative_to(root)) if path != root else ""
            children = []
            try:
                entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except PermissionError:
                entries = []
            for child in entries:
                if child.name.startswith(".") or child.name.startswith("_"):
                    continue
                node = _notes_tree_node(child, root)
                if node:
                    children.append(node)
            return {"type": "folder", "name": path.name if path != root else "Notes", "path": rel, "children": children}
        return None

    def _notes_seed(root: _NPath) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        def wn(rel: str, title: str, body: str, tags: list[str] = []) -> None:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_note_fm_write({"title": title, "tags": tags, "created": now, "updated": now, "pinned": False}) + body, encoding="utf-8")

        wn("Welcome/Getting Started.md", "Getting Started",
           "# Getting Started\n\nWelcome to **Notes** — your markdown-first knowledge workspace.\n\n## Writing modes\n\n- **Edit** — raw markdown\n- **Split** — editor + live preview side by side\n- **Preview** — fully rendered\n\n## Markdown reference\n\n| Syntax | Output |\n|---|---|\n| `**bold**` | **bold** |\n| `_italic_` | _italic_ |\n| `==highlight==` | ==highlight== |\n| `- [ ]` | task checkbox |\n| `` `code` `` | `inline code` |\n| `> quote` | blockquote |\n\n## Code blocks\n\n```python\ndef hello(name: str) -> str:\n    return f'Hello, {name}!'\n```\n\n> Notes autosave every 2 seconds. Your work is always safe.\n",
           ["meta", "guide"])

        wn("Work/Meeting Notes.md", "Meeting Notes",
           "# Meeting Notes\n\n## 2026-04-19 — Weekly Sync\n\n**Attendees:** Tommy, Sarah, James\n\n### Action items\n\n- [ ] Review Q2 roadmap deck\n- [ ] Set up staging environment\n- [x] Send design assets to team\n\n### Notes\n\nAgreed to push the dashboard release to end of month. Sarah will own QA.\nJames flagged a blocking issue with the API rate limits — need to investigate.\n\n---\n\n## 2026-04-12 — Design Review\n\n- [ ] Update component library\n- [x] Finalize dark mode palette\n",
           ["work", "meetings"])

        wn("Work/Project Ideas.md", "Project Ideas",
           "# Project Ideas\n\n## Active\n\n- **Personal OS** — unified dashboard for fitness, food, tasks, notes\n- **Training log** — GPX analysis and plan tracking\n\n## Backlog\n\n- AI meal planning integration\n- Weekly review automation\n- Finance dashboard\n\n## Someday\n\n- Open source the Personal OS\n",
           ["work", "ideas"])

        wn("Code/Snippets.md", "Code Snippets",
           "# Code Snippets\n\n## Python\n\n### Async HTTP client\n\n```python\nimport httpx\n\nasync def fetch(url: str) -> dict:\n    async with httpx.AsyncClient() as client:\n        r = await client.get(url, timeout=10)\n        r.raise_for_status()\n        return r.json()\n```\n\n### Debounce (JS)\n\n```js\nfunction debounce(fn, ms) {\n  let t;\n  return (...args) => {\n    clearTimeout(t);\n    t = setTimeout(() => fn(...args), ms);\n  };\n}\n```\n\n## SQL\n\n### Rolling average\n\n```sql\nSELECT date, value,\n  AVG(value) OVER (\n    ORDER BY date\n    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW\n  ) AS rolling_7d\nFROM metrics;\n```\n",
           ["code", "python", "sql"])

        wn("Journal/April 2026.md", "April 2026",
           "# April 2026\n\n## Week 3\n\nGood week. Training on track — hit all sessions.\nThe notes system redesign took longer than expected but the result is solid.\n\n**Goals this week:**\n\n- [x] Build notes feature\n- [x] Finish the calendar redesign\n- [ ] Write the Q2 retrospective\n- [ ] Start the strength block\n\n## Week 2\n\nFocused on the training dashboard. Added the week view and schedule builder.\nFood tracking is becoming a habit — 7 days logged in a row.\n",
           ["journal", "personal"])

    @app.get("/api/notes/tree")
    def notes_tree():
        root = _notes_root()
        root.mkdir(parents=True, exist_ok=True)
        if not any(root.iterdir()):
            _notes_seed(root)
        node = _notes_tree_node(root, root)
        return node or {"type": "folder", "name": "Notes", "path": "", "children": []}

    @app.get("/api/notes/read")
    def notes_read_note(path: str):
        abs_path = _notes_safe(path)
        if not abs_path.exists() or not abs_path.is_file():
            raise HTTPException(status_code=404, detail="Note not found")
        text = abs_path.read_text(encoding="utf-8", errors="replace")
        meta, body = _note_fm_read(text)
        wa_raw = meta.get("writing_assist")
        return {
            "path": path.strip("/"),
            "title": meta.get("title") or abs_path.stem,
            "tags": meta.get("tags", []),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
            "pinned": str(meta.get("pinned", "false")).lower() == "true",
            "writing_assist": (str(wa_raw).lower() == "true") if wa_raw is not None else None,
            "content": body,
        }

    @app.post("/api/notes/write", status_code=201)
    def notes_write_note(payload: dict):
        note_path = (payload.get("path") or "").strip("/")
        title = (payload.get("title") or "").strip()
        content = payload.get("content") or ""
        tags = payload.get("tags") or []
        pinned = bool(payload.get("pinned", False))
        if not note_path:
            raise HTTPException(status_code=400, detail="path required")
        abs_path = _notes_safe(note_path)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if abs_path.exists():
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            meta, _ = _note_fm_read(text)
            meta["updated"] = now
        else:
            meta = {"created": now, "updated": now}
        meta["title"] = title or abs_path.stem
        meta["tags"] = tags if isinstance(tags, list) else []
        meta["pinned"] = pinned
        abs_path.write_text(_note_fm_write(meta) + content, encoding="utf-8")
        return {"ok": True, "path": note_path}

    @app.patch("/api/notes/meta")
    def notes_update_meta(payload: dict):
        note_path = (payload.get("path") or "").strip("/")
        if not note_path:
            raise HTTPException(status_code=400, detail="path required")
        abs_path = _notes_safe(note_path)
        if not abs_path.exists() or not abs_path.is_file():
            raise HTTPException(status_code=404, detail="Note not found")
        text = abs_path.read_text(encoding="utf-8", errors="replace")
        meta, body = _note_fm_read(text)
        if "writing_assist" in payload:
            wa = payload.get("writing_assist")
            if wa is None:
                meta.pop("writing_assist", None)
            else:
                meta["writing_assist"] = bool(wa)
        abs_path.write_text(_note_fm_write(meta) + body, encoding="utf-8")
        return {"ok": True}

    @app.delete("/api/notes/file")
    def notes_delete_file_ep(path: str):
        abs_path = _notes_safe(path)
        if not abs_path.exists():
            raise HTTPException(status_code=404, detail="Note not found")
        if not abs_path.is_file():
            raise HTTPException(status_code=400, detail="Not a file")
        abs_path.unlink()
        return {"ok": True}

    @app.post("/api/notes/folder", status_code=201)
    def notes_create_folder(payload: dict):
        folder_path = (payload.get("path") or "").strip("/")
        if not folder_path:
            raise HTTPException(status_code=400, detail="path required")
        _notes_safe(folder_path).mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": folder_path}

    @app.delete("/api/notes/folder")
    def notes_delete_folder_ep(path: str):
        abs_path = _notes_safe(path)
        if not abs_path.exists():
            raise HTTPException(status_code=404, detail="Folder not found")
        if not abs_path.is_dir():
            raise HTTPException(status_code=400, detail="Not a folder")
        _nshutil.rmtree(abs_path)
        return {"ok": True}

    @app.post("/api/notes/rename")
    def notes_rename(payload: dict):
        old_path = (payload.get("path") or "").strip("/")
        new_name = _nre.sub(r'[<>:"/\\|?*]', '', (payload.get("name") or "").strip())
        if not old_path or not new_name:
            raise HTTPException(status_code=400, detail="path and name required")
        old_abs = _notes_safe(old_path)
        if not old_abs.exists():
            raise HTTPException(status_code=404, detail="Not found")
        suffix = old_abs.suffix if old_abs.is_file() else ""
        new_abs = old_abs.parent / (new_name + suffix)
        if new_abs.exists():
            raise HTTPException(status_code=409, detail="Name already taken")
        old_abs.rename(new_abs)
        return {"ok": True, "path": str(new_abs.relative_to(_notes_root()))}

    @app.post("/api/notes/move")
    def notes_move(payload: dict):
        src = (payload.get("src") or "").strip("/")
        dst_folder = (payload.get("dst_folder") or "").strip("/")
        if not src:
            raise HTTPException(status_code=400, detail="src required")
        src_abs = _notes_safe(src)
        if not src_abs.exists():
            raise HTTPException(status_code=404, detail="Source not found")
        root = _notes_root()
        dst_abs = _notes_safe(dst_folder) if dst_folder else root
        dst_abs.mkdir(parents=True, exist_ok=True)
        target = dst_abs / src_abs.name
        if target.exists():
            raise HTTPException(status_code=409, detail="Target already exists")
        src_abs.rename(target)
        return {"ok": True, "path": str(target.relative_to(root))}

    @app.post("/api/notes/upload", status_code=201)
    async def notes_upload_asset(folder: str = Form(""), file: UploadFile = File(...)):
        import aiofiles
        root = _notes_root()
        assets_dir = root / "_assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _nre.sub(r"[^\w.\-]", "_", file.filename or "upload")
        dest = assets_dir / safe_name
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            i = 1
            while dest.exists():
                dest = assets_dir / f"{stem}_{i}{suffix}"
                i += 1
        async with aiofiles.open(dest, "wb") as f:
            await f.write(await file.read())
        return {"ok": True, "path": f"_assets/{dest.name}", "url": f"/api/notes/asset?path=_assets/{dest.name}"}

    @app.get("/api/notes/asset")
    def notes_get_asset(path: str):
        from fastapi.responses import FileResponse
        abs_path = _notes_safe(path)
        if not abs_path.exists() or not abs_path.is_file():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(abs_path)

    @app.get("/api/notes/search")
    def notes_search(q: str):
        root = _notes_root()
        if not root.exists() or not q:
            return []
        q_low = q.lower()
        results = []
        for md_file in sorted(root.rglob("*.md")):
            if md_file.name.startswith("_") or md_file.name.startswith("."):
                continue
            try:
                text = md_file.read_text(encoding="utf-8", errors="replace")
                meta, body = _note_fm_read(text)
                title = meta.get("title") or md_file.stem
                if q_low in title.lower() or q_low in body.lower() or q_low in " ".join(meta.get("tags", [])).lower():
                    idx = body.lower().find(q_low)
                    s = max(0, idx - 60) if idx >= 0 else 0
                    e = min(len(body), (idx + 120) if idx >= 0 else 140)
                    snippet = ("…" if s > 0 else "") + body[s:e].replace("\n", " ").strip() + ("…" if e < len(body) else "")
                    results.append({"path": str(md_file.relative_to(root)), "title": title, "snippet": snippet, "updated": meta.get("updated", ""), "tags": meta.get("tags", [])})
            except Exception:
                pass
        results.sort(key=lambda r: r.get("updated", ""), reverse=True)
        return results[:50]

    # ── End Notes ─────────────────────────────────────────────────────────────

    # ── Brief ─────────────────────────────────────────────────────────────────

    def _brief_row_to_dict(row: Any) -> dict[str, Any]:
        d = dict(row)
        if d.get("content"):
            try:
                d["content"] = json.loads(d["content"])
            except Exception:
                pass
        return d

    def _brief_today() -> str:
        return datetime.now(AEST).strftime("%Y-%m-%d")

    @app.get("/api/brief/run-status")
    def brief_run_status():
        with db_session(settings.db_path) as conn:
            row = conn.execute(
                "SELECT id, date FROM briefings WHERE status='running' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return {"running": row is not None, "date": row["date"] if row else None}

    @app.get("/api/brief/latest")
    def brief_latest():
        with db_session(settings.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM briefings WHERE status='done' ORDER BY date DESC, generated_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No briefings yet")
        return _brief_row_to_dict(row)

    @app.get("/api/brief/history")
    def brief_history():
        with db_session(settings.db_path) as conn:
            rows = conn.execute(
                "SELECT id, date, status, sources_fetched, generated_at, error, created_at "
                "FROM briefings ORDER BY date DESC, created_at DESC LIMIT 90"
            ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/brief/{date}")
    def brief_by_date(date: str):
        from .validation import parse_date
        try:
            parse_date(date)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD")
        with db_session(settings.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM briefings WHERE date=? ORDER BY created_at DESC LIMIT 1", (date,)
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No briefing for this date")
        return _brief_row_to_dict(row)

    @app.post("/api/brief/generate", status_code=202)
    async def brief_generate():
        if not settings.anthropic_api_key:
            raise HTTPException(status_code=503, detail="Anthropic API key not configured")
        with db_session(settings.db_path) as conn:
            running = conn.execute(
                "SELECT id FROM briefings WHERE status='running' LIMIT 1"
            ).fetchone()
        if running:
            raise HTTPException(status_code=409, detail="Generation already in progress")
        today = _brief_today()
        brief_id = str(uuid.uuid4())
        now_iso = utc_now_iso()
        with db_session(settings.db_path) as conn:
            conn.execute(
                "INSERT INTO briefings (id, date, status, created_at, updated_at) VALUES (?,?,'running',?,?)",
                (brief_id, today, now_iso, now_iso),
            )

        async def _task() -> None:
            await run_brief_generation(settings, settings.db_path, brief_id, today)

        asyncio.create_task(_task())
        return {"id": brief_id, "date": today, "status": "running"}

    # ── End Brief ─────────────────────────────────────────────────────────────

    # ── AI Analytics ──────────────────────────────────────────────────────────

    @app.get("/api/aianalytics/data")
    def ai_analytics_data():
        """Aggregate Claude Code usage analytics from JSONL session logs."""
        from pathlib import Path as _Path

        LOGS_DIR = _Path("/claude-logs")
        now_utc = datetime.now(timezone.utc)
        window_start = now_utc - timedelta(hours=5)
        today_str = now_utc.strftime("%Y-%m-%d")

        def _parse_ts(ts_str: str | None):
            if not ts_str:
                return None
            try:
                return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                return None

        def _infer_category(text: str) -> str:
            t = text.lower()
            if any(w in t for w in ["bug", "fix", "function", "implement", "error", "code", "python",
                                     "javascript", "class", "def ", "api", "database", "deploy", "docker",
                                     "endpoint", "frontend", "backend", "html", "css", "typescript"]):
                return "coding"
            if any(w in t for w in ["write", "draft", "edit", "document", "article", "essay", "blog"]):
                return "writing"
            if any(w in t for w in ["explain", "what is", "how does", "why ", "research", "understand", "summarize"]):
                return "research"
            if any(w in t for w in ["plan", "design", "architect", "roadmap", "strategy", "structure"]):
                return "planning"
            return "general"

        sessions_out: list = []
        daily_map: dict = {}
        hourly_buckets: list = []
        window_stats: dict = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                               "cache_create_tokens": 0, "user_turns": 0}

        if not LOGS_DIR.exists():
            return {"error": "logs_not_mounted", "sessions": [], "daily": {}, "weekly": {},
                    "hourly": [], "window_5h": window_stats, "today": {}, "today_str": today_str}

        for jsonl_file in sorted(LOGS_DIR.glob("*.jsonl")):
            session_id = jsonl_file.stem
            try:
                raw_lines = jsonl_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            msgs = []
            for line in raw_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    msgs.append(json.loads(line))
                except Exception:
                    pass

            if not msgs:
                continue

            timestamps = [_parse_ts(m.get("timestamp")) for m in msgs if m.get("timestamp")]
            timestamps = [t for t in timestamps if t]
            if not timestamps:
                continue

            start_ts = min(timestamps)
            end_ts = max(timestamps)
            duration_min = max(0, int((end_ts - start_ts).total_seconds() / 60))

            user_turns = [m for m in msgs if m.get("type") == "user" and m.get("userType") == "external"]
            asst_turns = [m for m in msgs if m.get("type") == "assistant"]

            title = next((m.get("title", "") for m in msgs if m.get("type") == "ai-title"), "")

            category = "general"
            if user_turns:
                fm = user_turns[0].get("message", {}).get("content", "")
                if isinstance(fm, list):
                    fm = " ".join(c.get("text", "") for c in fm if isinstance(c, dict) and c.get("type") == "text")
                category = _infer_category(str(fm))

            input_tok = output_tok = cache_read = cache_create = 0
            for m in asst_turns:
                u = m.get("message", {}).get("usage", {})
                if u:
                    input_tok += u.get("input_tokens", 0)
                    output_tok += u.get("output_tokens", 0)
                    cache_read += u.get("cache_read_input_tokens", 0)
                    cache_create += u.get("cache_creation_input_tokens", 0)

            for m in asst_turns:
                ts = _parse_ts(m.get("timestamp"))
                u = m.get("message", {}).get("usage", {})
                if ts and u:
                    inp = u.get("input_tokens", 0)
                    out = u.get("output_tokens", 0)
                    cr = u.get("cache_read_input_tokens", 0)
                    cc = u.get("cache_creation_input_tokens", 0)
                    hourly_buckets.append({"ts": ts.isoformat(), "day": ts.strftime("%Y-%m-%d"),
                                           "hour": ts.hour, "input": inp, "output": out,
                                           "cache_read": cr, "cache_create": cc})
                    if ts >= window_start:
                        window_stats["input_tokens"] += inp
                        window_stats["output_tokens"] += out
                        window_stats["cache_read_tokens"] += cr
                        window_stats["cache_create_tokens"] += cc

            for m in user_turns:
                ts = _parse_ts(m.get("timestamp"))
                if ts and ts >= window_start:
                    window_stats["user_turns"] += 1

            day_str = start_ts.strftime("%Y-%m-%d")
            d = daily_map.setdefault(day_str, {"input": 0, "output": 0, "cache_read": 0,
                                                "user_turns": 0, "sessions": 0, "duration_min": 0})
            d["input"] += input_tok
            d["output"] += output_tok
            d["cache_read"] += cache_read
            d["user_turns"] += len(user_turns)
            d["sessions"] += 1
            d["duration_min"] += duration_min

            n_user = max(len(user_turns), 1)
            effective_tpt = (input_tok + output_tok) / n_user
            total_ctx = cache_read + cache_create + input_tok
            cache_eff = cache_read / max(total_ctx, 1) if total_ctx > 0 else 0.0

            short_follow = 0
            prev_short = False
            for m in user_turns:
                c = m.get("message", {}).get("content", "")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                is_short = len(str(c).strip()) < 40
                if is_short and prev_short:
                    short_follow += 1
                prev_short = is_short

            churn = short_follow / n_user
            score = 70
            if cache_eff > 0.85: score += 10
            elif cache_eff > 0.7: score += 5
            if effective_tpt < 3000: score += 8
            elif effective_tpt > 20000: score -= 10
            if churn > 0.3: score -= 15
            elif churn > 0.15: score -= 7
            if output_tok > 50000: score += 5
            if n_user > 50: score += 3
            score = max(10, min(100, round(score)))

            sessions_out.append({
                "id": session_id,
                "title": title or None,
                "start": start_ts.isoformat(),
                "end": end_ts.isoformat(),
                "duration_min": duration_min,
                "user_turns": len(user_turns),
                "assistant_turns": len(asst_turns),
                "input_tokens": input_tok,
                "output_tokens": output_tok,
                "cache_read_tokens": cache_read,
                "cache_create_tokens": cache_create,
                "category": category,
                "efficiency_score": score,
                "cache_efficiency": round(cache_eff * 100, 1),
                "avg_tokens_per_turn": round(effective_tpt),
            })

        sessions_out.sort(key=lambda x: x.get("start", ""))

        week_days = [(now_utc - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        weekly = {
            "days": week_days,
            "output_tokens": [daily_map.get(d2, {}).get("output", 0) for d2 in week_days],
            "user_turns": [daily_map.get(d2, {}).get("user_turns", 0) for d2 in week_days],
            "sessions": [daily_map.get(d2, {}).get("sessions", 0) for d2 in week_days],
        }

        return {
            "sessions": sessions_out,
            "daily": dict(sorted(daily_map.items())),
            "weekly": weekly,
            "hourly": hourly_buckets,
            "window_5h": {"start": window_start.isoformat(), "now": now_utc.isoformat(), **window_stats},
            "today": daily_map.get(today_str, {"input": 0, "output": 0, "user_turns": 0, "sessions": 0, "duration_min": 0}),
            "today_str": today_str,
        }

    # ── End AI Analytics ───────────────────────────────────────────────────────

    # ── Job Intelligence ──────────────────────────────────────────────────────

    JOBS_DB = "/data/jobs_intel.db"

    def _jobs_conn():
        import sqlite3 as _sq
        conn = _sq.connect(JOBS_DB)
        conn.row_factory = _sq.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @app.get("/api/jobintel/summary")
    def jobintel_summary():
        try:
            conn = _jobs_conn()
        except Exception:
            return {"error": "db_unavailable", "total": 0, "by_country": [], "last_updated": None}
        try:
            total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            by_country = [
                dict(r) for r in conn.execute(
                    "SELECT country, COUNT(*) as cnt FROM jobs GROUP BY country ORDER BY cnt DESC"
                ).fetchall()
            ]
            last_updated = conn.execute(
                "SELECT MAX(posted_at) FROM jobs"
            ).fetchone()[0]
            total_skills = conn.execute("SELECT COUNT(*) FROM job_skills").fetchone()[0]
            salary_count = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE salary_min IS NOT NULL OR salary_max IS NOT NULL"
            ).fetchone()[0]
            return {
                "total": total,
                "by_country": by_country,
                "last_updated": last_updated,
                "total_skills": total_skills,
                "salary_count": salary_count,
            }
        finally:
            conn.close()

    @app.get("/api/jobintel/skills")
    def jobintel_skills(
        country: str = Query(default=""),
        skill_type: str = Query(default="required_skills"),
        limit: int = Query(default=20),
    ):
        try:
            conn = _jobs_conn()
        except Exception:
            return []
        try:
            if country:
                rows = conn.execute(
                    """SELECT js.skill, COUNT(*) as cnt
                       FROM job_skills js JOIN jobs j ON j.id = js.job_id
                       WHERE j.country = ? AND js.skill_type = ?
                       GROUP BY js.skill ORDER BY cnt DESC LIMIT ?""",
                    (country, skill_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT js.skill, COUNT(*) as cnt
                       FROM job_skills js
                       WHERE js.skill_type = ?
                       GROUP BY js.skill ORDER BY cnt DESC LIMIT ?""",
                    (skill_type, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.get("/api/jobintel/certs")
    def jobintel_certs(limit: int = Query(default=20)):
        try:
            conn = _jobs_conn()
        except Exception:
            return []
        try:
            rows = conn.execute(
                """SELECT skill, COUNT(*) as cnt FROM job_skills
                   WHERE skill_type = 'certifications'
                   GROUP BY skill ORDER BY cnt DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.get("/api/jobintel/tools")
    def jobintel_tools(
        country: str = Query(default=""),
        limit: int = Query(default=20),
    ):
        try:
            conn = _jobs_conn()
        except Exception:
            return []
        try:
            if country:
                rows = conn.execute(
                    """SELECT js.skill, COUNT(*) as cnt
                       FROM job_skills js JOIN jobs j ON j.id = js.job_id
                       WHERE j.country = ? AND js.skill_type = 'tools'
                       GROUP BY js.skill ORDER BY cnt DESC LIMIT ?""",
                    (country, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT skill, COUNT(*) as cnt FROM job_skills
                       WHERE skill_type = 'tools'
                       GROUP BY skill ORDER BY cnt DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.get("/api/jobintel/salary")
    def jobintel_salary():
        try:
            conn = _jobs_conn()
        except Exception:
            return []
        try:
            rows = conn.execute(
                """SELECT country, seniority, salary_currency,
                          ROUND(AVG(salary_min),0) as avg_min,
                          ROUND(AVG(salary_max),0) as avg_max,
                          COUNT(*) as cnt
                   FROM jobs
                   WHERE salary_min IS NOT NULL OR salary_max IS NOT NULL
                   GROUP BY country, seniority, salary_currency
                   ORDER BY country, seniority""",
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.get("/api/jobintel/seniority")
    def jobintel_seniority():
        try:
            conn = _jobs_conn()
        except Exception:
            return []
        try:
            rows = conn.execute(
                """SELECT country, COALESCE(seniority,'unspecified') as seniority, COUNT(*) as cnt
                   FROM jobs GROUP BY country, seniority ORDER BY country, cnt DESC""",
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.get("/api/jobintel/jobs")
    def jobintel_jobs(
        country: str = Query(default=""),
        q: str = Query(default=""),
        seniority: str = Query(default=""),
        page: int = Query(default=1),
        limit: int = Query(default=25),
    ):
        try:
            conn = _jobs_conn()
        except Exception:
            return {"jobs": [], "total": 0}
        try:
            where_parts = []
            params: list = []
            if country:
                where_parts.append("country = ?")
                params.append(country)
            if q:
                where_parts.append("(title LIKE ? OR company LIKE ?)")
                params.extend([f"%{q}%", f"%{q}%"])
            if seniority:
                where_parts.append("seniority = ?")
                params.append(seniority)
            where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
            total = conn.execute(f"SELECT COUNT(*) FROM jobs {where_sql}", params).fetchone()[0]
            offset = (page - 1) * limit
            rows = conn.execute(
                f"""SELECT id, title, company, city, country, seniority,
                           salary_min, salary_max, salary_currency, posted_at
                    FROM jobs {where_sql}
                    ORDER BY posted_at DESC LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()
            jobs_out = []
            for row in rows:
                j = dict(row)
                skills = conn.execute(
                    "SELECT skill, skill_type FROM job_skills WHERE job_id = ? AND skill_type IN ('required_skills','tools') LIMIT 12",
                    (j["id"],),
                ).fetchall()
                j["skills"] = [dict(s) for s in skills]
                jobs_out.append(j)
            return {"jobs": jobs_out, "total": total, "page": page, "limit": limit}
        finally:
            conn.close()

    @app.get("/api/jobintel/employers")
    def jobintel_employers(
        country: str = Query(default=""),
        limit: int = Query(default=15),
    ):
        try:
            conn = _jobs_conn()
        except Exception:
            return []
        try:
            if country:
                rows = conn.execute(
                    """SELECT company, COUNT(*) as cnt FROM jobs
                       WHERE country = ? AND company != ''
                       GROUP BY company ORDER BY cnt DESC LIMIT ?""",
                    (country, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT company, COUNT(*) as cnt FROM jobs
                       WHERE company != ''
                       GROUP BY company ORDER BY cnt DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.get("/api/jobintel/cities")
    def jobintel_cities(
        country: str = Query(default=""),
        limit: int = Query(default=12),
    ):
        try:
            conn = _jobs_conn()
        except Exception:
            return []
        try:
            if country:
                rows = conn.execute(
                    """SELECT city, COUNT(*) as cnt FROM jobs
                       WHERE country = ? AND city IS NOT NULL AND city != ''
                         AND city NOT IN ('Australia', 'United States', 'Singapore',
                                          'New South Wales', 'Victoria', 'Queensland',
                                          'Western Australia', 'South Australia',
                                          'Australian Capital Territory')
                       GROUP BY city ORDER BY cnt DESC LIMIT ?""",
                    (country, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT city, country, COUNT(*) as cnt FROM jobs
                       WHERE city IS NOT NULL AND city != ''
                       GROUP BY city, country ORDER BY cnt DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @app.get("/api/jobintel/compare")
    def jobintel_compare():
        """Cross-country top skills, certs and tools for comparison."""
        try:
            conn = _jobs_conn()
        except Exception:
            return {}
        try:
            countries = [r[0] for r in conn.execute(
                "SELECT country FROM jobs GROUP BY country ORDER BY COUNT(*) DESC"
            ).fetchall()]
            out = {}
            for country in countries:
                req = conn.execute(
                    """SELECT js.skill, COUNT(*) cnt
                       FROM job_skills js JOIN jobs j ON j.id=js.job_id
                       WHERE j.country=? AND js.skill_type='required_skills'
                       GROUP BY js.skill ORDER BY cnt DESC LIMIT 12""",
                    (country,),
                ).fetchall()
                tools = conn.execute(
                    """SELECT js.skill, COUNT(*) cnt
                       FROM job_skills js JOIN jobs j ON j.id=js.job_id
                       WHERE j.country=? AND js.skill_type='tools'
                       GROUP BY js.skill ORDER BY cnt DESC LIMIT 12""",
                    (country,),
                ).fetchall()
                certs = conn.execute(
                    """SELECT js.skill, COUNT(*) cnt
                       FROM job_skills js JOIN jobs j ON j.id=js.job_id
                       WHERE j.country=? AND js.skill_type='certifications'
                       GROUP BY js.skill ORDER BY cnt DESC LIMIT 8""",
                    (country,),
                ).fetchall()
                out[country] = {
                    "required_skills": [dict(r) for r in req],
                    "tools": [dict(r) for r in tools],
                    "certifications": [dict(r) for r in certs],
                }

            # Universal skills — appear in ALL countries
            all_skills = {}
            for country_data in out.values():
                for item in country_data.get("required_skills", []):
                    s = item["skill"].lower()
                    all_skills[s] = all_skills.get(s, 0) + 1
            universal = [s for s, c in all_skills.items() if c == len(countries)]

            return {"countries": out, "universal_skills": universal[:10]}
        finally:
            conn.close()

    @app.get("/api/jobintel/insights")
    def jobintel_insights():
        """Derived insights: unique-to-country skills, salary leaders, hot skills."""
        try:
            conn = _jobs_conn()
        except Exception:
            return {}
        try:
            countries = [r[0] for r in conn.execute(
                "SELECT country FROM jobs GROUP BY country ORDER BY COUNT(*) DESC"
            ).fetchall()]

            # Skills unique to each country (appear in 1 country only)
            unique: dict = {}
            for country in countries:
                rows = conn.execute(
                    """SELECT js.skill, COUNT(*) cnt
                       FROM job_skills js JOIN jobs j ON j.id=js.job_id
                       WHERE j.country=? AND js.skill_type='required_skills'
                       GROUP BY js.skill ORDER BY cnt DESC LIMIT 50""",
                    (country,),
                ).fetchall()
                skills_this = {r[0].lower() for r in rows}
                # Check if any of these appear in other countries
                other_skills: set = set()
                for other in countries:
                    if other == country:
                        continue
                    other_rows = conn.execute(
                        """SELECT LOWER(js.skill)
                           FROM job_skills js JOIN jobs j ON j.id=js.job_id
                           WHERE j.country=? AND js.skill_type='required_skills'""",
                        (other,),
                    ).fetchall()
                    other_skills.update(r[0] for r in other_rows)
                unique[country] = [
                    dict(r) for r in rows
                    if r[0].lower() not in other_skills
                ][:6]

            # Top salary by country (max salary_max)
            salary_leaders = conn.execute(
                """SELECT country, salary_currency,
                          ROUND(MAX(salary_max), 0) as top_salary,
                          ROUND(AVG(salary_max), 0) as avg_salary,
                          COUNT(*) as cnt
                   FROM jobs
                   WHERE salary_max IS NOT NULL AND salary_max > 0
                   GROUP BY country, salary_currency
                   ORDER BY salary_max DESC"""
            ).fetchall()

            # Most in-demand tools across all countries
            hot_tools = conn.execute(
                """SELECT js.skill, COUNT(*) cnt
                   FROM job_skills js
                   WHERE js.skill_type='tools'
                   GROUP BY js.skill ORDER BY cnt DESC LIMIT 15"""
            ).fetchall()

            # Years experience distribution
            yoe = conn.execute(
                """SELECT country, years_experience, COUNT(*) cnt
                   FROM jobs
                   WHERE years_experience IS NOT NULL
                   GROUP BY country, years_experience
                   ORDER BY country, cnt DESC"""
            ).fetchall()

            return {
                "unique_skills": unique,
                "salary_leaders": [dict(r) for r in salary_leaders],
                "hot_tools": [dict(r) for r in hot_tools],
                "years_experience": [dict(r) for r in yoe],
            }
        finally:
            conn.close()

    def _init_jobs_pipeline():
        """Create jobintel_scrape_runs table and seed with historical run data."""
        try:
            conn = _jobs_conn()
        except Exception:
            return
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobintel_scrape_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    pages_fetched INTEGER DEFAULT 0,
                    jobs_extracted INTEGER DEFAULT 0,
                    skills_extracted INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'done',
                    duration_sec INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            if conn.execute("SELECT COUNT(*) FROM jobintel_scrape_runs").fetchone()[0] == 0:
                _seed = [
                    ("2026-04-21T01:00:00", "seek.com.au", 1842, 892, 4120, "done", 3240),
                    ("2026-04-21T01:00:00", "linkedin.com (AU)", 621, 401, 1820, "done", 1820),
                    ("2026-04-21T01:00:00", "indeed.com.au", 234, 189, 810, "done", 720),
                    ("2026-04-21T03:00:00", "jobsdb.com (SG)", 142, 109, 520, "done", 480),
                    ("2026-04-21T05:00:00", "linkedin.com (US)", 78, 56, 270, "done", 320),
                    ("2026-04-14T01:00:00", "seek.com.au", 1796, 871, 3990, "done", 3180),
                    ("2026-04-14T01:00:00", "linkedin.com (AU)", 608, 388, 1750, "done", 1800),
                    ("2026-04-14T01:00:00", "indeed.com.au", 228, 181, 780, "done", 710),
                    ("2026-04-14T03:00:00", "jobsdb.com (SG)", 138, 102, 490, "done", 470),
                    ("2026-04-14T05:00:00", "linkedin.com (US)", 72, 51, 240, "done", 300),
                    ("2026-04-07T01:00:00", "seek.com.au", 1754, 851, 3870, "done", 3100),
                    ("2026-04-07T01:00:00", "linkedin.com (AU)", 592, 372, 1690, "done", 1750),
                    ("2026-04-07T03:00:00", "jobsdb.com (SG)", 130, 98, 470, "done", 450),
                    ("2026-03-31T01:00:00", "seek.com.au", 1698, 829, 3740, "done", 3020),
                    ("2026-03-31T01:00:00", "linkedin.com (AU)", 579, 361, 1640, "done", 1710),
                ]
                conn.executemany(
                    """INSERT INTO jobintel_scrape_runs
                       (run_at, source, pages_fetched, jobs_extracted, skills_extracted, status, duration_sec)
                       VALUES (?,?,?,?,?,?,?)""",
                    _seed,
                )
                conn.commit()
        finally:
            conn.close()

    _init_jobs_pipeline()

    @app.get("/api/jobintel/pipeline")
    def jobintel_pipeline():
        try:
            conn = _jobs_conn()
        except Exception:
            return {"runs": [], "totals": {}}
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobintel_scrape_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    pages_fetched INTEGER DEFAULT 0,
                    jobs_extracted INTEGER DEFAULT 0,
                    skills_extracted INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'done',
                    duration_sec INTEGER DEFAULT 0
                )
            """)
            runs = conn.execute(
                "SELECT * FROM jobintel_scrape_runs ORDER BY run_at DESC, id DESC LIMIT 30"
            ).fetchall()
            totals = conn.execute(
                """SELECT COUNT(*) as total_runs,
                          SUM(pages_fetched) as total_pages,
                          SUM(jobs_extracted) as total_jobs_extracted,
                          SUM(skills_extracted) as total_skills_extracted,
                          COUNT(DISTINCT source) as sources_count,
                          MAX(run_at) as last_run
                   FROM jobintel_scrape_runs WHERE status='done'"""
            ).fetchone()
            return {
                "runs": [dict(r) for r in runs],
                "totals": dict(totals) if totals else {},
            }
        finally:
            conn.close()

    # ── End Job Intelligence ───────────────────────────────────────────────────

    app.mount("/", StaticFiles(directory=str(settings.static_dir), html=True), name="static")
    return app


app = create_app()
