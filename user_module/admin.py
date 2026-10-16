from django.contrib import admin
from .models import User, CustomerProfile, SellerProfile

admin.site.register(User)
admin.site.register(CustomerProfile)
admin.site.register(SellerProfile)
