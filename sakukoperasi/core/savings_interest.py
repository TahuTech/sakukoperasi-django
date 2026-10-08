"""
Kerangka perhitungan bunga simpanan.

Aturan bunga disimpan di `SavingsInterestRule` (berversi per tanggal berlaku). Dasar saldo
dihitung dari riwayat transaksi oleh fungsi di `BASIS_CALCULATORS`; menambah metode baru
cukup dengan menambah satu fungsi di sini dan satu pilihan di `SavingsInterestRule.Basis`.
"""

import calendar
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.db.models import Case, F, Sum, When

from .models import Savings, SavingsInterestRule, SavingsTransaction

DAYS_IN_YEAR = Decimal('365')


def month_bounds(year, month):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def daily_balances(savings, start, end):
    """Saldo akhir hari untuk setiap tanggal dalam [start, end], dihitung dari jumlah bertanda transaksi."""
    signed = Case(
        When(transaction_type=SavingsTransaction.TransactionType.WITHDRAWAL, then=-F('amount')),
        default=F('amount'),
    )
    transactions = savings.transactions.all()
    balance = transactions.filter(transaction_date__lt=start).aggregate(total=Sum(signed))['total'] or Decimal('0')
    changes = dict(
        transactions.filter(transaction_date__range=(start, end))
        .values('transaction_date')
        .annotate(total=Sum(signed))
        .values_list('transaction_date', 'total')
    )

    balances = []
    day = start
    while day <= end:
        balance += changes.get(day, Decimal('0'))
        balances.append(balance)
        day += timedelta(days=1)
    return balances


def lowest_balance(savings, start, end):
    return min(daily_balances(savings, start, end))


def end_balance(savings, start, end):
    return daily_balances(savings, start, end)[-1]


def average_daily_balance(savings, start, end):
    balances = daily_balances(savings, start, end)
    return sum(balances) / len(balances)


Basis = SavingsInterestRule.Basis

BASIS_CALCULATORS = {
    Basis.LOWEST_BALANCE: lowest_balance,
    Basis.END_BALANCE: end_balance,
    Basis.AVERAGE_DAILY_BALANCE: average_daily_balance,
}


def calculate_interest(savings, start, end):
    """
    Hitung bunga satu rekening untuk periode [start, end].

    Mengembalikan (jumlah_bunga, aturan). Bunga = saldo dasar × bunga%/tahun × hari/365,
    dibulatkan ke bawah ke rupiah penuh. Tanpa aturan berlaku → (0, None).
    """
    rule = SavingsInterestRule.rule_for(savings.product, end)
    if rule is None or rule.annual_rate <= 0:
        return Decimal('0'), rule

    base = BASIS_CALCULATORS[rule.basis](savings, start, end)
    if base <= 0 or base < rule.min_balance_for_interest:
        return Decimal('0'), rule

    days = Decimal((end - start).days + 1)
    interest = base * rule.annual_rate / Decimal('100') * days / DAYS_IN_YEAR
    return interest.quantize(Decimal('1'), rounding=ROUND_DOWN), rule


def post_monthly_interest(year, month, dry_run=False):
    """
    Posting bunga bulanan ke semua rekening aktif. Aman dijalankan ulang:
    rekening yang sudah menerima bunga periode ini dilewati (dijaga juga oleh constraint DB).

    Mengembalikan list (rekening, jumlah, status) untuk laporan.
    """
    start, end = month_bounds(year, month)
    results = []
    accounts = Savings.objects.filter(is_active=True).select_related('product', 'nasabah')

    for savings in accounts.iterator():
        already_posted = savings.transactions.filter(
            transaction_type=SavingsTransaction.TransactionType.INTEREST, interest_period=start,
        ).exists()
        if already_posted:
            results.append((savings, Decimal('0'), 'sudah diposting'))
            continue

        amount, rule = calculate_interest(savings, start, end)
        if amount <= 0:
            results.append((savings, amount, 'tidak dapat bunga'))
            continue

        if not dry_run:
            with transaction.atomic():
                SavingsTransaction.objects.create(
                    savings=savings,
                    transaction_type=SavingsTransaction.TransactionType.INTEREST,
                    amount=amount,
                    transaction_date=end,
                    interest_period=start,
                    interest_rule=rule,
                    notes=f'Bunga {start:%m/%Y} ({rule.annual_rate}%/thn, {rule.get_basis_display().lower()})',
                )
        results.append((savings, amount, 'simulasi' if dry_run else 'diposting'))

    return results
