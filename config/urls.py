"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""


from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from pathlib import Path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("products/", include("products.urls")),
    path("seller/", include("seller_module.urls")),
    path("customer/", include("user_module.urls")),
    path("custom-admin/", include("admin_module.urls")),
]

# Local development: serve media files normally.
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
else:
    # Demo deployment: serve only public image directories.
    public_media_dirs = [
        "products",
        "category_images",
        "seller_profiles",
        "store_logos",
    ]

    for media_dir in public_media_dirs:
        media_root = (settings.MEDIA_ROOT / media_dir).resolve()

        urlpatterns.append(
            re_path(
                rf"^media/{media_dir}/(?P<path>.*)$",
                serve,
                {
                    "document_root": str(media_root),
                    "show_indexes": False,
                },
            )
        )




