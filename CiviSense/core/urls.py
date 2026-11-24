from django.urls import path

from . import views as v
from . import views_assistant as va

urlpatterns = [
    path("", v.home, name="home"),
    path("login/<str:role>/", v.role_login, name="login"),
    path("register/", v.register, name="register"),
    path("logout/", v.logout_view, name="logout"),
    path("notifications/read/", v.notifications_read, name="notifications_read"),
    # administrator
    path("dashboard/", v.dashboard, name="dashboard"),
    path("manage/complaints/", v.complaint_list, name="complaints"),
    path("geo/", v.geo_page, name="geo"),
    path("mining/", v.mining_page, name="mining"),
    # shared complaint detail
    path("complaint/<str:ref>/", v.complaint_detail, name="complaint_detail"),
    # citizen
    path("citizen/", v.citizen_home, name="citizen_home"),
    path("citizen/submit/", v.citizen_submit, name="citizen_submit"),
    # officer
    path("officer/", v.officer_home, name="officer_home"),
    # AI assistant (Claude via MCP) and complaint review
    path("assistant/", va.assistant_page, name="assistant"),
    path("api/assistant/message/", va.api_assistant_message, name="api_assistant_message"),
    path("api/assistant/decide/", va.api_assistant_decide, name="api_assistant_decide"),
    path("api/assistant/reset/", va.api_assistant_reset, name="api_assistant_reset"),
    path("api/complaint/analyze/", va.api_complaint_analyze, name="api_complaint_analyze"),
    # JSON APIs
    path("api/suggest-category/", v.api_suggest_category, name="api_suggest"),
    path("api/ward-from-point/", v.api_ward_from_point, name="api_ward"),
    path("api/geo/", v.api_geo, name="api_geo"),
    path("api/geo/point/", v.api_geo_point, name="api_geo_point"),
]
