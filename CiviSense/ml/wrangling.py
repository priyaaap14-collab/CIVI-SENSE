"""Data wrangling pipeline for the Bengaluru civic complaints dataset.

Input : data/raw/bengaluru_civic_complaints_raw.csv  (cp1252 encoded, mixed date formats)
Output: data/cleaned/complaints_clean.csv
        data/cleaned/removed_exact_duplicates.csv  (nothing is discarded silently)
        data/cleaned/wrangling_report.json         (before / after statistics)

Exact duplicate removal here is different from *suspected* duplicate complaints,
which are found later by TF-IDF + cosine similarity and are never deleted.
"""
import json
import re

import numpy as np
import pandas as pd

# Bengaluru bounding box used to validate coordinates.
LAT_RANGE = (12.70, 13.30)
LON_RANGE = (77.30, 77.90)

AGENCY_FULL = {
    "BBMP": "Bruhat Bengaluru Mahanagara Palike",
    "BTP": "Bangalore Traffic Police",
    "BWSSB": "Bangalore Water Supply and Sewerage Board",
    "KSPCB": "Karnataka State Pollution Control Board",
    "BESCOM": "Bangalore Electricity Supply Company",
    "BCP": "Bengaluru City Police",
    "BMTC": "Bangalore Metropolitan Transport Corporation",
    "KSFES": "Karnataka State Fire and Emergency Services",
    "KSRTC": "Karnataka State Road Transport Corporation",
    "BDA": "Bangalore Development Authority",
}
# Long form / alternative spellings -> short code. Anything else is only upper-cased.
AGENCY_ALIASES = {
    "bruhat bengaluru mahanagara palike": "BBMP",
    "bangalore traffic police": "BTP",
    "karnataka state pollution control board": "KSPCB",
    "bangalore water supply and sewerage board": "BWSSB",
    "bangalore electricity supply company": "BESCOM",
}

# Raw category title (lower-case) -> canonical category. Documented judgement-based
# merge of obvious synonyms. The untouched raw value is kept in `original_category`.
CATEGORY_MAP = {
    "street lighting": "Street Lighting", "streetlights": "Street Lighting",
    "mobility - roads, footpaths and infrastructure": "Roads, Footpaths & Infrastructure",
    "mobility - roads, public transport": "Roads, Footpaths & Infrastructure",
    "roads and footpaths": "Roads, Footpaths & Infrastructure",
    "pwd": "Roads, Footpaths & Infrastructure",
    "garbage and unsanitary practices": "Garbage & Sanitation",
    "solid waste management": "Garbage & Sanitation", "waste management": "Garbage & Sanitation",
    "sanitation": "Garbage & Sanitation",
    "water supply and services": "Water Supply", "water supply": "Water Supply",
    "electricity and power supply": "Electricity & Power Supply",
    "electricity & power": "Electricity & Power Supply", "power supply": "Electricity & Power Supply",
    "crime and safety": "Crime & Safety", "safety and crime": "Crime & Safety",
    "public transport - bmtc": "Public Transport", "public transport - ksrtc": "Public Transport",
    "public transport (bmtc and metro)": "Public Transport",
    "parks & recreation": "Parks & Recreation", "parks & garden": "Parks & Recreation",
    "playgrounds": "Parks & Recreation",
    "animal husbandry": "Animal Husbandry", "animal catcher": "Animal Husbandry",
    "pollution": "Pollution", "fire pollution": "Pollution",
    "traffic and road safety": "Traffic & Road Safety",
    "sewerage systems": "Sewerage Systems", "storm water drains": "Storm Water Drains",
    "community infrastructure and services": "Community Infrastructure & Services",
    "trees and saplings": "Trees & Saplings", "public toilets": "Public Toilets",
    "lakes": "Lakes", "certificates": "Certificates", "covid 19": "Covid 19",
    "fire safety": "Fire Safety", "yellow spot": "Yellow Spot", "others": "Others",
    # one-off categories (<= 1 record each) are folded into Others
    "emp grievance redressal": "Others", "ration card": "Others", "pension": "Others",
    "proibition & sale of tobacco products, plastic carry bags": "Others",
}

DATE_RE = re.compile(r"^\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)")


def clean_text(s: str) -> str:
    """Lower-case, strip URLs / e-mails / phone numbers (citizen privacy), keep letters and digits."""
    s = str(s).lower()
    s = URL_RE.sub(" ", s)
    s = EMAIL_RE.sub(" ", s)
    s = PHONE_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(value):
    """Explicit month-first parser (the raw data uses '-' and '/' separators, month first)."""
    m = DATE_RE.match(str(value))
    if not m:
        return pd.NaT
    mo, d, y, h, mi, sec = m.groups()
    try:
        return pd.Timestamp(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(sec or 0))
    except ValueError:
        return pd.NaT


def _stats(df, geo_col=None):
    return {
        "records": int(len(df)),
        "missing_values": {c: int(v) for c, v in df.isna().sum().items() if v > 0},
        "total_missing_cells": int(df.isna().sum().sum()),
    }


def wrangle(raw_path, clean_path, report_path, removed_path=None, log=print):
    log(f"[1/12] Loading {raw_path}")
    raw = pd.read_csv(raw_path, encoding="cp1252", low_memory=False)

    log("[2/12] Inspecting columns and data types")
    dtypes = {c: str(t) for c, t in raw.dtypes.items()}
    before = _stats(raw)
    before.update({
        "columns": list(raw.columns), "dtypes": dtypes,
        "categories": int(raw["category_title"].nunique()),
        "wards": int(raw["ward_id"].nunique()),
        "agency_labels": int(raw["civic_agency_title"].nunique()),
        "date_string_formats": int(raw["created_at"].astype(str).str.replace(r"\d", "9", regex=True).nunique()),
    })

    log("[3/12] Missing values identified (see report)")
    df = raw.copy()
    df["raw_row_number"] = np.arange(2, len(df) + 2)  # line number in the raw file (header = line 1)

    log("[4/12] Removing exact duplicate records")
    dup_mask = df.duplicated(subset=list(raw.columns), keep="first")
    removed = df[dup_mask].copy()
    df = df[~dup_mask].copy()
    if removed_path is not None:
        removed.to_csv(removed_path, index=False)

    log("[5/12] Standardising dates")
    df["created_at"] = df["created_at"].map(parse_date)
    invalid_dates = int(df["created_at"].isna().sum())
    df = df.dropna(subset=["created_at"]).copy() if invalid_dates else df

    log("[6/12] Standardising categories / normalising departments")
    df["original_category"] = df["category_title"]
    key = df["category_title"].fillna("").str.strip().str.lower()
    df["category"] = key.map(CATEGORY_MAP)
    unmapped = df["category"].isna() & df["category_title"].notna()
    # keep a title-cased version of any category not in the map (never drop information)
    df.loc[unmapped, "category"] = df.loc[unmapped, "category_title"].str.strip().str.title()
    df["category"] = df["category"].fillna("Uncategorised")
    df["sub_category"] = df["sub_category_title"].fillna("Unspecified").str.strip().str.title()

    agency = df["civic_agency_title"].fillna("").str.strip()
    agency = agency.map(lambda a: AGENCY_ALIASES.get(a.lower(), a.upper() if a else ""))
    df["department"] = agency.replace("", np.nan)  # missing stays missing; never guessed

    # ward names: recover missing names from ward_id when another row provides it
    ward_names = df.dropna(subset=["ward_title"]).drop_duplicates("ward_id").set_index("ward_id")["ward_title"]
    df["ward_title"] = df["ward_title"].fillna(df["ward_id"].map(ward_names))
    df["ward_title"] = df["ward_title"].fillna("Ward " + df["ward_id"].astype(str)).str.strip()

    df["status"] = df["complaint_status_title"].str.strip()

    log("[7/12] Validating numerical and geographical fields")
    lat, lon = pd.to_numeric(df["latitude"], errors="coerce"), pd.to_numeric(df["longitude"], errors="coerce")
    geo_valid = lat.between(*LAT_RANGE) & lon.between(*LON_RANGE)
    df["latitude"], df["longitude"] = lat.where(geo_valid), lon.where(geo_valid)  # invalid -> missing, not fabricated
    df["geo_valid"] = geo_valid
    df["comment_count"] = pd.to_numeric(df["comment_count"], errors="coerce").fillna(0).clip(lower=0).astype(int)
    invalid_geo = int((~geo_valid).sum())

    log("[8/12] Cleaning text")
    for c in ("title", "description", "location", "address"):
        df[c] = df[c].astype("string").str.replace(r"\s+", " ", regex=True).str.strip()
    df["text_clean"] = (df["title"].fillna("") + " " + df["description"].fillna("")).map(clean_text)
    empty_text = int((df["text_clean"].str.len() == 0).sum())

    log("[9/12] Creating analytical features")
    df["year"] = df["created_at"].dt.year
    df["month"] = df["created_at"].dt.month
    df["year_month"] = df["created_at"].dt.strftime("%Y-%m")
    df["weekday"] = df["created_at"].dt.day_name()
    df["hour"] = df["created_at"].dt.hour
    df["word_count"] = df["text_clean"].str.split().map(len)
    reference_date = df["created_at"].max().normalize()
    df["age_days"] = (reference_date - df["created_at"]).dt.days
    # chronological id: stable identifier used to build historical complaint references
    df = df.sort_values(["created_at", "raw_row_number"]).reset_index(drop=True)
    df.insert(0, "hist_id", np.arange(1, len(df) + 1))

    cols = ["hist_id", "created_at", "year", "month", "year_month", "weekday", "hour", "ward_id", "ward_title",
            "category", "original_category", "sub_category", "department", "title", "description", "text_clean",
            "location", "address", "latitude", "longitude", "geo_valid", "status", "comment_count",
            "word_count", "age_days", "raw_row_number"]
    out = df[cols]

    log(f"[10/12] Saving cleaned dataset -> {clean_path}")
    out.to_csv(clean_path, index=False)

    after = _stats(out)
    after.update({
        "categories": int(out["category"].nunique()),
        "wards": int(out["ward_id"].nunique()),
        "departments": int(out["department"].nunique()),
        "valid_geo_records": int(out["geo_valid"].sum()),
    })
    log("[11/12] Building before/after report")
    report = {
        "raw_file": str(raw_path),
        "original_record_count": before["records"],
        "cleaned_record_count": after["records"],
        "exact_duplicates_removed": int(dup_mask.sum()),
        "invalid_dates": invalid_dates,
        "invalid_or_missing_coordinates_nulled": invalid_geo,
        "records_with_empty_text_after_cleaning": empty_text,
        "reference_date": str(reference_date.date()),
        "date_range": [str(out["created_at"].min()), str(out["created_at"].max())],
        "before": before, "after": after,
        "categories_before": int(raw["category_title"].nunique()), "categories_after": after["categories"],
        "rows_without_category_kept_as_Uncategorised": int((out["category"] == "Uncategorised").sum()),
        "rows_without_department_kept_unassigned": int(out["department"].isna().sum()),
        "status_counts": out["status"].value_counts().to_dict(),
        "category_counts": out["category"].value_counts().to_dict(),
        "limitations": [
            "No resolution timestamp exists: resolution time cannot be computed for historical records.",
            "No citizen/user id exists: independent reporters cannot be distinguished in historical data.",
            "No severity or public-impact field exists in the source data.",
            "address is missing for many records; location (street text) is present for all.",
            "Department is missing for some records and is left unassigned rather than guessed.",
            "Source status is the status at the time the dataset was published, not a status history.",
        ],
    }
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    log("[12/12] Done")
    return report
