from django.shortcuts import render
from .models import Cart, CartItem, Inventory, Order, OrderItem, Payment, OrderAddress
from django.db import transaction
from django.contrib.auth.decorators import login_required


@login_required
def checkout(request):
    cart = Cart.objects.get(user=request.user)
    cart_items = CartItem.objects.filter(cart=cart)

    if not cart_items.exists():
        return render(request, 'checkout.html', {
            'message': 'Your cart is empty.'
        })

    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        phone = request.POST.get('phone')
        address = request.POST.get('address')
        city = request.POST.get('city')
        state = request.POST.get('state')
        postal_code = request.POST.get('postal_code')

        total_amount = 0

        for item in cart_items:
            total_amount += item.product.price * item.quantity

        for item in cart_items:
            inventory = Inventory.objects.get(product=item.product)

            if item.quantity > inventory.quantity:
                return render(request, 'checkout.html', {
                    'message': f'Not enough stock for {item.product.name}.'
                })

        with transaction.atomic():

            order = Order.objects.create(
                user=request.user,
                total_amount=total_amount
            )

            OrderAddress.objects.create(
                order=order,
                full_name=full_name,
                phone=phone,
                address=address,
                city=city,
                state=state,
                postal_code=postal_code
            )

            for item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    quantity=item.quantity,
                    price=item.product.price
                )

            for item in cart_items:
                inventory = Inventory.objects.get(product=item.product)
                inventory.quantity -= item.quantity
                inventory.save()

            cart_items.delete()

        return render(request, 'checkout.html', {
            'order': order
        })

    return render(request, 'checkout.html')

@login_required
def payment(request, order_id):
    order = Order.objects.get(id=order_id)

    existing_payment = Payment.objects.filter(order=order).first()

    if existing_payment:
        return render(request, 'payment.html', {
            'order': order,
           
        })

    if request.method == 'POST':
        payment_method = request.POST.get('payment_method')

        Payment.objects.create(
            order=order,
            amount=order.total_amount,
            payment_method=payment_method,
            transaction_id='TEST-TXN-001',
            status='SUCCESS'
        )

        order.status = 'CONFIRMED'
        order.save()

        return render(request, 'payment.html', {
            'order': order,
            'message': 'Payment successful!',
            'payment_success':True
        })

    return render(request, 'payment.html', {
        'order': order
    })

@login_required
def my_orders(request):
    orders = Order.objects.filter(user=request.user).order_by('-created_at')

    return render(request, 'my_orders.html', {
        'orders': orders
    })

@login_required
def order_detail(request, order_id):
    order = Order.objects.get(
        id=order_id,
        user=request.user
    )

    order_items = OrderItem.objects.filter(order=order)

    payment = Payment.objects.filter(order=order).first()

    address = OrderAddress.objects.filter(order=order).first()

    return render(request, 'order_detail.html', {
        'order': order,
        'order_items': order_items,
        'payment': payment,
        'address': address
    })