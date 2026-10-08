import calendar
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.db.models import IntegerField, Max, Q, Sum
from django.db.models.functions import Cast
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.utils import timezone


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


def add_months(value, months):
    """Tambah `months` bulan; tanggal di-clamp ke akhir bulan (31 Jan + 1 bulan = 28/29 Feb)."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def positive_amount_validator(label):
    return MinValueValidator(Decimal('0.01'), f'{label} harus lebih dari 0.')


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
    # Anggota yang keluar dinonaktifkan, bukan dihapus, agar riwayat keuangan tetap utuh.
    is_active = models.BooleanField(default=True, verbose_name='Aktif')
    inactive_date = models.DateField(null=True, blank=True, verbose_name='Tanggal Nonaktif')

    def __str__(self):
        return f"{self.id_member} - {self.name}"

    class Meta:
        verbose_name = 'Anggota'
        verbose_name_plural = 'Anggota'

    def deactivate(self, on_date=None):
        self.is_active = False
        self.inactive_date = on_date or timezone.localdate()
        self.save(update_fields=['is_active', 'inactive_date'])

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
        help_text='Aturan pinjaman (mingguan atau bulanan)',
    )
    loan_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text='Jumlah pokok pinjaman',
    )
    installment_count = models.PositiveIntegerField(
        help_text='Jumlah cicilan (minggu untuk pinjaman mingguan, bulan untuk bulanan)',
    )
    installment_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text='Besar angsuran per periode (minggu/bulan)',
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
            f"{self.loan_rule.get_loan_type_display()} {self.loan_amount} / {self.installment_count}x "
            f"→ angsuran {self.installment_amount}, admin {self.admin_fee}"
        )

    def clean(self):
        if not self.loan_rule_id:
            return
        rule = self.loan_rule
        errors = {}
        if self.loan_amount is not None and self.loan_amount > rule.max_loan_amount:
            errors['loan_amount'] = f'Melebihi maksimal pinjaman {rule.get_loan_type_display().lower()} ({rule.max_loan_amount}).'
        if self.installment_count is not None and self.installment_count > rule.max_installments:
            errors['installment_count'] = f'Melebihi maksimal cicilan ({rule.max_installments}x).'
        if self.admin_fee is not None and self.loan_amount is not None and self.admin_fee >= self.loan_amount:
            errors['admin_fee'] = 'Biaya admin harus lebih kecil dari jumlah pinjaman.'
        if errors:
            raise ValidationError(errors)


class Jaminan(models.Model):
    class JenisPenjamin(models.TextChoices):
        BPKB = 'bpkb', 'BPKB'
        SURAT_TANAH = 'surat_tanah', 'Surat Tanah'
        LAINNYA = 'lainnya', 'Lainnya'

    member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
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


class Loan(models.Model):
    """
    Pinjaman mingguan atau bulanan.

    Nilai pinjaman di-snapshot dari LoanRateTable saat dibuat, sehingga perubahan tarif
    tidak mengubah pinjaman yang sudah berjalan. Pembayaran bebas nominal; status dihitung
    dari total pembayaran dibanding angsuran yang sudah jatuh tempo.
    """

    LoanType = LoanRule.LoanType

    class LoanStatus(models.TextChoices):
        PROSES = 'proses', 'Proses'
        LUNAS = 'lunas', 'Lunas'
        TELAT = 'telat', 'Telat'

    NUMBER_PREFIX = {LoanType.WEEKLY: 'PM', LoanType.MONTHLY: 'PB'}

    loan_number = models.PositiveIntegerField(
        unique=True,
        editable=False,
        verbose_name='Nomor Pinjaman',
    )
    loan_type = models.CharField(
        max_length=10,
        choices=LoanType.choices,
        default=LoanType.MONTHLY,
        editable=False,
        verbose_name='Jenis Pinjaman',
        help_text='Otomatis mengikuti jenis aturan pada tarif pinjaman.',
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name='loans',
        verbose_name='Nomor Anggota',
    )
    loan_rate_table = models.ForeignKey(
        LoanRateTable,
        on_delete=models.PROTECT,
        related_name='loans',
        verbose_name='Tarif Pinjaman',
    )
    jaminan = models.ForeignKey(
        Jaminan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='loans',
        verbose_name='ID Jaminan',
        help_text='Wajib untuk pinjaman bulanan.',
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
    admin_fee = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        editable=False,
        verbose_name='Biaya Admin',
    )
    disbursed_amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        editable=False,
        verbose_name='Jumlah Dicairkan',
        help_text='Jumlah pinjaman dikurangi biaya admin.',
    )
    status = models.CharField(
        max_length=10,
        choices=LoanStatus.choices,
        default=LoanStatus.PROSES,
        editable=False,
        verbose_name='Status Pinjaman',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-loan_date', '-created_at']
        verbose_name = 'Pinjaman'
        verbose_name_plural = 'Pinjaman'
        constraints = [
            # Backstop di level DB untuk aturan di clean(), aman terhadap request bersamaan.
            models.UniqueConstraint(
                fields=['member', 'loan_type'],
                condition=~Q(status='lunas'),
                name='unique_active_loan_per_member_type',
                violation_error_message='Anggota masih memiliki pinjaman sejenis yang belum lunas.',
            ),
            models.UniqueConstraint(
                fields=['jaminan'],
                condition=Q(jaminan__isnull=False) & ~Q(status='lunas'),
                name='unique_jaminan_per_active_loan',
                violation_error_message='Jaminan masih dipakai pinjaman lain yang belum lunas.',
            ),
        ]

    def __str__(self):
        return f"{self.formatted_number} - {self.member.id_member} - {self.get_status_display()}"

    @property
    def formatted_number(self):
        if not self.loan_number:
            return None
        return f"{self.NUMBER_PREFIX.get(self.loan_type, 'P')}-{self.loan_number:06d}"

    # --- Ringkasan keuangan ---

    @property
    def total_due(self):
        """Total yang harus dibayar (pokok + bunga) sesuai tabel tarif."""
        return (self.installment_amount or 0) * (self.installment_duration or 0)

    @property
    def total_paid(self):
        if not self.pk:
            return Decimal('0')
        return self.payments.aggregate(total=Sum('amount'))['total'] or Decimal('0')

    @property
    def remaining(self):
        return max(self.total_due - self.total_paid, Decimal('0'))

    @property
    def unpaid_penalties(self):
        if not self.pk:
            return Decimal('0')
        return self.penalties.filter(is_paid=False).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    # --- Jadwal & status ---

    def due_dates(self):
        """Tanggal jatuh tempo tiap angsuran, dihitung dari tanggal pinjam (tanpa akumulasi pergeseran)."""
        for period in range(1, (self.installment_duration or 0) + 1):
            if self.loan_type == self.LoanType.WEEKLY:
                yield self.loan_date + timedelta(weeks=period)
            else:
                yield add_months(self.loan_date, period)

    def periods_due(self, as_of):
        return sum(1 for due_date in self.due_dates() if due_date <= as_of)

    def compute_status(self, as_of=None):
        as_of = as_of or timezone.localdate()
        paid = self.total_paid
        if paid >= self.total_due and self.unpaid_penalties == 0:
            return self.LoanStatus.LUNAS
        if paid < self.installment_amount * self.periods_due(as_of):
            return self.LoanStatus.TELAT
        return self.LoanStatus.PROSES

    def refresh_status(self, as_of=None):
        """Hitung ulang status dan simpan hanya kolom status (tanpa validasi ulang seluruh pinjaman)."""
        new_status = self.compute_status(as_of)
        if new_status != self.status:
            Loan.objects.filter(pk=self.pk).update(status=new_status, updated_at=timezone.now())
            self.status = new_status
        return new_status

    # --- Validasi & penyimpanan ---

    def apply_rate_table(self):
        rate = self.loan_rate_table
        self.loan_type = rate.loan_rule.loan_type
        self.loan_amount = rate.loan_amount
        self.installment_amount = rate.installment_amount
        self.installment_duration = rate.installment_count
        self.admin_fee = rate.admin_fee
        self.disbursed_amount = rate.loan_amount - rate.admin_fee

    def clean(self):
        adding = self._state.adding
        if adding and self.loan_rate_table_id:
            self.apply_rate_table()
        elif not adding:
            original = Loan.objects.only('member_id', 'loan_rate_table_id').get(pk=self.pk)
            if (original.member_id, original.loan_rate_table_id) != (self.member_id, self.loan_rate_table_id):
                raise ValidationError('Anggota dan tarif pinjaman tidak dapat diubah setelah pinjaman dibuat.')

        errors = {}
        if self.loan_type == self.LoanType.MONTHLY and not self.jaminan_id:
            errors['jaminan'] = 'Pinjaman bulanan wajib memakai jaminan.'
        elif self.member_id and self.jaminan_id and self.jaminan.member_id != self.member_id:
            errors['jaminan'] = 'ID jaminan harus milik anggota yang sama dengan nomor anggota pinjaman.'
        elif (
            self.jaminan_id
            and self.status != self.LoanStatus.LUNAS
            and Loan.objects.exclude(pk=self.pk)
            .filter(jaminan_id=self.jaminan_id)
            .exclude(status=self.LoanStatus.LUNAS)
            .exists()
        ):
            errors['jaminan'] = 'Jaminan masih dipakai pinjaman lain yang belum lunas.'

        if adding and self.member_id:
            if not self.member.is_active:
                errors['member'] = 'Anggota nonaktif tidak dapat mengajukan pinjaman.'
            elif (
                Loan.objects.filter(member_id=self.member_id, loan_type=self.loan_type)
                .exclude(status=self.LoanStatus.LUNAS)
                .exists()
            ):
                errors['member'] = (
                    f'Anggota masih memiliki pinjaman {self.get_loan_type_display().lower()} yang belum lunas.'
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        auto_number = not self.loan_number

        def generate_ids():
            if auto_number:
                max_loan_number = Loan.objects.aggregate(max_number=Max('loan_number'))['max_number'] or 0
                self.loan_number = max_loan_number + 1
            return auto_number

        def do_save():
            self.full_clean()
            super(Loan, self).save(*args, **kwargs)

        save_with_generated_ids(generate_ids, do_save)


class LoanPayment(models.Model):
    """Pembayaran pinjaman bebas nominal. Append-only: koreksi dilakukan oleh admin di level DB/akuntansi."""

    IMMUTABLE_MESSAGE = 'Pembayaran pinjaman tidak dapat diubah atau dihapus.'

    loan = models.ForeignKey(
        Loan,
        on_delete=models.PROTECT,
        related_name='payments',
        verbose_name='Pinjaman',
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[positive_amount_validator('Jumlah pembayaran')],
        verbose_name='Jumlah Bayar',
    )
    payment_date = models.DateField(default=timezone.localdate, verbose_name='Tanggal Bayar')
    notes = models.TextField(blank=True, verbose_name='Catatan')
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='+',
        verbose_name='Dicatat oleh',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-created_at']
        verbose_name = 'Pembayaran Pinjaman'
        verbose_name_plural = 'Pembayaran Pinjaman'

    def __str__(self):
        return f"{self.loan.formatted_number} - Rp {self.amount:,.0f} ({self.payment_date})"

    def clean(self):
        """Validasi awal untuk form; pengecekan final tetap di save() dengan row lock."""
        if not self._state.adding:
            raise ValidationError(self.IMMUTABLE_MESSAGE)
        if self.loan_id and self.amount and self.amount > self.loan.remaining:
            raise ValidationError({'amount': f'Melebihi sisa pinjaman (Rp {self.loan.remaining:,.0f}).'})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(self.IMMUTABLE_MESSAGE)
        if Decimal(self.amount) <= 0:
            raise ValidationError({'amount': 'Jumlah pembayaran harus lebih dari 0.'})

        with transaction.atomic():
            # Kunci pinjaman agar pembayaran bersamaan tidak melebihi sisa tagihan.
            loan = Loan.objects.select_for_update().get(pk=self.loan_id)
            if Decimal(self.amount) > loan.remaining:
                raise ValidationError({'amount': f'Melebihi sisa pinjaman (Rp {loan.remaining:,.0f}).'})
            super().save(*args, **kwargs)
            loan.refresh_status()
        self.loan = loan

    def delete(self, *args, **kwargs):
        raise ValidationError(self.IMMUTABLE_MESSAGE)


class LoanPenalty(models.Model):
    """Denda keterlambatan yang diinput manual petugas (aturan denda berbeda per anggota)."""

    loan = models.ForeignKey(
        Loan,
        on_delete=models.PROTECT,
        related_name='penalties',
        verbose_name='Pinjaman',
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[positive_amount_validator('Jumlah denda')],
        verbose_name='Jumlah Denda',
    )
    penalty_date = models.DateField(default=timezone.localdate, verbose_name='Tanggal Denda')
    reason = models.TextField(verbose_name='Alasan')
    is_paid = models.BooleanField(default=False, verbose_name='Sudah Dibayar')
    paid_date = models.DateField(null=True, blank=True, verbose_name='Tanggal Dibayar')
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='+',
        verbose_name='Dicatat oleh',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Setelah dibuat, hanya status pembayaran denda yang boleh berubah.
    IMMUTABLE_FIELDS = ('loan_id', 'amount', 'penalty_date', 'reason')

    class Meta:
        ordering = ['-penalty_date', '-created_at']
        verbose_name = 'Denda Pinjaman'
        verbose_name_plural = 'Denda Pinjaman'

    def __str__(self):
        status = 'lunas' if self.is_paid else 'belum dibayar'
        return f"{self.loan.formatted_number} - Denda Rp {self.amount:,.0f} ({status})"

    def clean(self):
        if self._state.adding:
            if self.loan_id and self.loan.status == Loan.LoanStatus.LUNAS:
                raise ValidationError('Pinjaman sudah lunas; denda tidak dapat ditambahkan.')
        else:
            original = LoanPenalty.objects.get(pk=self.pk)
            if any(getattr(original, field) != getattr(self, field) for field in self.IMMUTABLE_FIELDS):
                raise ValidationError('Hanya status pembayaran denda yang dapat diubah.')
            if original.is_paid and not self.is_paid:
                raise ValidationError('Denda yang sudah dibayar tidak dapat dibatalkan.')

        if self.is_paid and not self.paid_date:
            self.paid_date = timezone.localdate()
        if not self.is_paid:
            self.paid_date = None

    def save(self, *args, **kwargs):
        with transaction.atomic():
            loan = Loan.objects.select_for_update().get(pk=self.loan_id)
            self.full_clean()
            super().save(*args, **kwargs)
            loan.refresh_status()

    def delete(self, *args, **kwargs):
        if self.is_paid:
            raise ValidationError('Denda yang sudah dibayar tidak dapat dihapus.')
        with transaction.atomic():
            loan = Loan.objects.select_for_update().get(pk=self.loan_id)
            result = super().delete(*args, **kwargs)
            loan.refresh_status()
        return result


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
        on_delete=models.PROTECT,
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
