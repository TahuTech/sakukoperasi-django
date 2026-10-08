"""
URL configuration for sakukoperasi project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter
from core.views import (
    JaminanViewSet,
    LoanPaymentViewSet,
    LoanPenaltyViewSet,
    MemberSavingsAccountViewSet,
    MemberViewSet,
    MonthlyLoanViewSet,
    NasabahViewSet,
    SavingsDueRateViewSet,
    SavingsInterestRuleViewSet,
    SavingsProductViewSet,
    SavingsTransactionViewSet,
    SavingsViewSet,
    WeeklyLoanViewSet,
)

router = DefaultRouter()
router.register(r'members', MemberViewSet)
router.register(r'jaminan', JaminanViewSet)
router.register(r'pinjaman-bulanan', MonthlyLoanViewSet, basename='pinjaman-bulanan')
router.register(r'pinjaman-mingguan', WeeklyLoanViewSet, basename='pinjaman-mingguan')
router.register(r'pembayaran-pinjaman', LoanPaymentViewSet)
router.register(r'denda-pinjaman', LoanPenaltyViewSet)
router.register(r'nasabah', NasabahViewSet)
router.register(r'rekening-simpanan', SavingsViewSet)
router.register(r'transaksi-simpanan', SavingsTransactionViewSet)
router.register(r'jenis-simpanan', SavingsProductViewSet)
router.register(r'aturan-bunga', SavingsInterestRuleViewSet)
router.register(r'akun-simpanan-anggota', MemberSavingsAccountViewSet)
router.register(r'nominal-simpanan', SavingsDueRateViewSet)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/token/', obtain_auth_token, name='api-token'),
    path('api/', include(router.urls)),
]
