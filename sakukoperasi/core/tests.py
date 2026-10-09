import threading
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import (
    Jaminan,
    Loan,
    LoanPayment,
    LoanPenalty,
    LoanRateTable,
    LoanRule,
    Member,
    MemberSavingsAccount,
    Nasabah,
    Savings,
    SavingsDueRate,
    SavingsInterestRule,
    SavingsProduct,
    SavingsTransaction,
    add_months,
)
from .member_savings import deposit, process_exit, summary
from .savings_interest import calculate_interest, post_monthly_interest


def create_member(id_member='A001', **kwargs):
    return Member.objects.create(
        id_member=id_member,
        name=kwargs.pop('name', 'Anggota Uji'),
        address=kwargs.pop('address', 'Alamat'),
        phone_number=kwargs.pop('phone_number', '0800'),
        **kwargs,
    )


def voluntary_product():
    return SavingsProduct.objects.get(code=SavingsProduct.VOLUNTARY)  # dibuat oleh migrasi 0016


def create_account(account_number='TAB-001', name='Nasabah Uji', member=None, opened_date=date(2026, 1, 1)):
    nasabah = Nasabah.objects.create(name=name if not member else '', member=member)
    return Savings.objects.create(
        account_number=account_number, nasabah=nasabah, product=voluntary_product(), opened_date=opened_date,
    )


def add_transaction(savings, transaction_type, amount, on_date=None):
    return SavingsTransaction.objects.create(
        savings=savings,
        transaction_type=transaction_type,
        amount=Decimal(amount),
        transaction_date=on_date or timezone.localdate(),
    )


DEPOSIT = SavingsTransaction.TransactionType.DEPOSIT
WITHDRAWAL = SavingsTransaction.TransactionType.WITHDRAWAL


class SavingsTransactionTests(TestCase):
    def setUp(self):
        self.savings = create_account()

    def test_deposit_and_withdrawal_update_balance(self):
        tx1 = add_transaction(self.savings, DEPOSIT, '1000')
        tx2 = add_transaction(self.savings, WITHDRAWAL, '300')

        self.savings.refresh_from_db()
        self.assertEqual(self.savings.balance, Decimal('700'))
        self.assertEqual((tx1.balance_before, tx1.balance_after), (Decimal('0'), Decimal('1000')))
        self.assertEqual((tx2.balance_before, tx2.balance_after), (Decimal('1000'), Decimal('700')))

    def test_balance_before_always_taken_from_current_balance(self):
        add_transaction(self.savings, DEPOSIT, '500')
        tx = SavingsTransaction(
            savings=self.savings,
            transaction_type=DEPOSIT,
            amount=Decimal('100'),
            transaction_date=date.today(),
            balance_before=Decimal('999999'),  # nilai dari klien harus diabaikan
        )
        tx.save()
        self.assertEqual(tx.balance_before, Decimal('500'))
        self.assertEqual(tx.balance_after, Decimal('600'))

    def test_withdrawal_exceeding_balance_is_rejected(self):
        add_transaction(self.savings, DEPOSIT, '100')
        with self.assertRaises(ValidationError):
            add_transaction(self.savings, WITHDRAWAL, '101')
        self.savings.refresh_from_db()
        self.assertEqual(self.savings.balance, Decimal('100'))
        self.assertEqual(self.savings.transactions.count(), 1)

    def test_non_positive_amount_is_rejected(self):
        for amount in ('0', '-50'):
            with self.assertRaises(ValidationError):
                add_transaction(self.savings, DEPOSIT, amount)
        self.assertEqual(self.savings.transactions.count(), 0)

    def test_transaction_is_immutable(self):
        tx = add_transaction(self.savings, DEPOSIT, '100')
        tx.amount = Decimal('5000')
        with self.assertRaises(ValidationError):
            tx.save()
        with self.assertRaises(ValidationError):
            tx.delete()
        self.savings.refresh_from_db()
        self.assertEqual(self.savings.balance, Decimal('100'))


class MemberIdTests(TestCase):
    def test_ids_are_generated_sequentially(self):
        first = create_member('A001')
        second = create_member('A002')
        self.assertEqual((first.id_week, first.id_month), ('001', '0001'))
        self.assertEqual((second.id_week, second.id_month), ('002', '0002'))

    def test_manual_id_is_normalized(self):
        member = create_member('A001', id_week='7', id_month='12')
        self.assertEqual((member.id_week, member.id_month), ('007', '0012'))

    def test_generated_id_skips_ids_taken_meanwhile(self):
        create_member('A001')  # id_week 001
        create_member('A002', id_week='002', id_month='0002')
        third = create_member('A003')
        self.assertEqual(third.id_week, '003')


def create_rate(loan_type, loan_amount=1_000_000, installment_count=10, installment_amount=120_000, admin_fee=50_000):
    rule, _ = LoanRule.objects.get_or_create(
        loan_type=loan_type,
        defaults={'max_loan_amount': 5_000_000, 'max_installments': 12, 'interest_rate': 0},
    )
    return LoanRateTable.objects.create(
        loan_rule=rule,
        loan_amount=loan_amount,
        installment_count=installment_count,
        installment_amount=installment_amount,
        admin_fee=admin_fee,
    )


WEEKLY = Loan.LoanType.WEEKLY
MONTHLY = Loan.LoanType.MONTHLY


class LoanTestMixin:
    def setUp(self):
        self.member = create_member()
        self.jaminan = Jaminan.objects.create(member=self.member, jenis_penjamin=Jaminan.JenisPenjamin.BPKB)
        self.monthly_rate = create_rate(MONTHLY)
        self.weekly_rate = create_rate(WEEKLY, loan_amount=500_000, installment_count=10, installment_amount=55_000, admin_fee=10_000)

    def create_monthly(self, member=None, jaminan=None, loan_date=date(2026, 1, 31), **kwargs):
        return Loan.objects.create(
            member=member or self.member,
            jaminan=jaminan or self.jaminan,
            loan_rate_table=self.monthly_rate,
            loan_date=loan_date,
            **kwargs,
        )

    def create_weekly(self, member=None, loan_date=date(2026, 1, 1)):
        return Loan.objects.create(
            member=member or self.member, loan_rate_table=self.weekly_rate, loan_date=loan_date,
        )

    def pay(self, loan, amount):
        return LoanPayment.objects.create(loan=loan, amount=Decimal(amount), payment_date=date(2026, 1, 1))


class AddMonthsTests(TestCase):
    def test_clamps_to_end_of_month(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2028, 1, 31), 1), date(2028, 2, 29))
        self.assertEqual(add_months(date(2026, 1, 31), 2), date(2026, 3, 31))
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))


class LoanCreationTests(LoanTestMixin, TestCase):
    def test_snapshot_values_from_rate_table(self):
        loan = self.create_monthly()
        self.assertEqual(loan.loan_number, self.member.id_month)
        self.assertEqual(loan.sequence, 1)
        self.assertEqual(loan.loan_type, MONTHLY)
        self.assertEqual(loan.admin_fee, Decimal('50000'))
        self.assertEqual(loan.disbursed_amount, Decimal('950000'))
        self.assertEqual(loan.total_due, Decimal('1200000'))

    def test_rate_change_does_not_affect_existing_loan(self):
        loan = self.create_monthly()
        LoanRateTable.objects.filter(pk=self.monthly_rate.pk).update(installment_amount=999_999)
        loan.refresh_from_db()
        loan.loan_date = date(2026, 2, 1)
        loan.save()
        loan.refresh_from_db()
        self.assertEqual(loan.installment_amount, Decimal('120000'))

    def test_weekly_loan_without_jaminan(self):
        loan = self.create_weekly()
        self.assertEqual(loan.loan_type, WEEKLY)
        self.assertEqual(loan.loan_number, self.member.id_week)
        self.assertIsNone(loan.jaminan)

    def test_monthly_loan_requires_jaminan(self):
        with self.assertRaises(ValidationError):
            Loan.objects.create(member=self.member, loan_rate_table=self.monthly_rate, loan_date=date(2026, 1, 1))

    def test_one_active_loan_per_type(self):
        self.create_monthly()
        self.create_weekly()  # beda jenis tetap boleh
        second_jaminan = Jaminan.objects.create(member=self.member, jenis_penjamin=Jaminan.JenisPenjamin.LAINNYA)
        with self.assertRaises(ValidationError):
            self.create_monthly(jaminan=second_jaminan)
        with self.assertRaises(ValidationError):
            self.create_weekly()

    def test_new_loan_allowed_after_previous_is_paid_off(self):
        loan = self.create_monthly()
        self.pay(loan, loan.total_due)
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.LoanStatus.LUNAS)
        self.create_monthly()  # jaminan sama boleh dipakai lagi setelah lunas

    def test_jaminan_cannot_back_two_active_loans(self):
        self.create_monthly()
        # Pinjaman mingguan boleh berjaminan (opsional), tapi tidak dengan jaminan yang masih terikat.
        with self.assertRaises(ValidationError) as ctx:
            Loan.objects.create(
                member=self.member, jaminan=self.jaminan, loan_rate_table=self.weekly_rate, loan_date=date(2026, 1, 1),
            )
        self.assertIn('jaminan', ctx.exception.message_dict)

    def test_jaminan_must_belong_to_member(self):
        other = create_member('B001')
        with self.assertRaises(ValidationError) as ctx:
            self.create_monthly(member=other)
        self.assertIn('jaminan', ctx.exception.message_dict)

    def test_inactive_member_cannot_borrow(self):
        self.member.deactivate()
        with self.assertRaises(ValidationError):
            self.create_weekly()

    def test_member_and_rate_are_locked_after_creation(self):
        loan = self.create_weekly()
        loan.member = create_member('B001')
        with self.assertRaises(ValidationError):
            loan.save()


class LoanNumberTests(LoanTestMixin, TestCase):
    def test_number_follows_member_ids(self):
        member = create_member('B001', id_week='7', id_month='12')
        jaminan = Jaminan.objects.create(member=member, jenis_penjamin=Jaminan.JenisPenjamin.BPKB)

        weekly = self.create_weekly(member=member)
        monthly = self.create_monthly(member=member, jaminan=jaminan)

        self.assertEqual(weekly.loan_number, '007')
        self.assertEqual(monthly.loan_number, '0012')
        self.assertEqual(str(weekly), f'007 - B001 - {weekly.get_status_display()}')

    def test_repeat_loan_keeps_number_with_next_sequence(self):
        first = self.create_weekly()
        self.pay(first, first.total_due)

        second = self.create_weekly(loan_date=date(2026, 6, 1))
        self.assertEqual(second.loan_number, first.loan_number)
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual(second.number_label, f'{second.loan_number} (ke-2)')

    def test_sequence_is_counted_per_type(self):
        self.create_weekly()
        monthly = self.create_monthly()
        self.assertEqual(monthly.sequence, 1)

    def test_number_is_snapshot_when_member_id_changes(self):
        loan = self.create_weekly()
        original = loan.loan_number
        self.member.id_week = '999'
        self.member.save()
        loan.refresh_from_db()
        self.assertEqual(loan.loan_number, original)


class LoanStatusTests(LoanTestMixin, TestCase):
    def test_monthly_becomes_late_after_due_date(self):
        loan = self.create_monthly(loan_date=date(2026, 1, 31))  # jatuh tempo 28 Feb, 31 Mar, ...
        self.assertEqual(loan.refresh_status(date(2026, 2, 27)), Loan.LoanStatus.PROSES)
        self.assertEqual(loan.refresh_status(date(2026, 2, 28)), Loan.LoanStatus.TELAT)

        self.pay(loan, 120_000)
        self.assertEqual(loan.refresh_status(date(2026, 3, 30)), Loan.LoanStatus.PROSES)
        self.assertEqual(loan.refresh_status(date(2026, 3, 31)), Loan.LoanStatus.TELAT)

    def test_weekly_uses_seven_day_periods(self):
        loan = self.create_weekly(loan_date=date(2026, 1, 1))
        self.assertEqual(loan.refresh_status(date(2026, 1, 7)), Loan.LoanStatus.PROSES)
        self.assertEqual(loan.refresh_status(date(2026, 1, 8)), Loan.LoanStatus.TELAT)
        self.pay(loan, 110_000)  # bayar 2 minggu sekaligus
        self.assertEqual(loan.refresh_status(date(2026, 1, 15)), Loan.LoanStatus.PROSES)
        self.assertEqual(loan.refresh_status(date(2026, 1, 22)), Loan.LoanStatus.TELAT)

    def test_unpaid_penalty_blocks_lunas(self):
        loan = self.create_monthly()
        penalty = LoanPenalty.objects.create(loan=loan, amount=Decimal('25000'), reason='Telat bulan Feb')
        self.pay(loan, loan.total_due)
        loan.refresh_from_db()
        self.assertEqual(loan.remaining, Decimal('0'))
        self.assertNotEqual(loan.status, Loan.LoanStatus.LUNAS)

        penalty.is_paid = True
        penalty.save()
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.LoanStatus.LUNAS)
        self.assertIsNotNone(penalty.paid_date)

    def test_refresh_loan_status_command(self):
        loan = self.create_weekly(loan_date=date(2026, 1, 1))
        call_command('refresh_loan_status', '--date', '2026-02-01', stdout=StringIO())
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.LoanStatus.TELAT)


class LoanPaymentTests(LoanTestMixin, TestCase):
    def test_overpayment_is_rejected(self):
        loan = self.create_weekly()
        with self.assertRaises(ValidationError):
            self.pay(loan, loan.total_due + 1)
        self.assertEqual(loan.payments.count(), 0)

    def test_payment_is_immutable(self):
        loan = self.create_weekly()
        payment = self.pay(loan, 1000)
        payment.amount = Decimal('5')
        with self.assertRaises(ValidationError):
            payment.save()
        with self.assertRaises(ValidationError):
            payment.delete()

    def test_penalty_rules(self):
        loan = self.create_weekly()
        penalty = LoanPenalty.objects.create(loan=loan, amount=Decimal('5000'), reason='Telat')
        penalty.amount = Decimal('1')
        with self.assertRaises(ValidationError):
            penalty.save()

        penalty.refresh_from_db()
        penalty.is_paid = True
        penalty.save()
        with self.assertRaises(ValidationError):
            penalty.delete()

        self.pay(loan, loan.total_due)
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.LoanStatus.LUNAS)
        with self.assertRaises(ValidationError):
            LoanPenalty.objects.create(loan=loan, amount=Decimal('5000'), reason='Setelah lunas')


class MemberDeletionTests(LoanTestMixin, TestCase):
    def test_member_with_financial_data_cannot_be_deleted(self):
        self.create_weekly()
        with self.assertRaises(ProtectedError):
            self.member.delete()

    def test_member_with_savings_account_cannot_be_deleted(self):
        member = create_member('B001')
        create_account(member=member)
        with self.assertRaises(ProtectedError):
            member.delete()

    def test_member_without_activity_can_be_deleted(self):
        member = create_member('B001')
        member.delete()
        self.assertFalse(Member.objects.filter(pk=member.pk).exists())


class LoanApiTests(LoanTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.admin = get_user_model().objects.create_superuser('admin', 'a@a.id', 'Rahasia-Kuat-123')
        self.client.force_authenticate(self.admin)

    def test_endpoints_are_separated_by_type(self):
        response = self.client.post(
            '/api/pinjaman-mingguan/',
            {'member': self.member.pk, 'loan_rate_table': self.weekly_rate.pk, 'loan_date': '2026-01-01'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['nomor_pinjaman'], self.member.id_week)
        self.assertEqual(response.json()['pinjaman_ke'], 1)
        self.assertEqual(response.json()['disbursed_amount'], '490000.00')

        response = self.client.post(
            '/api/pinjaman-mingguan/',
            {'member': create_member('B001').pk, 'loan_rate_table': self.monthly_rate.pk, 'loan_date': '2026-01-01'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

        self.assertEqual(len(self.client.get('/api/pinjaman-mingguan/').json()['results']), 1)
        self.assertEqual(len(self.client.get('/api/pinjaman-bulanan/').json()['results']), 0)

    def test_second_active_loan_returns_400(self):
        self.create_weekly()
        response = self.client.post(
            '/api/pinjaman-mingguan/',
            {'member': self.member.pk, 'loan_rate_table': self.weekly_rate.pk, 'loan_date': '2026-01-01'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('member', response.json())

    def test_payment_and_penalty_flow(self):
        loan = self.create_weekly()
        response = self.client.post(
            '/api/pembayaran-pinjaman/', {'loan': loan.pk, 'amount': '100000'}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['recorded_by'], 'admin')
        self.assertEqual(self.client.delete(f"/api/pembayaran-pinjaman/{response.json()['id']}/").status_code, 405)

        response = self.client.post(
            '/api/denda-pinjaman/', {'loan': loan.pk, 'amount': '5000', 'reason': 'Telat'}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        penalty_id = response.json()['id']

        detail = self.client.get(f'/api/pinjaman-mingguan/{loan.pk}/').json()
        self.assertEqual(detail['total_paid'], '100000.00')
        self.assertEqual(detail['remaining'], '450000.00')
        self.assertEqual(detail['unpaid_penalties'], '5000.00')

        response = self.client.patch(f'/api/denda-pinjaman/{penalty_id}/', {'amount': '1'}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(f'/api/denda-pinjaman/{penalty_id}/', {'is_paid': True}, format='json')
        self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(len(self.client.get(f'/api/pembayaran-pinjaman/?loan={loan.pk}').json()['results']), 1)

    def test_delete_member_with_loan_returns_409_and_can_deactivate(self):
        self.create_weekly()
        self.assertEqual(self.client.delete(f'/api/members/{self.member.pk}/').status_code, 409)

        response = self.client.post(f'/api/members/{self.member.pk}/nonaktifkan/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['is_active'])


class ApiValidationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        admin = get_user_model().objects.create_superuser('admin', 'a@a.id', 'Rahasia-Kuat-123')
        self.client.force_authenticate(admin)

    def test_member_crud_returns_savings(self):
        response = self.client.post(
            '/api/members/',
            {'id_member': 'A001', 'name': 'Uji', 'address': 'x', 'phone_number': '1'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        # Simpanan sukarela tidak otomatis dibuat untuk anggota baru.
        self.assertEqual(response.json()['rekening_simpanan'], [])
        self.assertEqual(self.client.get('/api/members/').status_code, 200)

    def test_model_validation_error_returns_400(self):
        other = create_member('B001')
        member = create_member('A001')
        rule = LoanRule.objects.create(
            loan_type=LoanRule.LoanType.MONTHLY, max_loan_amount=1, max_installments=1, interest_rate=0,
        )
        rate = LoanRateTable.objects.create(
            loan_rule=rule, loan_amount=1, installment_count=1, installment_amount=1,
        )
        jaminan_other = Jaminan.objects.create(member=other, jenis_penjamin=Jaminan.JenisPenjamin.BPKB)

        response = self.client.post(
            '/api/pinjaman-bulanan/',
            {'member': member.pk, 'jaminan': jaminan_other.pk, 'loan_rate_table': rate.pk, 'loan_date': '2026-01-01'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)


class ConcurrencyTests(TransactionTestCase):
    """Menjalankan operasi paralel di thread terpisah (koneksi DB terpisah)."""

    def run_parallel(self, func, count):
        errors = []
        barrier = threading.Barrier(count)

        def worker(index):
            try:
                barrier.wait()
                func(index)
            except Exception as exc:  # noqa: BLE001 - dikumpulkan untuk assertion
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return errors

    def test_parallel_deposits_do_not_lose_updates(self):
        savings_id = create_account().pk

        errors = self.run_parallel(
            lambda _: add_transaction(Savings.objects.get(pk=savings_id), DEPOSIT, '100'),
            count=10,
        )

        self.assertEqual(errors, [])
        self.assertEqual(Savings.objects.get(pk=savings_id).balance, Decimal('1000'))

    def test_parallel_payments_do_not_exceed_remaining(self):
        member = create_member()
        rate = create_rate(WEEKLY, loan_amount=500_000, installment_count=10, installment_amount=50_000, admin_fee=0)
        loan = Loan.objects.create(member=member, loan_rate_table=rate, loan_date=date(2026, 1, 1))

        # Total tagihan 500.000; 8 pembayaran paralel @100.000 → hanya 5 yang boleh berhasil.
        errors = self.run_parallel(
            lambda _: LoanPayment.objects.create(loan_id=loan.pk, amount=Decimal('100000')),
            count=8,
        )

        self.assertEqual(len(errors), 3)
        self.assertTrue(all(isinstance(error, ValidationError) for error in errors))
        loan.refresh_from_db()
        self.assertEqual(loan.total_paid, Decimal('500000'))
        self.assertEqual(loan.status, Loan.LoanStatus.LUNAS)

    def test_parallel_member_creation_generates_unique_ids(self):
        errors = self.run_parallel(lambda i: create_member(f'P{i:03d}'), count=8)

        self.assertEqual(errors, [])
        week_ids = sorted(Member.objects.values_list('id_week', flat=True))
        self.assertEqual(week_ids, [f'{i:03d}' for i in range(1, 9)])


class ApiPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = get_user_model().objects.create_user('petugas', password='Rahasia-Kuat-123')
        create_member()

    def test_anonymous_request_is_rejected(self):
        for url in ('/api/members/', '/api/jaminan/', '/api/pinjaman-bulanan/'):
            self.assertEqual(self.client.get(url).status_code, 401, url)

    def test_user_without_model_permission_cannot_write(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/api/members/').status_code, 200)
        response = self.client.post(
            '/api/members/',
            {'id_member': 'X1', 'name': 'x', 'address': 'x', 'phone_number': '1'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)

    def test_user_with_model_permission_can_write(self):
        self.staff.user_permissions.add(Permission.objects.get(codename='add_member'))
        self.client.force_authenticate(get_user_model().objects.get(pk=self.staff.pk))
        response = self.client.post(
            '/api/members/',
            {'id_member': 'X1', 'name': 'x', 'address': 'x', 'phone_number': '1'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)

    def test_token_authentication(self):
        response = self.client.post(
            '/api/auth/token/', {'username': 'petugas', 'password': 'Rahasia-Kuat-123'}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        token = response.json()['token']
        self.assertEqual(token, Token.objects.get(user=self.staff).key)

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(self.client.get('/api/members/').status_code, 200)


class SeedDefaultAdminTests(TestCase):
    def run_seed(self, *args):
        call_command('seed_default_admin', *args, stdout=StringIO())

    def test_requires_password(self):
        with self.assertRaises(CommandError):
            self.run_seed('--password', '')

    def test_auto_seed_without_password_is_skipped(self):
        self.run_seed('--if-not-exists', '--password', '')
        self.assertFalse(get_user_model().objects.exists())

    def test_weak_password_is_rejected(self):
        for weak in ('admin12345', '12345678'):
            with self.assertRaises(CommandError):
                self.run_seed('--password', weak)
        self.assertFalse(get_user_model().objects.exists())

    def test_creates_superuser_with_strong_password(self):
        self.run_seed('--username', 'boss', '--password', 'Koperasi-Aman-2026')
        user = get_user_model().objects.get(username='boss')
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password('Koperasi-Aman-2026'))


class AdminSmokeTests(LoanTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        admin_user = get_user_model().objects.create_superuser('admin', 'a@a.id', 'Rahasia-Kuat-123')
        self.client.force_login(admin_user)
        self.loan = self.create_weekly()
        self.penalty = LoanPenalty.objects.create(loan=self.loan, amount=Decimal('5000'), reason='Telat')

    def test_admin_pages_render(self):
        urls = [
            '/admin/member/loan/', '/admin/member/loan/add/', f'/admin/member/loan/{self.loan.pk}/change/',
            '/admin/member/loanpayment/', '/admin/member/loanpayment/add/',
            '/admin/member/loanpenalty/', f'/admin/member/loanpenalty/{self.penalty.pk}/change/',
            '/admin/member/member/', f'/admin/member/member/{self.member.pk}/change/',
            f'/admin/member/member/{self.member.pk}/delete/',
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_admin_payment_records_user(self):
        response = self.client.post(
            '/admin/member/loanpayment/add/',
            {'loan': self.loan.pk, 'amount': '50000', 'payment_date': '2026-01-05', 'notes': ''},
        )
        self.assertEqual(response.status_code, 302)
        payment = LoanPayment.objects.get()
        self.assertEqual(payment.recorded_by.username, 'admin')

    def test_admin_overpayment_shows_form_error(self):
        response = self.client.post(
            '/admin/member/loanpayment/add/',
            {'loan': self.loan.pk, 'amount': '99999999', 'payment_date': '2026-01-05', 'notes': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Melebihi sisa pinjaman')


class SavingsAccountTests(TestCase):
    def test_members_no_longer_get_automatic_account(self):
        create_member()
        self.assertFalse(Savings.objects.exists())

    def test_non_member_can_open_account_with_manual_number(self):
        account = create_account('BUKU-0042', name='Pak Umum')
        self.assertIsNone(account.nasabah.member)
        self.assertEqual(str(account), 'BUKU-0042 - Pak Umum (Rp 0)')

    def test_nasabah_from_member_copies_data(self):
        member = create_member('B001', name='Siti', phone_number='0812')
        account = create_account(member=member)
        self.assertEqual((account.nasabah.name, account.nasabah.phone_number), ('Siti', '0812'))

    def test_account_number_must_be_unique(self):
        create_account('TAB-1')
        with self.assertRaises(IntegrityError):
            create_account('TAB-1', name='Lain')

    def test_one_voluntary_account_per_nasabah(self):
        account = create_account('TAB-1')
        with self.assertRaises(IntegrityError):
            Savings.objects.create(account_number='TAB-2', nasabah=account.nasabah, product=voluntary_product())

    def test_minimum_balance_is_respected(self):
        product = voluntary_product()
        product.min_balance = Decimal('10000')
        product.save()
        account = create_account()
        add_transaction(account, DEPOSIT, '50000')
        with self.assertRaises(ValidationError) as ctx:
            add_transaction(account, WITHDRAWAL, '40001')
        self.assertIn('saldo mengendap', ' '.join(ctx.exception.messages))
        add_transaction(account, WITHDRAWAL, '40000')
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal('10000'))

    def test_withdrawal_disabled_by_product(self):
        product = voluntary_product()
        product.allow_withdrawal = False
        product.save()
        account = create_account()
        add_transaction(account, DEPOSIT, '1000')
        with self.assertRaises(ValidationError):
            add_transaction(account, WITHDRAWAL, '1')

    def test_transaction_date_rules(self):
        account = create_account()
        add_transaction(account, DEPOSIT, '1000', on_date=date(2026, 3, 10))
        with self.assertRaises(ValidationError):
            add_transaction(account, DEPOSIT, '1000', on_date=date(2026, 3, 9))  # mundur
        with self.assertRaises(ValidationError):
            add_transaction(account, DEPOSIT, '1000', on_date=timezone.localdate() + timedelta(days=1))

    def test_transaction_before_opening_date_is_rejected(self):
        account = create_account(opened_date=date(2026, 5, 1))
        with self.assertRaises(ValidationError):
            add_transaction(account, DEPOSIT, '1000', on_date=date(2026, 4, 30))

    def test_interest_cannot_be_posted_manually(self):
        account = create_account()
        with self.assertRaises(ValidationError):
            add_transaction(account, SavingsTransaction.TransactionType.INTEREST, '1000')

    def test_close_account(self):
        account = create_account()
        add_transaction(account, DEPOSIT, '1000')
        with self.assertRaises(ValidationError):
            account.close()
        add_transaction(account, WITHDRAWAL, '1000')
        account.close()
        self.assertFalse(account.is_active)
        with self.assertRaises(ValidationError):
            add_transaction(account, DEPOSIT, '1000')


class SavingsInterestTests(TestCase):
    """Saldo uji: 1.000.000 per 1 Mar, tarik 400.000 pada 11 Mar, setor 1.400.000 pada 21 Mar."""

    def setUp(self):
        self.product = voluntary_product()
        self.account = create_account(opened_date=date(2026, 2, 1))
        add_transaction(self.account, DEPOSIT, '1000000', on_date=date(2026, 2, 15))
        add_transaction(self.account, WITHDRAWAL, '400000', on_date=date(2026, 3, 11))
        add_transaction(self.account, DEPOSIT, '1400000', on_date=date(2026, 3, 21))
        self.march = (date(2026, 3, 1), date(2026, 3, 31))

    def add_rule(self, basis, rate='12', effective_from=date(2026, 1, 1), **kwargs):
        return SavingsInterestRule.objects.create(
            product=self.product, effective_from=effective_from, annual_rate=Decimal(rate), basis=basis, **kwargs,
        )

    def test_no_rule_means_no_interest(self):
        self.assertEqual(calculate_interest(self.account, *self.march), (Decimal('0'), None))

    def test_lowest_balance(self):
        self.add_rule(SavingsInterestRule.Basis.LOWEST_BALANCE)
        amount, _ = calculate_interest(self.account, *self.march)
        # 600.000 × 12% × 31/365 = 6.115,06 → dibulatkan ke bawah
        self.assertEqual(amount, Decimal('6115'))

    def test_end_balance(self):
        self.add_rule(SavingsInterestRule.Basis.END_BALANCE)
        amount, _ = calculate_interest(self.account, *self.march)
        # 2.000.000 × 12% × 31/365 = 20.383,56
        self.assertEqual(amount, Decimal('20383'))

    def test_average_daily_balance(self):
        self.add_rule(SavingsInterestRule.Basis.AVERAGE_DAILY_BALANCE)
        amount, _ = calculate_interest(self.account, *self.march)
        # rata-rata = (10×1.000.000 + 10×600.000 + 11×2.000.000) / 31 = 1.225.806,45
        # × 12% × 31/365 = 12.493,15
        self.assertEqual(amount, Decimal('12493'))

    def test_rule_version_in_effect_is_used(self):
        self.add_rule(SavingsInterestRule.Basis.END_BALANCE, rate='12', effective_from=date(2026, 1, 1))
        new_rule = self.add_rule(SavingsInterestRule.Basis.END_BALANCE, rate='6', effective_from=date(2026, 3, 1))
        amount, rule = calculate_interest(self.account, *self.march)
        self.assertEqual(rule, new_rule)
        self.assertEqual(amount, Decimal('10191'))

        _, feb_rule = calculate_interest(self.account, date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(feb_rule.annual_rate, Decimal('12'))

    def test_below_min_balance_for_interest(self):
        self.add_rule(SavingsInterestRule.Basis.LOWEST_BALANCE, min_balance_for_interest=Decimal('700000'))
        self.assertEqual(calculate_interest(self.account, *self.march)[0], Decimal('0'))

    def test_posting_is_idempotent_and_dry_run_saves_nothing(self):
        self.add_rule(SavingsInterestRule.Basis.END_BALANCE)
        post_monthly_interest(2026, 3, dry_run=True)
        self.assertFalse(self.account.transactions.filter(transaction_type='interest').exists())

        call_command('apply_savings_interest', '--period', '2026-03', stdout=StringIO())
        call_command('apply_savings_interest', '--period', '2026-03', stdout=StringIO())
        postings = self.account.transactions.filter(transaction_type='interest')
        self.assertEqual(postings.count(), 1)
        posting = postings.get()
        self.assertEqual((posting.amount, posting.transaction_date), (Decimal('20383'), date(2026, 3, 31)))

        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal('2020383'))

    def test_used_rule_is_locked(self):
        rule = self.add_rule(SavingsInterestRule.Basis.END_BALANCE)
        post_monthly_interest(2026, 3)
        rule.annual_rate = Decimal('1')
        with self.assertRaises(ValidationError):
            rule.full_clean()

    def test_unfinished_period_is_rejected(self):
        today = timezone.localdate()
        with self.assertRaises(CommandError):
            call_command('apply_savings_interest', '--period', f'{today:%Y-%m}', stdout=StringIO())


class SavingsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(get_user_model().objects.create_superuser('admin', 'a@a.id', 'Rahasia-Kuat-123'))
        self.account = create_account('BUKU-0042', name='Budi Santoso')
        create_account('BUKU-0043', name='Siti Aminah')

    def test_search_account_by_name(self):
        results = self.client.get('/api/rekening-simpanan/', {'search': 'budi'}).json()['results']
        self.assertEqual([r['account_number'] for r in results], ['BUKU-0042'])
        results = self.client.get('/api/nasabah/', {'search': 'siti'}).json()['results']
        self.assertEqual([r['name'] for r in results], ['Siti Aminah'])

    def test_deposit_withdraw_and_history(self):
        response = self.client.post(
            '/api/transaksi-simpanan/', {'savings': self.account.pk, 'transaction_type': 'deposit', 'amount': '50000'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['recorded_by'], 'admin')
        self.assertEqual(response.json()['balance_after'], '50000.00')

        response = self.client.post(
            '/api/transaksi-simpanan/', {'savings': self.account.pk, 'transaction_type': 'withdrawal', 'amount': '60000'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            '/api/transaksi-simpanan/', {'savings': self.account.pk, 'transaction_type': 'interest', 'amount': '5'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

        history = self.client.get('/api/transaksi-simpanan/', {'search': 'budi'}).json()['results']
        self.assertEqual(len(history), 1)
        self.assertEqual(self.client.delete(f"/api/transaksi-simpanan/{history[0]['id']}/").status_code, 405)

    def test_open_and_close_account(self):
        nasabah = Nasabah.objects.create(name='Baru')
        response = self.client.post(
            '/api/rekening-simpanan/',
            {'account_number': 'BUKU-0099', 'nasabah': nasabah.pk, 'product': voluntary_product().pk},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.client.delete(f"/api/rekening-simpanan/{response.json()['id']}/").status_code, 405)

        response = self.client.post(f"/api/rekening-simpanan/{response.json()['id']}/tutup/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['is_active'])

    def test_member_shows_accounts(self):
        member = create_member('B001')
        create_account('BUKU-0100', member=member)
        data = self.client.get(f'/api/members/{member.pk}/').json()
        self.assertEqual([r['account_number'] for r in data['rekening_simpanan']], ['BUKU-0100'])


class SavingsAdminSmokeTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('admin', 'a@a.id', 'Rahasia-Kuat-123'))
        self.account = create_account('BUKU-0042', name='Budi Santoso')
        add_transaction(self.account, DEPOSIT, '1000')

    def test_pages_render(self):
        for url in [
            '/admin/member/savings/', '/admin/member/savings/add/', f'/admin/member/savings/{self.account.pk}/change/',
            '/admin/member/savingstransaction/', '/admin/member/savingstransaction/add/',
            '/admin/member/nasabah/', '/admin/member/nasabah/add/', f'/admin/member/nasabah/{self.account.nasabah.pk}/change/',
            '/admin/member/savingsproduct/', '/admin/member/savingsinterestrule/add/',
            '/admin/member/savings/?q=budi',
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_autocomplete_finds_account_by_name(self):
        response = self.client.get('/admin/autocomplete/', {
            'term': 'budi', 'app_label': 'member', 'model_name': 'savingstransaction', 'field_name': 'savings',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.json()['results']], [str(self.account.pk)])

    def test_withdrawal_over_balance_shows_form_error(self):
        response = self.client.post('/admin/member/savingstransaction/add/', {
            'savings': self.account.pk, 'transaction_type': 'withdrawal', 'amount': '5000',
            'transaction_date': timezone.localdate().isoformat(), 'notes': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Saldo tidak cukup')



def product(code):
    return SavingsProduct.objects.get(code=code)


class MemberSavingsTestMixin:
    def setUp(self):
        super().setUp()
        self.pokok, self.wajib = product('pokok'), product('wajib')
        SavingsDueRate.objects.create(product=self.pokok, effective_from=date(2026, 1, 1), amount=Decimal('100000'))
        SavingsDueRate.objects.create(product=self.wajib, effective_from=date(2026, 1, 1), amount=Decimal('10000'))
        SavingsDueRate.objects.create(product=self.wajib, effective_from=date(2026, 4, 1), amount=Decimal('15000'))
        self.member = create_member('B001', name='Budi')
        self.account = MemberSavingsAccount.objects.create(
            account_number='SA-12', member=self.member, opened_date=date(2026, 2, 15),
        )


class MemberSavingsAccountTests(MemberSavingsTestMixin, TestCase):
    def test_account_creates_nasabah_and_two_sub_accounts(self):
        numbers = sorted(self.account.sub_accounts.values_list('account_number', flat=True))
        self.assertEqual(numbers, ['SA-12-POKOK', 'SA-12-WAJIB'])
        self.assertEqual(self.member.nasabah.name, 'Budi')

    def test_one_account_per_member_and_unique_id(self):
        with self.assertRaises(IntegrityError):
            MemberSavingsAccount.objects.create(account_number='SA-99', member=self.member)

    def test_renaming_account_renames_sub_accounts(self):
        self.account.account_number = 'SA-0012'
        self.account.save()
        numbers = sorted(self.account.sub_accounts.values_list('account_number', flat=True))
        self.assertEqual(numbers, ['SA-0012-POKOK', 'SA-0012-WAJIB'])

    def test_arrears_with_rate_change(self):
        deposit(self.account, 'pokok', Decimal('60000'), on_date=date(2026, 3, 1))
        deposit(self.account, 'wajib', Decimal('20000'), on_date=date(2026, 3, 1))
        info = summary(self.account, as_of=date(2026, 5, 10))
        # Pokok 100.000 - 60.000
        self.assertEqual(info['pokok']['tunggakan'], Decimal('40000'))
        # Wajib Feb 10.000 + Mar 10.000 + Apr 15.000 = 35.000; dibayar 20.000
        self.assertEqual(info['wajib']['kewajiban'], Decimal('35000'))
        self.assertEqual(info['wajib']['tunggakan'], Decimal('15000'))
        self.assertEqual(info['wajib']['tagihan_bulan_ini'], Decimal('15000'))

    def test_overpayment_reduces_current_bill(self):
        deposit(self.account, 'wajib', Decimal('50000'), on_date=date(2026, 3, 1))
        info = summary(self.account, as_of=date(2026, 5, 10))
        self.assertEqual(info['wajib']['tunggakan'], Decimal('0'))
        self.assertEqual(info['wajib']['tagihan_bulan_ini'], Decimal('0'))

    def test_no_rates_means_no_arrears(self):
        SavingsDueRate.objects.all().delete()
        info = summary(self.account, as_of=date(2026, 9, 1))
        self.assertEqual((info['pokok']['tunggakan'], info['wajib']['tunggakan']), (Decimal('0'), Decimal('0')))

    def test_arrears_stop_when_member_leaves(self):
        self.member.deactivate(date(2026, 4, 10))
        self.account.refresh_from_db()
        info = summary(self.account, as_of=date(2026, 9, 1))
        self.assertEqual(info['wajib']['kewajiban'], Decimal('20000'))  # Feb + Mar

    def test_withdrawal_rejected_while_member_active(self):
        deposit(self.account, 'wajib', Decimal('20000'), on_date=date(2026, 3, 1))
        with self.assertRaises(ValidationError):
            add_transaction(self.account.sub_account('wajib'), WITHDRAWAL, '1000')

    def test_default_deposit_amount_pays_everything_due(self):
        due = summary(self.account)['wajib']
        tx = deposit(self.account, 'wajib')
        self.assertEqual(tx.amount, due['tunggakan'] + due['tagihan_bulan_ini'])

    def test_no_interest_posted_to_member_savings(self):
        for code in ('pokok', 'wajib'):
            self.assertIsNone(SavingsInterestRule.rule_for(product(code), date(2026, 9, 30)))
        deposit(self.account, 'wajib', Decimal('20000'), on_date=date(2026, 3, 1))
        post_monthly_interest(2026, 3)
        self.assertFalse(SavingsTransaction.objects.filter(transaction_type='interest').exists())


class MemberExitTests(MemberSavingsTestMixin, LoanTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        deposit(self.account, 'pokok', Decimal('100000'), on_date=date(2026, 3, 1))
        deposit(self.account, 'wajib', Decimal('30000'), on_date=date(2026, 3, 1))

    def test_exit_rejected_with_active_loan(self):
        Loan.objects.create(member=self.member, loan_rate_table=self.weekly_rate, loan_date=date(2026, 3, 1))
        with self.assertRaises(ValidationError):
            process_exit(self.account)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)

    def test_exit_refunds_and_closes(self):
        refunds = process_exit(self.account)
        self.assertEqual(sorted(tx.amount for tx in refunds), [Decimal('30000'), Decimal('100000')])

        self.account.refresh_from_db()
        self.member.refresh_from_db()
        self.assertFalse(self.account.is_active)
        self.assertFalse(self.member.is_active)
        for sub in self.account.sub_accounts.all():
            self.assertEqual((sub.balance, sub.is_active), (Decimal('0'), False))


class MemberSavingsApiTests(MemberSavingsTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(get_user_model().objects.create_superuser('admin', 'a@a.id', 'Rahasia-Kuat-123'))

    def test_search_and_summary(self):
        data = self.client.get('/api/akun-simpanan-anggota/', {'search': 'budi'}).json()['results']
        self.assertEqual([a['account_number'] for a in data], ['SA-12'])
        self.assertEqual(set(data[0]['ringkasan']), {'pokok', 'wajib'})

        member = self.client.get(f'/api/members/{self.member.pk}/').json()
        self.assertEqual(member['akun_simpanan_anggota']['account_number'], 'SA-12')

    def test_deposit_by_choosing_type(self):
        url = f'/api/akun-simpanan-anggota/{self.account.pk}/setor/'
        response = self.client.post(url, {'jenis': 'pokok', 'amount': '25000'}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['nomor_rekening'], 'SA-12-POKOK')
        self.assertEqual(response.json()['recorded_by'], 'admin')

        response = self.client.post(url, {'jenis': 'wajib'}, format='json')  # nominal otomatis
        self.assertEqual(response.status_code, 201, response.content)

        self.assertEqual(self.client.post(url, {'jenis': 'sukarela', 'amount': '1'}, format='json').status_code, 400)

    def test_create_account_and_exit(self):
        member = create_member('C001', name='Citra')
        response = self.client.post(
            '/api/akun-simpanan-anggota/', {'account_number': 'SA-77', 'member': member.pk}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)

        response = self.client.post(f"/api/akun-simpanan-anggota/{response.json()['id']}/proses-keluar/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()['akun']['is_active'])


class MemberSavingsAdminTests(MemberSavingsTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('admin', 'a@a.id', 'Rahasia-Kuat-123'))

    def test_pages_render(self):
        for url in [
            '/admin/member/membersavingsaccount/', '/admin/member/membersavingsaccount/add/',
            f'/admin/member/membersavingsaccount/{self.account.pk}/change/',
            '/admin/member/savingsduerate/', '/admin/member/savingsduerate/add/',
            '/admin/member/member/?akun_simpanan=belum',
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_member_filter_without_account(self):
        create_member('C001', name='Belum Punya')
        response = self.client.get('/admin/member/member/', {'akun_simpanan': 'belum'})
        self.assertContains(response, 'Belum Punya')
        self.assertNotContains(response, '>Budi<')


class ApiPaginationFilterTests(LoanTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(get_user_model().objects.create_superuser('admin', 'a@a.id', 'Rahasia-Kuat-123'))

    def test_list_is_paginated(self):
        for i in range(3):
            create_member(f'P{i:03d}')
        data = self.client.get('/api/members/', {'page_size': 2}).json()
        self.assertEqual(data['count'], 4)
        self.assertEqual(len(data['results']), 2)
        self.assertIsNotNone(data['next'])

    def test_member_filters(self):
        create_member('X001', name='Nonaktif').deactivate()
        data = self.client.get('/api/members/', {'is_active': 'false'}).json()['results']
        self.assertEqual([m['id_member'] for m in data], ['X001'])
        data = self.client.get('/api/members/', {'has_akun_simpanan': 'false', 'search': 'nonaktif'}).json()['results']
        self.assertEqual(len(data), 1)

    def test_loan_filters_and_totals(self):
        loan = self.create_weekly(loan_date=date(2026, 1, 1))
        LoanPayment.objects.create(loan=loan, amount=Decimal('100000'), payment_date=date(2026, 1, 2))
        LoanPenalty.objects.create(loan=loan, amount=Decimal('5000'), reason='Telat')
        loan.refresh_from_db()  # status diperbarui oleh pembayaran/denda

        data = self.client.get('/api/pinjaman-mingguan/', {'loan_date_after': '2026-01-01', 'status': loan.status}).json()
        self.assertEqual(data['count'], 1)
        row = data['results'][0]
        self.assertEqual((row['total_paid'], row['unpaid_penalties']), ('100000.00', '5000.00'))

        self.assertEqual(self.client.get('/api/pinjaman-mingguan/', {'loan_date_before': '2025-12-31'}).json()['count'], 0)
        self.assertEqual(self.client.get('/api/denda-pinjaman/', {'is_paid': 'false'}).json()['count'], 1)

    def test_loan_list_query_count_does_not_grow_per_row(self):
        for i in range(5):
            member = create_member(f'Q{i:03d}')
            loan = Loan.objects.create(member=member, loan_rate_table=self.weekly_rate, loan_date=date(2026, 1, 1))
            LoanPayment.objects.create(loan=loan, amount=Decimal('1000'), payment_date=date(2026, 1, 2))
        # auth/session + count + page; tanpa query tambahan per pinjaman
        with self.assertNumQueries(2):
            response = self.client.get('/api/pinjaman-mingguan/')
        self.assertEqual(response.json()['count'], 5)

    def test_savings_transaction_filters(self):
        account = create_account()
        add_transaction(account, DEPOSIT, '1000', on_date=date(2026, 2, 1))
        add_transaction(account, WITHDRAWAL, '500', on_date=date(2026, 3, 1))
        url = '/api/transaksi-simpanan/'
        self.assertEqual(self.client.get(url, {'rekening': account.pk}).json()['count'], 2)
        self.assertEqual(self.client.get(url, {'transaction_type': 'withdrawal'}).json()['count'], 1)
        self.assertEqual(self.client.get(url, {'transaction_date_before': '2026-02-15'}).json()['count'], 1)
        ordered = self.client.get(url, {'ordering': 'transaction_date'}).json()['results']
        self.assertEqual([r['transaction_type'] for r in ordered], ['deposit', 'withdrawal'])
