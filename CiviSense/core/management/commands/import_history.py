import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ml.priority import DEFAULT_WEIGHTS
from core.models import Category, Complaint, Department, Ward
from core.services import log_audit, rescore_bulk
from ml.wrangling import AGENCY_FULL


class Command(BaseCommand):
    help = "Import the cleaned historical dataset into PostgreSQL (source = HISTORICAL)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete previously imported historical complaints first")

    @transaction.atomic
    def handle(self, *args, **opts):
        path = settings.CLEAN_DATASET
        if not path.exists():
            raise CommandError("Cleaned dataset missing. Run: python manage.py wrangle")
        existing = Complaint.objects.filter(source=Complaint.Source.HISTORICAL)
        if existing.exists():
            if not opts["reset"]:
                raise CommandError(f"{existing.count()} historical complaints already imported. Use --reset to re-import.")
            existing.delete()
        df = pd.read_csv(path, parse_dates=["created_at"], keep_default_na=True)
        self.stdout.write(f"Read {len(df)} cleaned records")

        for code in sorted(df["department"].dropna().unique()):
            Department.objects.get_or_create(code=code, defaults={"name": AGENCY_FULL.get(code, code)})
        dept = {d.code: d for d in Department.objects.all()}
        for wid, name in df.drop_duplicates("ward_id")[["ward_id", "ward_title"]].itertuples(index=False):
            Ward.objects.get_or_create(ward_id=int(wid), defaults={"name": name})
        ward = {w.ward_id: w for w in Ward.objects.all()}
        for cname in sorted(df["category"].unique()):
            sub = df[(df.category == cname) & df.department.notna()]
            default_dept = dept[sub["department"].mode().iloc[0]] if len(sub) else None
            Category.objects.get_or_create(name=cname, defaults={
                "default_department": default_dept, "severity_weight": DEFAULT_WEIGHTS.get(cname, 2)})
        cat = {c.name: c for c in Category.objects.all()}

        tz = timezone.get_current_timezone()
        objs = []
        for r in df.itertuples(index=False):
            objs.append(Complaint(
                ref=f"H-{int(r.hist_id):06d}", source=Complaint.Source.HISTORICAL, title=str(r.title)[:300],
                description=str(r.description), category=cat[r.category], sub_category=str(r.sub_category)[:200],
                original_category="" if pd.isna(r.original_category) else str(r.original_category)[:200],
                department=dept.get(r.department) if isinstance(r.department, str) else None,
                ward=ward[int(r.ward_id)], address=" ".join(x for x in [str(r.location), "" if pd.isna(r.address) else str(r.address)] if x and x != "nan"),
                latitude=None if pd.isna(r.latitude) else float(r.latitude),
                longitude=None if pd.isna(r.longitude) else float(r.longitude),
                status=r.status, comment_count=int(r.comment_count),
                created_at=r.created_at.to_pydatetime().replace(tzinfo=tz)))
        Complaint.objects.bulk_create(objs, batch_size=2000)
        n = rescore_bulk(Complaint.objects.filter(source=Complaint.Source.HISTORICAL))
        log_audit(None, "IMPORT_HISTORY", None, f"Imported {len(objs)} historical complaints")
        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(objs)} complaints, {Ward.objects.count()} wards, {Category.objects.count()} categories, "
            f"{Department.objects.count()} departments; priority computed for {n}."))
