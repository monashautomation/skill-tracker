from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("skills.urls")),
    path("api/v1/", include("skills.urls")),
    path("", include("skills.site_urls")),
]
