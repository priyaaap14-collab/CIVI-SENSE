from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (Assignment, AuditLog, Category, Complaint, Department, DuplicateGroup, DuplicateSuggestion,
                     Notification, StatusHistory, User, Ward)


@admin.register(User)
class CiviUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "department", "is_active")
    list_filter = ("role", "department", "is_active")
    fieldsets = UserAdmin.fieldsets + (("CiviSense", {"fields": ("role", "department", "phone")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("CiviSense", {"fields": ("role", "department", "email", "phone")}),)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "default_department", "severity_weight")
    list_editable = ("severity_weight",)
    search_fields = ("name",)


@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    list_display = ("ward_id", "name")
    search_fields = ("name",)


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = ("ref", "source", "title", "category", "ward", "status", "recommended_priority", "created_at")
    list_filter = ("source", "status", "recommended_priority", "department")
    search_fields = ("ref", "title", "description")
    raw_id_fields = ("citizen", "assigned_officer", "override_by", "duplicate_group", "predicted_category")
    list_select_related = ("category", "ward")


for m in (Assignment, StatusHistory, DuplicateGroup, Notification):
    admin.site.register(m)


@admin.register(DuplicateSuggestion)
class DuplicateSuggestionAdmin(admin.ModelAdmin):
    list_display = ("complaint", "other", "text_similarity", "combined_score", "state")
    list_filter = ("state",)
    raw_id_fields = ("complaint", "other", "reviewed_by")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "object_type", "object_id")
    list_filter = ("action",)
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False
