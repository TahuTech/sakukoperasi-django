"""
Simpanan pokok & wajib anggota: kewajiban, tunggakan, setoran, dan proses keluar.

Kewajiban dihitung dari `SavingsDueRate` (nominal berversi):
- Pokok  : nominal yang berlaku pada tanggal buka akun, dibayar sekali (boleh dicicil).
- Wajib  : nominal tiap bulan sejak bulan buka akun s.d. bulan lalu (bulan berjalan = tagihan bulan ini).
           Untuk anggota yang sudah keluar, dihitung s.d. bulan sebelum tanggal nonaktif.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Loan,
    LoanPenalty,
    MemberSavingsAccount,
    SavingsDueRate,
    SavingsProduct,
    SavingsTransaction,
)

ZERO = Decimal('0')


def first_of_month(value):
    return value.replace(day=1)


def iter_months(start, end):
    """Tanggal 1 tiap bulan dari bulan `start` s.d. bulan `end` (inklusif)."""
    current, last = first_of_month(start), first_of_month(end)
    while current <= last:
        yield current
        current = date(current.year + current.month // 12, current.month % 12 + 1, 1)


def previous_month(value):
    first = first_of_month(value)
    return date(first.year - 1, 12, 1) if first.month == 1 else date(first.year, first.month - 1, 1)


def total_deposited(savings):
    return savings.transactions.filter(
        transaction_type=SavingsTransaction.TransactionType.DEPOSIT,
    ).aggregate(total=Sum('amount'))['total'] or ZERO


def obligation(account, product, as_of):
    """Total kewajiban yang sudah jatuh tempo (pokok: saat buka; wajib: s.d. bulan lalu)."""
    if product.billing == SavingsProduct.Billing.ONCE:
        return SavingsDueRate.amount_for(product, account.opened_date)
    if product.billing != SavingsProduct.Billing.MONTHLY:
        return ZERO

    last_month = previous_month(as_of)
    member = account.member
    if not member.is_active and member.inactive_date:
        last_month = min(last_month, previous_month(member.inactive_date))
    if last_month < first_of_month(account.opened_date):
        return ZERO
    return sum(
        (SavingsDueRate.amount_for(product, month) for month in iter_months(account.opened_date, last_month)),
        ZERO,
    )


def summary(account, as_of=None):
    """Ringkasan per jenis (pokok/wajib): saldo, total setor, kewajiban, tunggakan, tagihan bulan ini."""
    as_of = as_of or timezone.localdate()
    result = {}
    for savings in account.sub_accounts.select_related('product'):
        product = savings.product
        deposited = total_deposited(savings)
        due = obligation(account, product, as_of)
        arrears = max(due - deposited, ZERO)

        if product.billing == SavingsProduct.Billing.ONCE:
            # Pokok langsung jatuh tempo saat akun dibuka.
            this_month = ZERO
        elif account.is_active and account.member.is_active and as_of >= account.opened_date:
            overpaid = max(deposited - due, ZERO)
            this_month = max(SavingsDueRate.amount_for(product, first_of_month(as_of)) - overpaid, ZERO)
        else:
            this_month = ZERO

        result[product.code] = {
            'nomor_rekening': savings.account_number,
            'saldo': savings.balance,
            'total_setor': deposited,
            'kewajiban': due,
            'tunggakan': arrears,
            'tagihan_bulan_ini': this_month,
        }
    return result


def deposit(account, product_code, amount=None, on_date=None, notes='', user=None):
    """
    Setor simpanan pokok/wajib. Jika `amount` kosong, dipakai total yang harus dibayar
    (tunggakan + tagihan bulan ini).
    """
    if product_code not in MemberSavingsAccount.SUB_ACCOUNT_PRODUCTS:
        raise ValidationError({'jenis': 'Jenis harus pokok atau wajib.'})
    if not account.is_active:
        raise ValidationError('Akun simpanan anggota sudah ditutup.')

    if amount is None:
        info = summary(account, on_date)[product_code]
        amount = info['tunggakan'] + info['tagihan_bulan_ini']
        if amount <= 0:
            raise ValidationError({'amount': 'Tidak ada kewajiban yang perlu dibayar; isi jumlah setoran.'})

    return SavingsTransaction.objects.create(
        savings=account.sub_account(product_code),
        transaction_type=SavingsTransaction.TransactionType.DEPOSIT,
        amount=amount,
        transaction_date=on_date or timezone.localdate(),
        notes=notes,
        recorded_by=user,
    )


def process_exit(account, user=None, on_date=None):
    """
    Anggota keluar: kembalikan seluruh simpanan pokok & wajib lalu tutup akun.
    Ditolak bila masih ada pinjaman belum lunas atau denda belum dibayar.
    """
    on_date = on_date or timezone.localdate()
    with transaction.atomic():
        account = MemberSavingsAccount.objects.select_for_update().select_related('member').get(pk=account.pk)
        if not account.is_active:
            raise ValidationError('Akun simpanan anggota sudah ditutup.')

        member = account.member
        if Loan.objects.filter(member=member).exclude(status=Loan.LoanStatus.LUNAS).exists():
            raise ValidationError('Anggota masih memiliki pinjaman yang belum lunas.')
        if LoanPenalty.objects.filter(loan__member=member, is_paid=False).exists():
            raise ValidationError('Anggota masih memiliki denda yang belum dibayar.')

        if member.is_active:
            member.deactivate(on_date)

        refunds = []
        for savings in account.sub_accounts.select_related('product'):
            savings.refresh_from_db()
            if savings.balance > 0:
                refunds.append(SavingsTransaction.objects.create(
                    savings=savings,
                    transaction_type=SavingsTransaction.TransactionType.WITHDRAWAL,
                    amount=savings.balance,
                    transaction_date=on_date,
                    notes='Pengembalian simpanan anggota keluar',
                    recorded_by=user,
                ))
            savings.close(on_date)

        account.is_active = False
        account.closed_date = on_date
        account.save(update_fields=['is_active', 'closed_date', 'updated_at'])
    return refunds
