import json

from django.test import TestCase


class OpenApiSchemaTests(TestCase):
    def test_schema_generates_and_documents_assignment_contract(self):
        response = self.client.get(
            '/api/v1/schema/',
            HTTP_HOST='localhost',
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 200)
        schema = json.loads(response.content)
        fuel_plan = schema['paths']['/routes/fuel-plan/']['post']
        fuel_plan_schema = fuel_plan['parameters'][0]['schema']
        properties = schema['definitions'][fuel_plan_schema['$ref'].split('/')[-1]][
            'properties'
        ]

        self.assertEqual(set(properties), {'start', 'finish'})
        self.assertEqual(set(fuel_plan['responses']), {'200', '400', '404', '422', '502'})
        self.assertIn('500 miles', fuel_plan['description'])
        self.assertNotIn('scaffolded', fuel_plan['description'].lower())

        response_ref = fuel_plan['responses']['200']['schema']['$ref'].split('/')[-1]
        response_properties = schema['definitions'][response_ref]['properties']
        self.assertEqual(
            set(response_properties),
            {
                'start', 'finish', 'distance_miles', 'duration_minutes',
                'route_geometry', 'fuel_stops', 'total_fuel_purchased',
                'total_fuel_cost', 'vehicle_assumptions',
            },
        )
        self.assertEqual(response_properties['total_fuel_cost']['type'], 'string')
        self.assertEqual(response_properties['total_fuel_purchased']['type'], 'string')
        stop_ref = response_properties['fuel_stops']['items']['$ref'].split('/')[-1]
        stop_properties = schema['definitions'][stop_ref]['properties']
        self.assertEqual(stop_properties['price_per_gallon']['type'], 'string')
        self.assertEqual(stop_properties['gallons_purchased']['type'], 'string')
        self.assertEqual(stop_properties['cost_usd']['type'], 'string')

    def test_schema_documents_station_filters_and_excludes_unsupported_paths(self):
        response = self.client.get(
            '/api/v1/schema/',
            HTTP_HOST='localhost',
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 200)
        schema = json.loads(response.content)
        station_list = schema['paths']['/fuel-stations/']['get']
        query_names = {parameter['name'] for parameter in station_list['parameters']}

        self.assertEqual(
            query_names,
            {'state', 'min_price', 'max_price', 'page', 'page_size'},
        )
        unsupported_paths = {
            '/fuel-stations/near-route/',
            '/admin/fuel-prices/import/',
            '/admin/fuel-prices/imports/{import_id}/',
            '/locations/validate/',
            '/fuel/estimate/',
        }
        self.assertTrue(unsupported_paths.isdisjoint(schema['paths']))
        self.assertTrue({
            '/health/',
            '/routes/preview/',
            '/routes/fuel-plan/',
            '/fuel-stations/',
            '/fuel-stations/{station_id}/',
            '/admin/fuel-stations/geocode/',
            '/admin/fuel-stations/geocode/status/',
        }.issubset(schema['paths']))
