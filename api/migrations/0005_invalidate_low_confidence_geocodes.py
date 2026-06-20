from django.db import migrations, models


def invalidate_unsafe_geocodes(apps, schema_editor):
    FuelStation = apps.get_model("api", "FuelStation")
    FuelStation.objects.filter(geocoding_status="success").filter(
        models.Q(geocoding_confidence="low") | models.Q(geocoding_stage=3)
    ).update(
        latitude=None,
        longitude=None,
        geocoding_status="pending",
        geocoding_failure_reason=None,
        geocoding_confidence=None,
        geocoding_stage=None,
        geocoding_strategy_version=0,
        geocode_job=None,
    )


class Migration(migrations.Migration):
    dependencies = [("api", "0004_geocoding_failure_classification")]

    operations = [
        migrations.RunPython(
            invalidate_unsafe_geocodes,
            reverse_code=migrations.RunPython.noop,
        )
    ]
