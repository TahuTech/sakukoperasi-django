from django.db import models
from django.db.models import IntegerField, Max
from django.db.models.functions import Cast
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator


class Member(models.Model):
    id_member = models.CharField(max_length=50, unique=True, verbose_name='ID Anggota')
    id_week = models.CharField(
        max_length=3,
        unique=True,
        blank=True,
        verbose_name='ID Mingguan',
        validators=[RegexValidator(r'^\d{3}$', 'ID mingguan harus tepat 3 digit (001-999).')],
    )
    id_month = models.CharField(
        max_length=4,
        unique=True,
        blank=True,
        verbose_name='ID Bulanan',
        validators=[RegexValidator(r'^\d{4}$', 'ID bulanan harus tepat 4 digit (0001-9999).')],
    )
    name = models.CharField(max_length=255, verbose_name='Nama')
    address = models.TextField(verbose_name='Alamat')
    phone_number = models.CharField(max_length=20, verbose_name='Nomor Telepon')
    def __str__(self):
        return f"{self.id_member} - {self.name}"
    
    class Meta:
        verbose_name = 'Anggota'
        verbose_name_plural = 'Anggota'

    def save(self, *args, **kwargs):
        if not self.id_week:
            max_id = (
                Member.objects.filter(id_week__regex=r'^\d+$')
                .annotate(id_num=Cast('id_week', IntegerField()))
                .aggregate(max_id=Max('id_num'))['max_id']
                or 0
            )
            next_id = max_id + 1
            if next_id > 999:
                raise ValidationError('ID mingguan sudah mencapai batas maksimal 999.')
            self.id_week = f"{next_id:03d}"
        else:
            if not str(self.id_week).isdigit():
                raise ValidationError('ID mingguan harus berisi hanya angka.')
            normalized = int(self.id_week)
            if normalized < 1 or normalized > 999:
                raise ValidationError('ID mingguan harus antara 001 dan 999.')
            self.id_week = f"{normalized:03d}"

        if not self.id_month:
            max_month = (
                Member.objects.filter(id_month__regex=r'^\d+$')
                .annotate(id_num=Cast('id_month', IntegerField()))
                .aggregate(max_id=Max('id_num'))['max_id']
                or 0
            )
            next_month = max_month + 1
            if next_month > 9999:
                raise ValidationError('ID bulanan sudah mencapai batas maksimal 9999.')
            self.id_month = f"{next_month:04d}"
        else:
            if not str(self.id_month).isdigit():
                raise ValidationError('ID bulanan harus berisi hanya angka.')
            normalized_month = int(self.id_month)
            if normalized_month < 1 or normalized_month > 9999:
                raise ValidationError('ID bulanan harus antara 0001 dan 9999.')
            self.id_month = f"{normalized_month:04d}"

        super().save(*args, **kwargs)


class LoanRule(models.Model):
    class LoanType(models.TextChoices):
        WEEKLY = 'weekly', 'Mingguan'
        MONTHLY = 'monthly', 'Bulanan'

    loan_type = models.CharField(
        max_length=10,
        choices=LoanType.choices,
        unique=True,
    )
    max_loan_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text='Maksimal jumlah pinjaman',
    )
    max_installments = models.PositiveIntegerField(
        help_text='Jumlah maksimal cicilan',
    )
    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text='Bunga pinjaman dalam persen (%)',
    )

    def __str__(self):
        return f"{self.get_loan_type_display()} - Maks {self.max_loan_amount} / {self.max_installments}x cicilan"
    
    class Meta:
        verbose_name = 'Aturan Pinjaman'
        verbose_name_plural = 'Aturan Pinjaman'


class LoanRateTable(models.Model):
    loan_rule = models.ForeignKey(
        LoanRule,
        on_delete=models.CASCADE,
        related_name='rate_tables',
        limit_choices_to={'loan_type': LoanRule.LoanType.MONTHLY},
        help_text='Aturan pinjaman (hanya bulanan)',
    )
    loan_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text='Jumlah pokok pinjaman',
    )
    installment_count = models.PositiveIntegerField(
        help_text='Jumlah cicilan (bulan)',
    )
    installment_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text='Besar angsuran per bulan',
    )
    admin_fee = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text='Biaya administrasi',
    )

    class Meta:
        unique_together = ('loan_rule', 'loan_amount', 'installment_count')
        verbose_name = 'Tabel Tarif Pinjaman'
        verbose_name_plural = 'Tabel Tarif Pinjaman'

    def __str__(self):
        return (
            f"{self.loan_amount} / {self.installment_count}x "
            f"→ angsuran {self.installment_amount}, admin {self.admin_fee}"
        )


class Jaminan(models.Model):
    class JenisPenjamin(models.TextChoices):
        BPKB = 'bpkb', 'BPKB'
        SURAT_TANAH = 'surat_tanah', 'Surat Tanah'
        LAINNYA = 'lainnya', 'Lainnya'

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name='jaminan',
        verbose_name='Anggota',
    )
    jenis_penjamin = models.CharField(
        max_length=20,
        choices=JenisPenjamin.choices,
        verbose_name='Jenis Penjamin',
    )
    keterangan = models.TextField(
        blank=True,
        verbose_name='Keterangan',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.member.id_member} - {self.get_jenis_penjamin_display()}"

    class Meta:
        verbose_name = 'Jaminan'
        verbose_name_plural = 'Jaminan'


class Savings(models.Model):
    """Akun tabungan anggota."""
    member = models.OneToOneField(
        Member,
        on_delete=models.CASCADE,
        related_name='tabungan',
    )
    balance = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text='Saldo tabungan saat ini',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Tabungan {self.member.id_member} - {self.member.name} (Rp {self.balance:,.0f})"

    class Meta:
        verbose_name = 'Tabungan'
        verbose_name_plural = 'Tabungan'


class SavingsTransaction(models.Model):
    """Detail setiap transaksi tabungan (setor/ambil)."""
    class TransactionType(models.TextChoices):
        DEPOSIT = 'deposit', 'Setor'
        WITHDRAWAL = 'withdrawal', 'Ambil'

    savings = models.ForeignKey(
        Savings,
        on_delete=models.CASCADE,
        related_name='transactions',
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text='Jumlah uang transaksi',
    )
    balance_before = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text='Saldo sebelum transaksi',
    )
    balance_after = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text='Saldo sesudah transaksi',
    )
    transaction_date = models.DateField(
        help_text='Tanggal transaksi',
    )
    notes = models.TextField(
        blank=True,
        help_text='Catatan transaksi (opsional)',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        """Otomatis hitung balance_before dan balance_after, serta update Savings."""
        from decimal import Decimal

        # Ambil saldo terakhir dari Savings
        if not self.balance_before:
            self.balance_before = self.savings.balance

        # Hitung balance_after berdasarkan tipe transaksi
        if self.transaction_type == self.TransactionType.DEPOSIT:
            self.balance_after = self.balance_before + Decimal(self.amount)
        elif self.transaction_type == self.TransactionType.WITHDRAWAL:
            self.balance_after = self.balance_before - Decimal(self.amount)
            if self.balance_after < 0:
                raise ValidationError(
                    f'Saldo tidak cukup. Saldo tersedia: Rp {self.balance_before:,.0f}'
                )

        # Simpan transaction
        super().save(*args, **kwargs)

        # Update saldo di Savings
        self.savings.balance = self.balance_after
        self.savings.save(update_fields=['balance', 'updated_at'])

    def __str__(self):
        return (
            f"{self.get_transaction_type_display()} - "
            f"{self.savings.member.id_member} - "
            f"Rp {self.amount:,.0f} ({self.transaction_date})"
        )

    class Meta:
        ordering = ['-transaction_date', '-created_at']
        verbose_name = 'Transaksi Tabungan'
        verbose_name_plural = 'Transaksi Tabungan'
