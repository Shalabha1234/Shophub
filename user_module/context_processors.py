
from django.db.models import Sum

from products.models import CartItem
from user_module.models import Wishlist


def customer_nav_counts(request):
    cart_count = 0
    wishlist_count = 0

    if (
        request.user.is_authenticated
        and getattr(request.user, "role", None) == "CUSTOMER"
    ):
        cart_count = (
            CartItem.objects.filter(
                cart__user=request.user
            ).aggregate(total=Sum("quantity"))["total"] or 0
        )

        wishlist_count = Wishlist.objects.filter(
            user=request.user
        ).count()

    return {
        "cart_count": cart_count,
        "wishlist_count": wishlist_count,
    }
