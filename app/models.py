"""Database models.

A single ``Record`` table holds the three PRD data categories (valuation, construction
cost, housing). Common search fields are real columns so they can be filtered and
indexed; category-specific detail that varies report to report lives in JSON columns.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
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


class Report(Base):
    """An uploaded source document and its AI extraction draft."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(String(1000))
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
    location: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)  # area / suburb
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

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    report: Mapped[Report | None] = relationship(back_populates="records")


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
