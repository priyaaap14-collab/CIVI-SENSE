"""Pure map helpers (numpy/pandas only, no Django) so they can be tested against the real dataset anywhere."""
import numpy as np
import pandas as pd

BLR_BOUNDS = (12.70, 13.30, 77.30, 77.90)       # south, north, west, east - the same box the complaint form enforces
FAR_FROM_WARD_KM = 6.0                          # flag (never move!) pins this far from their ward's typical area


def inside_bounds(df: pd.DataFrame) -> pd.Series:
    s, n, w, e = BLR_BOUNDS
    return df.latitude.between(s, n) & df.longitude.between(w, e)


def build_points(df: pd.DataFrame) -> list:
    """Group complaints stored at exactly the same coordinate (5 decimals, ~1 m) into ONE map point.

    Needs columns: latitude, longitude, label (DBSCAN cluster, -1 = noise), far_km, ref, category, status, source.
    Coordinates are reported exactly as stored - nothing is jittered or moved.
    Point = [lat, lon, cluster label, n complaints here, ref (only if n == 1), category, status, source initial, km from ward area or None]
    """
    if df.empty:
        return []
    df = df.assign(_lat=df.latitude.round(5), _lon=df.longitude.round(5))   # round ONCE: the key and the output must agree
    pts = []
    for _k, grp in df.groupby(["_lat", "_lon"], sort=False):
        first = grp.iloc[0]
        far = grp.far_km.max()
        pts.append([float(first._lat), float(first._lon), int(first.label), int(len(grp)),
                    first.ref if len(grp) == 1 else None,
                    first.category if grp.category.nunique() == 1 else "Mixed categories",
                    first.status if grp.status.nunique() == 1 else "Mixed", str(first.source)[0],
                    None if pd.isna(far) or far < FAR_FROM_WARD_KM else round(float(far), 1)])
    return pts


def far_from_ward_km(df: pd.DataFrame, centroids: dict) -> pd.Series:
    """km between each pin and the median location of its own ward (`centroids`: ward_pk -> {'lat','lon'}). NaN if unknown."""
    clat = df.ward_pk.map(lambda w: centroids.get(w, {}).get("lat", np.nan)).astype(float)
    clon = df.ward_pk.map(lambda w: centroids.get(w, {}).get("lon", np.nan)).astype(float)
    ok = clat.notna()
    out = pd.Series(np.nan, index=df.index)
    if ok.any():
        a, b, c, d = (np.radians(x.to_numpy()) for x in (df.latitude[ok], df.longitude[ok], clat[ok], clon[ok]))
        h = np.sin((c - a) / 2) ** 2 + np.cos(a) * np.cos(c) * np.sin((d - b) / 2) ** 2
        out[ok] = 2 * 6371.0088 * np.arcsin(np.sqrt(h))
    return out
