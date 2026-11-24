from django.core.management.base import BaseCommand

from core.models import Department, User


class Command(BaseCommand):
    help = "Create local demo accounts (admin, one officer per major department, one citizen). For local testing only."

    def handle(self, *args, **opts):
        specs = [("admin", User.Role.ADMIN, None), ("citizen1", User.Role.CITIZEN, None)]
        for code in ("BBMP", "BESCOM", "BWSSB", "BTP"):
            specs.append((f"officer_{code.lower()}", User.Role.OFFICER, code))
        for username, role, dept in specs:
            u, created = User.objects.get_or_create(username=username, defaults={"role": role})
            u.role = role
            u.email = f"{username}@civisense.local"
            u.department = Department.objects.filter(code=dept).first() if dept else None
            u.is_superuser = role == User.Role.ADMIN
            u.set_password("CiviSense@123")
            u.save()
            self.stdout.write(f"{'created' if created else 'updated'}: {username} ({role}) password: CiviSense@123")
