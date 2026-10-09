from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .filters import (
    JaminanFilter,
    LoanFilter,
    LoanPaymentFilter,
    LoanPenaltyFilter,
    MemberFilter,
    MemberSavingsAccountFilter,
    SavingsFilter,
    SavingsTransactionFilter,
)
from .member_savings import deposit, process_exit
from .models import (
    Jaminan,
    Loan,
    LoanPayment,
    LoanPenalty,
    Member,
    MemberSavingsAccount,
    Nasabah,
    Savings,
    SavingsDueRate,
    SavingsInterestRule,
    SavingsProduct,
    SavingsTransaction,
)
from .serializers import (
    JaminanSerializer,
    LoanPaymentSerializer,
    LoanPenaltySerializer,
    LoanSerializer,
    MemberSavingsAccountSerializer,
    MemberSavingsDepositSerializer,
    MemberSerializer,
    NasabahSerializer,
    SavingsDueRateSerializer,
    SavingsInterestRuleSerializer,
    SavingsProductSerializer,
    SavingsSerializer,
    SavingsTransactionSerializer,
)


class MemberViewSet(viewsets.ModelViewSet):
    queryset = Member.objects.select_related('akun_simpanan').prefetch_related('nasabah__rekening__product').order_by('id_member')
    serializer_class = MemberSerializer
    filterset_class = MemberFilter
    search_fields = ('id_member', 'id_week', 'id_month', 'name', 'phone_number')
    ordering_fields = ('id_member', 'id_week', 'id_month', 'name')

    @action(detail=True, methods=['post'])
    def nonaktifkan(self, request, pk=None):
        """Nonaktifkan anggota yang keluar; data keuangan tetap tersimpan."""
        if not request.user.has_perm('member.change_member'):
            raise PermissionDenied()
        member = self.get_object()
        member.deactivate()
        return Response(self.get_serializer(member).data, status=status.HTTP_200_OK)


class JaminanViewSet(viewsets.ModelViewSet):
    queryset = Jaminan.objects.select_related('member').order_by('-created_at')
    serializer_class = JaminanSerializer
    filterset_class = JaminanFilter
    search_fields = ('member__id_member', 'member__name', 'keterangan')


class BaseLoanViewSet(viewsets.ModelViewSet):
    """Pinjaman per jenis; jenis diturunkan dari endpoint dan divalidasi terhadap tarif."""

    loan_type = None
    serializer_class = LoanSerializer
    filterset_class = LoanFilter
    search_fields = ('loan_number', 'member__id_member', 'member__name')
    ordering_fields = ('loan_date', 'loan_number', 'status', 'created_at')

    def get_queryset(self):
        # with_totals: total bayar & denda dihitung dalam satu query, bukan per baris.
        return Loan.objects.with_totals().select_related(
            'member', 'jaminan', 'loan_rate_table', 'loan_rate_table__loan_rule',
        ).filter(loan_type=self.loan_type)

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'loan_type': self.loan_type}


class MonthlyLoanViewSet(BaseLoanViewSet):
    loan_type = Loan.LoanType.MONTHLY
    queryset = Loan.objects.filter(loan_type=Loan.LoanType.MONTHLY)


class WeeklyLoanViewSet(BaseLoanViewSet):
    loan_type = Loan.LoanType.WEEKLY
    queryset = Loan.objects.filter(loan_type=Loan.LoanType.WEEKLY)


class LoanRecordViewSetMixin:
    """Isi `recorded_by` dari user yang login."""

    search_fields = ('loan__loan_number', 'loan__member__id_member', 'loan__member__name')

    def perform_create(self, serializer):
        serializer.save(recorded_by=self.request.user)


class LoanPaymentViewSet(
    LoanRecordViewSetMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Pembayaran bersifat append-only: tidak ada update/delete."""

    queryset = LoanPayment.objects.select_related('loan', 'recorded_by')
    serializer_class = LoanPaymentSerializer
    filterset_class = LoanPaymentFilter


class LoanPenaltyViewSet(LoanRecordViewSetMixin, viewsets.ModelViewSet):
    queryset = LoanPenalty.objects.select_related('loan', 'recorded_by')
    serializer_class = LoanPenaltySerializer
    filterset_class = LoanPenaltyFilter


class SavingsProductViewSet(viewsets.ModelViewSet):
    queryset = SavingsProduct.objects.all()
    serializer_class = SavingsProductSerializer


class SavingsInterestRuleViewSet(viewsets.ModelViewSet):
    queryset = SavingsInterestRule.objects.select_related('product')
    serializer_class = SavingsInterestRuleSerializer

    def perform_destroy(self, instance):
        if instance.postings.exists():
            raise PermissionDenied('Aturan sudah dipakai untuk posting bunga dan tidak dapat dihapus.')
        instance.delete()


class NasabahViewSet(viewsets.ModelViewSet):
    """Cari nasabah: `?search=` nama, NIK, telepon, atau ID anggota."""

    queryset = Nasabah.objects.select_related('member')
    serializer_class = NasabahSerializer
    search_fields = ('name', 'nik', 'phone_number', 'member__id_member')


class SavingsViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Rekening simpanan; cari dengan `?search=` nomor rekening atau nama nasabah. Ditutup, tidak dihapus."""

    queryset = Savings.objects.select_related('nasabah', 'product')
    serializer_class = SavingsSerializer
    filterset_class = SavingsFilter
    ordering_fields = ('account_number', 'balance', 'opened_date')
    search_fields = ('account_number', 'nasabah__name', 'nasabah__phone_number', 'nasabah__member__id_member')

    @action(detail=True, methods=['post'])
    def tutup(self, request, pk=None):
        if not request.user.has_perm('member.change_savings'):
            raise PermissionDenied()
        account = self.get_object()
        account.close()
        return Response(self.get_serializer(account).data, status=status.HTTP_200_OK)


class SavingsTransactionViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Transaksi append-only. Filter `?rekening=<id>`, cari `?search=` nomor rekening / nama nasabah."""

    queryset = SavingsTransaction.objects.select_related('savings__nasabah', 'recorded_by')
    serializer_class = SavingsTransactionSerializer
    filterset_class = SavingsTransactionFilter
    search_fields = ('savings__account_number', 'savings__nasabah__name')
    ordering_fields = ('transaction_date', 'created_at', 'amount')

    def perform_create(self, serializer):
        serializer.save(recorded_by=self.request.user)


class SavingsDueRateViewSet(viewsets.ModelViewSet):
    queryset = SavingsDueRate.objects.select_related('product')
    serializer_class = SavingsDueRateSerializer


class MemberSavingsAccountViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Akun simpanan pokok & wajib anggota. Cari dengan `?search=` nama/ID anggota/ID akun.
    Setor lewat `POST {id}/setor/` (pilih jenis), pengembalian lewat `POST {id}/proses-keluar/`.
    """

    queryset = MemberSavingsAccount.objects.select_related('member')
    serializer_class = MemberSavingsAccountSerializer
    filterset_class = MemberSavingsAccountFilter
    search_fields = ('account_number', 'member__name', 'member__id_member')

    def _require_perm(self, perm):
        if not self.request.user.has_perm(perm):
            raise PermissionDenied()

    @action(detail=True, methods=['post'])
    def setor(self, request, pk=None):
        self._require_perm('member.add_savingstransaction')
        account = self.get_object()
        payload = MemberSavingsDepositSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        tx = deposit(
            account,
            data['jenis'],
            amount=data.get('amount'),
            on_date=data.get('transaction_date'),
            notes=data.get('notes', ''),
            user=request.user,
        )
        return Response(SavingsTransactionSerializer(tx).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='proses-keluar')
    def proses_keluar(self, request, pk=None):
        self._require_perm('member.change_member')
        account = self.get_object()
        refunds = process_exit(account, user=request.user)
        account.refresh_from_db()
        return Response({
            'akun': self.get_serializer(account).data,
            'pengembalian': SavingsTransactionSerializer(refunds, many=True).data,
        })
