from django.conf import settings
from django.core.management.base import BaseCommand

from ml.wrangling import wrangle


class Command(BaseCommand):
    help = "Run the data wrangling pipeline on the raw Bengaluru dataset and save the cleaned CSV + report."

    def handle(self, *args, **opts):
        raw = settings.RAW_DATASET
        if not raw.exists():
            self.stderr.write(f"Raw dataset not found at {raw}. Download bengaluru_civic_complaints_raw.csv from "
                              "https://github.com/sharmila-udaiyar/bengaluru-civic-complaints-analytics (data/raw/) "
                              "and place it in data/raw/.")
            return
        r = wrangle(raw, settings.CLEAN_DATASET, settings.WRANGLING_REPORT,
                    settings.CLEAN_DATASET.parent / "removed_exact_duplicates.csv", log=self.stdout.write)
        self.stdout.write(self.style.SUCCESS(
            f"Original {r['original_record_count']} -> cleaned {r['cleaned_record_count']} records; "
            f"{r['exact_duplicates_removed']} exact duplicates removed; categories {r['categories_before']} -> {r['categories_after']}."))
