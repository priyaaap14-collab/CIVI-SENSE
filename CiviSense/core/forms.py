import os
import uuid

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError

from .kannada import detect_language
from .models import Category, Complaint, User, Ward

LAT_RANGE, LON_RANGE = (12.70, 13.30), (77.30, 77.90)
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_MB = 5


class BootstrapMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault("class", "form-select")
            elif isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", "form-check-input")
            elif not isinstance(w, forms.FileInput):
                w.attrs.setdefault("class", "form-control")
            else:
                w.attrs.setdefault("class", "form-control")


class RegistrationForm(BootstrapMixin, UserCreationForm):
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=60, required=True, label="Full name")
    phone = forms.RegexField(r"^[0-9+\- ]{7,15}$", required=False, error_messages={"invalid": "Enter a valid phone number."})

    class Meta:
        model = User
        fields = ("username", "first_name", "email", "phone", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this e-mail already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.CITIZEN  # self-registration can only create citizens
        if commit:
            user.save()
        return user


class ComplaintForm(BootstrapMixin, forms.ModelForm):
    category = forms.ModelChoiceField(queryset=Category.objects.all(), required=False,
                                      empty_label="Let CiviSense suggest a category",
                                      help_text="Optional - leave blank to use the suggested category.")
    ward = forms.ModelChoiceField(queryset=Ward.objects.all(), empty_label="Select your ward")
    severity = forms.TypedChoiceField(coerce=int, choices=[(1, "1 - Minor inconvenience"), (2, "2 - Noticeable"),
                                                           (3, "3 - Significant"), (4, "4 - Serious"),
                                                           (5, "5 - Urgent / hazardous")], initial=3,
                                      label="How severe is the problem?")
    public_impact = forms.TypedChoiceField(coerce=int, choices=[(1, "Only me / one household"), (2, "My street or neighbourhood"),
                                                                (3, "A wide area / many people")], initial=2,
                                           label="Who is affected?")
    latitude = forms.FloatField(required=False, widget=forms.HiddenInput)
    longitude = forms.FloatField(required=False, widget=forms.HiddenInput)
    # Voice / language metadata. The text itself is always stored exactly as the citizen wrote or confirmed it.
    original_language = forms.ChoiceField(choices=Complaint.Language.choices, required=False, widget=forms.HiddenInput)
    input_method = forms.ChoiceField(choices=[("TYPED", "Typed"), ("VOICE", "Voice (speech-to-text)")], required=False,
                                     widget=forms.HiddenInput)
    voice_reviewed = forms.BooleanField(required=False, label="I have read the recognised text and it is correct")

    class Meta:
        model = Complaint
        fields = ["title", "description", "category", "ward", "address", "severity", "public_impact",
                  "latitude", "longitude", "image", "original_language", "input_method"]
        widgets = {"description": forms.Textarea(attrs={"rows": 5}),
                   "address": forms.Textarea(attrs={"rows": 2, "placeholder": "Street, landmark, area"})}

    def clean_title(self):
        t = self.cleaned_data["title"].strip()
        if len(t) < 5:
            raise ValidationError("Please give a short but descriptive title (at least 5 characters).")
        return t

    def clean_description(self):
        d = self.cleaned_data["description"].strip()
        if len(d) < 15:
            raise ValidationError("Please describe the issue in at least 15 characters.")
        return d

    def clean_input_method(self):
        return self.cleaned_data.get("input_method") or Complaint.InputMethod.TYPED

    def clean(self):
        data = super().clean()
        lat, lon = data.get("latitude"), data.get("longitude")
        if (lat is None) != (lon is None):
            raise ValidationError("Provide both latitude and longitude, or neither.")
        if lat is not None and not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
            raise ValidationError("The selected location is outside the Bengaluru area.")
        # Language: trust the characters actually written (a citizen may speak Kannada but correct it in English).
        text = f"{data.get('title', '')} {data.get('description', '')}"
        data["original_language"] = detect_language(text)
        # Never submit speech-to-text output the citizen has not read and confirmed.
        if data.get("input_method") == Complaint.InputMethod.VOICE and not data.get("voice_reviewed"):
            self.add_error("description", "Please tick the box to confirm you have checked the text recognised from your voice.")
        return data

    def clean_image(self):
        img = self.cleaned_data.get("image")
        if img and hasattr(img, "content_type"):
            ext = os.path.splitext(img.name)[1].lower()
            if ext not in ALLOWED_EXT or img.content_type not in ALLOWED_TYPES:
                raise ValidationError("Only JPG, PNG or WEBP images are allowed.")
            if img.size > MAX_IMAGE_MB * 1024 * 1024:
                raise ValidationError(f"Image must be smaller than {MAX_IMAGE_MB} MB.")
            img.name = f"{uuid.uuid4().hex}{ext}"  # never trust the client file name
        return img


class StatusForm(BootstrapMixin, forms.Form):
    status = forms.ChoiceField(choices=Complaint.Status.choices)
    remarks = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    resolution_details = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False,
                                         label="Resolution details")

    def clean(self):
        d = super().clean()
        if d.get("status") in ("Resolved", "Closed") and not d.get("resolution_details", "").strip():
            self.add_error("resolution_details", "Record how the complaint was resolved.")
        if d.get("status") == "Rejected" and not d.get("remarks", "").strip():
            self.add_error("remarks", "Add a remark explaining the rejection.")
        return d


class AssignForm(BootstrapMixin, forms.Form):
    officer = forms.ModelChoiceField(queryset=User.objects.filter(role=User.Role.OFFICER, is_active=True))


class OverrideForm(BootstrapMixin, forms.Form):
    level = forms.ChoiceField(choices=[("", "Use system recommendation")] + Complaint.Priority.choices, required=False)
    reason = forms.CharField(max_length=300, required=False)

    def clean(self):
        d = super().clean()
        if d.get("level") and not d.get("reason", "").strip():
            self.add_error("reason", "A reason is required when overriding the recommendation.")
        return d


class CategoryCorrectionForm(BootstrapMixin, forms.Form):
    category = forms.ModelChoiceField(queryset=Category.objects.all())
