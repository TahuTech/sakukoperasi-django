from rest_framework import viewsets
from .models import Jaminan, Member
from .serializers import JaminanSerializer, MemberSerializer


class MemberViewSet(viewsets.ModelViewSet):
    queryset = Member.objects.select_related('tabungan').all()
    serializer_class = MemberSerializer


class JaminanViewSet(viewsets.ModelViewSet):
    queryset = Jaminan.objects.select_related('member').all()
    serializer_class = JaminanSerializer
