from datetime import date, timedelta
from django.core.management.base import BaseCommand, CommandError

from core.models import Member, Savings, SavingsTransaction


class Command(BaseCommand):
    help = 'Seed sample member dengan tabungan dan transaksi untuk testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Hapus sample member dan data terkaitnya sebelum seed.',
        )

    def handle(self, *args, **options):
        # Check if Savings table exists
        try:
            if options['reset']:
                # Transaksi dilindungi (PROTECT); untuk data sample hapus transaksi lebih dulu.
                SavingsTransaction.objects.filter(savings__member__id_member='SAMPLE001').delete()
                Member.objects.filter(id_member='SAMPLE001').delete()
                self.stdout.write(self.style.WARNING('Sample member dihapus.'))
        except Exception as e:
            if 'member_savings' in str(e) or 'relation' in str(e):
                raise CommandError(
                    'Database belum di-migrate!\n'
                    'Jalankan: ./tahu migrate\n'
                    'Kemudian coba lagi: ./tahu seed_sample_member'
                )
            raise

        # Buat sample member
        member, member_created = Member.objects.get_or_create(
            id_member='SAMPLE001',
            defaults={
                'name': 'Anggota Sample',
                'address': 'Jl. Sample No. 1, Kota Sample',
                'phone_number': '08123456789',
            },
        )
        
        if member_created:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Member dibuat: {member}')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Member sudah ada: {member}')
            )

        # Savings otomatis dibuat via signal, tapi pastikan ada
        savings, savings_created = Savings.objects.get_or_create(
            member=member,
            defaults={'balance': 0},
        )
        
        if savings_created:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Akun Savings dibuat')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Akun Savings sudah ada')
            )

        # Clear transaksi lama jika reset
        if options['reset']:
            SavingsTransaction.objects.filter(savings=savings).delete()
            savings.balance = 0
            savings.save()

        # Buat sample transactions untuk testing
        sample_transactions = [
            {
                'transaction_type': SavingsTransaction.TransactionType.DEPOSIT,
                'amount': 500_000,
                'transaction_date': date.today() - timedelta(days=5),
                'notes': 'Setor tunai',
            },
            {
                'transaction_type': SavingsTransaction.TransactionType.DEPOSIT,
                'amount': 300_000,
                'transaction_date': date.today() - timedelta(days=3),
                'notes': 'Setor dari iuran',
            },
            {
                'transaction_type': SavingsTransaction.TransactionType.WITHDRAWAL,
                'amount': 100_000,
                'transaction_date': date.today() - timedelta(days=1),
                'notes': 'Ambil untuk kebutuhan',
            },
            {
                'transaction_type': SavingsTransaction.TransactionType.DEPOSIT,
                'amount': 250_000,
                'transaction_date': date.today(),
                'notes': 'Setor tabungan mingguan',
            },
        ]

        for tx_data in sample_transactions:
            # Jangan buat transaksi duplikat
            existing = SavingsTransaction.objects.filter(
                savings=savings,
                transaction_type=tx_data['transaction_type'],
                amount=tx_data['amount'],
                transaction_date=tx_data['transaction_date'],
            ).exists()

            if not existing:
                tx = SavingsTransaction(
                    savings=savings,
                    **tx_data
                )
                tx.save()
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  ✓ Transaksi: {tx.get_transaction_type_display()} '
                        f'Rp {tx.amount:,.0f} ({tx.transaction_date})'
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'  ~ Transaksi sudah ada: {tx_data["transaction_type"]} '
                        f'Rp {tx_data["amount"]:,.0f}'
                    )
                )

        # Refresh saldo dari database
        savings.refresh_from_db()
        
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(
            self.style.SUCCESS(
                f'Sample Member: {member.id_member} - {member.name}\n'
                f'Saldo Tabungan: Rp {savings.balance:,.0f}\n'
                f'Total Transaksi: {savings.transactions.count()}\n'
            )
        )
        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write(
            self.style.SUCCESS(
                'Silakan akses admin (http://localhost:8000/admin/) untuk lihat detail.\n'
                'Path: Savings atau SavingsTransaction'
            )
        )
