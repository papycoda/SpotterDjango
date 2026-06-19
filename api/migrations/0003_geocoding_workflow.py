import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0002_fuelstation_rack_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='GeocodingRateLimit',
            fields=[
                (
                    'id',
                    models.PositiveSmallIntegerField(
                        default=1,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ('next_allowed_at', models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.AddField(
            model_name='fuelstation',
            name='geocode_job',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='stations',
                to='api.geocodejob',
            ),
        ),
        migrations.AddField(
            model_name='geocodejob',
            name='heartbeat_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='geocodejob',
            name='worker_id',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AlterField(
            model_name='fuelstation',
            name='geocoding_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('claimed', 'Claimed'),
                    ('processing', 'Processing'),
                    ('success', 'Success'),
                    ('failed', 'Failed'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
    ]
