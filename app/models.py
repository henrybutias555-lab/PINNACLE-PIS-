"""Database models.

A single ``Record`` table holds the three PRD data categories (valuation, construction
cost, housing). Common search fields are real columns so they can be filtered and
indexed; category-specific detail that varies report to report lives in JSON columns.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

ROLES = ("admin", "senior", "junior", "viewer")
CATEGORIES = ("valuation", "cost", "housing")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ONLINE_WINDOW_S = 150   # an open PIS page checks in every minute

    @property
    def online(self) -> bool:
        seen, out = _aware(self.last_seen_at), _aware(self.signed_out_at)
        if seen is None or (out is not None and out >= seen):
            return False
        return (utcnow() - seen).total_seconds() <= self.ONLINE_WINDOW_S


def _aware(d: datetime | None) -> datetime | None:
    """SQLite gives back naive datetimes; they are stored as UTC."""
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


class Report(Base):
    """An uploaded source document and its AI extraction draft."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(500))
    # The original file is kept in the database so it survives on serverless hosts
    # (no persistent disk). Deferred so listing reports does not load file bytes.
    file_data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_type: Mapped[str] = mapped_column(String(20))
    report_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # uploaded -> extracted -> confirmed | rejected | failed
    status: Mapped[str] = mapped_column(String(20), default="uploaded", index=True)
    extraction_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    uploaded_by: Mapped[User | None] = relationship(foreign_keys=[uploaded_by_id])
    records: Mapped[list["Record"]] = relationship(back_populates="report")


class Record(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(20), index=True)  # valuation | cost | housing

    # Common descriptors
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gps_address: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)  # Ghana Post GPS
    location: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)  # area / suburb
    district: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)  # MMDA
    city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    property_type: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    purpose: Mapped[str | None] = mapped_column(String(120), nullable=True)
    client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    record_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="GHS")
    size_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Valuation
    valuation_basis: Mapped[str | None] = mapped_column(String(30), nullable=True)  # capital | rental | both
    method: Mapped[str | None] = mapped_column(String(120), nullable=True)
    property_valued: Mapped[str | None] = mapped_column(Text, nullable=True)
    basis_of_value: Mapped[str | None] = mapped_column(String(160), nullable=True)  # e.g. Market Value
    valuer: Mapped[str | None] = mapped_column(String(255), nullable=True)  # valuer identity
    capital_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    annual_rent: Mapped[float | None] = mapped_column(Float, nullable=True)
    yield_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_per_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)
    rent_per_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)
    assumptions: Mapped[str | None] = mapped_column(Text, nullable=True)
    comparables: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Construction cost
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_per_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    specification: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Housing market
    avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_monthly_rent: Mapped[float | None] = mapped_column(Float, nullable=True)
    supply_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    demand_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    affordability_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    stock_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Report sections (property, location, improvements, title, valuation, marketability, assumptions)
    property_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    use_occupancy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    highest_best_use: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gps_coordinates: Mapped[str | None] = mapped_column(String(80), nullable=True)
    land_size: Mapped[str | None] = mapped_column(String(120), nullable=True)
    accessibility: Mapped[str | None] = mapped_column(Text, nullable=True)
    building_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    accommodation: Mapped[str | None] = mapped_column(Text, nullable=True)
    construction_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    services: Mapped[str | None] = mapped_column(Text, nullable=True)
    floor_areas: Mapped[str | None] = mapped_column(Text, nullable=True)
    ancillary_structures: Mapped[str | None] = mapped_column(Text, nullable=True)
    ownership: Mapped[str | None] = mapped_column(String(160), nullable=True)
    tenure: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_of_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unexpired_term: Mapped[str | None] = mapped_column(String(120), nullable=True)
    planning_status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    inspection_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    forced_sale_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_rating: Mapped[str | None] = mapped_column(String(255), nullable=True)
    accessibility_rating: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state_of_repair: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_conformity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    neighbourhood_rating: Mapped[str | None] = mapped_column(String(255), nullable=True)
    overall_marketability: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encumbrances: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_disclaimer: Mapped[str | None] = mapped_column(Text, nullable=True)
    inspection_limitation: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose_restriction: Mapped[str | None] = mapped_column(Text, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    report: Mapped[Report | None] = relationship(back_populates="records")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="record", cascade="all, delete-orphan",
                                                           order_by="Attachment.id")


ATTACHMENT_KINDS = ("photo", "site_plan", "title_document", "allocation_note", "valuation_certificate", "supporting")


class Attachment(Base):
    """A file attached to a record (photographs, site plan, title documents, ...).
    Stored in the database so it survives on hosts without a persistent disk."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    record: Mapped[Record] = relationship(back_populates="attachments")


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
