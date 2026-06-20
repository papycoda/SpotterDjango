from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0003_geocoding_workflow'),
    ]

    operations = [
        migrations.AddField(
            model_name='fuelstation',
            name='geocoding_failure_reason',
            field=models.CharField(
                blank=True,
                choices=[
                    ('no_match_osm', 'No OSM Match'),
                    ('outside_usa', 'Outside USA'),
                    ('not_fuel_station', 'Not Fuel Station'),
                    ('city_mismatch', 'City Mismatch'),
                    ('state_mismatch', 'State Mismatch'),
                    ('rate_limited', 'Rate Limited'),
                    ('network_error', 'Network Error'),
                    ('upstream_error', 'Upstream Error'),
                    ('invalid_response', 'Invalid Response'),
                    ('invalid_coordinates', 'Invalid Coordinates'),
                    ('unknown', 'Unknown Error'),
                ],
                help_text='Reason why geocoding failed',
                max_length=30,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='fuelstation',
            name='geocoding_confidence',
            field=models.CharField(
                blank=True,
                choices=[
                    ('high', 'High'),
                    ('medium', 'Medium'),
                    ('low', 'Low'),
                ],
                help_text='Confidence level of geocoding result',
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='fuelstation',
            name='geocoding_stage',
            field=models.SmallIntegerField(
                blank=True,
                help_text='Which stage of geocoding succeeded (1-4)',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='fuelstation',
            name='geocoding_strategy_version',
            field=models.SmallIntegerField(
                default=0,
                help_text='Deterministic geocoding strategy version last completed',
            ),
        ),
    ]
