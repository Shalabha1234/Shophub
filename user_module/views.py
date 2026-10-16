from datetime import timedelta
from urllib.parse import urlencode

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.core.validators import validate_email
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.contrib import messages
from django.db.models import Avg, Count, F, Q
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from products.models import (
    Category,
    Product,
    Cart,
    CartItem,
    Order,
    OrderItem,
    OrderAddress,
    Payment,
    Inventory,
    Refund,
    Review,
    Shipment,
    Store,
)
from products.services import cancel_order

from .decorators import customer_required

from .models import (
    User,
    Wishlist,
    CustomerAddress,
)


# ============================================================
# CUSTOMER DASHBOARD
# ============================================================


@customer_required
def customer_dashboard(request):

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'change_password':

            current_password = request.POST.get(
                'current_password', ''
            )
            new_password = request.POST.get(
                'new_password', ''
            )
            confirm_password = request.POST.get(
                'confirm_password', ''
            )

            if not request.user.check_password(current_password):
                messages.error(
                    request,
                    'Your current password is incorrect.'
                )

            elif new_password != confirm_password:
                messages.error(
                    request,
                    'The new passwords do not match.'
                )

            elif current_password == new_password:
                messages.error(
                    request,
                    'Your new password must be different from your current password.'
                )

            else:
                try:
                    validate_password(
                        new_password,
                        user=request.user
                    )

                    request.user.set_password(new_password)
                    request.user.save()

                    update_session_auth_hash(
                        request,
                        request.user
                    )

                    messages.success(
                        request,
                        'Your password has been changed successfully.'
                    )

                    return redirect('customer_dashboard')

                except ValidationError as error:
                    for message in error.messages:
                        messages.error(request, message)

    recent_orders = Order.objects.filter(
        user=request.user
    ).order_by('-created_at')[:3]

    return render(
        request,
        'customer_dashboard.html',
        {
            'customer': request.user,
            'recent_orders': recent_orders,
        }
    )


def customer_logout(request):

    logout(request)

    return redirect('customer_login')


# ============================================================
# CUSTOMER HOME
# ============================================================

def customer_home(request):

    categories = Category.objects.all()

    # Search submitted from the navbar.
    search_query = request.GET.get('q', '').strip()

    if search_query:
        return redirect(
            reverse('customer_product_list')
            + '?'
            + urlencode({'q': search_query})
        )

    products = Product.objects.filter(
        status='ACTIVE'
    ).select_related(
        'inventory',
        'store',
        'category'
    ).order_by('-id')

    # 12 products per page
    products = Paginator(products, 12).get_page(
        request.GET.get('page')
    )

    wishlist_product_ids = []

    if request.user.is_authenticated:
        wishlist_product_ids = list(
            Wishlist.objects.filter(
                user=request.user
            ).values_list('product_id', flat=True)
        )

    return render(
        request,
        'customer_home.html',
        {
            'categories': categories,
            'products': products,
            'wishlist_product_ids': wishlist_product_ids,
        }
    )


# ============================================================
# CUSTOMER PRODUCT LIST (SEARCH RESULTS)
# ============================================================

def customer_product_list(request):

    search_query = request.GET.get('q', '').strip()
    selected_category = request.GET.get('category', '')
    selected_store = request.GET.get('store', '')
    sort_by = request.GET.get('sort', 'newest')

    # Get active products
    products = Product.objects.filter(
        status='ACTIVE'
    ).select_related(
        'category',
        'inventory',
        'store'
    )

    # --------------------------------------------------------
    # SEARCH BY PRODUCT NAME AND DESCRIPTION
    # Match products containing ANY search keyword
    # --------------------------------------------------------

    if search_query:

        search_terms = search_query.split()

        search_filter = Q()

        for term in search_terms:

            search_filter |= (
                Q(name__icontains=term)
                | Q(description__icontains=term)
            )

        products = products.filter(
            search_filter
        ).distinct()

    # --------------------------------------------------------
    # FILTER BY CATEGORY
    # --------------------------------------------------------

    if selected_category.isdigit():

        products = products.filter(
            category_id=selected_category
        )

    # Filter by seller store when the customer clicks View Shop.
    if selected_store.isdigit():
        products = products.filter(
            store_id=selected_store
        )

    # --------------------------------------------------------
    # SORT PRODUCTS
    # --------------------------------------------------------

    ordering = {
        'newest': '-id',
        'name': 'name',
        'price_low': 'price',
        'price_high': '-price',
    }

    products = products.order_by(
        ordering.get(sort_by, '-id')
    )

    # --------------------------------------------------------
    # DISPLAY SEARCH RESULTS
    # --------------------------------------------------------

    return render(
        request,
        'customer_product_list.html',
        {
            'products': products,
            'categories': Category.objects.all(),
            'search_query': search_query,
            'selected_category': selected_category,
            'sort_by': sort_by,
            'selected_store': selected_store,
        }
    )


# ============================================================
# WISHLIST
# ============================================================

@customer_required
def wishlist(request):
    wishlist_items = Wishlist.objects.filter(
        user=request.user
    ).select_related('product')

    return render(
        request,
        'wishlist.html',
        {
            'wishlist_items': wishlist_items,
        }
    )


@require_POST
def toggle_wishlist(request, product_id):
    if not request.user.is_authenticated:
        return JsonResponse(
            {
                'success': False,
                'message': 'Please log in to use your wishlist.',
            },
            status=401,
        )

    if request.user.role != 'CUSTOMER':
        return JsonResponse(
            {
                'success': False,
                'message': 'Only customers can use the wishlist.',
            },
            status=403,
        )

    product = get_object_or_404(
        Product,
        id=product_id,
        status='ACTIVE',
    )

    wishlist_item = Wishlist.objects.filter(
        user=request.user,
        product=product,
    ).first()

    if wishlist_item:
        wishlist_item.delete()
        added = False
    else:
        Wishlist.objects.get_or_create(
            user=request.user,
            product=product,
        )
        added = True

    return JsonResponse({
        'success': True,
        'added': added,
        'product_id': product.id,
        'wishlist_count': Wishlist.objects.filter(
            user=request.user
        ).count(),
    })


@customer_required
@require_POST
def add_to_wishlist(request, product_id):
    product = get_object_or_404(
        Product,
        id=product_id,
        status='ACTIVE',
    )

    Wishlist.objects.get_or_create(
        user=request.user,
        product=product,
    )

    return redirect(request.POST.get('next') or 'wishlist')


@customer_required
@require_POST
def remove_from_wishlist(request, product_id):
    Wishlist.objects.filter(
        user=request.user,
        product_id=product_id,
    ).delete()

    return redirect(request.POST.get('next') or 'wishlist')


# ============================================================
# CUSTOMER REGISTER
# ============================================================

def customer_register(request):

    if request.method == 'POST':

        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')

        error = None

        # 1. Valid email format
        try:
            validate_email(email)
        except ValidationError:
            error = 'Please enter a valid email address.'

        # 2. Email not already used (any role, any capitalisation)
        if error is None and User.objects.filter(
            email__iexact=email
        ).exists():
            error = 'An account with this email already exists.'

        # 3. Password strength (uses AUTH_PASSWORD_VALIDATORS in settings)
        if error is None:
            try:
                validate_password(password, user=User(email=email))
            except ValidationError as e:
                error = ' '.join(e.messages)

        # 4. Create the account
        if error is None:
            try:
                User.objects.create_user(
                    email=email,
                    password=password,
                    role='CUSTOMER'
                )
                return redirect('customer_login')
            except IntegrityError:
                # Two people registering the same email at once
                error = 'An account with this email already exists.'

        return render(
            request,
            'customer_register.html',
            {'error': error}
        )

    return render(
        request,
        'customer_register.html'
    )


# ============================================================
# CUSTOMER LOGIN
# ============================================================

def customer_login(request):

    if request.method == 'POST':

        email = request.POST.get('email')
        password = request.POST.get('password')

        user = authenticate(
            request,
            email=email,
            password=password
        )

        if user is not None and user.role == 'CUSTOMER':

            login(request, user)

            return redirect(
                'customer_dashboard'
            )

        return render(
            request,
            'customer_login.html',
            {
                'error': 'Invalid email or password.'
            }
        )

    return render(
        request,
        'customer_login.html'
    )


# ============================================================
# CUSTOMER PRODUCT DETAIL
# ============================================================

def customer_product_detail(request, product_id):

    product = get_object_or_404(
        Product.objects.select_related(
            'inventory',
            'category',
            'store'
        ).prefetch_related(
            'gallery_images'
        ),
        id=product_id,
        status='ACTIVE'
    )

    # --------------------------------------------------------
    # PRODUCT REVIEWS AND RATINGS
    # --------------------------------------------------------

    all_reviews = product.reviews.select_related('user')

    stats = all_reviews.aggregate(
        average=Avg('rating'),
        total=Count('id')
    )

    total_reviews = stats['total'] or 0

    average_rating = (
        round(stats['average'], 1)
        if total_reviews else None
    )

    counts = {
        row['rating']: row['n']
        for row in all_reviews.values('rating').annotate(
            n=Count('id')
        )
    }

    rating_breakdown = [
        {
            'stars': stars,
            'count': counts.get(stars, 0),
            'percent': (
                round(counts.get(stars, 0) * 100 / total_reviews)
                if total_reviews else 0
            ),
        }
        for stars in (5, 4, 3, 2, 1)
    ]

    reviews = list(all_reviews.order_by('-created_at')[:10])

    for review in reviews:
        review.display_name = review.user.email.split('@')[0]

    # --------------------------------------------------------
    # CAN THE CUSTOMER REVIEW THIS PRODUCT?
    # --------------------------------------------------------

    user_review = None
    can_review = False

    if request.user.is_authenticated:

        user_review = all_reviews.filter(
            user=request.user
        ).first()

        can_review = OrderItem.objects.filter(
            order__user=request.user,
            order__status='DELIVERED',
            product=product
        ).exists()

    # --------------------------------------------------------
    # STORE STATISTICS
    # Ratings are aggregated from reviews of this store's
    # products. They are not a separate seller-rating system.
    # --------------------------------------------------------

    store_product_count = 0
    store_average_rating = None
    store_rating_count = 0

    if product.store_id:

        store_product_count = Product.objects.filter(
            store_id=product.store_id,
            status='ACTIVE'
        ).count()

        store_stats = Review.objects.filter(
            product__store_id=product.store_id
        ).aggregate(
            average=Avg('rating'),
            total=Count('id')
        )

        store_rating_count = store_stats['total'] or 0

        if store_rating_count:
            store_average_rating = round(
                store_stats['average'], 1
            )

    # --------------------------------------------------------
    # RECOMMENDATIONS
    # ONLY active products in the current product's category.
    # Never fill missing slots with products from other
    # categories, even if they belong to the same store.
    # --------------------------------------------------------

    similar_products = []

    if product.category_id:

        similar_products = list(
            Product.objects.filter(
                status='ACTIVE',
                category_id=product.category_id
            ).exclude(
                id=product.id
            ).select_related(
                'inventory',
                'store',
                'category'
            ).annotate(
                avg_rating=Avg('reviews__rating'),
                review_count=Count('reviews', distinct=True)
            ).order_by(
                F('avg_rating').desc(nulls_last=True),
                '-id'
            )[:4]
        )

    for item in similar_products:
        item.stars = round(item.avg_rating or 0)

    return render(
        request,
        'customer_product_detail.html',
        {
            'product': product,
            'reviews': reviews,
            'total_reviews': total_reviews,
            'average_rating': average_rating,
            'average_stars': round(average_rating or 0),
            'rating_breakdown': rating_breakdown,
            'user_review': user_review,
            'can_review': can_review,
            'similar_products': similar_products,

            # Store card data
            'store_product_count': store_product_count,
            'store_average_rating': store_average_rating,
            'store_rating_count': store_rating_count,
        }
    )


# ============================================================
# ADD TO CART
# ============================================================

@customer_required
@require_POST
def add_to_cart(request, product_id):

    # A normal cart purchase replaces any unfinished Buy Now
    request.session.pop('buy_now_product_id', None)

    product = get_object_or_404(
        Product,
        id=product_id,
        status='ACTIVE'
    )

    inventory = get_object_or_404(
        Inventory,
        product=product
    )

    # Product is completely out of stock
    if inventory.quantity <= 0:

        return redirect(
            'customer_product_detail',
            product_id=product.id
        )

    cart, created = Cart.objects.get_or_create(
        user=request.user
    )

    cart_item, created = CartItem.objects.get_or_create(
        cart=cart,
        product=product
    )

    # Product already exists in cart
    if not created:

        # Do not allow quantity above available stock
        if cart_item.quantity >= inventory.quantity:

            return redirect(
                'customer_cart'
            )

        cart_item.quantity += 1
        cart_item.save()

    return redirect(
        'customer_cart'
    )


# ============================================================
# CUSTOMER CART
# ============================================================

@customer_required
def customer_cart(request):

    # Viewing the cart cancels any unfinished Buy Now
    request.session.pop('buy_now_product_id', None)

    cart, created = Cart.objects.get_or_create(
        user=request.user
    )

    cart_items = CartItem.objects.filter(
        cart=cart
    ).select_related(
        'product',
        'product__inventory'
    )

    cart_total = 0
    has_available_items = False

    for item in cart_items:

        if item.product.inventory.quantity <= 0:

            item.is_out_of_stock = True

        else:

            item.is_out_of_stock = False
            has_available_items = True

            item.subtotal = (
                item.product.price * item.quantity
            )

            cart_total += item.subtotal

    return render(
        request,
        'customer_cart.html',
        {
            'cart': cart,
            'cart_items': cart_items,
            'cart_total': cart_total,
            'has_available_items': has_available_items,
        }
    )


# ============================================================
# BUY NOW
# ============================================================

@customer_required
@require_POST
def buy_now(request, product_id):

    product = get_object_or_404(
        Product,
        id=product_id,
        status='ACTIVE'
    )

    inventory = get_object_or_404(
        Inventory,
        product=product
    )

    # Do not allow Buy Now for out-of-stock products
    if inventory.quantity <= 0:

        return redirect(
            'customer_product_detail',
            product_id=product.id
        )

    request.session['buy_now_product_id'] = product.id

    return redirect(
        'customer_checkout'
    )


# ============================================================
# CUSTOMER CHECKOUT
# ============================================================

@customer_required
def customer_checkout(request):

    cart, created = Cart.objects.get_or_create(
        user=request.user
    )

    # Check whether this is a Buy Now checkout.
    buy_now_product_id = request.session.get(
        'buy_now_product_id'
    )

    if buy_now_product_id:

        product = get_object_or_404(
            Product.objects.select_related('inventory'),
            id=buy_now_product_id,
            status='ACTIVE'
        )

        buy_now_item = CartItem(
            cart=cart,
            product=product,
            quantity=1
        )

        buy_now_item.subtotal = product.price

        cart_items = [buy_now_item]
        cart_total = product.price

    else:

        cart_items = list(
            CartItem.objects.filter(
                cart=cart
            ).select_related(
                'product',
                'product__inventory'
            )
        )

        cart_total = 0

        for item in cart_items:

            inventory = getattr(
                item.product,
                'inventory',
                None
            )

            available_quantity = (
                inventory.quantity if inventory else 0
            )

            item.is_out_of_stock = (
                available_quantity <= 0
            )

            item.insufficient_stock = (
                item.quantity > available_quantity
            )

            item.available_quantity = available_quantity

            item.subtotal = (
                item.product.price * item.quantity
            )

            cart_total += item.subtotal

    # Check stock availability.
    stock_error = None

    for item in cart_items:

        inventory = getattr(
            item.product,
            'inventory',
            None
        )

        available_quantity = (
            inventory.quantity if inventory else 0
        )

        if available_quantity < item.quantity:

            stock_error = (
                f"Sorry, only {available_quantity} unit(s) "
                f"of {item.product.name} are available, "
                f"but your cart has {item.quantity}."
            )

            break

    # Handle checkout form submissions.
    if request.method == 'POST':

        action = request.POST.get('action')

        # ----------------------------------------------------
        # SELECT DELIVERY ADDRESS
        # ----------------------------------------------------

        if action == 'select_address':

            if stock_error:

                messages.error(request, stock_error)

                return redirect('customer_checkout')

            selected_address_id = request.POST.get(
                'selected_address'
            )

            selected_address = get_object_or_404(
                CustomerAddress,
                id=selected_address_id,
                user=request.user
            )

            request.session['selected_address_id'] = (
                selected_address.id
            )

            return redirect('customer_payment')

        # ----------------------------------------------------
        # SAVE NEW ADDRESS
        # ----------------------------------------------------

        if action == 'save_address':

            address_type = request.POST.get(
                'address_type', 'HOME'
            )

            full_name = request.POST.get(
                'full_name', ''
            ).strip()

            phone = request.POST.get(
                'phone', ''
            ).strip()

            # These names must match CustomerAddress model.
            house_name = request.POST.get(
                'house_name', ''
            ).strip()

            street = request.POST.get(
                'street', ''
            ).strip()

            city = request.POST.get(
                'city', ''
            ).strip()

            state = request.POST.get(
                'state', ''
            ).strip()

            pincode = request.POST.get(
                'pincode', ''
            ).strip()

            is_default = (
                request.POST.get('is_default') == 'on'
            )

            # Validate required fields before saving.
            if not all([
                full_name,
                phone,
                house_name,
                street,
                city,
                state,
                pincode,
            ]):

                messages.error(
                    request,
                    'Please fill in all required address fields.'
                )

                return redirect('customer_checkout')

            has_addresses = CustomerAddress.objects.filter(
                user=request.user
            ).exists()

            # The first saved address becomes the default.
            if not has_addresses:
                is_default = True

            # Clear the previous default only after validation.
            if is_default:

                CustomerAddress.objects.filter(
                    user=request.user
                ).update(is_default=False)

            CustomerAddress.objects.create(
                user=request.user,
                address_type=address_type,
                full_name=full_name,
                phone=phone,
                house_name=house_name,
                street=street,
                city=city,
                state=state,
                pincode=pincode,
                is_default=is_default,
            )

            messages.success(
                request,
                'Your delivery address has been saved successfully.'
            )

            return redirect('customer_checkout')

        # ----------------------------------------------------
        # EDIT EXISTING ADDRESS
        # ----------------------------------------------------

        if action == 'edit_address':

            address_id = request.POST.get(
                'address_id'
            )

            address = get_object_or_404(
                CustomerAddress,
                id=address_id,
                user=request.user
            )

            address_type = request.POST.get(
                'address_type', 'HOME'
            )

            full_name = request.POST.get(
                'full_name', ''
            ).strip()

            phone = request.POST.get(
                'phone', ''
            ).strip()

            house_name = request.POST.get(
                'house_name', ''
            ).strip()

            street = request.POST.get(
                'street', ''
            ).strip()

            city = request.POST.get(
                'city', ''
            ).strip()

            state = request.POST.get(
                'state', ''
            ).strip()

            pincode = request.POST.get(
                'pincode', ''
            ).strip()

            is_default = (
                request.POST.get('is_default') == 'on'
            )

            # Validate before changing the existing address.
            if not all([
                full_name,
                phone,
                house_name,
                street,
                city,
                state,
                pincode,
            ]):

                messages.error(
                    request,
                    'Please fill in all required address fields.'
                )

                return redirect('customer_checkout')

            if is_default:

                CustomerAddress.objects.filter(
                    user=request.user
                ).exclude(
                    id=address.id
                ).update(
                    is_default=False
                )

            address.address_type = address_type
            address.full_name = full_name
            address.phone = phone
            address.house_name = house_name
            address.street = street
            address.city = city
            address.state = state
            address.pincode = pincode
            address.is_default = is_default

            address.save()

            messages.success(
                request,
                'Your delivery address has been updated successfully.'
            )

            return redirect('customer_checkout')

    # --------------------------------------------------------
    # LOAD SAVED ADDRESSES
    # --------------------------------------------------------

    addresses = CustomerAddress.objects.filter(
        user=request.user
    ).order_by('-is_default', '-created_at')

    selected_address_id = request.session.get(
        'selected_address_id'
    )

    return render(
        request,
        'customer_checkout.html',
        {
            'cart': cart,
            'cart_items': cart_items,
            'cart_total': cart_total,
            'addresses': addresses,
            'selected_address_id': selected_address_id,
            'stock_error': stock_error,
        }
    )


# ============================================================
# CUSTOMER PAYMENT
# ============================================================

@customer_required
def customer_payment(request):

    cart, created = Cart.objects.get_or_create(
        user=request.user
    )

    # ========================================================
    # CHECK IF THIS IS A BUY NOW CHECKOUT
    # ========================================================

    buy_now_product_id = request.session.get(
        'buy_now_product_id'
    )

    if buy_now_product_id:

        product = get_object_or_404(
            Product.objects.select_related('inventory'),
            id=buy_now_product_id,
            status='ACTIVE'
        )

        # Temporary item for Buy Now
        buy_now_item = CartItem(
            cart=cart,
            product=product,
            quantity=1
        )

        buy_now_item.subtotal = product.price

        cart_items = [buy_now_item]

        cart_total = product.price

    else:

        # ====================================================
        # NORMAL CART PAYMENT
        # Skips out-of-stock items, same as checkout
        # ====================================================

        cart_items = CartItem.objects.filter(
            cart=cart,
            product__inventory__quantity__gt=0
        ).select_related(
            'product',
            'product__inventory'
        )

        for item in cart_items:

            item.subtotal = (
                item.product.price * item.quantity
            )

        cart_total = sum(
            item.subtotal
            for item in cart_items
        )

    # Nothing to pay for (for example, the page was reloaded after
    # the order was already placed): go back to the cart instead of
    # creating an empty order.
    if not cart_items:
        return redirect('customer_cart')

    # ========================================================
    # GET SELECTED ADDRESS
    # ========================================================

    selected_address = CustomerAddress.objects.filter(
        id=request.session.get('selected_address_id'),
        user=request.user
    ).first()

    # No address chosen yet: send the customer back to checkout.
    if selected_address is None:
        return redirect('customer_checkout')

    # ========================================================
    # HANDLE PAYMENT FORM
    # ========================================================

    if request.method == 'POST':

        payment_method = request.POST.get(
            'payment_method'
        )

        # Only the payment methods the site offers are accepted.
        if payment_method not in ('UPI', 'CARD', 'COD'):

            return render(
                request,
                'customer_payment.html',
                {
                    'cart': cart,
                    'cart_items': cart_items,
                    'cart_total': cart_total,
                    'selected_address': selected_address,
                    'error': 'Please choose a payment method.',
                }
            )

        # ====================================================
        # CHECK STOCK BEFORE CREATING ORDER
        # ====================================================

        for item in cart_items:

            inventory = Inventory.objects.get(
                product=item.product
            )

            if inventory.quantity < item.quantity:

                return render(
                    request,
                    'customer_payment.html',
                    {
                        'cart': cart,
                        'cart_items': cart_items,
                        'cart_total': cart_total,
                        'selected_address': selected_address,
                        'error': (
                            f'Sorry, {item.product.name} '
                            f'does not have enough stock.'
                        ),
                    }
                )

        # ====================================================
        # CREATE ORDER
        # ====================================================

        try:

            with transaction.atomic():

                order = Order.objects.create(
                    user=request.user,
                    total_amount=cart_total,
                    status='PENDING'
                )

                # ============================================
                # CREATE ORDER ITEMS
                # ============================================

                for item in cart_items:

                    OrderItem.objects.create(
                        order=order,
                        product=item.product,
                        quantity=item.quantity,
                        price=item.product.price
                    )

                # ============================================
                # CREATE ORDER ADDRESS
                # ============================================

                OrderAddress.objects.create(
                    order=order,
                    full_name=selected_address.full_name,
                    phone=selected_address.phone,
                    address=(
                        selected_address.house_name
                        + ", "
                        + selected_address.street
                    ),
                    city=selected_address.city,
                    state=selected_address.state,
                    postal_code=selected_address.pincode,
                )

                # ============================================
                # CREATE PAYMENT (DEMO - always succeeds)
                # ============================================

                Payment.objects.create(
                    order=order,
                    amount=cart_total,
                    payment_method=payment_method,
                    transaction_id=f"DEMO-TXN-{order.id}",
                    status='SUCCESS'
                )

                # ============================================
                # UPDATE INVENTORY
                # ============================================

                for item in cart_items:

                    inventory = (
                        Inventory.objects
                        .select_for_update()
                        .get(
                            product=item.product
                        )
                    )

                    if inventory.quantity < item.quantity:

                        raise ValueError(
                            f'Not enough stock for '
                            f'{item.product.name}'
                        )

                    inventory.quantity -= item.quantity

                    inventory.save()

                # ============================================
                # CLEAR CART (normal cart purchases only;
                # Buy Now does not touch the cart)
                # ============================================

                if not buy_now_product_id:
                    cart_items.delete()

        except ValueError as e:

            return render(
                request,
                'customer_payment.html',
                {
                    'cart': cart,
                    'cart_items': cart_items,
                    'cart_total': cart_total,
                    'selected_address': selected_address,
                    'error': str(e),
                }
            )

        # ====================================================
        # CLEAR BUY NOW SESSION
        # ====================================================

        if buy_now_product_id:

            del request.session[
                'buy_now_product_id'
            ]

        # ====================================================
        # ORDER SUCCESS
        # ====================================================

        return redirect(
            'customer_order_success',
            order_id=order.id
        )

    # ========================================================
    # PAYMENT PAGE
    # ========================================================

    return render(
        request,
        'customer_payment.html',
        {
            'cart': cart,
            'cart_items': cart_items,
            'cart_total': cart_total,
            'selected_address': selected_address,
        }
    )


# ============================================================
# ORDER SUCCESS
# ============================================================

@customer_required
def customer_order_success(request, order_id):

    order = get_object_or_404(
        Order,
        id=order_id,
        user=request.user
    )

    return render(
        request,
        'customer_order_success.html',
        {
            'order': order,
        }
    )


# ============================================================
# CUSTOMER ORDERS
# ============================================================

@customer_required
def customer_orders(request):

    # Get orders belonging to the logged-in customer
    orders = Order.objects.filter(
        user=request.user
    ).prefetch_related(
        'orderitem_set__product'
    ).order_by('-created_at')

    # Get refund requests grouped by order ID
    refunds_by_order = {
        refund.order_id: refund
        for refund in Refund.objects.filter(
            order__user=request.user
        ).order_by('-requested_at')
    }

    for order in orders:
        order.refund = refunds_by_order.get(order.id)

    for order in orders:

        # Calculate each product's subtotal
        for item in order.orderitem_set.all():

            item.subtotal = (
                item.price * item.quantity
            )

        # Set the order tracking stage
        if order.status == 'PENDING':

            order.tracking_stage = 0

        elif order.status == 'CONFIRMED':

            order.tracking_stage = 1

        elif order.status == 'PROCESSING':

            order.tracking_stage = 2

        elif order.status in [
            'SHIPPED',
            'IN_TRANSIT'
        ]:

            order.tracking_stage = 3

        elif order.status == 'OUT_FOR_DELIVERY':

            order.tracking_stage = 4

        elif order.status == 'DELIVERED':

            order.tracking_stage = 5

        else:

            order.tracking_stage = 0

    # Display orders and refund information
    return render(
        request,
        'customer_orders.html',
        {
            'orders': orders,
            'refunds_by_order': refunds_by_order,
        }
    )


@customer_required
def customer_cancel_order(request, order_id):

    order = get_object_or_404(
        Order,
        id=order_id,
        user=request.user
    )

    if request.method == 'POST':

        # Shared helper: only cancels PENDING / CONFIRMED / PROCESSING
        # orders, puts the stock back and creates a refund for paid
        # UPI / Card orders. Everything happens together or not at all.
        cancel_order(order, 'Order cancelled by customer')

    return redirect('customer_orders')


@customer_required
@require_POST
def customer_return_order(request, order_id):

    order = get_object_or_404(
        Order,
        id=order_id,
        user=request.user
    )

    # Only delivered orders can be returned
    if order.status != 'DELIVERED':
        return redirect('customer_orders')

    # Find the shipment and its delivery date
    shipment = Shipment.objects.filter(
        order=order
    ).first()

    if not shipment or not shipment.delivered_at:
        return redirect('customer_orders')

    # Allow returns for 7 days from delivery
    return_deadline = shipment.delivered_at + timedelta(days=7)

    if timezone.now() > return_deadline:
        return redirect('customer_orders')

    # Find the original payment
    payment = Payment.objects.filter(
        order=order
    ).first()

    if not payment:
        return redirect('customer_orders')

    # Prevent duplicate return/refund requests
    existing_refund = Refund.objects.filter(
        order=order
    ).first()

    if existing_refund:
        return redirect('customer_orders')

    # Create a return/refund request for COD, UPI, or Card
    Refund.objects.create(
        order=order,
        payment=payment,
        amount=payment.amount,
        reason='Product return requested by customer'
    )

    return redirect('customer_orders')


# ============================================================
# STORE PAGE
# ============================================================

def customer_store_detail(request, store_id):
    store = get_object_or_404(
        Store,
        id=store_id,
        status='ACTIVE'
    )

    store_products = (
        Product.objects
        .filter(
            store=store,
            status='ACTIVE'
        )
        .select_related('category', 'inventory', 'store')
        .order_by('-id')
    )

    return render(
        request,
        'customer_store_detail.html',
        {
            'store': store,
            'products': store_products,
            'store_product_count': store_products.count(),
        }
    )


# ============================================================
# UPDATE CART QUANTITY
# ============================================================

@customer_required
@require_POST
def update_cart_quantity(request, item_id, action):

    cart_item = get_object_or_404(
        CartItem,
        id=item_id,
        cart__user=request.user
    )

    inventory = get_object_or_404(
        Inventory,
        product=cart_item.product
    )

    # ========================================================
    # INCREASE QUANTITY
    # ========================================================

    if action == 'increase':

        # Do not allow cart quantity
        # to exceed available stock
        if cart_item.quantity < inventory.quantity:

            cart_item.quantity += 1

            cart_item.save()

    # ========================================================
    # DECREASE QUANTITY
    # ========================================================

    elif action == 'decrease':

        if cart_item.quantity > 1:

            cart_item.quantity -= 1

            cart_item.save()

    return redirect(
        'customer_cart'
    )


# ============================================================
# REMOVE FROM CART
# ============================================================

@customer_required
@require_POST
def remove_from_cart(request, item_id):

    cart_item = get_object_or_404(
        CartItem,
        id=item_id,
        cart__user=request.user
    )

    cart_item.delete()

    return redirect(
        'customer_cart'
    )


# ============================================================
# SUBMIT REVIEW
# ============================================================

@customer_required
@require_POST
def submit_review(request, product_id):

    product = get_object_or_404(
        Product,
        id=product_id,
        status='ACTIVE'
    )

    detail_url = (
        reverse('customer_product_detail', args=[product.id])
        + '#reviews'
    )

    # Only customers who received the product can review it
    has_received = OrderItem.objects.filter(
        order__user=request.user,
        order__status='DELIVERED',
        product=product
    ).exists()

    if not has_received:

        messages.error(
            request,
            'You can review a product after it has been delivered.'
        )

        return redirect(detail_url)

    try:
        rating = int(request.POST.get('rating', ''))
    except ValueError:
        rating = 0

    if rating not in (1, 2, 3, 4, 5):

        messages.error(
            request,
            'Please choose a rating from 1 to 5 stars.'
        )

        return redirect(detail_url)

    comment = request.POST.get('comment', '').strip()[:1000]

    # One review per customer: submitting again updates it
    Review.objects.update_or_create(
        product=product,
        user=request.user,
        defaults={
            'rating': rating,
            'comment': comment,
        }
    )

    messages.success(
        request,
        'Thank you! Your review has been saved.'
    )

    return redirect(detail_url)