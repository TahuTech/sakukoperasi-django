from rest_framework import serializers
from .models import Jaminan, Member, Savings


class SavingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Savings
        fields = ('id', 'balance', 'created_at', 'updated_at')


class MemberSerializer(serializers.ModelSerializer):
    tabungan = SavingsSerializer(read_only=True)

    class Meta:
        model = Member
        fields = (
            'id',
            'id_member',
            'id_week',
            'id_month',
            'name',
            'address',
            'phone_number',
            'tabungan',
        )
        extra_kwargs = {
            'id_week': {'required': False, 'allow_blank': True},
            'id_month': {'required': False, 'allow_blank': True},
        }


class JaminanSerializer(serializers.ModelSerializer):
    nomor_anggota = serializers.CharField(source='member.id_member', read_only=True)

    class Meta:
        model = Jaminan
        fields = ('id', 'member', 'nomor_anggota', 'jenis_penjamin', 'keterangan', 'created_at')
