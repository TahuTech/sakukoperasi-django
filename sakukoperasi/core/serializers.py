from rest_framework import serializers
from .models import (
    Jaminan,
    Loan,
    LoanPayment,
    LoanPenalty,
    Member,
    Nasabah,
    Savings,
    SavingsInterestRule,
    SavingsProduct,
    SavingsTransaction,
)


def money_field(**kwargs):
    return serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True, **kwargs)


class SavingsProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavingsProduct
        fields = ('id', 'code', 'name', 'min_balance', 'allow_withdrawal', 'is_active')


class SavingsInterestRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavingsInterestRule
        fields = (
            'id', 'product', 'effective_from', 'annual_rate', 'basis',
            'min_balance_for_interest', 'notes', 'created_at',
        )

    def validate(self, attrs):
        # Aturan yang sudah dipakai posting bunga dikunci (riwayat audit); buat aturan baru.
        if self.instance and self.instance.postings.exists():
            raise serializers.ValidationError(
                'Aturan ini sudah dipakai untuk posting bunga. Buat aturan baru dengan tanggal berlaku baru.'
            )
        return attrs


class NasabahSerializer(serializers.ModelSerializer):
    id_anggota = serializers.CharField(source='member.id_member', read_only=True, default=None)

    class Meta:
        model = Nasabah
        fields = ('id', 'name', 'nik', 'address', 'phone_number', 'member', 'id_anggota', 'created_at')
        extra_kwargs = {'name': {'required': False, 'allow_blank': True}}

    def validate(self, attrs):
        member = attrs.get('member', getattr(self.instance, 'member', None))
        if not (attrs.get('name') or getattr(self.instance, 'name', '') or member):
            raise serializers.ValidationError({'name': 'Nama nasabah wajib diisi (atau pilih anggota).'})
        return attrs


class SavingsSerializer(serializers.ModelSerializer):
    nama_nasabah = serializers.CharField(source='nasabah.name', read_only=True)
    jenis_simpanan = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = Savings
        fields = (
            'id', 'account_number', 'nasabah', 'nama_nasabah', 'product', 'jenis_simpanan',
            'balance', 'is_active', 'opened_date', 'closed_date', 'created_at', 'updated_at',
        )

    def validate(self, attrs):
        # Pemilik & jenis simpanan dikunci setelah rekening dibuka.
        if self.instance:
            for field in ('nasabah', 'product'):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError({field: 'Tidak dapat diubah setelah rekening dibuka.'})
        return attrs


class SavingsTransactionSerializer(serializers.ModelSerializer):
    nomor_rekening = serializers.CharField(source='savings.account_number', read_only=True)
    nama_nasabah = serializers.CharField(source='savings.nasabah.name', read_only=True)
    recorded_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = SavingsTransaction
        fields = (
            'id', 'savings', 'nomor_rekening', 'nama_nasabah', 'transaction_type', 'amount',
            'balance_before', 'balance_after', 'transaction_date', 'notes',
            'interest_period', 'interest_rule', 'recorded_by', 'created_at',
        )
        read_only_fields = ('balance_before', 'balance_after')

    def validate_transaction_type(self, value):
        if value == SavingsTransaction.TransactionType.INTEREST:
            raise serializers.ValidationError('Bunga hanya dapat diposting oleh sistem.')
        return value


class MemberSerializer(serializers.ModelSerializer):
    rekening_simpanan = serializers.SerializerMethodField()

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
            'rekening_simpanan',
        )
        # Status aktif hanya diubah lewat endpoint nonaktifkan, bukan edit biasa.
        read_only_fields = ('is_active', 'inactive_date')
        extra_kwargs = {
            'id_week': {'required': False, 'allow_blank': True},
            'id_month': {'required': False, 'allow_blank': True},
        }

    def get_rekening_simpanan(self, obj):
        """Rekening simpanan milik anggota (lewat data nasabah); kosong jika belum menabung."""
        # Reverse OneToOne yang belum ada melempar exception turunan AttributeError → None.
        nasabah = getattr(obj, 'nasabah', None)
        if nasabah is None:
            return []
        return [
            {
                'id': account.id,
                'account_number': account.account_number,
                'jenis_simpanan': account.product.name,
                'balance': f'{account.balance:.2f}',
                'is_active': account.is_active,
            }
            for account in nasabah.rekening.all()
        ]


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
