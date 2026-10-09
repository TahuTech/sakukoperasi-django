from django.core.management.base import BaseCommand

from core.models import LoanRule, LoanRateTable


LOAN_RULES = [
    {
        'loan_type': LoanRule.LoanType.WEEKLY,
        'max_loan_amount': 1_000_000,
        'max_installments': 10,
        'interest_rate': 10.00,
    },
    {
        'loan_type': LoanRule.LoanType.MONTHLY,
        'max_loan_amount': 5_000_000,
        'max_installments': 12,
        'interest_rate': 0.00,
    },
]

# (loan_amount, installment_count, installment_amount, admin_fee)
MONTHLY_RATE_TABLES = [
    (1_000_000, 6,  187_000, 50_000),
    (1_000_000, 10, 120_000, 50_000),
    (1_000_000, 12, 104_000, 50_000),

    (1_250_000, 6,  234_000, 55_000),
    (1_250_000, 10, 150_000, 55_000),
    (1_250_000, 12, 130_000, 55_000),

    (1_500_000, 6,  280_000, 60_000),
    (1_500_000, 10, 180_000, 60_000),
    (1_500_000, 12, 155_000, 60_000),

    (1_750_000, 6,  372_000, 65_000),
    (1_750_000, 10, 210_000, 65_000),
    (1_750_000, 12, 181_000, 65_000),

    (2_000_000, 6,  374_000, 70_000),
    (2_000_000, 10, 240_000, 70_000),
    (2_000_000, 12, 207_000, 70_000),

    (2_250_000, 6,  420_000, 75_000),
    (2_250_000, 10, 270_000, 75_000),
    (2_250_000, 12, 232_500, 75_000),

    (2_500_000, 6,  467_000, 80_000),
    (2_500_000, 10, 300_000, 80_000),
    (2_500_000, 12, 259_000, 80_000),

    (2_750_000, 6,  514_000, 85_000),
    (2_750_000, 10, 330_000, 85_000),
    (2_750_000, 12, 285_000, 85_000),

    (3_000_000, 6,  560_000, 90_000),
    (3_000_000, 10, 360_000, 90_000),
    (3_000_000, 12, 310_000, 90_000),

    (3_250_000, 6,  607_000, 95_000),
    (3_250_000, 10, 390_000, 95_000),
    (3_250_000, 12, 336_000, 95_000),

    (3_500_000, 6,  654_000, 100_000),
    (3_500_000, 10, 420_000, 100_000),
    (3_500_000, 12, 362_000, 100_000),

    (3_750_000, 6,  700_000, 105_000),
    (3_750_000, 10, 450_000, 105_000),
    (3_750_000, 12, 387_500, 105_000),

    (4_000_000, 6,  747_000, 110_000),
    (4_000_000, 10, 480_000, 110_000),
    (4_000_000, 12, 414_000, 110_000),

    (4_250_000, 6,  794_000, 115_000),
    (4_250_000, 10, 510_000, 115_000),
    (4_250_000, 12, 440_000, 115_000),

    (4_500_000, 6,  850_000, 120_000),
    (4_500_000, 10, 540_000, 120_000),
    (4_500_000, 12, 465_000, 120_000),

    (4_750_000, 6,  887_000, 125_000),
    (4_750_000, 10, 570_000, 125_000),
    (4_750_000, 12, 491_000, 125_000),

    (5_000_000, 6,  934_000, 130_000),
    (5_000_000, 10, 600_000, 130_000),
    (5_000_000, 12, 517_000, 130_000),
]


class Command(BaseCommand):
    help = 'Seed default loan rules and monthly rate tables.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete rate tables that are not used by any loan before seeding.',
        )

    def handle(self, *args, **options):
        if options['reset']:
            # Tarif yang sudah dipakai pinjaman dilindungi (PROTECT), jadi hanya hapus yang belum terpakai.
            unused = LoanRateTable.objects.filter(loans__isnull=True)
            deleted, _ = unused.delete()
            kept = LoanRateTable.objects.count()
            self.stdout.write(self.style.WARNING(f'{deleted} unused rate tables cleared.'))
            if kept:
                self.stdout.write(self.style.WARNING(f'{kept} rate tables kept because they are used by loans.'))

        for data in LOAN_RULES:
            rule, created = LoanRule.objects.update_or_create(
                loan_type=data['loan_type'],
                defaults={
                    'max_loan_amount': data['max_loan_amount'],
                    'max_installments': data['max_installments'],
                    'interest_rate': data['interest_rate'],
                },
            )
            label = 'Created' if created else 'Updated'
            self.stdout.write(
                self.style.SUCCESS(
                    f"{label} LoanRule: {rule}"
                )
            )

        monthly_rule = LoanRule.objects.get(loan_type=LoanRule.LoanType.MONTHLY)

        for loan_amount, installment_count, installment_amount, admin_fee in MONTHLY_RATE_TABLES:
            entry, created = LoanRateTable.objects.update_or_create(
                loan_rule=monthly_rule,
                loan_amount=loan_amount,
                installment_count=installment_count,
                defaults={
                    'installment_amount': installment_amount,
                    'admin_fee': admin_fee,
                },
            )
            label = 'Created' if created else 'Updated'
            self.stdout.write(
                self.style.SUCCESS(
                    f"{label} LoanRateTable: {entry}"
                )
            )

        self.stdout.write(self.style.SUCCESS('Loan rules seeded successfully.'))
