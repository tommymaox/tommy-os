from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .validation import bounded_positive, parse_date, parse_time, trimmed, non_negative


MEAL_SLOTS = {"breakfast", "lunch", "dinner", "snack"}


class EventCreate(BaseModel):
    title: str
    date: str
    type: Optional[str] = "event"
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    notes: Optional[str] = None
    color: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return trimmed(value, field_name="title", max_length=120) or ""

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        return parse_date(value)

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        return parse_time(value)

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="notes", max_length=1000, allow_blank=True)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="type", max_length=40) if value is not None else "event"

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="color", max_length=32, allow_blank=True)

    @model_validator(mode="after")
    def validate_time_order(self) -> "EventCreate":
        if self.start_time and self.end_time:
            start = datetime.strptime(self.start_time, "%H:%M")
            end = datetime.strptime(self.end_time, "%H:%M")
            if end <= start:
                raise ValueError("end_time must be after start_time")
        return self


class EventUpdate(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    type: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    notes: Optional[str] = None
    color: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="title", max_length=120) if value is not None else None

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        return parse_date(value) if value is not None else None

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        return parse_time(value)

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="notes", max_length=1000, allow_blank=True)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="type", max_length=40) if value is not None else None

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="color", max_length=32, allow_blank=True)


class TodoCreate(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return trimmed(value, field_name="text", max_length=240) or ""


class TodoUpdate(BaseModel):
    text: Optional[str] = None
    done: Optional[bool] = None

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="text", max_length=240) if value is not None else None


class KbValue(BaseModel):
    value: Any


class FoodItemBase(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    serving_size: Optional[float] = None
    serving_unit: Optional[str] = None
    kj: Optional[float] = None
    protein: Optional[float] = None
    carbs: Optional[float] = None
    fat: Optional[float] = None
    fibre: Optional[float] = None
    notes: Optional[str] = None
    ingredients: Optional[Any] = None
    steps: Optional[Any] = None
    photo: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="name", max_length=120) if value is not None else None

    @field_validator("brand")
    @classmethod
    def validate_brand(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="brand", max_length=120, allow_blank=True)

    @field_validator("serving_unit")
    @classmethod
    def validate_unit(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="serving_unit", max_length=20) if value is not None else None

    @field_validator("serving_size")
    @classmethod
    def validate_serving_size(cls, value: float | None) -> float | None:
        return bounded_positive(value, field_name="serving_size", default=100, minimum=0.01, maximum=100000) if value is not None else None

    @field_validator("kj", "protein", "carbs", "fat", "fibre")
    @classmethod
    def validate_macro(cls, value: float | None, info) -> float | None:
        return non_negative(value, field_name=info.field_name, default=0) if value is not None else None


class FoodItemCreate(FoodItemBase):
    name: str
    serving_size: Optional[float] = None
    serving_unit: str = "g"
    kj: float = 0
    protein: float = 0
    carbs: float = 0
    fat: float = 0
    fibre: float = 0


class FoodItemUpdate(FoodItemBase):
    pass


class RecipeIngredientInput(BaseModel):
    food_item_id: Optional[str] = None
    custom_name: Optional[str] = None
    quantity: float = 1
    kj_override: Optional[float] = None
    protein_override: Optional[float] = None
    carbs_override: Optional[float] = None
    fat_override: Optional[float] = None
    fibre_override: Optional[float] = None

    @field_validator("custom_name")
    @classmethod
    def validate_custom_name(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="custom_name", max_length=120) if value is not None else None

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, value: float) -> float:
        return bounded_positive(value, field_name="quantity", default=1, minimum=0.01, maximum=100000)

    @field_validator("kj_override", "protein_override", "carbs_override", "fat_override", "fibre_override")
    @classmethod
    def validate_override(cls, value: float | None, info) -> float | None:
        return non_negative(value, field_name=info.field_name, default=0) if value is not None else None

    @model_validator(mode="after")
    def validate_source(self) -> "RecipeIngredientInput":
        if bool(self.food_item_id) == bool(self.custom_name):
            raise ValueError("Ingredient must reference a food item or a custom name")
        return self


class FoodRecipeCreate(BaseModel):
    name: str
    servings: float = 1
    notes: Optional[str] = None
    ingredients: list[RecipeIngredientInput] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return trimmed(value, field_name="name", max_length=120) or ""

    @field_validator("servings")
    @classmethod
    def validate_servings(cls, value: float) -> float:
        return bounded_positive(value, field_name="servings", default=1)

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="notes", max_length=1000, allow_blank=True)

    @model_validator(mode="after")
    def validate_ingredients(self) -> "FoodRecipeCreate":
        if not self.ingredients:
            raise ValueError("Recipe must include at least one ingredient")
        return self


class FoodRecipeUpdate(BaseModel):
    name: Optional[str] = None
    servings: Optional[float] = None
    notes: Optional[str] = None
    ingredients: Optional[list[RecipeIngredientInput]] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="name", max_length=120) if value is not None else None

    @field_validator("servings")
    @classmethod
    def validate_servings(cls, value: float | None) -> float | None:
        return bounded_positive(value, field_name="servings", default=1) if value is not None else None

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="notes", max_length=1000, allow_blank=True)


class FoodLogBase(BaseModel):
    meal_slot: Optional[str] = None
    food_item_id: Optional[str] = None
    recipe_id: Optional[str] = None
    custom_name: Optional[str] = None
    servings: Optional[float] = None
    kj_override: Optional[float] = None
    protein_override: Optional[float] = None
    carbs_override: Optional[float] = None
    fat_override: Optional[float] = None
    fibre_override: Optional[float] = None
    notes: Optional[str] = None

    @field_validator("meal_slot")
    @classmethod
    def validate_meal_slot(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = trimmed(value, field_name="meal_slot", max_length=20) or "snack"
        if value not in MEAL_SLOTS:
            raise ValueError(f"meal_slot must be one of {sorted(MEAL_SLOTS)}")
        return value

    @field_validator("custom_name")
    @classmethod
    def validate_custom_name(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="custom_name", max_length=120) if value is not None else None

    @field_validator("servings")
    @classmethod
    def validate_servings(cls, value: float | None) -> float | None:
        return bounded_positive(value, field_name="servings", default=1, minimum=0.1, maximum=10000) if value is not None else None

    @field_validator("kj_override", "protein_override", "carbs_override", "fat_override", "fibre_override")
    @classmethod
    def validate_override(cls, value: float | None, info) -> float | None:
        return non_negative(value, field_name=info.field_name, default=0) if value is not None else None

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="notes", max_length=500, allow_blank=True)


class FoodLogCreate(FoodLogBase):
    date: str
    meal_slot: str
    servings: float = 1

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        return parse_date(value)

    @model_validator(mode="after")
    def validate_source(self) -> "FoodLogCreate":
        sources = [bool(self.food_item_id), bool(self.recipe_id), bool(self.custom_name)]
        if sum(sources) != 1:
            raise ValueError("Food log entry must have exactly one source")
        return self


class FoodLogUpdate(FoodLogBase):
    pass


class FoodAiParseInput(BaseModel):
    text: str
    meal_slot: Optional[str] = None

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return trimmed(value, field_name="text", max_length=2000) or ""

    @field_validator("meal_slot")
    @classmethod
    def validate_meal_slot(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = trimmed(value, field_name="meal_slot", max_length=20)
        if value not in MEAL_SLOTS:
            raise ValueError(f"meal_slot must be one of {sorted(MEAL_SLOTS)}")
        return value


class FoodAiParsedEntry(BaseModel):
    name: str
    servings: float = 1
    amount_text: Optional[str] = None
    food_item_id: Optional[str] = None
    source_type: str = "estimate"
    kj: float = 0
    protein: float = 0
    carbs: float = 0
    fat: float = 0
    fibre: float = 0
    confidence: float = 0.5
    notes: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return trimmed(value, field_name="name", max_length=160) or ""

    @field_validator("amount_text")
    @classmethod
    def validate_amount_text(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="amount_text", max_length=120, allow_blank=True)

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str) -> str:
        value = trimmed(value.lower(), field_name="source_type", max_length=20) or "estimate"
        if value not in {"library", "estimate"}:
            raise ValueError("source_type must be library or estimate")
        return value

    @field_validator("servings")
    @classmethod
    def validate_servings(cls, value: float) -> float:
        return bounded_positive(value, field_name="servings", default=1, minimum=0.01, maximum=1000)

    @field_validator("kj", "protein", "carbs", "fat", "fibre")
    @classmethod
    def validate_macros(cls, value: float, info) -> float:
        return non_negative(value, field_name=info.field_name, default=0)

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if value < 0:
            return 0
        if value > 1:
            return 1
        return round(value, 2)

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="notes", max_length=240, allow_blank=True)


class FoodAiParseResult(BaseModel):
    entries: list[FoodAiParsedEntry] = Field(default_factory=list)
    summary: Optional[str] = None
    warning: Optional[str] = None
    model: Optional[str] = None

    @field_validator("summary", "warning", "model")
    @classmethod
    def validate_strings(cls, value: str | None, info) -> str | None:
        max_length = 500 if info.field_name != "model" else 80
        return trimmed(value, field_name=info.field_name, max_length=max_length, allow_blank=True)


WIKI_CATEGORIES = {"fitness", "career", "learning", "finance", "people", "projects", "ideas", "reference"}


class WikiPageCreate(BaseModel):
    title: str
    category: str
    tags: list[str] = Field(default_factory=list)
    summary: str = ""
    content: str = ""

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return trimmed(value, field_name="title", max_length=120) or ""

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in WIKI_CATEGORIES:
            raise ValueError(f"category must be one of {sorted(WIKI_CATEGORIES)}")
        return value

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return trimmed(value, field_name="summary", max_length=240, allow_blank=True) or ""

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        return [t.strip().lower() for t in value if t.strip()][:20]


class WikiPageUpdate(BaseModel):
    title: Optional[str] = None
    tags: Optional[list[str]] = None
    summary: Optional[str] = None
    content: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="title", max_length=120) if value is not None else None

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="summary", max_length=240, allow_blank=True) if value is not None else None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [t.strip().lower() for t in value if t.strip()][:20]


RAW_SOURCE_TYPES = {"article", "note", "transcript", "book-chapter", "video", "podcast", "other"}


class RawSourceCreate(BaseModel):
    title: str
    type: str = "note"
    source_url: str = ""
    content: str = ""

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return trimmed(value, field_name="title", max_length=200) or ""

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in RAW_SOURCE_TYPES:
            raise ValueError(f"type must be one of {sorted(RAW_SOURCE_TYPES)}")
        return value

    @field_validator("source_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return trimmed(value, field_name="source_url", max_length=500, allow_blank=True) or ""


class RawSourceUpdate(BaseModel):
    title: Optional[str] = None
    type: Optional[str] = None
    source_url: Optional[str] = None
    content: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="title", max_length=200) if value is not None else None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value not in RAW_SOURCE_TYPES:
            raise ValueError(f"type must be one of {sorted(RAW_SOURCE_TYPES)}")
        return value

    @field_validator("source_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="source_url", max_length=500, allow_blank=True) if value is not None else None


class RawSourceIngest(BaseModel):
    category: str

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        value = value.strip().lower()
        valid = {"fitness", "career", "learning", "finance", "people", "projects", "ideas", "reference", "core"}
        if value not in valid:
            raise ValueError(f"category must be one of {sorted(valid)}")
        return value


class ClientLogEntry(BaseModel):
    level: str = "info"
    event: str
    message: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        return trimmed(value.lower(), field_name="level", max_length=20) or "info"

    @field_validator("event")
    @classmethod
    def validate_event(cls, value: str) -> str:
        return trimmed(value, field_name="event", max_length=120) or ""

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str | None) -> str | None:
        return trimmed(value, field_name="message", max_length=500, allow_blank=True)


class ClientLogBatch(BaseModel):
    entries: list[ClientLogEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_entries(self) -> "ClientLogBatch":
        if len(self.entries) > 50:
            raise ValueError("At most 50 log entries may be sent at once")
        return self
