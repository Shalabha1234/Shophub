"""
Check the seller ledger (Transaction rows) against the real records and
repair it. Nothing changes unless you ask for it.

    python manage.py backfill_ledger --dry-run
        List every row it WOULD add, every wrong amount and every
        duplicate. Changes nothing. Run this first and read the list.

    python manage.py backfill_ledger
        Add the missing rows (dated to when the event really happened).

    python manage.py backfill_ledger --fix-amounts
        Also correct rows whose amount differs from the real records.

    python manage.py backfill_ledger --remove-stale
        Also delete REFUND / COMMISSION_REVERSAL rows of refunds for
        orders that were never delivered (they never touched the seller's
        money). Payout rows are NEVER deleted: a payout row whose payout
        is not PAID is only listed for you to review.

Safe to run more than once. The seller balances do not depend on these
rows (they are calculated from orders, refunds and payouts), so this
only repairs the transaction list.
"""

import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

from products.models import Order, Payout, Refund, Shipment, Transaction
from products.services import delivery_amounts, refund_amounts_by_store


class Command(BaseCommand):
    help = 'Report and repair missing or wrong seller ledger rows.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List what would change; change nothing.',
        )
        parser.add_argument(
            '--fix-amounts', action='store_true',
            help='Also correct existing rows that have a wrong amount.',
        )
        parser.add_argument(
            '--remove-stale', action='store_true',
            help='Also delete refund rows of orders that were never delivered.',
        )

    # --------------------------------------------------------

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.fix_amounts = options['fix_amounts']
        self.counts = defaultdict(int)
        self.lines = []

        with db_transaction.atomic():
            self.check_expected_rows()
            self.report_duplicates()
            self.report_payout_rows_to_review()

            if options['remove_stale']:
                self.remove_stale_refund_rows()

            if self.dry_run:
                db_transaction.set_rollback(True)

        for line in self.lines:
            self.stdout.write(line)

        verb = 'Would' if self.dry_run else 'Did'
        self.stdout.write(self.style.SUCCESS(
            f"\n{verb}: add {self.counts['add']}, "
            f"correct {self.counts['fix']}, "
            f"remove {self.counts['remove']} row(s). "
            f"Wrong amounts left as they are: {self.counts['differs']}. "
            f"Duplicate groups: {self.counts['duplicate']}. "
            f"Payout rows to review: {self.counts['review']}."
        ))

        if self.dry_run:
            self.stdout.write('Dry run: nothing was changed.')

    # --------------------------------------------------------

    def log(self, symbol, text):
        self.lines.append(f'{symbol:<10} {text}')

    def check_row(self, store, transaction_type, description, amount, when, note=''):
        """Compare one expected row with the database."""

        existing = list(Transaction.objects.filter(
            seller=store,
            transaction_type=transaction_type,
            description=description,
        ))

        label = f"[{store.store_name}] {transaction_type:<19} '{description}'"

        if not existing:
            self.log('ADD', f'{label}  Rs {amount}  dated {when:%Y-%m-%d} {note}')
            self.counts['add'] += 1

            row = Transaction.objects.create(
                seller=store,
                transaction_type=transaction_type,
                description=description,
                amount=amount,
                status='COMPLETED',
            )

            if when:
                Transaction.objects.filter(pk=row.pk).update(created_at=when)

            return

        if len(existing) > 1:
            return  # reported by report_duplicates

        row = existing[0]

        if row.amount != amount:

            if self.fix_amounts:
                self.log('CORRECT', f'{label}  Rs {row.amount} -> Rs {amount}')
                self.counts['fix'] += 1
                row.amount = amount
                row.save(update_fields=['amount'])
            else:
                self.log(
                    'DIFFERS',
                    f'{label}  has Rs {row.amount}, expected Rs {amount}'
                    f'  (use --fix-amounts to correct)'
                )
                self.counts['differs'] += 1

    # --------------------------------------------------------

    def check_expected_rows(self):
        delivered_at = {
            shipment.order_id: shipment.delivered_at
            for shipment in Shipment.objects.exclude(delivered_at=None)
        }

        # Sales and commission of delivered orders
        for order in Order.objects.filter(status='DELIVERED').order_by('id'):

            if order.id in delivered_at:
                when, note = delivered_at[order.id], ''
            else:
                when = order.created_at
                note = '(no delivery record: dated from order creation)'

            for store, (sale, commission) in delivery_amounts(order).items():

                if sale > 0:
                    self.check_row(
                        store, 'SALE', f'Order #{order.id}', sale, when, note,
                    )

                if commission > 0:
                    self.check_row(
                        store, 'COMMISSION',
                        f'Platform commission - Order #{order.id}',
                        commission, when, note,
                    )

        # Paid payouts
        for payout in Payout.objects.filter(status='PAID').order_by('id'):
            self.check_row(
                payout.seller, 'PAYOUT', f'Payout #{payout.id}',
                payout.amount, payout.paid_at or payout.created_at,
            )

        # Processed refunds of delivered orders
        refunds = Refund.objects.filter(
            status='PROCESSED',
            order__status='DELIVERED',
        ).select_related('order').order_by('id')

        for refund in refunds:
            when = refund.processed_at or refund.requested_at

            for store, amount, commission_back in refund_amounts_by_store(refund):

                self.check_row(
                    store, 'REFUND', f'Refund #{refund.id}', amount, when,
                )

                if commission_back > 0:
                    self.check_row(
                        store, 'COMMISSION_REVERSAL',
                        f'Commission reversed - Refund #{refund.id}',
                        commission_back, when,
                    )

    # --------------------------------------------------------

    def report_duplicates(self):
        groups = defaultdict(list)

        for row in Transaction.objects.all().order_by('id'):
            key = (row.seller_id, row.transaction_type, row.description)
            groups[key].append(row)

        for (seller_id, transaction_type, description), rows in groups.items():
            if len(rows) > 1:
                ids = ', '.join(str(row.id) for row in rows)
                self.log(
                    'DUPLICATE',
                    f"seller {seller_id} {transaction_type} '{description}' "
                    f"appears {len(rows)} times (row ids {ids}) - NOT changed, "
                    f"please review"
                )
                self.counts['duplicate'] += 1

    def report_payout_rows_to_review(self):
        """Payout history is never deleted, only listed."""

        status_of = dict(Payout.objects.values_list('id', 'status'))

        for row in Transaction.objects.filter(transaction_type='PAYOUT'):
            match = re.fullmatch(r'Payout #(\d+)', row.description or '')

            if not match:
                continue

            payout_status = status_of.get(int(match.group(1)))

            if payout_status != 'PAID':
                self.log(
                    'REVIEW',
                    f"payout row '{row.description}' Rs {row.amount} exists "
                    f"but the payout is {payout_status or 'missing'} - kept"
                )
                self.counts['review'] += 1

    def remove_stale_refund_rows(self):
        valid_refund_ids = set(
            Refund.objects.filter(
                status='PROCESSED',
                order__status='DELIVERED',
            ).values_list('id', flat=True)
        )

        patterns = {
            'REFUND': r'Refund #(\d+)',
            'COMMISSION_REVERSAL': r'Commission reversed - Refund #(\d+)',
        }

        for transaction_type, pattern in patterns.items():
            for row in Transaction.objects.filter(
                transaction_type=transaction_type
            ):
                match = re.fullmatch(pattern, row.description or '')

                if match and int(match.group(1)) not in valid_refund_ids:
                    self.log(
                        'REMOVE',
                        f"[{row.seller.store_name}] {transaction_type} "
                        f"'{row.description}' Rs {row.amount} "
                        f"(refund is not a processed refund of a delivered order)"
                    )
                    self.counts['remove'] += 1
                    row.delete()