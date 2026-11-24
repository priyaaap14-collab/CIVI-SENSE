"""Transparent rule-based priority recommendation engine (no machine learning).

Score (0-100) = category weight + citizen severity + related reports + waiting time + public impact.
Every component is returned so the interface can show exactly why a level was recommended.
The score is a triage aid only: it does not predict real-world danger or guarantee resolution,
and officers can override the recommended level.
"""

MAX_POINTS = {"category": 25, "severity": 25, "related": 20, "waiting": 20, "impact": 10}
THRESHOLDS = [(65, "Critical"), (50, "High"), (35, "Medium"), (0, "Low")]
FINISHED = {"Resolved", "Closed", "Rejected"}
WAIT_CAP_DAYS = 60   # waiting time saturates after 60 days
RELATED_CAP = 5      # related reports saturate after 5

# Category severity weights (1-5): documented judgement about public-safety / service-continuity impact.
# They are stored on the Category table, so administrators can edit them.
DEFAULT_WEIGHTS = {
    "Electricity & Power Supply": 5, "Crime & Safety": 5, "Fire Safety": 5,
    "Water Supply": 4, "Sewerage Systems": 4, "Storm Water Drains": 4, "Traffic & Road Safety": 4, "Covid 19": 4,
    "Roads, Footpaths & Infrastructure": 3, "Garbage & Sanitation": 3, "Street Lighting": 3, "Animal Husbandry": 3,
    "Pollution": 3, "Public Toilets": 3, "Trees & Saplings": 3, "Public Transport": 3,
    "Lakes": 2, "Parks & Recreation": 2, "Community Infrastructure & Services": 2, "Others": 2,
    "Yellow Spot": 2, "Uncategorised": 2, "Certificates": 1,
}


def level_for(score):
    for cut, name in THRESHOLDS:
        if score >= cut:
            return name
    return "Low"


def compute_priority(category_weight, severity, related_count, wait_days, impact, comment_count, status, source):
    """Return dict(score, level, factors). `impact` is 1-3 (live) or None; historical uses comment_count as a proxy."""
    cw = category_weight or 2
    f = []
    cat_pts = cw / 5 * MAX_POINTS["category"]
    f.append({"factor": "Category", "points": round(cat_pts, 1), "max": MAX_POINTS["category"],
              "detail": f"category severity weight {cw}/5"})

    if severity:
        sev_pts = (severity - 1) / 4 * MAX_POINTS["severity"]
        f.append({"factor": "Severity", "points": round(sev_pts, 1), "max": MAX_POINTS["severity"],
                  "detail": f"citizen-reported severity {severity}/5"})
    else:
        sev_pts = 0.5 * MAX_POINTS["severity"]
        f.append({"factor": "Severity", "points": round(sev_pts, 1), "max": MAX_POINTS["severity"],
                  "detail": "not provided (historical record) - neutral midpoint used"})

    rel_pts = min(related_count, RELATED_CAP) / RELATED_CAP * MAX_POINTS["related"]
    f.append({"factor": "Related reports", "points": round(rel_pts, 1), "max": MAX_POINTS["related"],
              "detail": f"{related_count} similar nearby report(s) found by text similarity"})

    if status in FINISHED:
        wait_pts, wd = 0.0, "complaint already finished - no waiting points"
    else:
        wait_pts = min(max(wait_days, 0), WAIT_CAP_DAYS) / WAIT_CAP_DAYS * MAX_POINTS["waiting"]
        wd = f"waiting {max(wait_days, 0):.0f} day(s) (saturates at {WAIT_CAP_DAYS})"
    f.append({"factor": "Waiting time", "points": round(wait_pts, 1), "max": MAX_POINTS["waiting"], "detail": wd})

    if impact:
        imp_pts = (impact - 1) / 2 * MAX_POINTS["impact"]
        idet = {1: "affects an individual", 2: "affects a neighbourhood", 3: "affects a wide area"}.get(impact, "")
    else:
        imp_pts = min(comment_count or 0, 10) / 10 * MAX_POINTS["impact"]
        idet = f"{comment_count or 0} public comment(s) used as an engagement proxy (historical record)"
    f.append({"factor": "Public impact", "points": round(imp_pts, 1), "max": MAX_POINTS["impact"], "detail": idet})

    score = round(cat_pts + sev_pts + rel_pts + wait_pts + imp_pts, 1)
    return {"score": score, "level": level_for(score), "factors": f}
