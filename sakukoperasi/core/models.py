from decimal import Decimal

from django.db import IntegrityError, models, transaction
from django.db.models import IntegerField, Max
from django.db.models.functions import Cast
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator


# Jumlah percobaan ulang saat ID otomatis bentrok karena request bersamaan.
ID_GENERATION_RETRIES = 10


def save_with_generated_ids(generate_ids, do_save):
    """
    Simpan instance yang memiliki ID berurutan hasil `Max() + 1`.

    Dua request bersamaan bisa menghasilkan ID yang sama; constraint unique di DB
    akan menolak salah satunya (IntegrityError). Dalam kasus itu ID dihitung ulang
    dan penyimpanan dicoba lagi. `generate_ids` mengembalikan True jika ada ID yang
    dibuat otomatis; jika tidak, bentrok bukan berasal dari generator sehingga tidak di-retry.
    """
    for attempt in range(ID_GENERATION_RETRIES):
        has_generated_ids = generate_ids()
        try:
            # Savepoint agar transaksi luar tetap bisa dipakai setelah IntegrityError.
            with transaction.atomic():
                do_save()
            return
        except IntegrityError:
            if not has_generated_ids or attempt == ID_GENERATION_RETRIES - 1:
                raise


def next_sequence(queryset, field):
    """Nilai numerik terbesar dari `field` (CharField berisi angka) + 1."""
    max_id = (
        queryset.filter(**{f'{field}__regex': r'^\d+$'})
        .annotate(id_num=Cast(field, IntegerField()))
        .aggregate(max_id=Max('id_num'))['max_id']
        or 0
    )
    return max_id + 1


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
        auto_week = not self.id_week
        auto_month = not self.id_month

        if not auto_week:
            if not str(self.id_week).isdigit():
                raise ValidationError({'id_week': 'ID mingguan harus berisi hanya angka.'})
            normalized = int(self.id_week)
            if normalized < 1 or normalized > 999:
                raise ValidationError({'id_week': 'ID mingguan harus antara 001 dan 999.'})
            self.id_week = f"{normalized:03d}"

        if not auto_month:
            if not str(self.id_month).isdigit():
                raise ValidationError({'id_month': 'ID bulanan harus berisi hanya angka.'})
            normalized_month = int(self.id_month)
            if normalized_month < 1 or normalized_month > 9999:
                raise ValidationError({'id_month': 'ID bulanan harus antara 0001 dan 9999.'})
            self.id_month = f"{normalized_month:04d}"

        def generate_ids():
            if auto_week:
                next_id = next_sequence(Member.objects.all(), 'id_week')
                if next_id > 999:
                    raise ValidationError({'id_week': 'ID mingguan sudah mencapai batas maksimal 999.'})
                self.id_week = f"{next_id:03d}"
            if auto_month:
                next_month = next_sequence(Member.objects.all(), 'id_month')
                if next_month > 9999:
                    raise ValidationError({'id_month': 'ID bulanan sudah mencapai batas maksimal 9999.'})
                self.id_month = f"{next_month:04d}"
            return auto_week or auto_month

        save_with_generated_ids(generate_ids, lambda: super(Member, self).save(*args, **kwargs))


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
        verbose_name='Nomor Anggota',
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


class MonthlyLoan(models.Model):
    class LoanStatus(models.TextChoices):
        PROSES = 'proses', 'Proses'
        LUNAS = 'lunas', 'Lunas'
        TELAT = 'telat', 'Telat'

    loan_number = models.PositiveIntegerField(
        unique=True,
        editable=False,
        verbose_name='Nomor Pinjaman Bulanan',
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name='monthly_loans',
        verbose_name='Nomor Anggota',
    )
    loan_rate_table = models.ForeignKey(
        LoanRateTable,
        on_delete=models.PROTECT,
        related_name='monthly_loans',
        verbose_name='Daftar Pinjaman Bulanan',
    )
    jaminan = models.ForeignKey(
        Jaminan,
        on_delete=models.PROTECT,
        related_name='monthly_loans',
        verbose_name='ID Jaminan',
    )
    loan_date = models.DateField(verbose_name='Tanggal Peminjaman')
    installment_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        editable=False,
        verbose_name='Jumlah Angsuran',
    )
    loan_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        editable=False,
        verbose_name='Jumlah Pinjaman',
    )
    installment_duration = models.PositiveIntegerField(
        editable=False,
        verbose_name='Lama Angsuran',
    )
    status = models.CharField(
        max_length=10,
        choices=LoanStatus.choices,
        default=LoanStatus.PROSES,
        verbose_name='Status Pinjaman',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.loan_rate_table and self.loan_rate_table.loan_rule.loan_type != LoanRule.LoanType.MONTHLY:
            raise ValidationError('Pinjaman bulanan hanya boleh memakai LoanRule tipe bulanan.')

        if self.member_id and self.jaminan_id and self.jaminan.member_id != self.member_id:
            raise ValidationError('ID jaminan harus milik anggota yang sama dengan nomor anggota pinjaman.')

    def save(self, *args, **kwargs):
        auto_number = not self.loan_number

        if self.loan_rate_table_id:
            self.loan_amount = self.loan_rate_table.loan_amount
            self.installment_amount = self.loan_rate_table.installment_amount
            self.installment_duration = self.loan_rate_table.installment_count

        def generate_ids():
            if auto_number:
                max_loan_number = MonthlyLoan.objects.aggregate(max_number=Max('loan_number'))['max_number'] or 0
                self.loan_number = max_loan_number + 1
            return auto_number

        def do_save():
            self.full_clean()
            super(MonthlyLoan, self).save(*args, **kwargs)

        save_with_generated_ids(generate_ids, do_save)

    def __str__(self):
        return f"PB-{self.loan_number:06d} - {self.member.id_member} - {self.get_status_display()}"

    class Meta:
        ordering = ['-loan_date', '-created_at']
        verbose_name = 'Pinjaman Bulanan'
        verbose_name_plural = 'Pinjaman Bulanan'


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
        validators=[MinValueValidator(Decimal('0.01'), 'Jumlah transaksi harus lebih dari 0.')],
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

    IMMUTABLE_MESSAGE = 'Transaksi tabungan tidak dapat diubah atau dihapus. Buat transaksi koreksi.'

    def clean(self):
        """Validasi awal untuk form (admin). Pengecekan final tetap di save() dengan row lock."""
        if not self._state.adding:
            raise ValidationError(self.IMMUTABLE_MESSAGE)
        if (
            self.savings_id
            and self.amount
            and self.transaction_type == self.TransactionType.WITHDRAWAL
            and self.amount > self.savings.balance
        ):
            raise ValidationError(
                {'amount': f'Saldo tidak cukup. Saldo tersedia: Rp {self.savings.balance:,.0f}'}
            )

    def save(self, *args, **kwargs):
        """
        Catat transaksi dan perbarui saldo Savings secara atomik.

        Transaksi bersifat append-only: koreksi dilakukan dengan transaksi pembalik,
        sehingga riwayat balance_before/balance_after selalu konsisten dengan saldo.
        """
        if not self._state.adding:
            raise ValidationError(self.IMMUTABLE_MESSAGE)

        amount = Decimal(self.amount)
        if amount <= 0:
            raise ValidationError({'amount': 'Jumlah transaksi harus lebih dari 0.'})

        with transaction.atomic():
            # Kunci baris Savings agar transaksi bersamaan tidak saling menimpa saldo.
            savings = Savings.objects.select_for_update().get(pk=self.savings_id)
            self.balance_before = savings.balance

            if self.transaction_type == self.TransactionType.DEPOSIT:
                self.balance_after = self.balance_before + amount
            elif self.transaction_type == self.TransactionType.WITHDRAWAL:
                self.balance_after = self.balance_before - amount
                if self.balance_after < 0:
                    raise ValidationError(
                        {'amount': f'Saldo tidak cukup. Saldo tersedia: Rp {self.balance_before:,.0f}'}
                    )
            else:
                raise ValidationError({'transaction_type': 'Jenis transaksi tidak valid.'})

            super().save(*args, **kwargs)

            savings.balance = self.balance_after
            savings.save(update_fields=['balance', 'updated_at'])

        self.savings = savings

    def delete(self, *args, **kwargs):
        raise ValidationError(self.IMMUTABLE_MESSAGE)

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
