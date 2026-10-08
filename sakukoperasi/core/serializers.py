from rest_framework import serializers
from .models import Jaminan, Member, MonthlyLoan, Savings


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


class MonthlyLoanSerializer(serializers.ModelSerializer):
    nomor_anggota = serializers.CharField(source='member.id_member', read_only=True)
    nomor_pinjaman = serializers.SerializerMethodField()

    class Meta:
        model = MonthlyLoan
        fields = (
            'id',
            'loan_number',
            'nomor_pinjaman',
            'member',
            'nomor_anggota',
            'jaminan',
            'loan_rate_table',
            'loan_date',
            'loan_amount',
            'installment_amount',
            'installment_duration',
            'status',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('loan_number', 'loan_amount', 'installment_amount', 'installment_duration', 'created_at', 'updated_at')

    def get_nomor_pinjaman(self, obj):
        if obj.loan_number:
            return f"PB-{obj.loan_number:06d}"
        return None

    def validate(self, attrs):
        member = attrs.get('member') or getattr(self.instance, 'member', None)
        jaminan = attrs.get('jaminan') or getattr(self.instance, 'jaminan', None)

        if member and jaminan and jaminan.member_id != member.id:
            raise serializers.ValidationError({
                'jaminan': 'ID jaminan harus milik nomor anggota yang sama.'
            })

        return attrs
