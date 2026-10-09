"""Ringkasan operasional untuk dasbor admin."""

from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.urls import reverse
from django.utils import timezone

from .models import Loan, LoanPenalty, Member, Savings, SavingsProduct, SavingsTransaction

ZERO = Decimal('0')


def _changelist(model_name, query=''):
    url = reverse(f'admin:member_{model_name}_changelist')
    return f'{url}?{query}' if query else url


def build_dashboard(user):
    """
    Kartu ringkasan untuk dasbor, hanya untuk data yang boleh dilihat user.
    Tiap kartu: label, nilai, keterangan, tautan, dan nada warna (neutral/success/warning/danger).
    """
    can = user.has_perm
    cards = []

    if can('member.view_member'):
        members = Member.objects.aggregate(
            active=Count('pk', filter=Q(is_active=True)),
            without_account=Count('pk', filter=Q(is_active=True, akun_simpanan__isnull=True)),
        )
        cards.append({
            'label': 'Anggota aktif',
            'value': members['active'],
            'note': 'Lihat daftar anggota',
            'url': _changelist('member', 'is_active__exact=1'),
            'tone': 'neutral',
        })
        cards.append({
            'label': 'Belum punya akun simpanan',
            'value': members['without_account'],
            'note': 'Akun simpanan pokok & wajib',
            'url': _changelist('member', 'is_active__exact=1&akun_simpanan=belum'),
            'tone': 'warning' if members['without_account'] else 'success',
        })

    if can('member.view_loan'):
        remaining = ExpressionWrapper(
            F('installment_amount') * F('installment_duration') - F('annotated_total_paid'),
            output_field=DecimalField(max_digits=15, decimal_places=2),
        )
        loans = Loan.objects.with_totals().exclude(status=Loan.LoanStatus.LUNAS).aggregate(
            active=Count('pk'),
            late=Count('pk', filter=Q(status=Loan.LoanStatus.TELAT)),
            outstanding=Sum(remaining),
        )
        cards.append({
            'label': 'Pinjaman berjalan',
            'value': loans['active'],
            'money': loans['outstanding'] or ZERO,
            'note': 'Total sisa pinjaman',
            'url': _changelist('loan', 'status__exact=proses'),
            'tone': 'neutral',
        })
        cards.append({
            'label': 'Pinjaman telat',
            'value': loans['late'],
            'note': 'Perlu ditindaklanjuti',
            'url': _changelist('loan', 'status__exact=telat'),
            'tone': 'danger' if loans['late'] else 'success',
        })

    if can('member.view_loanpenalty'):
        penalties = LoanPenalty.objects.filter(is_paid=False).aggregate(count=Count('pk'), total=Sum('amount'))
        cards.append({
            'label': 'Denda belum dibayar',
            'value': penalties['count'],
            'money': penalties['total'] or ZERO,
            'note': 'Total denda terutang',
            'url': _changelist('loanpenalty', 'is_paid__exact=0'),
            'tone': 'warning' if penalties['count'] else 'success',
        })

    if can('member.view_savings'):
        balances = dict(
            Savings.objects.filter(is_active=True)
            .values('product__code')
            .annotate(total=Sum('balance'))
            .values_list('product__code', 'total')
        )
        for product in SavingsProduct.objects.filter(is_active=True).order_by('pk'):
            cards.append({
                'label': f'Saldo {product.name.lower()}',
                'money': balances.get(product.code) or ZERO,
                'note': 'Rekening aktif',
                'url': _changelist('savings', f'product__id__exact={product.pk}'),
                'tone': 'neutral',
            })

    if can('member.view_savingstransaction'):
        today = timezone.localdate()
        cards.append({
            'label': 'Transaksi simpanan hari ini',
            'value': SavingsTransaction.objects.filter(transaction_date=today).count(),
            'note': today.strftime('%d/%m/%Y'),
            'url': _changelist('savingstransaction', f'transaction_date__day={today.day}'
                               f'&transaction_date__month={today.month}&transaction_date__year={today.year}'),
            'tone': 'neutral',
        })

    return cards
