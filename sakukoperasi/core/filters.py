"""FilterSet API (django-filter). Rentang tanggal: `?<field>_after=YYYY-MM-DD&<field>_before=YYYY-MM-DD`."""

import django_filters

from .models import (
    Jaminan,
    Loan,
    LoanPayment,
    LoanPenalty,
    Member,
    MemberSavingsAccount,
    Savings,
    SavingsTransaction,
)


class MemberFilter(django_filters.FilterSet):
    has_akun_simpanan = django_filters.BooleanFilter(
        field_name='akun_simpanan', lookup_expr='isnull', exclude=True, label='Punya akun simpanan pokok & wajib',
    )

    class Meta:
        model = Member
        fields = ('is_active',)


class JaminanFilter(django_filters.FilterSet):
    class Meta:
        model = Jaminan
        fields = ('member', 'jenis_penjamin')


class LoanFilter(django_filters.FilterSet):
    loan_date = django_filters.DateFromToRangeFilter()

    class Meta:
        model = Loan
        fields = ('member', 'status', 'jaminan')


class LoanPaymentFilter(django_filters.FilterSet):
    payment_date = django_filters.DateFromToRangeFilter()

    class Meta:
        model = LoanPayment
        fields = ('loan',)


class LoanPenaltyFilter(django_filters.FilterSet):
    penalty_date = django_filters.DateFromToRangeFilter()

    class Meta:
        model = LoanPenalty
        fields = ('loan', 'is_paid')


class SavingsFilter(django_filters.FilterSet):
    jenis = django_filters.CharFilter(field_name='product__code', label='Kode jenis simpanan')

    class Meta:
        model = Savings
        fields = ('nasabah', 'product', 'is_active', 'member_account')


class SavingsTransactionFilter(django_filters.FilterSet):
    rekening = django_filters.NumberFilter(field_name='savings_id', label='ID rekening')
    transaction_date = django_filters.DateFromToRangeFilter()

    class Meta:
        model = SavingsTransaction
        fields = ('rekening', 'transaction_type')


class MemberSavingsAccountFilter(django_filters.FilterSet):
    class Meta:
        model = MemberSavingsAccount
        fields = ('is_active', 'member')
