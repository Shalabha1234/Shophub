from django.urls import path
from .views import(
    seller_login,
    seller_logout,
    seller_register,
    seller_orders,
    seller_dashboard,
    seller_products,
    add_product, 
    edit_product,
    deactivate_product,
    activate_product,
    seller_inventory,
    seller_profile,
    business_details,
    verification_documents,
    delete_verification_document,
    replace_verification_document,
    bank_details,
    finance_overview,
    request_payout,
    seller_wallet,
    delete_product,
    seller_product_categories,
    seller_tax_audit_reports,
    seller_tax_report_excel,
    seller_audit_ledger_csv, 
      
)

urlpatterns = [
    path('login/', seller_login, name='seller_login'),
    path('register/', seller_register, name='seller_register'),
    path('orders/', seller_orders, name='seller_orders'),
    path('dashboard/', seller_dashboard, name='seller_dashboard'),
    path('products/', seller_products, name='seller_products'),
    path('products/add/', add_product, name='add_product'),
    path('products/<int:product_id>/edit/', edit_product, name='edit_product'),
    path('products/<int:product_id>/deactivate/', deactivate_product, name='deactivate_product'),
    path(
    'products/<int:product_id>/activate/',
    activate_product,
    name='activate_product'
    ),

    path(
        'products/<int:product_id>/delete/',
        delete_product,
        name='delete_product',
    ),
     path('inventory/', seller_inventory, name='seller_inventory'),
     path('logout/', seller_logout, name='seller_logout'),
     path('profile/', seller_profile, name='seller_profile'),
     path('business-details/',business_details,name='business_details'),
     path('verification-documents/', verification_documents, name = 'verification_documents'),
     path(
    'verification-documents/<int:document_id>/delete/',
    delete_verification_document,
    name='delete_verification_document',
     ),
     path(
    'verification-documents/<int:document_id>/replace/',
    replace_verification_document,
    name='replace_verification_document',
     ),
    path( 'bank-details/', 
        bank_details,
          name='bank_details',
     ),

     path(
    'finance/',
    finance_overview,
    name='finance_overview',
        ),

    path(
    'request-payout/',
    request_payout,
    name='request_payout'
),
     path(
    'wallet/',
    seller_wallet,
    name='seller_wallet'
),

path(
    'product-categories/',
    seller_product_categories,
    name='seller_product_categories',
),

path(
    'tax-audit-reports/',
    seller_tax_audit_reports,
    name='seller_tax_audit_reports',
),

path(
    'tax-audit-reports/export/<str:report_type>/',
    seller_tax_report_excel,
    name='seller_tax_report_excel',
),

path(
    'tax-audit-reports/audit-ledger.csv',
    seller_audit_ledger_csv,
    name='seller_audit_ledger_csv',
),



     
]