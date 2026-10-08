from rest_framework import serializers
from .models import Jaminan, Loan, LoanPayment, LoanPenalty, Member, Savings


def money_field(**kwargs):
    return serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True, **kwargs)


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
            'is_active',
            'inactive_date',
            'tabungan',
        )
        # Status aktif hanya diubah lewat endpoint nonaktifkan, bukan edit biasa.
        read_only_fields = ('is_active', 'inactive_date')
        extra_kwargs = {
            'id_week': {'required': False, 'allow_blank': True},
            'id_month': {'required': False, 'allow_blank': True},
        }


class JaminanSerializer(serializers.ModelSerializer):
    nomor_anggota = serializers.CharField(source='member.id_member', read_only=True)

    class Meta:
        model = Jaminan
        fields = ('id', 'member', 'nomor_anggota', 'jenis_penjamin', 'keterangan', 'created_at')


class LoanSerializer(serializers.ModelSerializer):
    """Serializer pinjaman; jenis pinjaman ditentukan oleh endpoint (context `loan_type`)."""

    nomor_anggota = serializers.CharField(source='member.id_member', read_only=True)
    nomor_pinjaman = serializers.CharField(source='loan_number', read_only=True)
    pinjaman_ke = serializers.IntegerField(source='sequence', read_only=True)
    total_due = money_field(label='Total Tagihan')
    total_paid = money_field(label='Total Dibayar')
    remaining = money_field(label='Sisa Pinjaman')
    unpaid_penalties = money_field(label='Denda Belum Dibayar')

    class Meta:
        model = Loan
        fields = (
            'id',
            'nomor_pinjaman',
            'pinjaman_ke',
            'loan_type',
            'member',
            'nomor_anggota',
            'jaminan',
            'loan_rate_table',
            'loan_date',
            'loan_amount',
            'admin_fee',
            'disbursed_amount',
            'installment_amount',
            'installment_duration',
            'total_due',
            'total_paid',
            'remaining',
            'unpaid_penalties',
            'status',
            'created_at',
            'updated_at',
        )
        # Field non-editable di model (snapshot tarif, status, jenis) otomatis read-only.
        read_only_fields = ('created_at', 'updated_at')

    def validate(self, attrs):
        loan_type = self.context.get('loan_type')
        rate_table = attrs.get('loan_rate_table')
        if rate_table and loan_type and rate_table.loan_rule.loan_type != loan_type:
            raise serializers.ValidationError({
                'loan_rate_table': f'Tarif harus untuk pinjaman {Loan.LoanType(loan_type).label.lower()}.'
            })

        member = attrs.get('member') or getattr(self.instance, 'member', None)
        jaminan = attrs.get('jaminan') or getattr(self.instance, 'jaminan', None)
        if member and jaminan and jaminan.member_id != member.id:
            raise serializers.ValidationError({
                'jaminan': 'ID jaminan harus milik nomor anggota yang sama.'
            })

        return attrs


class LoanPaymentSerializer(serializers.ModelSerializer):
    nomor_pinjaman = serializers.CharField(source='loan.formatted_number', read_only=True)
    recorded_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = LoanPayment
        fields = ('id', 'loan', 'nomor_pinjaman', 'amount', 'payment_date', 'notes', 'recorded_by', 'created_at')


class LoanPenaltySerializer(serializers.ModelSerializer):
    nomor_pinjaman = serializers.CharField(source='loan.formatted_number', read_only=True)
    recorded_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = LoanPenalty
        fields = (
            'id', 'loan', 'nomor_pinjaman', 'amount', 'penalty_date', 'reason',
            'is_paid', 'paid_date', 'recorded_by', 'created_at',
        )
