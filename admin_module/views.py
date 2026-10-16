

# Create your views here.
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect, get_object_or_404
from products.services import (
           cancel_order,
            record_delivery,
            record_refund_transactions,
        )
from products.models import (
    Category,
    Product,
    Order,
    Payment,
    Payout,
    Refund,
    Transaction,
)

from django.contrib import messages
from user_module.models import User, SellerProfile
from django.db.models import Sum
from django.utils import timezone

def admin_required(view_func):
    return user_passes_test(
        lambda user: user.is_authenticated and user.role == 'ADMIN',
        login_url='/custom-admin/login/'
    )(view_func)


def admin_login(request):

    if request.user.is_authenticated and request.user.role == 'ADMIN':
        return redirect('admin_dashboard')

    if request.method == 'POST':

        email = request.POST.get('email')
        password = request.POST.get('password')

        user = authenticate(
            request,
            email=email,
            password=password
        )

        if user is not None and user.role == 'ADMIN':
            login(request, user)
            return redirect('admin_dashboard')

        return render(
            request,
            'admin_module/login.html',
            {
                'error': 'Invalid admin email or password.'
            }
        )

    return render(
        request,
        'admin_module/login.html'
    )

def admin_logout(request):
    logout(request)
    return redirect('admin_login')





@admin_required
def admin_dashboard(request):

    total_users = User.objects.count()

    total_customers = User.objects.filter(
        role='CUSTOMER'
    ).count()

    total_sellers = User.objects.filter(
        role='SELLER'
    ).count()

    total_products = Product.objects.count()

    total_orders = Order.objects.count()

    total_revenue = Order.objects.filter(
        status__in=[
            'CONFIRMED',
            'PROCESSING',
            'SHIPPED',
            'IN_TRANSIT',
            'OUT_FOR_DELIVERY',
            'DELIVERED'
        ]
    ).aggregate(
        total=Sum('total_amount')
    )['total'] or 0

    return render(
        request,
        'admin_module/dashboard.html',
        {
            'total_users': total_users,
            'total_customers': total_customers,
            'total_sellers': total_sellers,
            'total_products': total_products,
            'total_orders': total_orders,
            'total_revenue': total_revenue,
        }
    )

@admin_required
def admin_refund_list(request):

    refunds = Refund.objects.select_related(
        'order',
        'order__user',
        'payment'
    ).order_by('-requested_at')

    return render(
        request,
        'admin_module/refund_list.html',
        {
            'refunds': refunds
        }
    )


@admin_required
def admin_refund_detail(request, refund_id):

    refund = get_object_or_404(
        Refund.objects.select_related(
            'order',
            'order__user',
            'payment'
        ),
        id=refund_id
    )

    order_items = refund.order.orderitem_set.select_related(
        'product'
    ).all()

    return render(
        request,
        'admin_module/refund_detail.html',
        {
            'refund': refund,
            'order_items': order_items,
        }
    )


@admin_required
def admin_refund_update_status(request, refund_id):
    from django.db import transaction as db_transaction
 
    # 404 if the refund does not exist
    get_object_or_404(Refund, id=refund_id)
 
    if request.method == 'POST':
 
        new_status = request.POST.get('status')
 
        with db_transaction.atomic():
 
            # Lock the refund and read its status again, so two admins
            # (or a double click) cannot process the same refund twice.
            refund = Refund.objects.select_for_update().get(pk=refund_id)
 
            if refund.status == 'REQUESTED' and new_status in (
                'APPROVED',
                'REJECTED',
            ):
                refund.status = new_status
                refund.save()
 
            elif refund.status == 'APPROVED' and new_status == 'PROCESSED':
 
                refund.status = 'PROCESSED'
                refund.processed_at = timezone.now()
                refund.save()
 
                # NOTE: this records the refund as processed. With the
                # demo payment no money moves; with a real payment
                # gateway, send the refund through it first.
                if refund.payment_id:
                    refund.payment.status = 'REFUNDED'
                    refund.payment.save()
 
                # REFUND + COMMISSION_REVERSAL rows for each seller
                # (delivered orders only).
                record_refund_transactions(refund)
 
    return redirect(
        'admin_refund_detail',
        refund_id=refund_id
    )
 
 
# Which payout status may follow which. PAID and FAILED are final, so a
# completed payment (and its ledger row) can never be silently undone.
PAYOUT_TRANSITIONS = {
    'PENDING': {'PROCESSING', 'PAID', 'FAILED'},
    'PROCESSING': {'PENDING', 'PAID', 'FAILED'},
    'PAID': set(),
    'FAILED': set(),
}






@admin_required
def category_list(request):

    categories = Category.objects.all().order_by('name')

    for category in categories:
        category.product_count = Product.objects.filter(
            category=category
        ).count()

    return render(
        request,
        'admin_module/category_list.html',
        {
            'categories': categories
        }
    )


@admin_required
def category_add(request):

    if request.method == 'POST':

        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        image = request.FILES.get('image')

        if not name:
            return render(
                request,
                'admin_module/category_add.html',
                {
                    'error': 'Category name is required.',
                    'name': name,
                    'description': description,
                }
            )

        if Category.objects.filter(name__iexact=name).exists():
            return render(
                request,
                'admin_module/category_add.html',
                {
                    'error': 'A category with this name already exists.',
                    'name': name,
                    'description': description,
                }
            )

        Category.objects.create(
            name=name,
            description=description,
            image=image
        )

        messages.success(
            request,
            f'"{name}" was added successfully.'
        )

        return redirect('admin_category_list')

    return render(
        request,
        'admin_module/category_add.html'
    )


@admin_required
def category_edit(request, category_id):

    category = get_object_or_404(
        Category,
        id=category_id
    )

    if request.method == 'POST':

        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        image = request.FILES.get('image')

        context = {
            'category': category,
            'name': name,
            'description': description,
        }

        if not name:
            context['error'] = 'Category name is required.'

            return render(
                request,
                'admin_module/category_edit.html',
                context
            )

        duplicate_exists = Category.objects.filter(
            name__iexact=name
        ).exclude(
            id=category.id
        ).exists()

        if duplicate_exists:
            context['error'] = (
                'A category with this name already exists.'
            )

            return render(
                request,
                'admin_module/category_edit.html',
                context
            )

        category.name = name
        category.description = description

        # Keep the current image unless a replacement is uploaded.
        if image:
            category.image = image

        category.save()

        messages.success(
            request,
            f'"{category.name}" was updated successfully.'
        )

        return redirect('admin_category_list')

    return render(
        request,
        'admin_module/category_edit.html',
        {
            'category': category,
            'name': category.name,
            'description': category.description,
        }
    )


@admin_required
def category_delete(request, category_id):

    # Only allow deletion through a POST request.
    if request.method != 'POST':
        return redirect('admin_category_list')

    category = get_object_or_404(
        Category,
        id=category_id
    )

    # Always check the product count on the server.
    product_count = Product.objects.filter(
        category=category
    ).count()

    if product_count > 0:

        messages.error(
            request,
            f'"{category.name}" cannot be deleted because '
            f'it contains {product_count} product(s). '
            'Move the products to another category first.'
        )

        return redirect('admin_category_list')

    category_name = category.name

    category.delete()

    messages.success(
        request,
        f'"{category_name}" was deleted successfully.'
    )

    return redirect('admin_category_list')


    
@admin_required
def admin_product_list(request):
    products = Product.objects.select_related(
        'store',
        'store__seller',
        'store__seller__user',
        'category',
        'inventory'
    ).order_by('-id')

    return render(
        request,
        'admin_module/product_list.html',
        {
            'products': products
        }
    )

@admin_required
def admin_product_detail(request, product_id):

    product = get_object_or_404(
        Product.objects.select_related(
            'store',
            'store__seller',
            'store__seller__user',
            'category',
            'inventory'
        ),
        id=product_id
    )

    return render(
        request,
        'admin_module/product_detail.html',
        {
            'product': product
        }
    )

@admin_required
def admin_product_toggle_status(request, product_id):

    product = get_object_or_404(
        Product,
        id=product_id
    )

    if product.status == 'ACTIVE':
        product.status = 'INACTIVE'
    else:
        product.status = 'ACTIVE'

    product.save()

    return redirect(
        'admin_product_detail',
        product_id=product.id
    )

@admin_required
def admin_user_list(request):

    users = User.objects.all().order_by('-date_joined')

    return render(
        request,
        'admin_module/user_list.html',
        {
            'users': users
        }
    )

@admin_required
def admin_user_detail(request, user_id):

    user = get_object_or_404(
        User,
        id=user_id
    )

    return render(
        request,
        'admin_module/user_detail.html',
        {
            'user': user
        }
    )

@admin_required
def admin_user_toggle_status(request, user_id):

    user = get_object_or_404(
        User,
        id=user_id
    )

    # Do not allow an admin account
    # to be deactivated.
    if user.role == 'ADMIN':
        return redirect(
            'admin_user_detail',
            user_id=user.id
        )

    if user.is_active:
        user.is_active = False
    else:
        user.is_active = True

    user.save()

    return redirect(
        'admin_user_detail',
        user_id=user.id
    )

@admin_required
def admin_seller_list(request):

    sellers = SellerProfile.objects.select_related(
        'user',
        'store'
    ).order_by('-id')

    return render(
        request,
        'admin_module/seller_list.html',
        {
            'sellers': sellers
        }
    )

@admin_required
def admin_seller_detail(request, seller_id):

    seller = get_object_or_404(
        SellerProfile.objects.select_related(
            'user',
            'store'
        ),
        id=seller_id
    )

    return render(
        request,
        'admin_module/seller_detail.html',
        {
            'seller': seller
        }
    )


@admin_required
def admin_seller_toggle_status(request, seller_id):

    seller = get_object_or_404(
        SellerProfile.objects.select_related(
            'user',
            'store'
        ),
        id=seller_id
    )

    if seller.store.status == 'ACTIVE':
        seller.store.status = 'SUSPENDED'
    else:
        seller.store.status = 'ACTIVE'

    seller.store.save()

    return redirect(
        'admin_seller_detail',
        seller_id=seller.id
    )


@admin_required
def admin_order_list(request):

    status = request.GET.get('status')

    orders = Order.objects.select_related(
        'user'
    ).order_by('-created_at')

    if status:
        orders = orders.filter(status=status)

    return render(
        request,
        'admin_module/order_list.html',
        {
            'orders': orders,
            'selected_status': status,
        }
    )




@admin_required
def admin_order_detail(request, order_id):

    order = get_object_or_404(
        Order.objects.select_related(
            'user'
        ).prefetch_related(
            'orderitem_set__product'
        ),
        id=order_id
    )

    status_order = [
        'PENDING',
        'CONFIRMED',
        'PROCESSING',
        'SHIPPED',
        'IN_TRANSIT',
        'OUT_FOR_DELIVERY',
        'DELIVERED'
    ]

    if order.status in status_order:
            current_index = status_order.index(order.status)
            available_statuses = status_order[current_index:]
    else:
        # A cancelled order has no further status changes
        available_statuses = [order.status]
    cancellable_statuses = [
        'PENDING',
        'CONFIRMED',
        'PROCESSING'
    ]

    if order.status in cancellable_statuses:
        available_statuses.append('CANCELLED')

    return render(
        request,
        'admin_module/order_detail.html',
        {
            'order': order,
            'available_statuses': available_statuses
        }
    )

@admin_required
def admin_order_update_status(request, order_id):
    from django.db import transaction as db_transaction
 
    order = get_object_or_404(
        Order,
        id=order_id
    )
 
    if request.method == 'POST':
 
        new_status = request.POST.get('status')
 
        status_order = [
            'PENDING',
            'CONFIRMED',
            'PROCESSING',
            'SHIPPED',
            'IN_TRANSIT',
            'OUT_FOR_DELIVERY',
            'DELIVERED'
        ]
 
        valid_statuses = status_order + ['CANCELLED']
 
        if new_status in valid_statuses:
 
            # A cancelled order cannot be reopened
            if order.status == 'CANCELLED':
                return redirect(
                    'admin_order_detail',
                    order_id=order.id
                )
 
            # Prevent moving an order backwards
            if (
                order.status in status_order
                and new_status in status_order
                and status_order.index(new_status)
                < status_order.index(order.status)
            ):
                return redirect(
                    'admin_order_detail',
                    order_id=order.id
                )
 
            if new_status == 'CANCELLED':
 
                # Only before shipping. Puts the stock back and creates
                # a refund request for a successful UPI / Card payment.
                if not cancel_order(order, 'Order cancelled by admin'):
                    messages.error(
                        request,
                        'This order can no longer be cancelled.'
                    )
 
            elif new_status != order.status or new_status == 'DELIVERED':
 
                # Status and delivery records are saved together.
                # Delivering also creates the shipment (which starts the
                # return window) and every seller's SALE / COMMISSION rows,
                # exactly like the seller's own Delivered button. Repeating
                # DELIVERED is safe: nothing is duplicated.
                with db_transaction.atomic():
 
                    order.status = new_status
                    order.save()
 
                    if new_status == 'DELIVERED':
                        record_delivery(order)
 
    return redirect(
        'admin_order_detail',
        order_id=order.id
    )



@admin_required
def admin_payment_list(request):

    payments = Payment.objects.select_related(
        'order',
        'order__user'
    ).order_by('-order__created_at')

    return render(
        request,
        'admin_module/payment_list.html',
        {
            'payments': payments
        }
    )

@admin_required
def admin_payment_detail(request, payment_id):
    payment = get_object_or_404(
        Payment.objects.select_related(
            'order',
            'order__user'
        ),
        id=payment_id
    )

    address = getattr(
        payment.order,
        'orderaddress',
        None
    )

    return render(
        request,
        'admin_module/payment_detail.html',
        {
            'payment': payment,
            'address': address,
        }
    )

@admin_required
def admin_payout_list(request):

    payouts = Payout.objects.select_related(
        'seller',
        'seller__seller',
        'seller__seller__user'
    ).order_by('-created_at')

    return render(
        request,
        'admin_module/payout_list.html',
        {
            'payouts': payouts
        }
    )

@admin_required
def admin_payout_detail(request, payout_id):

    payout = get_object_or_404(
        Payout.objects.select_related(
            'seller',
            'seller__seller',
            'seller__seller__user'
        ),
        id=payout_id
    )

    return render(
        request,
        'admin_module/payout_detail.html',
        {
            'payout': payout
        }
    )

@admin_required
def admin_payout_update_status(request, payout_id):
    from django.db import transaction as db_transaction
 
    # 404 if the payout does not exist
    get_object_or_404(Payout, id=payout_id)
 
    if request.method == 'POST':
 
        new_status = request.POST.get('status')
 
        with db_transaction.atomic():
 
            # Lock the payout and read its status again
            payout = Payout.objects.select_for_update().get(pk=payout_id)
 
            if new_status == payout.status:
                pass
 
            elif new_status not in PAYOUT_TRANSITIONS.get(
                payout.status, set()
            ):
                messages.error(
                    request,
                    f'A payout that is {payout.get_status_display()} '
                    f'cannot be changed to {new_status}.'
                )
 
            else:
 
                payout.status = new_status
 
                if new_status == 'PAID':
                    payout.paid_at = timezone.now()
 
                payout.save()
 
                if new_status == 'PAID':
 
                    # One ledger row per paid payout (no duplicates).
                    Transaction.objects.get_or_create(
                        seller=payout.seller,
                        transaction_type='PAYOUT',
                        description=f'Payout #{payout.id}',
                        defaults={
                            'amount': payout.amount,
                            'status': 'COMPLETED',
                        }
                    )
 
    return redirect(
        'admin_payout_detail',
        payout_id=payout_id
    )

@admin_required
def admin_profile(request):

    return render(
        request,
        'admin_module/profile.html',
        {
            'user': request.user
        }
    )














