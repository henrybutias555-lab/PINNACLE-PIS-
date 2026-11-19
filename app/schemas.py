from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Typed or exported markers meaning "the report did not state this": stored as blank.
NOT_STATED = {"not stated", "not stated in report", "n/a", "na", "-", "--", "—", "–", "nil", "not available",
              "not given", "not provided", "unknown", "none stated"}


def is_not_stated(v: Any) -> bool:
    return isinstance(v, str) and v.strip().lower().rstrip(".") in NOT_STATED


Role = Literal["admin", "senior", "junior", "viewer"]
Category = Literal["valuation", "cost", "housing"]


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_seen_at: datetime | None = None
    signed_out_at: datetime | None = None
    online: bool = False

    @field_validator("created_at", "last_seen_at", "signed_out_at", mode="after")
    @classmethod
    def _utc(cls, v):
        from datetime import timezone
        return v.replace(tzinfo=timezone.utc) if v is not None and v.tzinfo is None else v


class UserCreate(BaseModel):
    email: str
    full_name: str = ""
    password: str = Field(min_length=8)
    role: Role = "viewer"


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class Comparable(BaseModel):
    address: str | None = None
    property_type: str | None = None
    date: str | None = None
    price: float | None = None
    rent: float | None = None
    size_sqm: float | None = None
    price_per_sqm: float | None = None
    notes: str | None = None


class RecordBase(BaseModel):
    category: Category
    title: str | None = None
    address: str | None = None
    gps_address: str | None = None
    location: str | None = None
    district: str | None = None
    city: str | None = None
    region: str | None = None
    property_type: str | None = None
    purpose: str | None = None
    client: str | None = None
    record_date: date | None = None
    currency: str = "GHS"
    size_sqm: float | None = None

    valuation_basis: str | None = None
    method: str | None = None
    property_valued: str | None = None
    basis_of_value: str | None = None
    valuer: str | None = None
    capital_value: float | None = None
    annual_rent: float | None = None
    yield_pct: float | None = None
    value_per_sqm: float | None = None
    rent_per_sqm: float | None = None
    assumptions: str | None = None
    comparables: list[Comparable] | None = None

    total_cost: float | None = None
    cost_per_sqm: float | None = None
    cost_breakdown: dict[str, float] | None = None
    specification: str | None = None

    avg_price: float | None = None
    avg_monthly_rent: float | None = None
    supply_units: int | None = None
    demand_level: str | None = None
    affordability_ratio: float | None = None
    stock_notes: str | None = None

    property_name: str | None = None
    owner: str | None = None
    use_occupancy: str | None = None
    highest_best_use: str | None = None
    gps_coordinates: str | None = None
    land_size: str | None = None
    accessibility: str | None = None
    building_type: str | None = None
    accommodation: str | None = None
    construction_details: str | None = None
    services: str | None = None
    floor_areas: str | None = None
    ancillary_structures: str | None = None
    ownership: str | None = None
    tenure: str | None = None
    source_of_title: str | None = None
    unexpired_term: str | None = None
    planning_status: str | None = None
    inspection_date: date | None = None
    forced_sale_value: float | None = None
    location_rating: str | None = None
    accessibility_rating: str | None = None
    state_of_repair: str | None = None
    user_conformity: str | None = None
    neighbourhood_rating: str | None = None
    overall_marketability: str | None = None
    encumbrances: str | None = None
    legal_disclaimer: str | None = None
    inspection_limitation: str | None = None
    purpose_restriction: str | None = None
    notes: str | None = None
    tags: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _not_stated_is_blank(cls, data: Any):
        if isinstance(data, dict):
            data = {k: (None if is_not_stated(v) else v) for k, v in data.items()}
            if data.get("currency") is None and "currency" in data:
                data.pop("currency")
            if data.get("gps_address"):
                from .services.rules import clean_gps_fields
                clean_gps_fields(data)
        return data

    @field_validator("record_date", "inspection_date", mode="before")
    @classmethod
    def _blank_date(cls, v: Any):
        if v in ("", None):
            return None
        return v

    @field_validator("cost_breakdown", mode="before")
    @classmethod
    def _clean_breakdown(cls, v: Any):
        if not v:
            return None
        if isinstance(v, dict):
            return {k: float(x) for k, x in v.items() if x not in (None, "")}
        return v


class RecordIn(RecordBase):
    pass


class RecordUpdate(BaseModel):
    """Partial update; any RecordBase field may be supplied."""

    model_config = ConfigDict(extra="allow")


class RecordOut(RecordBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    report_id: int | None = None
    created_at: datetime
    updated_at: datetime


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    file_type: str
    report_type: str | None
    title: str | None
    status: str
    extraction_method: str | None
    draft: dict | None
    error: str | None
    created_at: datetime
    confirmed_at: datetime | None
    record_count: int = 0


class ConfirmIn(BaseModel):
    records: list[RecordIn]


class CommentaryIn(BaseModel):
    basis_of_value: str | None = None
    location: str | None = None
    district: str | None = None
    city: str | None = None
    property_type: str | None = None
    category: Category | None = None
    purpose: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    tone: Literal["formal", "concise"] = "formal"


class AnalysisExportIn(CommentaryIn):
    group_by: str | None = "location,property_type"
    interval: Literal["month", "quarter", "year"] = "quarter"
    commentary: str | None = None


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_email: str | None
    action: str
    target: str | None
    detail: str | None
    ip: str | None
    created_at: datetime


TokenOut.model_rebuild()
