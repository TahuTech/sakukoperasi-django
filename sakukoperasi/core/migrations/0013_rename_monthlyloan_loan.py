from django.db import migrations


class Migration(migrations.Migration):
    """MonthlyLoan digeneralisasi menjadi Loan (mingguan & bulanan); data lama ikut terbawa."""

    dependencies = [
        ('member', '0012_savingstransaction_amount_min_value'),
    ]

    operations = [
        migrations.RenameModel(old_name='MonthlyLoan', new_name='Loan'),
    ]
