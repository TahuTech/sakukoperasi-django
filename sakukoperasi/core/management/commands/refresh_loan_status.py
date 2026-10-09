from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Loan


class Command(BaseCommand):
    help = 'Hitung ulang status semua pinjaman yang belum lunas (jalankan harian via cron).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            help='Tanggal acuan YYYY-MM-DD (default: hari ini). Berguna untuk pengujian.',
        )

    def handle(self, *args, **options):
        try:
            as_of = date.fromisoformat(options['date']) if options['date'] else timezone.localdate()
        except ValueError as exc:
            raise CommandError('Format --date harus YYYY-MM-DD.') from exc

        changed = 0
        loans = Loan.objects.exclude(status=Loan.LoanStatus.LUNAS)
        for loan in loans.iterator():
            previous = loan.status
            if loan.refresh_status(as_of) != previous:
                changed += 1

        self.stdout.write(self.style.SUCCESS(f'Status pinjaman diperbarui per {as_of}: {changed} berubah.'))
