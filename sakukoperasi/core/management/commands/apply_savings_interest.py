from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.savings_interest import month_bounds, post_monthly_interest


class Command(BaseCommand):
    help = 'Posting bunga simpanan bulanan sesuai Aturan Bunga yang berlaku (jalankan tiap awal bulan).'

    def add_arguments(self, parser):
        parser.add_argument('--period', help='Periode YYYY-MM (default: bulan lalu).')
        parser.add_argument('--dry-run', action='store_true', help='Hitung saja tanpa menyimpan transaksi.')

    def handle(self, *args, **options):
        today = timezone.localdate()
        if options['period']:
            try:
                period = datetime.strptime(options['period'], '%Y-%m').date()
            except ValueError as exc:
                raise CommandError('Format --period harus YYYY-MM.') from exc
        else:
            # Hari terakhir bulan lalu → awal bulan lalu.
            period = (today.replace(day=1) - timedelta(days=1)).replace(day=1)

        _, period_end = month_bounds(period.year, period.month)
        if period_end >= today:
            raise CommandError(f'Periode {period:%Y-%m} belum selesai; bunga hanya untuk bulan yang sudah lewat.')

        results = post_monthly_interest(period.year, period.month, dry_run=options['dry_run'])
        posted = [r for r in results if r[2] in ('diposting', 'simulasi')]
        for savings, amount, status in posted:
            self.stdout.write(f'  {savings.account_number} {savings.nasabah.name}: Rp {amount:,.0f} ({status})')

        total = sum(amount for _, amount, _ in posted)
        label = 'Simulasi' if options['dry_run'] else 'Posting'
        self.stdout.write(self.style.SUCCESS(
            f'{label} bunga {period:%Y-%m}: {len(posted)} rekening, total Rp {total:,.0f}. '
            f'{len(results) - len(posted)} rekening dilewati.'
        ))
