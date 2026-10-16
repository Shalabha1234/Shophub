
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth import (
    authenticate,
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum, Count, Q
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from products.models import (
    OrderItem,
    Order,
    Product,
    ProductImage,
    Category,
    Inventory,
    Payout,
    Transaction,
    Refund,
    Shipment,
)
from products.services import cancel_order, record_delivery

from user_module.models import User, SellerProfile

from seller_module.decorators import seller_required
from seller_module.models import (
    Store,
    VerificationDocument,
    BankDetails,
)



def seller_login(request):

    if request.method == 'POST':

        email = request.POST.get('email')
        password = request.POST.get('password')

        user = authenticate(
            request,
            email=email,
            password=password
        )

        if user is not None and user.role == 'SELLER':
            login(request, user)
            return redirect('seller_dashboard')

        return render(
            request,
            'seller_login.html',
            {
                'error': 'Invalid email or password.'
            }
        )

    return render(
        request,
        'seller_login.html'
    )


def seller_register(request):

    if request.method == 'POST':

        email = request.POST.get('email')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        store_name = request.POST.get('store_name')

        if password != confirm_password:
            return render(
                request,
                'seller_register.html',
                {
                    'error': 'Passwords do not match.'
                }
            )

        if len(password) < 8:
            return render(
                request,
                'seller_register.html',
                {
                    'error': 'Password must be at least 8 characters long.'
                }
            )

        if User.objects.filter(email=email).exists():
            return render(
                request,
                'seller_register.html',
                {
                    'error': 'An account with this email already exists.'
                }
            )

        user = User.objects.create_user(
            email=email,
            password=password,
            role='SELLER'
        )

        SellerProfile.objects.create(
            user=user
        )

        Store.objects.create(
            seller=user.sellerprofile,
            store_name=store_name
        )

        return redirect('seller_login')

    return render(
        request,
        'seller_register.html'
    )


def seller_logout(request):

    logout(request)

    return redirect('seller_login')


@seller_required
def seller_dashboard(request):

    store = request.user.sellerprofile.store

    products_count = Product.objects.filter(
        store=store
    ).count()

    order_items = OrderItem.objects.filter(
        product__store=store
    )

    orders_count = order_items.values(
        'order'
    ).distinct().count()

    pending_orders = order_items.filter(
        order__status='PENDING'
    ).values(
        'order'
    ).distinct().count()

    low_stock_count = Inventory.objects.filter(
        product__store=store,
        quantity__lte=models.F('low_stock_limit')
    ).count()

    revenue = sum(
        item.price * item.quantity
        for item in order_items
        if item.order.status != 'CANCELLED'
    )

    return render(
        request,
        'seller_dashboard.html',
        {
            'products_count': products_count,
            'orders_count': orders_count,
            'pending_orders': pending_orders,
            'revenue': revenue,
            'low_stock_count': low_stock_count,
        }
    )


# ============================================================
# SELLER ORDERS
# ============================================================

STATUS_ORDER = [
    'PENDING',
    'CONFIRMED',
    'PROCESSING',
    'SHIPPED',
    'IN_TRANSIT',
    'OUT_FOR_DELIVERY',
    'DELIVERED',
]






def _update_order_status(request, store):
    """Update this seller's shipment without prematurely delivering
    the complete customer order."""

    order_id = request.POST.get('order_id', '')
    new_status = request.POST.get('status', '')

    if not order_id.isdigit():
        messages.error(request, 'Invalid order.')
        return

    if new_status not in dict(Order.STATUS_CHOICES):
        messages.error(request, 'Invalid order status.')
        return

    order = get_object_or_404(
        Order.objects.filter(
            orderitem__product__store=store
        ).distinct(),
        id=order_id,
    )

    if order.status == 'CANCELLED':
        messages.error(request, 'A cancelled order cannot be changed.')
        return

    # Cancellation retains the existing whole-order rules.
    if new_status == 'CANCELLED':
        if cancel_order(order, 'Order cancelled by seller'):
            messages.success(
                request,
                f'Order #{order.id} was cancelled.',
            )
        else:
            messages.error(
                request,
                'This order can no longer be cancelled.',
            )
        return

    # Identify every store that has items in this order.
    seller_stores = {
        item.product.store
        for item in OrderItem.objects.filter(
            order=order
        ).select_related('product', 'product__store')
        if item.product.store is not None
    }

    if store not in seller_stores:
        messages.error(request, 'This order does not belong to your store.')
        return

    with transaction.atomic():
        # Ensure every seller has their own shipment.
        # Existing order progress is used when initializing shipments,
        # so existing orders do not automatically reset to PENDING.
        initial_status = (
            order.status
            if order.status in STATUS_ORDER
            else 'PENDING'
        )

        for seller_store in seller_stores:
            Shipment.objects.get_or_create(
                order=order,
                seller=seller_store,
                defaults={'status': initial_status},
            )

        shipment = get_object_or_404(
            Shipment.objects.select_for_update(),
            order=order,
            seller=store,
        )

        current_status = shipment.status

        if current_status not in STATUS_ORDER:
            messages.error(
                request,
                'This shipment has a status that cannot be updated here.',
            )
            return

        if new_status == current_status:
            messages.info(request, 'Your shipment already has this status.')
            return

        # Preserve the existing forward-only status progression.
        if (
            new_status in STATUS_ORDER
            and STATUS_ORDER.index(new_status)
            < STATUS_ORDER.index(current_status)
        ):
            messages.error(
                request,
                'A shipment cannot move back to an earlier status.',
            )
            return

        if new_status == 'DELIVERED':
            # This records only this seller's delivery and ledger entries.
            record_delivery(order, store=store)
        else:
            shipment.status = new_status

            if new_status == 'SHIPPED' and not shipment.shipped_at:
                shipment.shipped_at = timezone.now()

            shipment.save()

        # Derive the overall order status from the least advanced
        # seller shipment. DELIVERED requires every seller to be delivered.
        shipments = list(
            Shipment.objects.filter(
                order=order,
                seller__in=seller_stores,
            )
        )

        all_delivered = (
            len(shipments) == len(seller_stores)
            and all(s.status == 'DELIVERED' for s in shipments)
        )

        if all_delivered:
            order.status = 'DELIVERED'
        else:
            valid_statuses = [
                s.status for s in shipments
                if s.status in STATUS_ORDER
            ]

            if valid_statuses:
                order.status = min(
                    valid_statuses,
                    key=STATUS_ORDER.index,
                )

        order.save(update_fields=['status', 'updated_at'])

    messages.success(
        request,
        f'Your shipment for Order #{order.id} is now '
        f'{new_status.replace("_", " ").title()}.',
    )


@seller_required
def seller_orders(request):

    store = request.user.sellerprofile.store

    # Status updates: do the work, then redirect so a page refresh
    # does not submit the same form again.
    if request.method == 'POST':
        _update_order_status(request, store)
        return redirect('seller_orders')

    # Each order once, newest first
    orders = Order.objects.filter(
        orderitem__product__store=store
    ).distinct().select_related(
        'user',
        'orderaddress'
    ).order_by('-id')

    orders_data = []

    for order in orders:

        # Only this seller's products in the order
        seller_items = list(
            OrderItem.objects.filter(
                order=order,
                product__store=store
            ).select_related('product')
        )

        for item in seller_items:
            item.line_total = item.price * item.quantity

        seller_total = sum(
            (item.line_total for item in seller_items),
            Decimal('0.00')
        )

        refund = Refund.objects.filter(
            order=order
        ).order_by('-requested_at').first()

       
        shipment = Shipment.objects.filter(
                order=order,
                seller=store,
            ).first()

        orders_data.append({
                'order': order,
                'seller_items': seller_items,
                'seller_total': seller_total,
                'refund': refund,
                'shipment': shipment,
                'seller_status': shipment.status if shipment else order.status,
            })


    return render(
        request,
        'seller_orders.html',
        {
            'orders_data': orders_data,
        }
    )


@seller_required
def seller_products(request):

    store = request.user.sellerprofile.store

    products = Product.objects.filter(
        store=store
    ).select_related(
        'category',
        'inventory'
    )

    category_id = request.GET.get('category')

    if category_id:
        products = products.filter(category_id=category_id)

    return render(
        request,
        'seller_products.html',
        {
            'products': products
        }
    )


from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from products.models import Product, ProductImage, Inventory, Category


@seller_required
def add_product(request):
    seller_profile = getattr(request.user, 'sellerprofile', None)

    if not seller_profile or not hasattr(seller_profile, 'store'):
        return redirect('seller_dashboard')

    store = seller_profile.store
    categories = Category.objects.all()

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        price_value = request.POST.get('price', '').strip()
        category_id = request.POST.get('category', '').strip()

        main_image = request.FILES.get('image')
        gallery_images = request.FILES.getlist('gallery_images')

        context = {
            'store': store,
            'categories': categories,
        }

        if not name or not price_value or not category_id:
            context['error'] = (
                'Please enter the product name, price, and category.'
            )
            return render(request, 'add_product.html', context)

        try:
            price = Decimal(price_value)

            if not price.is_finite() or price < 0:
                raise InvalidOperation

        except (InvalidOperation, ValueError, TypeError):
            context['error'] = 'Please enter a valid, non-negative price.'
            return render(request, 'add_product.html', context)

        category = get_object_or_404(Category, id=category_id)

        try:
            with transaction.atomic():
                product = Product.objects.create(
                    store=store,
                    category=category,
                    name=name,
                    description=description,
                    price=price,
                    image=main_image,
                )

                Inventory.objects.create(product=product)

                for image in gallery_images:
                    ProductImage.objects.create(
                        product=product,
                        image=image,
                    )

        except Exception:
            context['error'] = (
                'Unable to save the product. Please try again.'
            )
            return render(request, 'add_product.html', context)

        return redirect('seller_products')

    return render(
        request,
        'add_product.html',
        {
            'store': store,
            'categories': categories,
        },
    )


@seller_required
def edit_product(request, product_id):
    seller_profile = getattr(request.user, 'sellerprofile', None)

    if not seller_profile or not hasattr(seller_profile, 'store'):
        return redirect('seller_dashboard')

    store = seller_profile.store

    product = get_object_or_404(
        Product,
        id=product_id,
        store=store,
    )

    categories = Category.objects.all()

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        price_value = request.POST.get('price', '').strip()
        category_id = request.POST.get('category', '').strip()

        context = {
            'product': product,
            'categories': categories,
        }

        if not name or not price_value or not category_id:
            context['error'] = (
                'Please enter the product name, price, and category.'
            )
            return render(request, 'edit_product.html', context)

        try:
            price = Decimal(price_value)

            if not price.is_finite() or price < 0:
                raise InvalidOperation

        except (InvalidOperation, ValueError, TypeError):
            context['error'] = 'Please enter a valid, non-negative price.'
            return render(request, 'edit_product.html', context)

        category = get_object_or_404(Category, id=category_id)

        new_main_image = request.FILES.get('image')
        gallery_images = request.FILES.getlist('gallery_images')
        delete_image_ids = request.POST.getlist('delete_gallery_images')

        try:
            with transaction.atomic():
                product.name = name
                product.description = description
                product.price = price
                product.category = category

                # Keep the current main image unless a new one is uploaded.
                if new_main_image:
                    product.image = new_main_image

                product.save()

                # Delete only images belonging to this product.
                if delete_image_ids:
                    product.gallery_images.filter(
                        id__in=delete_image_ids
                    ).delete()

                # Save every newly uploaded gallery image.
                for image in gallery_images:
                    ProductImage.objects.create(
                        product=product,
                        image=image,
                    )

        except Exception:
            context['error'] = (
                'Unable to update the product. Please try again.'
            )
            return render(request, 'edit_product.html', context)

        return redirect('seller_products')

    return render(
        request,
        'edit_product.html',
        {
            'product': product,
            'categories': categories,
        },
    )


@seller_required
def deactivate_product(request, product_id):

    store = request.user.sellerprofile.store

    product = get_object_or_404(
        Product,
        id=product_id,
        store=store
    )

    product.status = 'INACTIVE'
    product.save()

    return redirect('seller_products')


@seller_required
def activate_product(request, product_id):

    store = request.user.sellerprofile.store

    product = get_object_or_404(
        Product,
        id=product_id,
        store=store
    )

    product.status = 'ACTIVE'
    product.save()

    return redirect('seller_products')


from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from products.models import Product


@seller_required
def delete_product(request, product_id):
    seller_profile = getattr(request.user, 'sellerprofile', None)

    if not seller_profile or not hasattr(seller_profile, 'store'):
        return redirect('seller_dashboard')

    product = get_object_or_404(
        Product,
        id=product_id,
        store=seller_profile.store,
    )

    if request.method == 'POST':
        product.delete()
        return redirect('seller_products')

    return render(
        request,
        'delete_product.html',
        {'product': product},
    )


# ============================================================
# SELLER INVENTORY
# ============================================================

@seller_required
def seller_inventory(request):

    store = request.user.sellerprofile.store

    if request.method == 'POST':

        inventory_id = request.POST.get('inventory_id', '')
        quantity_value = request.POST.get('quantity', '').strip()

        if not inventory_id.isdigit():
            messages.error(request, 'Invalid inventory item.')
            return redirect('seller_inventory')

        # Only this seller's own stock can be changed
        inventory = get_object_or_404(
            Inventory,
            id=inventory_id,
            product__store=store
        )

        try:
            quantity = int(quantity_value)
        except ValueError:
            quantity = -1

        if quantity < 0 or quantity > 1000000:
            messages.error(
                request,
                'Please enter a whole number between 0 and 1,000,000.'
            )
        else:
            inventory.quantity = quantity
            inventory.save()

            messages.success(
                request,
                f'Stock for {inventory.product.name} '
                f'updated to {quantity}.'
            )

        # Redirect so a page refresh does not resubmit the form
        return redirect('seller_inventory')

    inventory = Inventory.objects.filter(
        product__store=store
    ).select_related(
        'product'
    )

    total_stock = inventory.aggregate(
        total=Sum('quantity')
    )['total'] or 0

    low_stock_count = Inventory.objects.filter(
        product__store=store,
        quantity__lte=models.F('low_stock_limit')
    ).count()

    return render(
        request,
        'seller_inventory.html',
        {
            'inventory': inventory,
            'total_stock': total_stock,
            'low_stock_count': low_stock_count
        }
    )


@seller_required
def seller_profile(request):
    if getattr(request.user, "role", None) != "SELLER":
        return redirect("seller_login")

    try:
        seller_profile = request.user.sellerprofile
        store = seller_profile.store
    except (SellerProfile.DoesNotExist, Store.DoesNotExist):
        messages.error(
            request,
            "Your seller profile or store could not be found."
        )
        return redirect("seller_dashboard")

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        # 1. Update seller profile and profile photo
        if action == "profile_update":
            seller_profile.phone = request.POST.get("phone", "").strip()
            seller_profile.address = request.POST.get("address", "").strip()

            profile_image = request.FILES.get("profile_image")

            if profile_image:
                if not profile_image.content_type.startswith("image/"):
                    messages.error(
                        request,
                        "Please upload a valid image for your profile photo."
                    )
                    return redirect("seller_profile")

                seller_profile.profile_image = profile_image

            seller_profile.save()
            messages.success(request, "Profile updated successfully.")

        # 2. Update business logo
        elif action == "logo_update":
            business_logo = request.FILES.get("business_logo")

            if not business_logo:
                messages.error(request, "Please select a business logo.")
            elif not business_logo.content_type.startswith("image/"):
                messages.error(request, "Please select a valid image file.")
            else:
                store.logo = business_logo
                store.save(update_fields=["logo"])
                messages.success(
                    request, "Business logo uploaded successfully."
                )

        # 3. Change password
        elif action == "change_password":
            current_password = request.POST.get("current_password", "")
            new_password = request.POST.get("new_password", "")
            confirm_password = request.POST.get("confirm_password", "")

            if not request.user.check_password(current_password):
                messages.error(request, "Your current password is incorrect.")

            elif not new_password:
                messages.error(request, "Please enter a new password.")

            elif new_password != confirm_password:
                messages.error(request, "The new passwords do not match.")

            else:
                try:
                    validate_password(new_password, user=request.user)

                    request.user.set_password(new_password)
                    request.user.save(update_fields=["password"])

                    # Keep the seller logged in after changing the password.
                    update_session_auth_hash(request, request.user)

                    messages.success(
                        request,
                        "Your password has been changed successfully."
                    )

                except ValidationError as error:
                    for error_message in error.messages:
                        messages.error(request, error_message)

        # 4. Close seller account safely
        elif action == "delete_account":
            delete_password = request.POST.get("delete_password", "")

            if not request.user.check_password(delete_password):
                messages.error(
                    request,
                    "Your password is incorrect. Your account is unchanged."
                )
            else:
                # Preserve orders, transactions, refunds and payout history.
                store.status = "SUSPENDED"
                store.save(update_fields=["status"])

                request.user.is_active = False
                request.user.save(update_fields=["is_active"])

                logout(request)

                return redirect("seller_login")

        else:
            messages.error(
                request,
                "Unknown form action. Please refresh the page and try again."
            )

        return redirect("seller_profile")

    return render(
        request,
        "seller_profile.html",
        {
            "seller_profile": seller_profile,
            "store": store,
        },
    )


@seller_required
def business_details(request):

    store = request.user.sellerprofile.store

    if request.method == 'POST':

        store.store_name = request.POST.get('store_name')
        store.description = request.POST.get('description')
        store.address = request.POST.get('address')

        if request.FILES.get('logo'):
            store.logo = request.FILES.get('logo')

        if request.FILES.get('banner'):
            store.banner = request.FILES.get('banner')

        store.save()

        return redirect('business_details')

    return render(
        request,
        'business_details.html',
        {
            'store': store,
        }
    )


@seller_required
def verification_documents(request):

    seller_profile = request.user.sellerprofile

    if request.method == 'POST':

        document_type = request.POST.get('document_type')
        document = request.FILES.get('document')

        if document:

            VerificationDocument.objects.create(
                seller=seller_profile,
                document_type=document_type,
                document=document
            )

            return redirect(
                'verification_documents'
            )

    documents = VerificationDocument.objects.filter(
        seller=seller_profile
    ).order_by(
        '-uploaded_at'
    )

    return render(
        request,
        'verification_documents.html',
        {
            'documents': documents,
        }
    )


@seller_required
def delete_verification_document(request, document_id):

    seller_profile = request.user.sellerprofile

    document = get_object_or_404(
        VerificationDocument,
        id=document_id,
        seller=seller_profile
    )

    if request.method == 'POST':
        document.delete()

    return redirect(
        'verification_documents'
    )


@seller_required
def replace_verification_document(request, document_id):

    seller_profile = request.user.sellerprofile

    document = get_object_or_404(
        VerificationDocument,
        id=document_id,
        seller=seller_profile
    )

    if request.method == 'POST':

        new_document = request.FILES.get(
            'document'
        )

        if new_document:

            document.document = new_document
            document.status = 'PENDING'
            document.save()

    return redirect(
        'verification_documents'
    )


@seller_required
def bank_details(request):

    seller_profile = request.user.sellerprofile

    bank_details, created = BankDetails.objects.get_or_create(
        seller=seller_profile
    )

    if request.method == 'POST':

        bank_details.account_holder_name = request.POST.get(
            'account_holder_name'
        )

        bank_details.account_number = request.POST.get(
            'account_number'
        )

        bank_details.ifsc_code = request.POST.get(
            'ifsc_code'
        )

        bank_details.bank_name = request.POST.get(
            'bank_name'
        )

        bank_details.branch_name = request.POST.get(
            'branch_name'
        )

        bank_details.save()

        return redirect(
            'bank_details'
        )

    return render(
        request,
        'bank_details.html',
        {
            'bank_details': bank_details,
        }
    )


from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _seller_financial_summary(store):
    """
    Calculate consistent seller finance figures.

    Sales and commission are based on delivered orders.
    Processed refunds are allocated proportionally across
    the products in each order because Refund has no OrderItem FK.
    """

    ZERO = Decimal('0.00')
    CENT = Decimal('0.01')
    ONE_HUNDRED = Decimal('100')

    def money(value):
        return Decimal(value).quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )

    # 1. Delivered items belonging to this seller.
    # Support both new per-seller shipments and legacy delivered orders.
    delivered_items = list(
        OrderItem.objects.filter(
            product__store=store,
        ).filter(
            Q(order__status='DELIVERED')
            | Q(
                order__shipments__seller=store,
                order__shipments__status='DELIVERED',
            )
        ).distinct().select_related('order', 'product')
    )

    gross_sales = ZERO
    gross_commission = ZERO
    delivered_order_ids = set()

    for item in delivered_items:
        line_total = item.price * item.quantity
        line_commission = (
            line_total
            * item.product.commission_percentage
            / ONE_HUNDRED
        )

        gross_sales += line_total
        gross_commission += line_commission
        delivered_order_ids.add(item.order_id)

    # 2. Processed refunds for these delivered orders.
    refunds_by_order = {}

    refund_rows = Refund.objects.filter(
        order_id__in=delivered_order_ids,
        status='PROCESSED',
    ).values(
        'order_id'
    ).annotate(
        refund_total=Sum('amount')
    )

    for row in refund_rows:
        refunds_by_order[row['order_id']] = (
            row['refund_total'] or ZERO
        )

    # 3. Allocate each order's refund proportionally across
    # its items. Cap refunds at the order's item subtotal.
    refunded_sales = ZERO
    refunded_commission = ZERO

    if refunds_by_order:
        all_order_items = list(
            OrderItem.objects.filter(
                order_id__in=refunds_by_order.keys()
            ).select_related('product')
        )

        items_by_order = {}

        for item in all_order_items:
            items_by_order.setdefault(
                item.order_id, []
            ).append(item)

        for order_id, order_items in items_by_order.items():
            order_subtotal = sum(
                (
                    item.price * item.quantity
                    for item in order_items
                ),
                ZERO,
            )

            if order_subtotal <= ZERO:
                continue

            refund_amount = min(
                refunds_by_order[order_id],
                order_subtotal,
            )

            for item in order_items:
                if item.product.store_id != store.id:
                    continue

                item_total = item.price * item.quantity

                allocated_refund = (
                    refund_amount
                    * item_total
                    / order_subtotal
                )

                allocated_commission = (
                    allocated_refund
                    * item.product.commission_percentage
                    / ONE_HUNDRED
                )

                refunded_sales += allocated_refund
                refunded_commission += allocated_commission

    # Round final totals to two decimal places.
    gross_sales = money(gross_sales)
    gross_commission = money(gross_commission)
    refunded_sales = min(
        money(refunded_sales),
        gross_sales,
    )
    refunded_commission = min(
        money(refunded_commission),
        gross_commission,
    )

    total_sales = money(gross_sales - refunded_sales)

    platform_commission = money(
        gross_commission - refunded_commission
    )

    seller_earnings = money(
        total_sales - platform_commission
    )

    # 4. Payouts waiting to be completed, including processing.
    pending_payout = money(
        Payout.objects.filter(
            seller=store,
            status__in=['PENDING', 'PROCESSING'],
        ).aggregate(
            total=Sum('amount')
        )['total'] or ZERO
    )

    paid_payout = money(
        Payout.objects.filter(
            seller=store,
            status='PAID',
        ).aggregate(
            total=Sum('amount')
        )['total'] or ZERO
    )

    # 5. Available balance after refunds and payouts.
    available_balance = money(
        seller_earnings - pending_payout - paid_payout
    )

    # 6. Holding balance: this seller's items not yet delivered,
    # excluding cancelled orders and legacy fully delivered orders.
    holding_items = OrderItem.objects.filter(
        product__store=store,
    ).exclude(
        order__status='CANCELLED',
    ).exclude(
        Q(order__status='DELIVERED')
        | Q(
            order__shipments__seller=store,
            order__shipments__status='DELIVERED',
        )
    ).distinct().select_related('product')

    holding_sales = ZERO
    holding_commission = ZERO

    for item in holding_items:
        line_total = item.price * item.quantity

        holding_sales += line_total
        holding_commission += (
            line_total
            * item.product.commission_percentage
            / ONE_HUNDRED
        )

    holding_balance = money(
        holding_sales - holding_commission
    )

    gst_on_commission = money(
        platform_commission * Decimal('0.18')
    )

    return {
        'total_sales': total_sales,
        'platform_commission': platform_commission,
        'seller_earnings': seller_earnings,
        'pending_payout': pending_payout,
        'paid_payout': paid_payout,
        'available_balance': available_balance,
        'total_refund': refunded_sales,
        'holding_balance': holding_balance,
        'gst_on_commission': gst_on_commission,
    }




# Transaction types that take money away from the seller
DEBIT_TRANSACTION_TYPES = ('COMMISSION', 'PAYOUT', 'REFUND')

# How many ledger rows the page shows
TRANSACTION_LIMIT = 100


def _bank_details_complete(seller_profile):
    """A payout needs somewhere to go."""

    bank = BankDetails.objects.filter(
        seller=seller_profile
    ).first()

    return bool(
        bank
        and bank.account_holder_name
        and bank.account_number
        and bank.ifsc_code
    )





# Transaction types that take money away from the seller
DEBIT_TRANSACTION_TYPES = ('COMMISSION', 'PAYOUT', 'REFUND')

# How many ledger rows the page shows
TRANSACTION_LIMIT = 100


def _bank_details_complete(seller_profile):
    """A payout needs somewhere to go."""

    bank = BankDetails.objects.filter(
        seller=seller_profile
    ).first()

    return bool(
        bank
        and bank.account_holder_name
        and bank.account_number
        and bank.ifsc_code
    )


@seller_required
def finance_overview(request):

    seller_profile = getattr(request.user, 'sellerprofile', None)

    if not seller_profile:
        return redirect('seller_dashboard')

    try:
        store = seller_profile.store
    except Store.DoesNotExist:
        return redirect('seller_dashboard')

    summary = _seller_financial_summary(store)

    transaction_type = request.GET.get('type', '').strip()

    transactions = Transaction.objects.filter(
        seller=store,
    )

    if transaction_type == 'REFUND':
        # The Refunds tab also shows the commission handed back
        transactions = transactions.filter(
            transaction_type__in=['REFUND', 'COMMISSION_REVERSAL'],
        )
    elif transaction_type in dict(Transaction.TYPE_CHOICES):
        transactions = transactions.filter(
            transaction_type=transaction_type,
        )
    else:
        transaction_type = ''

    # Newest first; the id keeps rows with the same time in a fixed order
    transactions = list(
        transactions.order_by('-created_at', '-id')[:TRANSACTION_LIMIT + 1]
    )

    transactions_truncated = len(transactions) > TRANSACTION_LIMIT
    transactions = transactions[:TRANSACTION_LIMIT]

    # The template shows these in red with a minus sign
    for entry in transactions:
        entry.is_debit = (
            entry.transaction_type in DEBIT_TRANSACTION_TYPES
        )

    payouts = Payout.objects.filter(
        seller=store,
    ).order_by('-created_at', '-id')

    # A negative balance means refunds were larger than the earnings
    # that were already paid out. Shown as an amount owed, not as a
    # "balance available to request".
    balance_owed = max(
        -summary['available_balance'],
        Decimal('0.00')
    )

    return render(
        request,
        'finance_overview.html',
        {
            **summary,
            'transactions': transactions,
            'transactions_truncated': transactions_truncated,
            'transaction_limit': TRANSACTION_LIMIT,
            'transaction_type': transaction_type,
            'payouts': payouts,
            'balance_owed': balance_owed,
            'bank_details_missing': not _bank_details_complete(
                seller_profile
            ),
        },
    )


@seller_required
def request_payout(request):

    if request.method != 'POST':
        return redirect('finance_overview')

    seller_profile = getattr(request.user, 'sellerprofile', None)

    if not seller_profile:
        messages.error(request, 'Seller profile not found.')
        return redirect('finance_overview')

    try:
        store = seller_profile.store
    except Store.DoesNotExist:
        messages.error(request, 'Your store could not be found.')
        return redirect('finance_overview')

    # Money cannot be sent without bank details
    if not _bank_details_complete(seller_profile):
        messages.error(
            request,
            'Please add your bank details before requesting a payout.',
        )
        return redirect('finance_overview')

    amount_value = request.POST.get('amount', '').strip()

    try:
        amount = Decimal(amount_value)

        if not amount.is_finite() or amount <= Decimal('0.00'):
            raise InvalidOperation

        if amount != amount.quantize(Decimal('0.01')):
            messages.error(
                request,
                'Please enter an amount with no more than two decimal places.',
            )
            return redirect('finance_overview')

    except (InvalidOperation, ValueError, TypeError):
        messages.error(request, 'Please enter a valid payout amount.')
        return redirect('finance_overview')

    try:
        with transaction.atomic():
            # Serialize payout requests for this store.
            store = Store.objects.select_for_update().get(
                pk=store.pk,
            )

            summary = _seller_financial_summary(store)
            available_balance = summary['available_balance']

            if amount > available_balance:
                messages.error(
                    request,
                    'Insufficient available balance for this payout.',
                )
                return redirect('finance_overview')

            Payout.objects.create(
                seller=store,
                amount=amount,
                status='PENDING',
            )

        messages.success(
            request,
            'Payout request submitted successfully.',
        )

    except Store.DoesNotExist:
        messages.error(request, 'Your store could not be found.')

    return redirect('finance_overview')


@seller_required
def seller_wallet(request):
    if request.user.role != 'SELLER':
        return redirect('seller_login')

    seller_profile = getattr(request.user, 'sellerprofile', None)

    if not seller_profile:
        return redirect('seller_dashboard')

    try:
        store = seller_profile.store
    except Store.DoesNotExist:
        return redirect('seller_dashboard')

    summary = _seller_financial_summary(store)

    return render(
        request,
        'seller_wallet.html',
        {
            'available_balance': summary['available_balance'],
            'holding_balance': summary['holding_balance'],
            'pending_payout': summary['pending_payout'],
            'seller_earnings': summary['seller_earnings'],
            'paid_payout': summary['paid_payout'],
            'total_refund': summary['total_refund'],
            'gst_on_commission': summary['gst_on_commission'],
            'balance_owed': max(
                -summary['available_balance'],
                Decimal('0.00')
            ),
        },
    )


# ============================================================
# SELLER PRODUCT CATEGORIES
# ============================================================

@seller_required
def seller_product_categories(request):
    seller_profile = getattr(request.user, 'sellerprofile', None)

    if not seller_profile or not hasattr(seller_profile, 'store'):
        return redirect('seller_dashboard')

    store = seller_profile.store

    categories = Category.objects.annotate(
        seller_product_count=Count(
            'product',
            filter=models.Q(product__store=store),
        )
    ).order_by('name')

    search_query = request.GET.get('q', '').strip()

    if search_query:
        categories = categories.filter(
            name__icontains=search_query
        ) | categories.filter(
            description__icontains=search_query
        )

    return render(
        request,
        'seller_product_categories.html',
        {
            'categories': categories,
            'search_query': search_query,
        }
    )


# ============================================================
# TAX & AUDIT REPORT HELPERS
# ============================================================

def _seller_report_filters(request):
    """Read optional year, quarter and month filters."""

    today = timezone.localdate()
    year_value = request.GET.get('year', str(today.year))
    quarter_value = request.GET.get('quarter', '')
    month_value = request.GET.get('month', '')

    try:
        year = int(year_value)
        if year < 2000 or year > 2100:
            year = today.year
    except (TypeError, ValueError):
        year = today.year

    try:
        quarter = int(quarter_value)
        if quarter not in (1, 2, 3, 4):
            quarter = None
    except (TypeError, ValueError):
        quarter = None

    try:
        month = int(month_value)
        if month not in range(1, 13):
            month = None
    except (TypeError, ValueError):
        month = None

    # Indian financial year: April to March.
    # Quarter 1 = Apr-Jun, Q2 = Jul-Sep,
    # Q3 = Oct-Dec, Q4 = Jan-Mar.
    start_month = None
    end_month = None

    if quarter:
        start_month = ((quarter - 1) * 3 + 4)
        if start_month > 12:
            start_month -= 12
        end_month = start_month + 2
        if end_month > 12:
            end_month -= 12

    return {
        'year': year,
        'quarter': quarter,
        'month': month,
    }


def _seller_audit_data(store, filters):
    """
    Build seller-specific tax and audit reporting data.

    Sales are based on delivered orders created within the selected
    period. Processed refunds for those orders are allocated
    proportionally across all items in each order.

    Tax values are estimates only, not statutory filing calculations.
    """

    ZERO = Decimal("0.00")
    CENT = Decimal("0.01")
    ONE_HUNDRED = Decimal("100")

    def money(value):
        return Decimal(value).quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )

    year = filters["year"]
    quarter = filters["quarter"]
    month = filters["month"]

    # Selected Indian financial year: April through March.
    start_date = date(year, 4, 1)
    end_date = date(year + 1, 4, 1)

    if month:
        actual_year = year if month >= 4 else year + 1
        start_date = date(actual_year, month, 1)

        if month == 12:
            end_date = date(actual_year + 1, 1, 1)
        else:
            end_date = date(actual_year, month + 1, 1)

    elif quarter:
        quarter_periods = {
            1: (4, 7, year),
            2: (7, 10, year),
            3: (10, 1, year),
            4: (1, 4, year + 1),
        }

        first_month, next_month, actual_year = (
            quarter_periods[quarter]
        )

        start_date = date(actual_year, first_month, 1)

        if quarter == 3:
            end_date = date(actual_year + 1, next_month, 1)
        else:
            end_date = date(actual_year, next_month, 1)

    start_dt = timezone.make_aware(
        datetime.combine(start_date, datetime.min.time())
    )
    end_dt = timezone.make_aware(
        datetime.combine(end_date, datetime.min.time())
    )

    # 1. Delivered items in the selected period.
    items = list(
    OrderItem.objects.filter(
        product__store=store,
        order__created_at__gte=start_dt,
        order__created_at__lt=end_dt,
    ).filter(
        Q(order__status='DELIVERED')
        | Q(
            order__shipments__seller=store,
            order__shipments__status='DELIVERED',
        )
    ).distinct()
    .select_related('order', 'product')
    .order_by('order__created_at', 'order_id', 'id')
    )

    order_ids = {item.order_id for item in items}

    gross_sales = ZERO
    gross_commission = ZERO
    ledger = []

    # Retain item-level sale rows so historical orders are represented
    # even when transaction entries were not created at delivery time.
    for item in items:
        line_total = money(item.price * item.quantity)

        item_commission = money(
            line_total
            * item.product.commission_percentage
            / ONE_HUNDRED
        )

        gross_sales += line_total
        gross_commission += item_commission

        ledger.append({
            "date": item.order.created_at,
            "reference": f"Order #{item.order_id}",
            "description": item.product.name,
            "type": "Sale",
            "gross": line_total,
            "commission": item_commission,
            "amount": money(line_total - item_commission),
            "status": item.order.status,
        })

    # 2. Find processed refunds for these delivered orders.
    processed_refunds = list(
        Refund.objects.filter(
            order_id__in=order_ids,
            status="PROCESSED",
        ).order_by("order_id", "id")
    )

    refunds_by_order = {}

    for refund in processed_refunds:
        refunds_by_order.setdefault(refund.order_id, []).append(refund)

    refunded_sales = ZERO
    refunded_commission = ZERO

    if refunds_by_order:
        # Load every item in each order to allocate a refund fairly
        # when an order contains products from multiple sellers.
        all_order_items = list(
            OrderItem.objects.filter(
                order_id__in=refunds_by_order.keys()
            ).select_related("product", "order")
        )

        items_by_order = {}

        for item in all_order_items:
            items_by_order.setdefault(item.order_id, []).append(item)

        for order_id, refunds in refunds_by_order.items():
            order_items = items_by_order.get(order_id, [])

            order_subtotal = sum(
                (
                    item.price * item.quantity
                    for item in order_items
                ),
                ZERO,
            )

            if order_subtotal <= ZERO:
                continue

            # Multiple processed refunds are supported, but the total
            # refund cannot exceed the order's item subtotal.
            total_refund_amount = sum(
                (refund.amount for refund in refunds),
                ZERO,
            )

            capped_refund = min(
                total_refund_amount,
                order_subtotal,
            )

            # Allocate the capped total across the order's items.
            for item in order_items:
                if item.product.store_id != store.id:
                    continue

                item_total = item.price * item.quantity

                allocated_refund = (
                    capped_refund * item_total / order_subtotal
                )

                allocated_commission = (
                    allocated_refund
                    * item.product.commission_percentage
                    / ONE_HUNDRED
                )

                refunded_sales += allocated_refund
                refunded_commission += allocated_commission

            # Add refund rows to the ledger without adding a second
            # copy of the corresponding SALE/COMMISSION transactions.
            remaining_refund = capped_refund

            for refund in refunds:
                if remaining_refund <= ZERO:
                    break

                refund_amount = min(
                    refund.amount,
                    remaining_refund,
                )
                remaining_refund -= refund_amount

                # Seller share of this refund, based on the seller's
                # proportional share of the order subtotal.
                seller_share = sum(
                    (
                        refund_amount
                        * item.price
                        * item.quantity
                        / order_subtotal
                        for item in order_items
                        if item.product.store_id == store.id
                    ),
                    ZERO,
                )

                seller_commission_adjustment = sum(
                    (
                        refund_amount
                        * item.price
                        * item.quantity
                        / order_subtotal
                        * item.product.commission_percentage
                        / ONE_HUNDRED
                        for item in order_items
                        if item.product.store_id == store.id
                    ),
                    ZERO,
                )

                ledger.append({
                    "date": (
                        refund.processed_at
                        or refund.requested_at
                    ),
                    "reference": f"Refund #{refund.id}",
                    "description": (
                        f"Processed refund - Order #{order_id}"
                    ),
                    "type": "Refund",
                    "gross": money(-seller_share),
                    "commission": money(
                        -seller_commission_adjustment
                    ),
                    "amount": money(
                        -(seller_share - seller_commission_adjustment)
                    ),
                    "status": refund.status,
                })

    # 3. Compute net figures after processed refunds.
    gross_sales = money(gross_sales)
    gross_commission = money(gross_commission)
    refunded_sales = min(money(refunded_sales), gross_sales)
    refunded_commission = min(
        money(refunded_commission),
        gross_commission,
    )

    net_sales = money(gross_sales - refunded_sales)
    net_commission = money(
        gross_commission - refunded_commission
    )
    seller_earnings = money(net_sales - net_commission)

    # 4. Include payout records once in the ledger.
    payouts = list(
        Payout.objects.filter(
            seller=store,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        ).order_by("created_at", "id")
    )

    payout_total = money(
        sum(
            (
                payout.amount
                for payout in payouts
                if payout.status == "PAID"
            ),
            ZERO,
        )
    )

    for payout in payouts:
        ledger.append({
            "date": payout.created_at,
            "reference": f"Payout #{payout.id}",
            "description": "Seller payout",
            "type": "Payout",
            "gross": ZERO,
            "commission": ZERO,
            "amount": (
                money(-payout.amount)
                if payout.status == "PAID"
                else ZERO
            ),
            "status": payout.status,
        })

    # 5. Estimate tax-related figures for display only.
    # Verify applicable rules and thresholds before using for filing.
    gst_on_commission = money(
        net_commission * Decimal("0.18")
    )
    estimated_tcs = money(
        net_sales * Decimal("0.005")
    )
    estimated_tds = money(
        net_sales * Decimal("0.001")
    )

    estimated_net_payout = money(
        seller_earnings
        - gst_on_commission
        - estimated_tcs
        - estimated_tds
    )

    ledger.sort(key=lambda entry: entry["date"])

    return {
        "start_date": start_date,
        "end_date": end_date,
        "gross_sales": gross_sales,
        "commission": gross_commission,
        "refunded_sales": refunded_sales,
        "refunded_commission": refunded_commission,
        "net_sales": net_sales,
        "net_commission": net_commission,
        "gst_on_commission": gst_on_commission,
        "estimated_tcs": estimated_tcs,
        "estimated_tds": estimated_tds,
        "seller_earnings": seller_earnings,
        "payout_total": payout_total,
        "estimated_net_payout": estimated_net_payout,
        "ledger": ledger,
        "payouts": payouts,
    }


# ============================================================
# SELLER TAX & AUDIT REPORTS
# ============================================================

@seller_required
def seller_tax_audit_reports(request):
    seller_profile = getattr(request.user, 'sellerprofile', None)

    if not seller_profile or not hasattr(seller_profile, 'store'):
        return redirect('seller_dashboard')

    filters = _seller_report_filters(request)
    report = _seller_audit_data(seller_profile.store, filters)

    return render(
        request,
        'seller_tax_audit_reports.html',
        {
            **report,
            **filters,
            'years': range(
                timezone.localdate().year - 4,
                timezone.localdate().year + 2
            ),
        }
    )


# ============================================================
# EXPORT TAX REPORTS TO EXCEL
# ============================================================

@seller_required
def seller_tax_report_excel(request, report_type):
    seller_profile = getattr(request.user, 'sellerprofile', None)

    if not seller_profile or not hasattr(seller_profile, 'store'):
        return redirect('seller_dashboard')

    if report_type not in ('gstr8', 'form26q'):
        return redirect('seller_tax_audit_reports')

    from openpyxl import Workbook

    filters = _seller_report_filters(request)
    report = _seller_audit_data(seller_profile.store, filters)

    workbook = Workbook()
    sheet = workbook.active

    if report_type == 'gstr8':
        sheet.title = 'GSTR-8 Estimate'
        sheet.append(['Field', 'Amount (INR)'])
        sheet.append(['Period start', str(report['start_date'])])
        sheet.append(['Period end (exclusive)', str(report['end_date'])])
        sheet.append(['Gross delivered sales', float(report['gross_sales'])])
        sheet.append(['Platform commission', float(report['commission'])])
        sheet.append(['GST on commission (estimated)', float(report['gst_on_commission'])])
        sheet.append(['Estimated TCS (0.5% assumption)', float(report['estimated_tcs'])])
    else:
        sheet.title = 'Form 26Q Estimate'
        sheet.append(['Field', 'Amount (INR)'])
        sheet.append(['Period start', str(report['start_date'])])
        sheet.append(['Period end (exclusive)', str(report['end_date'])])
        sheet.append(['Gross delivered sales (estimate base)', float(report['gross_sales'])])
        sheet.append(['Estimated TDS (0.1% assumption)', float(report['estimated_tds'])])
        sheet.append(['PAN status', 'Not stored in ShopHub model'])
        sheet.append(['Filing status', 'Not an official tax filing'])

    sheet.append([])
    sheet.append([
        'Notice',
        'Dashboard estimate only. Verify rates, thresholds, tax base, '
        'PAN/GSTIN and statutory records before filing.'
    ])

    response = HttpResponse(
        content_type=(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    )
    response['Content-Disposition'] = (
        f'attachment; filename="{report_type}_estimate.xlsx"'
    )
    workbook.save(response)
    return response


# ============================================================
# EXPORT AUDIT LEDGER TO CSV
# ============================================================

@seller_required
def seller_audit_ledger_csv(request):
    seller_profile = getattr(request.user, 'sellerprofile', None)

    if not seller_profile or not hasattr(seller_profile, 'store'):
        return redirect('seller_dashboard')

    filters = _seller_report_filters(request)
    report = _seller_audit_data(seller_profile.store, filters)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        'attachment; filename="shophub_audit_ledger.csv"'
    )

    writer = csv.writer(response)
    writer.writerow([
        'Date', 'Reference', 'Description', 'Type',
        'Gross Amount', 'Commission', 'Amount', 'Status',
    ])

    for entry in report['ledger']:
        writer.writerow([
            entry['date'].strftime('%Y-%m-%d %H:%M'),
            entry['reference'],
            entry['description'],
            entry['type'],
            entry['gross'],
            entry['commission'],
            entry['amount'],
            entry['status'],
        ])

    return response