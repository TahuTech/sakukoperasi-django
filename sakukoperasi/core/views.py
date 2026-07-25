from rest_framework import viewsets
from .models import Jaminan, Member, MonthlyLoan
from .serializers import JaminanSerializer, MemberSerializer, MonthlyLoanSerializer


class MemberViewSet(viewsets.ModelViewSet):
    queryset = Member.objects.select_related('savings').all()
    serializer_class = MemberSerializer


class JaminanViewSet(viewsets.ModelViewSet):
    queryset = Jaminan.objects.select_related('member').all()
    serializer_class = JaminanSerializer


class MonthlyLoanViewSet(viewsets.ModelViewSet):
    queryset = MonthlyLoan.objects.select_related('member', 'jaminan', 'loan_rate_table', 'loan_rate_table__loan_rule').all()
    serializer_class = MonthlyLoanSerializer
