"""Regenerates tests/fixtures/cnf_sample/: a tiny, SYNTHETIC dataset in CNF 2015 file layout.

Nutrient values here are made up for tests and must never be used as real nutrition data.
Files are written Latin-1 encoded, like the real CNF release.
"""

import csv
from pathlib import Path

OUT = Path(__file__).parent / "cnf_sample"

FILES = {
    "FOOD GROUP": [
        ["FoodGroupID", "FoodGroupCode", "FoodGroupName", "FoodGroupNameF"],
        [5, 5, "Poultry Products", "Produits de volaille"],
        [13, 13, "Beef Products", "Produits de boeuf"],
        [16, 16, "Legumes and Legume Products", "Légumineuses"],
    ],
    "FOOD NAME": [
        [
            "FoodID",
            "FoodCode",
            "FoodGroupID",
            "FoodSourceID",
            "FoodDescription",
            "FoodDescriptionF",
            "FoodDateOfEntry",
            "FoodDateOfPublication",
            "CountryCode",
            "ScientificName",
        ],
        [
            1001,
            1001,
            16,
            0,
            "Lentils, red, boiled",
            "Lentilles, rouges, bouillies",
            "2015-01-01",
            "",
            "",
            "",
        ],
        [
            1002,
            1002,
            5,
            0,
            "Chicken, broiler, breast, roasted",
            "Poulet, poitrine, rôti",
            "2015-01-01",
            "",
            "",
            "",
        ],
        [
            1003,
            1003,
            13,
            0,
            "Beef, ground, pan-fried",
            "Boeuf, haché, poêlé",
            "2015-01-01",
            "",
            "",
            "",
        ],
    ],
    "NUTRIENT NAME": [
        [
            "NutrientID",
            "NutrientCode",
            "NutrientSymbol",
            "NutrientUnit",
            "NutrientName",
            "NutrientNameF",
            "Tagname",
            "NutrientDecimals",
        ],
        [203, 203, "PROT", "g", "PROTEIN", "PROTÉINES", "PROCNT", 2],
        [204, 204, "FAT", "g", "FAT (TOTAL LIPIDS)", "LIPIDES", "FAT", 2],
        [205, 205, "CARB", "g", "CARBOHYDRATE, TOTAL (BY DIFFERENCE)", "GLUCIDES", "CHOCDF", 2],
        [208, 208, "KCAL", "kCal", "ENERGY (KILOCALORIES)", "ÉNERGIE", "ENERC_KCAL", 0],
        [255, 255, "MOIS", "g", "MOISTURE", "HUMIDITÉ", "WATER", 2],
        [269, 269, "TSUG", "g", "SUGARS, TOTAL", "SUCRES", "SUGAR", 2],
        [291, 291, "TDF", "g", "FIBRE, TOTAL DIETARY", "FIBRES", "FIBTG", 1],
        [301, 301, "CA", "mg", "CALCIUM", "CALCIUM", "CA", 0],
        [303, 303, "FE", "mg", "IRON", "FER", "FE", 2],
        [307, 307, "NA", "mg", "SODIUM", "SODIUM", "NA", 0],
        [309, 309, "ZN", "mg", "ZINC", "ZINC", "ZN", 2],
        [324, 324, "D-IU", "IU", "VITAMIN D (INTERNATIONAL UNITS)", "VITAMINE D", "VITD", 0],
        [339, 328, "D-UG", "µg", "VITAMIN D (D2 + D3)", "VITAMINE D", "VITDC", 1],
        [401, 401, "VITC", "mg", "VITAMIN C", "VITAMINE C", "VITC", 1],
        [418, 418, "B12", "µg", "VITAMIN B-12", "VITAMINE B12", "VITB12", 2],
        [815, 435, "DFE", "µg", "DIETARY FOLATE EQUIVALENTS", "ÉQUIVALENTS DE FOLATE", "FOLDFE", 0],
        [606, 606, "TSAT", "g", "FATTY ACIDS, SATURATED, TOTAL", "SATURÉS", "FASAT", 3],
        [
            621,
            621,
            "DHA",
            "g",
            "FATTY ACIDS, POLYUNSATURATED, 22:6 n-3, DOCOSAHEXAENOIC (DHA)",
            "DHA",
            "F22D6",
            3,
        ],
        [
            629,
            629,
            "EPA",
            "g",
            "FATTY ACIDS, POLYUNSATURATED, 20:5 n-3, EICOSAPENTAENOIC (EPA)",
            "EPA",
            "F20D5",
            3,
        ],
        [
            631,
            631,
            "DPA",
            "g",
            "FATTY ACIDS, POLYUNSATURATED, 22:5 n-3, DOCOSAPENTAENOIC (DPA)",
            "DPA",
            "F22D5",
            3,
        ],
        [
            831,
            851,
            "ALA",
            "g",
            "FATTY ACIDS, POLYUNSATURATED, 18:3 c,c,c n-3  LINOLENIC, OCTADECATRIENOIC",
            "ALA",
            "F18D3CN3",
            3,
        ],
        [
            868,
            902,
            "TOmega n-3",
            "g",
            "FATTY ACIDS, POLYUNSATURATED, TOTAL OMEGA  N-3",
            "OMÉGA-3",
            "",
            3,
        ],
    ],
    # Synthetic values. Lentils have vitamin D only in IU (tests the IU fallback);
    # chicken has both (tests that µg wins); beef has a blank value (skipped).
    "NUTRIENT AMOUNT": [
        [
            "FoodID",
            "NutrientID",
            "NutrientValue",
            "StandardError",
            "NumberofObservations",
            "NutrientSourceID",
            "NutrientDateOfEntry",
        ],
        [1001, 208, 100, "", "", 0, ""],
        [1001, 203, 10, "", "", 0, ""],
        [1001, 205, 20, "", "", 0, ""],
        [1001, 204, 1, "", "", 0, ""],
        [1001, 291, 8, "", "", 0, ""],
        [1001, 303, 3, "", "", 0, ""],
        [1001, 324, 40, "", "", 0, ""],
        [1001, 831, 0.1, "", "", 0, ""],
        [1001, 868, 0.12, "", "", 0, ""],
        [1001, 255, 70, "", "", 0, ""],
        [1002, 208, 200, "", "", 0, ""],
        [1002, 203, 30, "", "", 0, ""],
        [1002, 339, 0.5, "", "", 0, ""],
        [1002, 324, 999, "", "", 0, ""],
        [1002, 418, 0.3, "", "", 0, ""],
        [1002, 621, 0.02, "", "", 0, ""],
        [1003, 208, 250, "", "", 0, ""],
        [1003, 309, "", "", "", 0, ""],
    ],
    "MEASURE NAME": [
        ["MeasureID", "MeasureDescription", "MeasureDescriptionF"],
        [341, "1 cup", "1 tasse"],
        [342, "125 mL", "125 mL"],
        [500, "100 g", "100 g"],
        [601, "1/2 breast", "1/2 poitrine"],
    ],
    "CONVERSION FACTOR": [
        ["FoodID", "MeasureID", "ConversionFactorValue", "ConvFactorDateOfEntry"],
        [1001, 341, 1.98, ""],
        [1001, 342, 1.05, ""],
        [1001, 500, 1, ""],
        [1002, 601, 0.86, ""],
        [1002, 500, 1, ""],
        [1003, 500, 1, ""],
    ],
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, rows in FILES.items():
        with (OUT / f"{name}.csv").open("w", encoding="latin-1", newline="") as f:
            csv.writer(f).writerows(rows)


if __name__ == "__main__":
    main()
