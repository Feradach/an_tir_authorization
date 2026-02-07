from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('authorizations', '0001_initial'),
    ]

    # This migration intentionally does nothing.
    #
    # The Authorization model is already created in 0001_initial. Keeping this
    # migration in the chain (as a no-op) preserves compatibility for existing
    # environments where 0002 is already recorded, while allowing fresh database
    # setups to migrate cleanly without "table already exists" errors.
    operations = []
