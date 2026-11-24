"""CiviSense data mining algorithms.

1. Classification      : TF-IDF + Logistic Regression
2. Duplicate detection : TF-IDF + cosine similarity (+ geographic distance and date proximity)
3. Hotspot detection   : DBSCAN (haversine metric)
4. Ward clustering     : K-Means (standardised features, elbow + silhouette)
"""
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from django.conf import settings
from sklearn.cluster import DBSCAN, KMeans
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, classification_report, confusion_matrix,
                             precision_recall_fscore_support, silhouette_score, davies_bouldin_score)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from .wrangling import clean_text

ART = Path(settings.ML_ARTIFACTS)
CLASSIFIER_FILE = ART / "classifier.joblib"
DUP_FILE = ART / "duplicate_index.joblib"
RESULTS_FILE = ART / "results.json"
MIN_CLASS_SIZE = 20          # categories with fewer labelled records are not trained on
RANDOM_STATE = 42

# ---- duplicate scoring rules (transparent, documented on the Data Mining page) -------------------
GEO_FULL_M, GEO_ZERO_M = 250, 1500        # geo factor: 1.0 within 250 m, falls linearly to 0 at 1.5 km
TIME_FULL_D, TIME_FLOOR_D, TIME_FLOOR = 30, 365, 0.6
NO_GEO_FACTOR = 0.85                      # location unknown for one of the two complaints
MIN_OPERATING_THRESHOLD = 0.50


def load_results():
    if RESULTS_FILE.exists():
        return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    return {}


def save_results(update: dict):
    res = load_results()
    res.update(update)
    ART.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(res, indent=2, default=float), encoding="utf-8")


def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * np.arcsin(np.sqrt(a))


# =================================================================================================
# 1. CLASSIFICATION
# =================================================================================================
def make_classifier_pipeline():
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=60000, sublinear_tf=True, stop_words="english")
    clf = LogisticRegression(C=5.0, max_iter=1000, class_weight="balanced", random_state=RANDOM_STATE)
    return vec, clf


def train_classifier(texts, labels, log=print):
    df = pd.DataFrame({"t": texts, "y": labels})
    df = df[df["t"].str.len() > 0]
    counts = df["y"].value_counts()
    kept = counts[counts >= MIN_CLASS_SIZE].index
    excluded = {k: int(v) for k, v in counts[counts < MIN_CLASS_SIZE].items()}
    df = df[df["y"].isin(kept)]
    log(f"  training on {len(df)} labelled complaints, {len(kept)} categories; excluded (<{MIN_CLASS_SIZE}): {excluded}")

    Xtr, Xte, ytr, yte = train_test_split(df["t"], df["y"], test_size=0.2, stratify=df["y"], random_state=RANDOM_STATE)
    vec, clf = make_classifier_pipeline()
    t0 = time.time()
    A = vec.fit_transform(Xtr)
    clf.fit(A, ytr)
    pred = clf.predict(vec.transform(Xte))
    labels_sorted = sorted(kept)
    p_m, r_m, f_m, _ = precision_recall_fscore_support(yte, pred, average="macro", zero_division=0)
    p_w, r_w, f_w, _ = precision_recall_fscore_support(yte, pred, average="weighted", zero_division=0)
    cm = confusion_matrix(yte, pred, labels=labels_sorted)
    report = classification_report(yte, pred, labels=labels_sorted, output_dict=True, zero_division=0)
    metrics = {
        "algorithm": "TF-IDF (1-2 grams, sublinear tf) + Logistic Regression (class_weight=balanced)",
        "n_train": int(len(Xtr)), "n_test": int(len(Xte)), "n_classes": len(labels_sorted),
        "excluded_classes": excluded, "split": "stratified 80/20, random_state=42",
        "accuracy": accuracy_score(yte, pred),
        "macro_precision": p_m, "macro_recall": r_m, "macro_f1": f_m,
        "weighted_precision": p_w, "weighted_recall": r_w, "weighted_f1": f_w,
        "labels": labels_sorted, "confusion_matrix": cm.tolist(),
        "per_class": {k: {m: float(v[m]) for m in ("precision", "recall", "f1-score", "support")}
                      for k, v in report.items() if k in labels_sorted},
        "training_seconds": round(time.time() - t0, 1),
        "note": "Metrics are measured on the held-out 20% test split. The deployed model is refit on all labelled data.",
    }
    # deployment model: refit on every labelled record
    vec2, clf2 = make_classifier_pipeline()
    clf2.fit(vec2.fit_transform(df["t"]), df["y"])
    ART.mkdir(parents=True, exist_ok=True)
    joblib.dump({"vectorizer": vec2, "classifier": clf2}, CLASSIFIER_FILE)
    save_results({"classification": metrics})
    return metrics


_clf_cache = {}


def predict_category(text, top_k=3):
    """Return [(category_name, probability)] for a complaint text, or [] if no model is trained."""
    if not CLASSIFIER_FILE.exists():
        return []
    mtime = CLASSIFIER_FILE.stat().st_mtime
    if _clf_cache.get("mtime") != mtime:
        _clf_cache.update(mtime=mtime, model=joblib.load(CLASSIFIER_FILE))
    m = _clf_cache["model"]
    t = clean_text(text)
    if not t:
        return []
    proba = m["classifier"].predict_proba(m["vectorizer"].transform([t]))[0]
    order = np.argsort(proba)[::-1][:top_k]
    return [(str(m["classifier"].classes_[i]), float(proba[i])) for i in order]


# =================================================================================================
# 2. DUPLICATE DETECTION
# =================================================================================================
def combined_score(cos, dist_m, days):
    """Text similarity adjusted by geographic distance and date proximity (all arrays/scalars)."""
    cos = np.asarray(cos, dtype=float)
    if dist_m is None:
        geo = np.full(cos.shape, NO_GEO_FACTOR)
    else:
        d = np.asarray(dist_m, dtype=float)
        geo = np.where(np.isnan(d), NO_GEO_FACTOR,
                       np.clip(1 - (d - GEO_FULL_M) / (GEO_ZERO_M - GEO_FULL_M), 0, 1))
    if days is None:
        tf = np.ones(cos.shape)
    else:
        dd = np.abs(np.asarray(days, dtype=float))
        tf = np.where(np.isnan(dd), 1.0,
                      np.where(dd <= TIME_FULL_D, 1.0,
                               np.clip(1 - (1 - TIME_FLOOR) * (dd - TIME_FULL_D) / (TIME_FLOOR_D - TIME_FULL_D), TIME_FLOOR, 1)))
    return cos * geo * tf


def make_dup_vectorizer():
    return TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, stop_words="english")


def _perturb(text, rng):
    toks = text.split()
    if len(toks) < 6:
        return text
    keep = [t for t in toks if rng.random() > 0.25] or toks
    for _ in range(max(1, len(keep) // 10)):          # a few local swaps (re-worded report)
        i = int(rng.integers(0, max(1, len(keep) - 1)))
        keep[i], keep[min(i + 1, len(keep) - 1)] = keep[min(i + 1, len(keep) - 1)], keep[i]
    return " ".join(keep)


def evaluate_duplicates(texts, categories, vec, n=1500, seed=RANDOM_STATE):
    """Constructed evaluation set (documented): positives = re-worded copies of a complaint
    (25% of words dropped, a few swaps); negatives = pairs of different complaints, half from the
    same category (hard negatives), half random. Real human duplicate labels do not exist in the data."""
    rng = np.random.default_rng(seed)
    texts = pd.Series(list(texts)).reset_index(drop=True)
    cats = pd.Series(list(categories)).reset_index(drop=True)
    ok = texts[texts.str.split().map(len) >= 6].index.to_numpy()
    idx = rng.choice(ok, size=min(n, len(ok)), replace=False)
    pos_b = [_perturb(texts[i], rng) for i in idx]
    A = vec.transform(texts[idx])
    pos = np.asarray(A.multiply(vec.transform(pos_b)).sum(axis=1)).ravel()

    by_cat = {c: g.index.to_numpy() for c, g in cats.groupby(cats)}
    hard, rand = [], []
    for i in idx:
        pool = by_cat[cats[i]]
        j = int(rng.choice(pool))
        while j == i and len(pool) > 1:
            j = int(rng.choice(pool))
        hard.append(j)
        k = int(rng.choice(ok))
        rand.append(k if k != i else int(rng.choice(ok)))
    neg_h = np.asarray(A.multiply(vec.transform(texts[hard])).sum(axis=1)).ravel()
    neg_r = np.asarray(A.multiply(vec.transform(texts[rand])).sum(axis=1)).ravel()
    neg = np.concatenate([neg_h, neg_r])
    scores = np.concatenate([pos, neg])
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
    rows = []
    for t in np.round(np.arange(0.10, 0.96, 0.05), 2):
        pred = scores >= t
        tp = int((pred & (y == 1)).sum())
        fp = int((pred & (y == 0)).sum())
        fn = int((~pred & (y == 1)).sum())
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        rows.append({"threshold": float(t), "precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "fn": fn})
    best = max(rows, key=lambda r: (r["f1"], r["threshold"]))
    operating = max(best["threshold"], MIN_OPERATING_THRESHOLD)
    return {
        "evaluation_set": {"positives": int(len(pos)), "negatives_same_category": int(len(neg_h)),
                           "negatives_random": int(len(neg_r)),
                           "construction": "Positives are synthetic re-worded copies (25% of words dropped, local word swaps) "
                                           "of real complaints; negatives are different real complaints. This is a constructed "
                                           "benchmark, not human-labelled ground truth, so scores are optimistic for real-world "
                                           "paraphrases. Same-category negatives may contain unlabelled genuine duplicates."},
        "thresholds": rows, "best_f1_threshold": best["threshold"], "operating_threshold": float(operating),
        "mean_similarity": {"positives": float(pos.mean()), "same_category_negatives": float(neg_h.mean()),
                            "random_negatives": float(neg_r.mean())},
    }


def build_duplicate_index(df, log=print):
    """df columns: pk, ref, text_clean, latitude, longitude, ts (epoch days), category."""
    vec = make_dup_vectorizer()
    X = vec.fit_transform(df["text_clean"])
    joblib.dump({"vectorizer": vec, "X": X.tocsr(), "pk": df["pk"].to_numpy(), "lat": df["latitude"].to_numpy(float),
                 "lon": df["longitude"].to_numpy(float), "ts": df["ts"].to_numpy(float)}, DUP_FILE)
    ev = evaluate_duplicates(df["text_clean"], df["category"], vec)
    ev["index_size"] = int(X.shape[0])
    ev["vocabulary_size"] = int(X.shape[1])
    save_results({"duplicates": ev})
    return vec, X, ev


def mine_historical_pairs(threshold, block=400, log=print):
    """Find suspected duplicate pairs inside the indexed (historical) records. Returns list of tuples
    (pk_a, pk_b, cos, combined, dist_m, days) with pk_a < pk_b."""
    idx = joblib.load(DUP_FILE)
    X, pk, lat, lon, ts = idx["X"], idx["pk"], idx["lat"], idx["lon"], idx["ts"]
    n = X.shape[0]
    XT = X.T.tocsc()
    out = []
    for s in range(0, n, block):
        e = min(n, s + block)
        S = (X[s:e] @ XT).toarray()
        for r in range(e - s):
            i = s + r
            S[r, : i + 1] = 0                      # keep only j > i
        rows, cols = np.nonzero(S >= threshold)
        if len(rows) == 0:
            continue
        i_idx = rows + s
        cos = S[rows, cols]
        d = haversine_m(lat[i_idx], lon[i_idx], lat[cols], lon[cols])
        days = ts[i_idx] - ts[cols]
        comb = combined_score(cos, d, days)
        for k in np.nonzero(comb >= threshold)[0]:
            out.append((int(pk[i_idx[k]]), int(pk[cols[k]]), float(cos[k]), float(comb[k]),
                        None if np.isnan(d[k]) else float(d[k]), float(abs(days[k]))))
        log(f"  similarity blocks {e}/{n}, pairs so far {len(out)}")
    return out


_dup_cache = {}


def _load_dup_index():
    if not DUP_FILE.exists():
        return None
    m = DUP_FILE.stat().st_mtime
    if _dup_cache.get("m") != m:
        _dup_cache.update(m=m, idx=joblib.load(DUP_FILE))
    return _dup_cache["idx"]


def find_related(text, lat=None, lon=None, when=None, live_rows=None, exclude_pk=None, top_n=5, min_score=None):
    """Compare a complaint with indexed historical complaints and live complaints.

    live_rows: iterable of (pk, ref, text_clean, lat, lon, epoch_days) for complaints created after training.
    Returns a list of dicts sorted by combined score.
    """
    idx = _load_dup_index()
    ev = load_results().get("duplicates", {})
    thr = min_score if min_score is not None else ev.get("operating_threshold", MIN_OPERATING_THRESHOLD)
    t = clean_text(text)
    if not t or idx is None:
        return []
    vec = idx["vectorizer"]
    q = vec.transform([t])
    cos = np.asarray((idx["X"] @ q.T).todense()).ravel()
    pks, lats, lons, tss = idx["pk"], idx["lat"], idx["lon"], idx["ts"]
    if live_rows:
        lv = list(live_rows)
        if lv:
            lc = np.asarray((vec.transform([r[2] for r in lv]) @ q.T).todense()).ravel()
            cos = np.concatenate([cos, lc])
            pks = np.concatenate([pks, [r[0] for r in lv]])
            lats = np.concatenate([lats, [np.nan if r[3] is None else r[3] for r in lv]])
            lons = np.concatenate([lons, [np.nan if r[4] is None else r[4] for r in lv]])
            tss = np.concatenate([tss, [r[5] for r in lv]])
    cand = np.nonzero(cos >= 0.25)[0]
    if len(cand) == 0:
        return []
    if lat is not None and lon is not None:
        dist = haversine_m(lat, lon, lats[cand], lons[cand])
    else:
        dist = np.full(len(cand), np.nan)
    days = (when - tss[cand]) if when is not None else None
    comb = combined_score(cos[cand], dist, days)
    order = np.argsort(comb)[::-1]
    res = []
    for o in order:
        if comb[o] < thr or len(res) >= top_n:
            break
        pk = int(pks[cand[o]])
        if exclude_pk is not None and pk == exclude_pk:
            continue
        res.append({"pk": pk, "text_similarity": float(cos[cand[o]]), "combined": float(comb[o]),
                    "distance_m": None if np.isnan(dist[o]) else float(dist[o]),
                    "days_apart": None if days is None else float(abs(days[o]))})
    return res


# =================================================================================================
# 3. DBSCAN HOTSPOTS
# =================================================================================================
def run_dbscan(df, eps_m=300, min_samples=15):
    """df columns: pk, latitude, longitude, category, ward. Coordinates must already be valid (no NaN)."""
    out = {"eps_m": eps_m, "min_samples": min_samples, "n_points": int(len(df))}
    if len(df) < max(min_samples, 2):
        out.update(n_clusters=0, noise_points=int(len(df)), clusters=[], labels=[], silhouette=None, note="Too few points")
        return out
    X = np.radians(df[["latitude", "longitude"]].to_numpy(float))
    db = DBSCAN(eps=eps_m / 6371000.0, min_samples=min_samples, metric="haversine", algorithm="ball_tree").fit(X)
    labels = db.labels_
    n_clusters = int(len(set(labels)) - (1 if -1 in labels else 0))
    d = df.assign(cluster=labels)
    clusters = []
    for cid, g in d[d.cluster >= 0].groupby("cluster"):
        c_lat, c_lon = g.latitude.mean(), g.longitude.mean()
        radius = float(haversine_m(c_lat, c_lon, g.latitude.to_numpy(), g.longitude.to_numpy()).max())
        clusters.append({
            "id": int(cid), "size": int(len(g)), "lat": float(c_lat), "lon": float(c_lon), "radius_m": round(radius),
            "top_category": g.category.value_counts().index[0],
            "top_category_share": round(float(g.category.value_counts(normalize=True).iloc[0]), 2),
            "top_ward": g.ward.value_counts().index[0],
            "categories": g.category.value_counts().head(3).to_dict(),
        })
    clusters.sort(key=lambda c: -c["size"])
    sil = None
    db_idx = None
    core = labels >= 0
    if n_clusters >= 2 and core.sum() > 2:
        rng = np.random.default_rng(RANDOM_STATE)
        pick = np.nonzero(core)[0]
        if len(pick) > 4000:
            pick = rng.choice(pick, 4000, replace=False)
        try:
            sil = float(silhouette_score(X[pick], labels[pick], metric="haversine"))
            db_idx = float(davies_bouldin_score(np.degrees(X[pick]), labels[pick]))
        except ValueError:
            pass
    sizes = [c["size"] for c in clusters]
    out.update(n_clusters=n_clusters, noise_points=int((labels == -1).sum()), clusters=clusters,
               labels=labels.tolist(), silhouette=sil, davies_bouldin=db_idx,
               clustered_points=int(core.sum()),
               size_stats={"min": min(sizes), "median": float(np.median(sizes)), "max": max(sizes)} if sizes else None)
    return out


# =================================================================================================
# 4. K-MEANS WARD CLUSTERING
# =================================================================================================
FEATURE_LABELS = {"log_volume": "complaint volume", "backlog_rate": "backlog rate (not resolved/closed/rejected)",
                  "resolved_rate": "resolved rate", "mean_comments": "average public comments",
                  "median_age_open": "median age of open complaints"}


def ward_clustering(df, reference_date, min_complaints=10, top_cats=6, k_min=2, k_max=10):
    """df columns: ward, category, status, comment_count, created_at (tz-naive)."""
    d = df.copy()
    d["finished"] = d["status"].isin(["Resolved", "Closed", "Rejected"])
    d["resolved"] = d["status"].isin(["Resolved", "Closed"])
    d["age"] = (pd.Timestamp(reference_date) - d["created_at"]).dt.days
    vol = d.groupby("ward").size()
    keep = vol[vol >= min_complaints].index
    excluded = {w: int(vol[w]) for w in vol.index if w not in keep}
    d = d[d.ward.isin(keep)]
    top = d["category"].value_counts().head(top_cats).index.tolist()
    g = d.groupby("ward")
    F = pd.DataFrame({
        "log_volume": np.log1p(g.size()),
        "backlog_rate": 1 - g["finished"].mean(),
        "resolved_rate": g["resolved"].mean(),
        "mean_comments": g["comment_count"].mean(),
        "median_age_open": d[~d.finished].groupby("ward")["age"].median(),
    })
    F["median_age_open"] = F["median_age_open"].fillna(0)
    share = pd.crosstab(d["ward"], d["category"].where(d["category"].isin(top), "Other categories"), normalize="index")
    for c in share.columns:
        F["share: " + c] = share[c]
    F = F.fillna(0)
    Z = StandardScaler().fit_transform(F)

    ks = [k for k in range(k_min, min(k_max, len(F) - 1) + 1)]
    inertia, sil = {}, {}
    for k in ks:
        km = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE).fit(Z)
        inertia[k] = float(km.inertia_)
        sil[k] = float(silhouette_score(Z, km.labels_))
    cand = [k for k in ks if 3 <= k <= 8] or ks
    best_k = max(cand, key=lambda k: sil[k])
    km = KMeans(n_clusters=best_k, n_init=20, random_state=RANDOM_STATE).fit(Z)
    F["cluster"] = km.labels_
    overall = F.drop(columns="cluster")
    mu, sd = overall.mean(), overall.std().replace(0, 1)
    clusters = []
    for cid in range(best_k):
        sub = F[F.cluster == cid].drop(columns="cluster")
        zc = ((sub.mean() - mu) / sd)
        hi = zc[zc >= 0.5].sort_values(ascending=False).head(3)
        lo = zc[zc <= -0.5].sort_values().head(2)
        parts = []
        for f, z in hi.items():
            parts.append(f"higher {FEATURE_LABELS.get(f, f.replace('share: ', 'share of '))} ({z:+.1f} SD)")
        for f, z in lo.items():
            parts.append(f"lower {FEATURE_LABELS.get(f, f.replace('share: ', 'share of '))} ({z:+.1f} SD)")
        prof = {
            "wards": int(len(sub)), "avg_complaints": float(np.expm1(sub["log_volume"]).mean()),
            "backlog_rate": float(sub["backlog_rate"].mean()), "resolved_rate": float(sub["resolved_rate"].mean()),
            "top_category": sub[[c for c in sub.columns if c.startswith("share: ")]].mean().idxmax().replace("share: ", ""),
        }
        clusters.append({"id": cid, "wards": sorted(sub.index.tolist()), "profile": prof,
                         "explanation": "; ".join(parts) if parts else "close to the city-wide average on every feature"})
    clusters.sort(key=lambda c: -c["profile"]["avg_complaints"])
    pcs = PCA(n_components=2, random_state=RANDOM_STATE).fit_transform(Z)
    return {
        "k_range": ks, "inertia": inertia, "silhouette": sil, "chosen_k": best_k, "silhouette_at_k": sil[best_k],
        "inertia_at_k": inertia[best_k], "n_wards": int(len(F)), "excluded_wards": excluded, "top_categories": top,
        "clusters": clusters, "min_complaints": min_complaints,
        "pca": [{"ward": w, "x": float(p[0]), "y": float(p[1]), "cluster": int(c)}
                for w, p, c in zip(F.index, pcs, F["cluster"])],
        "features": [c for c in F.columns if c != "cluster"],
    }
