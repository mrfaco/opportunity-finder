from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import include, path


def root(_request):
    return HttpResponseRedirect("/admin/")


urlpatterns = [
    path("", root),
    path("admin/", admin.site.urls),
    path("api/v1/", include("api.urls")),
]
