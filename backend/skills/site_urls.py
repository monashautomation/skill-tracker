"""Website routes (mounted at / in config/urls.py)."""
from django.urls import path

from . import auth_authentik, site_api, site_views as v


urlpatterns = [
    path("login/", v.login_page, name="login"),
    path("auth/login/", auth_authentik.login, name="auth_login"),
    path("auth/callback/", auth_authentik.callback, name="auth_callback"),
    path("auth/logout/", auth_authentik.logout, name="logout"),
    # --- UI PAGE ROUTES GO BELOW THIS LINE ---------------------------------
    path("", v.roster, name="roster"),
    path("people/<int:pk>/", v.profile, name="profile"),
    path("people/<int:pk>/photo/", v.photo, name="photo"),
    path("people/<int:pk>/skills/", v.skill_save, name="skill_save"),
    path("people/<int:pk>/skills/<int:skill_id>/remove/", v.skill_remove, name="skill_remove"),
    path("people/<int:pk>/ratings/", v.ratings_save, name="ratings_save"),
    path("skills/", v.skills_map, name="skills_map"),
    path("points/", v.points, name="points"),
    path("points/submit/", v.points_submit, name="points_submit"),
    path("points/award/", v.points_award, name="points_award"),
    path("points/<int:pk>/review/", v.points_review, name="points_review"),
    path("inductions/", v.inductions, name="inductions"),
    path("inductions/request/", v.induction_request, name="induction_request"),
    path("inductions/<int:pk>/review/", v.induction_review, name="induction_review"),
    path("directory/sync/", v.directory_sync_now, name="directory_sync_now"),
    path("api-keys/", site_api.api_keys, name="api_keys"),
    path("api-keys/create/", site_api.api_key_create, name="api_key_create"),
    path("api-keys/<int:pk>/revoke/", site_api.api_key_revoke, name="api_key_revoke"),
]
