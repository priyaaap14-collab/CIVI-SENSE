import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Complaint, DuplicateSuggestion
from core.services import rescore_bulk
from ml import mining
from ml.wrangling import clean_text


class Command(BaseCommand):
    help = ("Train the classifier (TF-IDF + Logistic Regression), build the duplicate index (TF-IDF + cosine), "
            "evaluate both, mine suspected duplicate pairs among historical records and refresh priority scores.")

    def handle(self, *args, **opts):
        hist = Complaint.objects.filter(source=Complaint.Source.HISTORICAL).select_related("category")
        if not hist.exists():
            raise CommandError("No historical complaints in the database. Run: python manage.py import_history")
        rows = list(hist.values_list("pk", "ref", "title", "description", "category__name", "latitude", "longitude", "created_at"))
        df = pd.DataFrame(rows, columns=["pk", "ref", "title", "description", "category", "latitude", "longitude", "created_at"])
        df["text_clean"] = (df["title"] + " " + df["description"]).map(clean_text)
        df["ts"] = df["created_at"].map(lambda d: d.timestamp() / 86400.0)

        self.stdout.write("1/4 Training classifier ...")
        lab = df[df["category"] != "Uncategorised"]
        # human-corrected live complaints are trusted labels and join the training data
        corr = Complaint.objects.filter(source="LIVE", category_corrected=True).exclude(category=None)
        if corr.exists():
            extra = pd.DataFrame([(f"{c.title} {c.description}", c.category.name) for c in corr], columns=["raw", "category"])
            extra["text_clean"] = extra["raw"].map(clean_text)
            lab = pd.concat([lab, extra[["text_clean", "category"]]], ignore_index=True)
            self.stdout.write(f"   including {len(extra)} human-corrected live complaints")
        m = mining.train_classifier(lab["text_clean"], lab["category"], log=self.stdout.write)
        self.stdout.write(self.style.SUCCESS(
            f"   accuracy={m['accuracy']:.3f} macro-F1={m['macro_f1']:.3f} macro-precision={m['macro_precision']:.3f} "
            f"macro-recall={m['macro_recall']:.3f} ({m['n_classes']} classes, {m['n_test']} test records)"))

        self.stdout.write("2/4 Building duplicate index and evaluating similarity threshold ...")
        _, X, ev = mining.build_duplicate_index(df, log=self.stdout.write)
        self.stdout.write(f"   best-F1 threshold={ev['best_f1_threshold']}, operating threshold={ev['operating_threshold']}")

        self.stdout.write("3/4 Mining suspected duplicate pairs among historical complaints ...")
        pairs = mining.mine_historical_pairs(ev["operating_threshold"], log=lambda s: None)
        with transaction.atomic():
            DuplicateSuggestion.objects.filter(complaint__source="HISTORICAL", other__source="HISTORICAL",
                                               state=DuplicateSuggestion.State.SUGGESTED).delete()
            DuplicateSuggestion.objects.bulk_create(
                [DuplicateSuggestion(complaint_id=a, other_id=b, text_similarity=c, combined_score=comb,
                                     distance_m=d, days_apart=days) for a, b, c, comb, d, days in pairs],
                batch_size=2000, ignore_conflicts=True)
            counts = {}
            for a, b, *_ in pairs:
                counts[a] = counts.get(a, 0) + 1
                counts[b] = counts.get(b, 0) + 1
            Complaint.objects.filter(source="HISTORICAL").update(related_count=0)
            upd = list(Complaint.objects.filter(pk__in=counts.keys()))
            for c in upd:
                c.related_count = counts[c.pk]
            Complaint.objects.bulk_update(upd, ["related_count"], batch_size=2000)
        mining.save_results({"duplicate_mining": {"pairs_found": len(pairs), "complaints_involved": len(counts),
                                                  "threshold": ev["operating_threshold"]}})
        self.stdout.write(self.style.SUCCESS(f"   {len(pairs)} suspected pairs involving {len(counts)} complaints (nothing merged or deleted)"))

        self.stdout.write("4/4 Recomputing priority recommendations ...")
        n = rescore_bulk(Complaint.objects.all())
        self.stdout.write(self.style.SUCCESS(f"Done. Priority refreshed for {n} complaints."))
