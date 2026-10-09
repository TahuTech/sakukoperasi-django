from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Member, Nasabah, Savings, SavingsProduct, SavingsTransaction

SAMPLE_ACCOUNT = 'SAMPLE-0001'


class Command(BaseCommand):
    help = 'Seed sample anggota + rekening simpanan sukarela dengan transaksi untuk testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Hapus data sample (anggota, nasabah, rekening, transaksi) sebelum seed.',
        )

    def handle(self, *args, **options):
        if options['reset']:
            # Data keuangan dilindungi (PROTECT); untuk data sample hapus dari level transaksi ke atas.
            SavingsTransaction.objects.filter(savings__account_number=SAMPLE_ACCOUNT).delete()
            Savings.objects.filter(account_number=SAMPLE_ACCOUNT).delete()
            Nasabah.objects.filter(member__id_member='SAMPLE001').delete()
            Member.objects.filter(id_member='SAMPLE001').delete()
            self.stdout.write(self.style.WARNING('Data sample dihapus.'))

        member, _ = Member.objects.get_or_create(
            id_member='SAMPLE001',
            defaults={
                'name': 'Anggota Sample',
                'address': 'Jl. Sample No. 1, Kota Sample',
                'phone_number': '08123456789',
            },
        )
        nasabah, _ = Nasabah.objects.get_or_create(member=member)
        product, _ = SavingsProduct.objects.get_or_create(
            code=SavingsProduct.VOLUNTARY, defaults={'name': 'Simpanan Sukarela'},
        )
        savings, created = Savings.objects.get_or_create(
            account_number=SAMPLE_ACCOUNT,
            defaults={'nasabah': nasabah, 'product': product, 'opened_date': timezone.localdate() - timedelta(days=10)},
        )

        if created:
            today = timezone.localdate()
            for days_ago, tx_type, amount, notes in [
                (5, SavingsTransaction.TransactionType.DEPOSIT, 500_000, 'Setor tunai'),
                (3, SavingsTransaction.TransactionType.DEPOSIT, 300_000, 'Setor dari iuran'),
                (1, SavingsTransaction.TransactionType.WITHDRAWAL, 100_000, 'Ambil untuk kebutuhan'),
                (0, SavingsTransaction.TransactionType.DEPOSIT, 250_000, 'Setor tabungan mingguan'),
            ]:
                SavingsTransaction.objects.create(
                    savings=savings,
                    transaction_type=tx_type,
                    amount=amount,
                    transaction_date=today - timedelta(days=days_ago),
                    notes=notes,
                )

        savings.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(
            f'Rekening {savings.account_number} - {nasabah.name}: saldo Rp {savings.balance:,.0f}, '
            f'{savings.transactions.count()} transaksi.'
        ))
