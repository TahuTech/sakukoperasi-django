from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('member', '0007_add_jaminan_and_monthly_loan'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        """
                        ALTER TABLE member_member
                        DROP COLUMN IF EXISTS guaranted_id_id;
                        """
                    ),
                    reverse_sql=(
                        """
                        ALTER TABLE member_member
                        ADD COLUMN IF NOT EXISTS guaranted_id_id bigint NULL;
                        """
                    ),
                ),
            ],
            state_operations=[],
        ),
    ]
