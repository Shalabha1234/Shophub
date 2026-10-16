from django.urls import path
from . import views

urlpatterns = [

    path('login/', views.admin_login, name='admin_login'),
    
    path('logout/', views.admin_logout, name='admin_logout'),


    path(
        '',
        views.admin_dashboard,
        name='admin_dashboard'
     ),

    path(
    'users/',
    views.admin_user_list,
    name='admin_user_list'
    ),

    path(
    'users/<int:user_id>/',
    views.admin_user_detail,
    name='admin_user_detail'
    ),
    path(
    'users/<int:user_id>/toggle-status/',
    views.admin_user_toggle_status,
    name='admin_user_toggle_status'
    ),

    path(
    'sellers/',
    views.admin_seller_list,
    name='admin_seller_list'
    ),
    path(
    'sellers/<int:seller_id>/',
    views.admin_seller_detail,
    name='admin_seller_detail'
    ),

    path(
    'sellers/<int:seller_id>/toggle-status/',
    views.admin_seller_toggle_status,
    name='admin_seller_toggle_status'
    ),
    path(
        'categories/',
        views.category_list,
        name='admin_category_list'
    ),

    path(
        'categories/add/',
        views.category_add,
        name='admin_category_add'
    ),

    path(
        'categories/<int:category_id>/edit/',
        views.category_edit,
        name='admin_category_edit'
    ),

    path(
        'categories/<int:category_id>/delete/',
        views.category_delete,
        name='admin_category_delete'
    ),

    path(
    'products/',
    views.admin_product_list,
    name='admin_product_list'
    ),

    path(
    'products/<int:product_id>/',
    views.admin_product_detail,
    name='admin_product_detail'
    ),

    path(
    'products/<int:product_id>/toggle-status/',
    views.admin_product_toggle_status,
    name='admin_product_toggle_status'
    ),

    path(
    'orders/',
    views.admin_order_list,
    name='admin_order_list'
    ),
    path(
    'orders/<int:order_id>/',
    views.admin_order_detail,
    name='admin_order_detail'
    ),
    path(
    'orders/<int:order_id>/update-status/',
    views.admin_order_update_status,
    name='admin_order_update_status'
    ),
    
  #payments
    path(
    'payments/',
    views.admin_payment_list,
    name='admin_payment_list'
    ),
    path(
    'payments/<int:payment_id>/',views.admin_payment_detail,name='admin_payment_detail'
    ),
    path('refunds/', views.admin_refund_list, name='admin_refund_list'),

    path(
    'refunds/<int:refund_id>/',
    views.admin_refund_detail,
    name='admin_refund_detail'
),

path(
    'refunds/<int:refund_id>/update-status/',
    views.admin_refund_update_status,
    name='admin_refund_update_status'
),

    path(
    'payouts/',
    views.admin_payout_list,
    name='admin_payout_list'
    ),

    path(
    'payouts/<int:payout_id>/',
    views.admin_payout_detail,
    name='admin_payout_detail'
    ),
    path(
    'payouts/<int:payout_id>/update-status/',
    views.admin_payout_update_status,
    name='admin_payout_update_status'
    ),
    path(
    'profile/',
    views.admin_profile,
    name='admin_profile'
    ),
    



]