from django.conf import settings
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.generic import RedirectView
from django.views.static import serve as media_serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('qrapp/', include('qrapp.urls')),
    path('', RedirectView.as_view(url='/qrapp/login/', permanent=False)),
    # Uploaded event pictures must also serve when DEBUG=False (event-day
    # hardening). Uploads are staff-only and the files are non-sensitive,
    # so the static-style media view is acceptable on the LAN deployment.
    re_path(r'^media/(?P<path>.*)$', media_serve, {'document_root': settings.MEDIA_ROOT}),
]
