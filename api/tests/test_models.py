from django.test import SimpleTestCase

from api.models import FuelStation


class FuelStationModelTests(SimpleTestCase):
    def test_stores_rack_id_from_source_csv(self):
        field_names = {field.name for field in FuelStation._meta.fields}

        self.assertIn("rack_id", field_names)
