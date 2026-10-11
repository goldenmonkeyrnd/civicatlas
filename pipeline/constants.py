from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YEAR = "2024"
OWNERSHIP = "5"
FIPS = tuple(f"24{code}" for code in (
    "001", "003", "005", "009", "011", "013", "015", "017", "019", "021",
    "023", "025", "027", "029", "031", "033", "035", "037", "039", "041",
    "043", "045", "047", "510",
))
INDUSTRIES = ("10", "11", "21", "22", "23", "31-33", "42", "44-45", "48-49",
              "51", "52", "53", "54", "55", "56", "61", "62", "71", "72", "81", "99")
GEOMETRY_URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_5m.zip"
TITLES_URL = "https://data.bls.gov/cew/doc/titles/industry/industry_titles.csv"
FIELDS = {"employment": "annual_avg_emplvl", "establishments": "annual_avg_estabs", "annual_pay": "avg_annual_pay"}
