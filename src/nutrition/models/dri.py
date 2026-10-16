"""Health Canada Dietary Reference Intakes, loaded from seeds/ (see nutrition.sources.hc_dri).

Ages are in months; ``age_max_months`` is exclusive and NULL means "and older".
"""

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nutrition.db import Base
from nutrition.models.food import Nutrient


class DriValue(Base):
    __tablename__ = "dri_value"

    id: Mapped[int] = mapped_column(primary_key=True)
    nutrient_id: Mapped[int] = mapped_column(ForeignKey("nutrient.id"), index=True)
    sex: Mapped[str] = mapped_column(String(3))  # "M" | "F" | "any"
    life_stage: Mapped[str] = mapped_column(String(20))  # "general" | "pregnancy" | "lactation"
    age_min_months: Mapped[int] = mapped_column(Integer)
    age_max_months: Mapped[int | None] = mapped_column(Integer)
    age_label: Mapped[str] = mapped_column(String(50))
    value_type: Mapped[str] = mapped_column(String(10))  # EAR | RDA | AI | UL | CDRR
    amount: Mapped[float | None] = mapped_column(Float)  # NULL when not determinable/missing
    unit: Mapped[str] = mapped_column(String(10))
    per_kg_body_weight: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40))  # ok | not_determinable | missing_from_source
    footnotes: Mapped[str] = mapped_column(Text, default="")
    source_table: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str] = mapped_column(String(500))
    source_date: Mapped[str] = mapped_column(String(20))

    nutrient: Mapped[Nutrient] = relationship()


class Amdr(Base):
    """Acceptable Macronutrient Distribution Range, as percent of energy."""

    __tablename__ = "amdr"

    id: Mapped[int] = mapped_column(primary_key=True)
    nutrient_id: Mapped[int] = mapped_column(ForeignKey("nutrient.id"))
    age_min_months: Mapped[int] = mapped_column(Integer)
    age_max_months: Mapped[int | None] = mapped_column(Integer)
    age_label: Mapped[str] = mapped_column(String(50))
    low_pct: Mapped[float] = mapped_column(Float)
    high_pct: Mapped[float] = mapped_column(Float)
    source_url: Mapped[str] = mapped_column(String(500))
    source_date: Mapped[str] = mapped_column(String(20))

    nutrient: Mapped[Nutrient] = relationship()


class EerEquation(Base):
    """One Estimated Energy Requirement equation (kcal/day).

    EER = constant + age_years_coef * age[y] + height_cm_coef * height[cm]
          + weight_kg_coef * weight[kg] + gestation_weeks_coef * gestation[weeks]
    """

    __tablename__ = "eer_equation"

    id: Mapped[int] = mapped_column(primary_key=True)
    life_stage: Mapped[str] = mapped_column(String(20))
    sex: Mapped[str] = mapped_column(String(3))
    age_min_months: Mapped[int] = mapped_column(Integer)
    age_max_months: Mapped[int | None] = mapped_column(Integer)
    # Inclusive completed months postpartum, as published ("0 to 6", "7 to 12").
    postpartum_min_months: Mapped[int | None] = mapped_column(Integer)
    postpartum_max_months: Mapped[int | None] = mapped_column(Integer)
    pre_pregnancy_bmi: Mapped[str | None] = mapped_column(String(20))
    pa_cat: Mapped[str] = mapped_column(String(20))  # inactive | low_active | active | ... | any
    constant: Mapped[float] = mapped_column(Float)
    age_years_coef: Mapped[float] = mapped_column(Float)
    height_cm_coef: Mapped[float] = mapped_column(Float)
    weight_kg_coef: Mapped[float] = mapped_column(Float)
    gestation_weeks_coef: Mapped[float] = mapped_column(Float)
    equation_text: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str] = mapped_column(String(500))
    source_date: Mapped[str] = mapped_column(String(20))
