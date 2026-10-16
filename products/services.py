from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import (
    Inventory,
    Order,
    OrderItem,
    Payment,
    Refund,
    Shipment,
    Transaction,
)

CANCELLABLE_STATUSES = ('PENDING', 'CONFIRMED', 'PROCESSING')

CENT = Decimal('0.01')
ONE_HUNDRED = Decimal('100')


def _money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


# ============================================================
# CANCEL ORDER
# ============================================================

@transaction.atomic
def cancel_order(order, reason):
    """
    Cancel an order: put the stock back and, if the customer really paid
    online (UPI/Card payment with status SUCCESS), create a refund request.

    The order row is locked and its status re-checked, so cancelling twice
    (double click, two admins) restores the stock only once.
    Returns True if the order was cancelled, False if it can't be.
    """
    order = Order.objects.select_for_update().get(pk=order.pk)

    if order.status not in CANCELLABLE_STATUSES:
        return False

    for item in OrderItem.objects.filter(order=order):
        inventory = Inventory.objects.select_for_update().filter(
            product=item.product
        ).first()

        if inventory:
            inventory.quantity += item.quantity
            inventory.save()

    order.status = 'CANCELLED'
    order.save()

    payment = Payment.objects.filter(order=order).first()

    # Money is only owed back if it was actually received.
    if (
        payment
        and payment.payment_method in ('UPI', 'CARD')
        and payment.status == 'SUCCESS'
    ):
        Refund.objects.get_or_create(
            order=order,
            payment=payment,
            defaults={
                'amount': payment.amount,
                'reason': reason,
            },
        )

    return True


# ============================================================
# DELIVERY AND THE SELLER LEDGER
# ============================================================

def delivery_amounts(order):
    """
    What each seller earned from this order.

    Returns {store: (sale_amount, commission_amount)}. The same numbers
    are used when the order is delivered and by the backfill command, so
    they can never disagree.
    """
    sales = {}
    commissions = {}
    stores = {}

    items = OrderItem.objects.filter(
        order=order
    ).select_related('product', 'product__store')

    for item in items:
        store = item.product.store

        if store is None:
            continue

        line_total = item.price * item.quantity

        stores[store.id] = store
        sales[store.id] = sales.get(store.id, Decimal('0.00')) + line_total
        commissions[store.id] = (
            commissions.get(store.id, Decimal('0.00'))
            + line_total
            * item.product.commission_percentage
            / ONE_HUNDRED
        )

    return {
        stores[store_id]: (_money(sales[store_id]), _money(commissions[store_id]))
        for store_id in stores
    }



@transaction.atomic
def record_delivery(order, store=None):
    if order.status == 'CANCELLED':
        return

    amounts = delivery_amounts(order)
    """
    Mark delivery for one seller, or all sellers when called by admin.

    Seller calls update only that seller's shipment and ledger.
    Admin calls without a store process all sellers.
    Repeated calls do not duplicate ledger entries.
    """
    amounts = delivery_amounts(order)

    if not amounts:
        return

    # A seller may deliver only their own store's items.
    if store is not None:
        if store not in amounts:
            return
        seller_amounts = {store: amounts[store]}
    else:
        seller_amounts = amounts

    # Create or update one shipment per seller for this order.
    for seller_store, (sale, commission) in seller_amounts.items():
        shipment, _ = Shipment.objects.get_or_create(
            order=order,
            seller=seller_store,
            defaults={'status': 'DELIVERED'},
        )

        if not shipment.delivered_at:
            shipment.delivered_at = timezone.now()

        shipment.status = 'DELIVERED'
        shipment.save()

        # Record only this seller's sale.
        if sale > 0:
            Transaction.objects.get_or_create(
                seller=seller_store,
                transaction_type='SALE',
                description=f'Order #{order.id}',
                defaults={
                    'amount': sale,
                    'status': 'COMPLETED',
                },
            )

        # Record only this seller's platform commission.
        if commission > 0:
            Transaction.objects.get_or_create(
                seller=seller_store,
                transaction_type='COMMISSION',
                description=f'Platform commission - Order #{order.id}',
                defaults={
                    'amount': commission,
                    'status': 'COMPLETED',
                },
            )

    # Mark the overall order delivered only when every seller's
    # shipment has been delivered.
    all_sellers_delivered = Shipment.objects.filter(
        order=order,
        seller__in=amounts.keys(),
    ).count() == len(amounts) and not Shipment.objects.filter(
        order=order,
        seller__in=amounts.keys(),
    ).exclude(status='DELIVERED').exists()

    if all_sellers_delivered:
        order.status = 'DELIVERED'
        order.save(update_fields=['status', 'updated_at'])



# ============================================================
# REFUNDS AND THE SELLER LEDGER
# ============================================================

def refund_amounts_by_store(refund):
    """
    Split a refund between the sellers in the order, in proportion to
    each seller's item total.

    Returns a list of (store, refund_amount, commission_reversal):
    the part of the refund that comes out of that seller's sales, and the
    part of the platform commission that is handed back to the seller.
    The last seller takes the rounding remainder so the parts add up.

    The refund is limited to what the order's items add up to, the same
    limit the finance summary uses. A refund of zero or less gives [].
    """
    items = list(
        refund.order.orderitem_set.select_related(
            'product',
            'product__store',
        )
    )

    totals = {}
    commissions = {}
    stores = {}

    for item in items:
        store = item.product.store

        if store is None:
            continue

        line_total = item.price * item.quantity

        totals[store.id] = (
            totals.get(store.id, Decimal('0.00')) + line_total
        )
        commissions[store.id] = (
            commissions.get(store.id, Decimal('0.00'))
            + line_total * item.product.commission_percentage / ONE_HUNDRED
        )
        stores[store.id] = store

    grand_total = sum(totals.values(), Decimal('0.00'))

    if grand_total <= 0 or refund.amount <= 0:
        return []

    refundable = min(refund.amount, grand_total)

    shares = []
    remaining = refundable
    store_ids = list(totals)

    for index, store_id in enumerate(store_ids):

        if index == len(store_ids) - 1:
            share = remaining
        else:
            share = _money(refundable * totals[store_id] / grand_total)
            remaining -= share

        if totals[store_id] > 0:
            commission_back = _money(
                share * commissions[store_id] / totals[store_id]
            )
        else:
            commission_back = Decimal('0.00')

        shares.append((stores[store_id], share, commission_back))

    return shares


def record_refund_transactions(refund):
    """
    Add the ledger rows for a processed refund to each affected seller:

      REFUND               the sale amount taken back from the seller
      COMMISSION_REVERSAL  the platform commission handed back

    Together they equal the seller's real loss, so the transaction list
    adds up to the balance shown on the finance page.

    Only delivered orders are recorded: a seller's SALE row exists only
    for delivered orders, so a refund for an order that was cancelled
    before delivery never touched the seller's money. Safe to call twice.
    Returns the number of rows created.
    """
    if refund.order.status != 'DELIVERED':
        return 0

    created_count = 0

    for store, amount, commission_back in refund_amounts_by_store(refund):

        _, created = Transaction.objects.get_or_create(
            seller=store,
            transaction_type='REFUND',
            description=f'Refund #{refund.id}',
            defaults={
                'amount': amount,
                'status': 'COMPLETED',
            }
        )

        if created:
            created_count += 1

        if commission_back > 0:

            _, created = Transaction.objects.get_or_create(
                seller=store,
                transaction_type='COMMISSION_REVERSAL',
                description=f'Commission reversed - Refund #{refund.id}',
                defaults={
                    'amount': commission_back,
                    'status': 'COMPLETED',
                }
            )

            if created:
                created_count += 1

    return created_count