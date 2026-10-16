from django.urls import path
from .views import checkout, payment, my_orders, order_detail

urlpatterns = [
    path('checkout/', checkout, name='checkout'),
    path('payment/<int:order_id>/', payment, name='payment'),
    path('orders/', my_orders, name='my_orders'),
    path('orders/<int:order_id>/',order_detail,name='order_detail'),
]