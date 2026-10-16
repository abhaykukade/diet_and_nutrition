"""Food composition tables.

Everything here is source-agnostic: CNF is the first ``DataSource``; USDA FoodData Central
can be added as another source with its own importer and nutrient map, without schema changes.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nutrition.db import Base


class DataSource(Base):
    __tablename__ = "data_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)  # e.g. "cnf", "usda_fdc"
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str | None] = mapped_column(String(50))
    url: Mapped[str | None] = mapped_column(String(500))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime)


class Nutrient(Base):
    """The app's own nutrient vocabulary (seeded from seeds/nutrients.csv)."""

    __tablename__ = "nutrient"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)  # e.g. "iron"
    name: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(20))  # canonical unit, e.g. "mg"
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_key: Mapped[bool] = mapped_column(Boolean, default=False)  # shown on dashboard


class SourceNutrient(Base):
    """A nutrient as defined by a data source, optionally mapped to a canonical ``Nutrient``."""

    __tablename__ = "source_nutrient"
    __table_args__ = (UniqueConstraint("source_id", "source_nutrient_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id"))
    source_nutrient_id: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str | None] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20))
    nutrient_id: Mapped[int | None] = mapped_column(ForeignKey("nutrient.id"))
    factor: Mapped[float] = mapped_column(Float, default=1.0)  # source value * factor = canonical
    priority: Mapped[int] = mapped_column(Integer, default=0)  # lower wins when several map


class FoodGroup(Base):
    __tablename__ = "food_group"
    __table_args__ = (UniqueConstraint("source_id", "source_group_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id"))
    source_group_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    name_fr: Mapped[str | None] = mapped_column(String(200))


class Food(Base):
    __tablename__ = "food"
    __table_args__ = (UniqueConstraint("source_id", "source_food_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id"))
    source_food_id: Mapped[str] = mapped_column(String(32))
    food_group_id: Mapped[int | None] = mapped_column(ForeignKey("food_group.id"))
    name: Mapped[str] = mapped_column(String(300), index=True)
    name_fr: Mapped[str | None] = mapped_column(String(300))
    # Foods dropped from a later source release are deactivated, never deleted,
    # so historical log entries keep pointing at them.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    source: Mapped[DataSource] = relationship()
    food_group: Mapped[FoodGroup | None] = relationship()
    nutrients: Mapped[list["FoodNutrient"]] = relationship(
        back_populates="food", cascade="all, delete-orphan"
    )
    portions: Mapped[list["FoodPortion"]] = relationship(
        back_populates="food", cascade="all, delete-orphan"
    )


class FoodNutrient(Base):
    """Amount of a canonical nutrient per 100 g edible portion."""

    __tablename__ = "food_nutrient"

    food_id: Mapped[int] = mapped_column(
        ForeignKey("food.id", ondelete="CASCADE"), primary_key=True
    )
    nutrient_id: Mapped[int] = mapped_column(ForeignKey("nutrient.id"), primary_key=True)
    amount_per_100g: Mapped[float] = mapped_column(Float)
    source_nutrient_id: Mapped[int | None] = mapped_column(ForeignKey("source_nutrient.id"))

    food: Mapped[Food] = relationship(back_populates="nutrients")
    nutrient: Mapped[Nutrient] = relationship()


class FoodPortion(Base):
    """A household measure for a food, e.g. '1 cup, cooked' = 198 g."""

    __tablename__ = "food_portion"

    id: Mapped[int] = mapped_column(primary_key=True)
    food_id: Mapped[int] = mapped_column(ForeignKey("food.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(String(200))
    grams: Mapped[float] = mapped_column(Float)
    origin: Mapped[str] = mapped_column(String(20), default="source")  # "source" | "user"
    source_measure_id: Mapped[str | None] = mapped_column(String(32))

    food: Mapped[Food] = relationship(back_populates="portions")
