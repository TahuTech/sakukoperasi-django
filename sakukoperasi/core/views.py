from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .models import Jaminan, Loan, LoanPayment, LoanPenalty, Member
from .serializers import (
    JaminanSerializer,
    LoanPaymentSerializer,
    LoanPenaltySerializer,
    LoanSerializer,
    MemberSerializer,
)


class MemberViewSet(viewsets.ModelViewSet):
    queryset = Member.objects.select_related('tabungan').all()
    serializer_class = MemberSerializer

    @action(detail=True, methods=['post'])
    def nonaktifkan(self, request, pk=None):
        """Nonaktifkan anggota yang keluar; data keuangan tetap tersimpan."""
        if not request.user.has_perm('member.change_member'):
            raise PermissionDenied()
        member = self.get_object()
        member.deactivate()
        return Response(self.get_serializer(member).data, status=status.HTTP_200_OK)


class JaminanViewSet(viewsets.ModelViewSet):
    queryset = Jaminan.objects.select_related('member').all()
    serializer_class = JaminanSerializer


class BaseLoanViewSet(viewsets.ModelViewSet):
    """Pinjaman per jenis; jenis diturunkan dari endpoint dan divalidasi terhadap tarif."""

    loan_type = None
    serializer_class = LoanSerializer

    def get_queryset(self):
        return Loan.objects.select_related(
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
    """Filter `?loan=<id>` dan isi `recorded_by` dari user yang login."""

    def get_queryset(self):
        queryset = super().get_queryset()
        loan_id = self.request.query_params.get('loan')
        if loan_id:
            queryset = queryset.filter(loan_id=loan_id)
        return queryset

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


class LoanPenaltyViewSet(LoanRecordViewSetMixin, viewsets.ModelViewSet):
    queryset = LoanPenalty.objects.select_related('loan', 'recorded_by')
    serializer_class = LoanPenaltySerializer
