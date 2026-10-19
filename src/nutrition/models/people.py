"""Household members (people whose intake is tracked) and app users (who log in).

They are separate on purpose: a baby is a person but not a user, and in Phase 3 several users
on different phones may log for the same person.
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nutrition.db import Base
from nutrition.models.food import Nutrient

SEXES = {"M": "Male", "F": "Female"}
ACTIVITY_LEVELS = {
    "inactive": "Inactive",
    "low_active": "Low active",
    "active": "Active",
    "very_active": "Very active",
}
LIFE_STAGES = {"general": "Not pregnant or breastfeeding", "pregnancy": "Pregnant",
               "lactation": "Breastfeeding"}  # fmt: skip
GOALS = {"maintain": "Maintain", "gain_muscle": "Build muscle / gain weight"}


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Person(Base):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    date_of_birth: Mapped[date] = mapped_column(Date)
    sex: Mapped[str] = mapped_column(String(1))  # "M" | "F"
    height_cm: Mapped[float | None] = mapped_column(Float)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    activity_level: Mapped[str] = mapped_column(String(20), default="low_active")
    life_stage: Mapped[str] = mapped_column(String(20), default="general")
    # Baby's birth date while breastfeeding: selects the postpartum energy equation.
    postpartum_since: Mapped[date | None] = mapped_column(Date)
    goal: Mapped[str] = mapped_column(String(20), default="maintain")
    # Extra calories above estimated requirement for the "gain_muscle" goal (10-20%).
    energy_surplus_pct: Mapped[float] = mapped_column(Float, default=10.0)
    # Health Canada footnote: iron requirement is 1.8x for vegetarians. Off by default because
    # the household eats chicken and fish; turn on per person if those are rare.
    vegetarian_iron: Mapped[bool] = mapped_column(Boolean, default=False)
    # None = automatic (on under 12 months). When on, the dashboard shows solids variety
    # instead of targets met/unmet, because breast milk/formula isn't logged.
    milk_feeds_not_logged: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    overrides: Mapped[list["TargetOverride"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )
    weights: Mapped[list["WeightEntry"]] = relationship(
        back_populates="person", cascade="all, delete-orphan", order_by="WeightEntry.date"
    )


class TargetOverride(Base):
    """A personal target that replaces the computed one for a nutrient."""

    __tablename__ = "target_override"
    __table_args__ = (UniqueConstraint("person_id", "nutrient_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"))
    nutrient_id: Mapped[int] = mapped_column(ForeignKey("nutrient.id"))
    amount: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(String(300), default="")

    person: Mapped[Person] = relationship(back_populates="overrides")
    nutrient: Mapped[Nutrient] = relationship()


class WeightEntry(Base):
    __tablename__ = "weight_entry"
    __table_args__ = (UniqueConstraint("person_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date)
    weight_kg: Mapped[float] = mapped_column(Float)

    person: Mapped[Person] = relationship(back_populates="weights")
