from django.contrib import admin
from .models import(
Product,
Category,
Inventory, 
Cart,
CartItem,
Order,
OrderItem,
OrderAddress,
Payment,
Shipment,
Payout,
Transaction
)
# Register your models here.

admin.site.register(Product)
admin.site.register(Category)
admin.site.register(Inventory)
admin.site.register(Cart)
admin.site.register(CartItem)
admin.site.register(Order)
admin.site.register(OrderItem)
admin.site.register(OrderAddress)
admin.site.register(Payment)
admin.site.register(Shipment)
admin.site.register(Payout)
admin.site.register(Transaction)