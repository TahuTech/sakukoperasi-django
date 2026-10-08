import threading
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase
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
    Savings,
    SavingsTransaction,
    add_months,
)


def create_member(id_member='A001', **kwargs):
    return Member.objects.create(
        id_member=id_member,
        name=kwargs.pop('name', 'Anggota Uji'),
        address=kwargs.pop('address', 'Alamat'),
        phone_number=kwargs.pop('phone_number', '0800'),
        **kwargs,
    )


def add_transaction(savings, transaction_type, amount):
    return SavingsTransaction.objects.create(
        savings=savings,
        transaction_type=transaction_type,
        amount=Decimal(amount),
        transaction_date=date.today(),
    )


DEPOSIT = SavingsTransaction.TransactionType.DEPOSIT
WITHDRAWAL = SavingsTransaction.TransactionType.WITHDRAWAL


class SavingsTransactionTests(TestCase):
    def setUp(self):
        self.savings = create_member().tabungan

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
        self.assertEqual(loan.loan_number, 1)
        self.assertEqual(loan.formatted_number, 'PB-000001')
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
        self.assertEqual(loan.formatted_number, 'PM-000001')
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

    def test_member_with_savings_transaction_cannot_be_deleted(self):
        member = create_member('B001')
        add_transaction(member.tabungan, DEPOSIT, '100')
        with self.assertRaises(ProtectedError):
            member.delete()

    def test_member_without_activity_can_be_deleted(self):
        member = create_member('B001')
        member.delete()
        self.assertFalse(Member.objects.filter(pk=member.pk).exists())
        self.assertFalse(Savings.objects.filter(member_id=member.pk).exists())


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
        self.assertEqual(response.json()['nomor_pinjaman'], 'PM-000001')
        self.assertEqual(response.json()['disbursed_amount'], '490000.00')

        response = self.client.post(
            '/api/pinjaman-mingguan/',
            {'member': create_member('B001').pk, 'loan_rate_table': self.monthly_rate.pk, 'loan_date': '2026-01-01'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

        self.assertEqual(len(self.client.get('/api/pinjaman-mingguan/').json()), 1)
        self.assertEqual(len(self.client.get('/api/pinjaman-bulanan/').json()), 0)

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

        self.assertEqual(len(self.client.get(f'/api/pembayaran-pinjaman/?loan={loan.pk}').json()), 1)

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
        self.assertEqual(response.json()['tabungan']['balance'], '0.00')
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
        savings_id = create_member().tabungan.pk

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
