"""Map data-shaping tests against the REAL dataset coordinates (data/raw CSV) - no Django needed.
The shipped Django test `core.tests_assistant` additionally exercises the same helpers through the real API/database."""
import os, sys, unittest
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.geo_utils import BLR_BOUNDS, build_points, far_from_ward_km, inside_bounds

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw", "bengaluru_civic_complaints_raw.csv")


@unittest.skipUnless(os.path.exists(CSV), "dataset not present")
class RealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = pd.read_csv(CSV, encoding="cp1252")
        df = pd.DataFrame({"ref": [f"H-{i}" for i in range(len(raw))], "latitude": raw.latitude, "longitude": raw.longitude,
                           "category": raw.iloc[:, [i for i, c in enumerate(raw.columns) if "categ" in c.lower()][0]].fillna("Uncategorised"),
                           "ward_pk": raw.ward_id, "status": "Open", "source": "HISTORICAL"})
        df = df.dropna(subset=["latitude", "longitude"])
        cls.raw_count = len(df)
        df = df[inside_bounds(df)].reset_index(drop=True)
        from sklearn.cluster import DBSCAN
        rad = np.radians(df[["latitude", "longitude"]].to_numpy())
        df["label"] = DBSCAN(eps=300 / 6371008.8, min_samples=15, metric="haversine", algorithm="ball_tree").fit_predict(rad)
        med = df.groupby("ward_pk")[["latitude", "longitude"]].median()
        cents = {int(k): {"lat": r.latitude, "lon": r.longitude} for k, r in med.iterrows()}
        df["far_km"] = far_from_ward_km(df, cents)
        cls.df, cls.pts = df, build_points(df)

    def test_every_complaint_is_accounted_for(self):
        self.assertEqual(sum(p[3] for p in self.pts), len(self.df))
        self.assertGreater(len(self.df), 15000)

    def test_marker_coordinates_are_real_stored_coordinates(self):
        real = set(zip(self.df.latitude.round(5).astype(float), self.df.longitude.round(5).astype(float)))
        for p in self.pts:
            self.assertIn((p[0], p[1]), real)
            s, n, w, e = BLR_BOUNDS
            self.assertTrue(s <= p[0] <= n and w <= p[1] <= e)

    def test_stacked_coordinates_are_grouped_not_overlapped(self):
        stacked = [p for p in self.pts if p[3] > 1]
        self.assertTrue(stacked, "the dataset has many complaints at identical coordinates")
        self.assertEqual(len({(p[0], p[1]) for p in self.pts}), len(self.pts), "one marker per distinct coordinate")
        print(f"\n  {len(self.df)} complaints -> {len(self.pts)} markers; {len(stacked)} markers hold >1 complaint; max stack {max(p[3] for p in self.pts)}")

    def test_cluster_labels_come_from_real_dbscan(self):
        labels = {p[2] for p in self.pts}
        self.assertTrue(any(l >= 0 for l in labels))
        self.assertTrue(-1 in labels)

    def test_far_flags_exist_and_never_move_points(self):
        flagged = [p for p in self.pts if p[8]]
        self.assertTrue(flagged)
        self.assertTrue(all(p[8] >= 6.0 for p in flagged))
        print(f"  {len(flagged)} markers flagged as >=6 km from their ward's usual area (shown at their stored position)")

    def test_empty_and_missing_coordinates(self):
        self.assertEqual(build_points(self.df.iloc[0:0]), [])
        bad = pd.DataFrame({"latitude": [None, 55.0, 12.97], "longitude": [77.5, 77.5, 77.59]})
        self.assertEqual(inside_bounds(bad).tolist(), [False, False, True])


if __name__ == "__main__":
    unittest.main()
