"""Preferred export boundaries and independent supplier/price expectations."""
import copy
import csv
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_phase_three import fixture, contract, POLICY
from src.engine.preferred_export import render, COLUMNS


class PreferredExport(unittest.TestCase):
    def data(self):
        return dict(fixture(), policies=[copy.deepcopy(POLICY)])

    def rows(self, data, day='2026-11-01', version=None):
        text = render(data, day, version)
        reader = csv.DictReader(io.StringIO(text))
        self.assertEqual(reader.fieldnames, COLUMNS)
        self.assertIn('\r\n', text)
        return list(reader)

    def test_latest_purchase_price_and_no_mutation(self):
        data = self.data()
        contract(data, price='80')
        before = copy.deepcopy(data)
        self.assertEqual(self.rows(data), [dict(item_code='I1', rank='1', supplier_id='S1', reference_price='100.00')])
        self.assertEqual(data, before)
        self.assertEqual(render(data,'2026-11-01'),render(data,'2026-11-01'))

    def test_contract_only_and_exclusion_boundaries(self):
        data = self.data(); data['po_history'] = []
        contract(data, price='80.125', start_date='2026-11-01')
        self.assertEqual(self.rows(data)[0]['reference_price'], '80.13')
        self.assertEqual(self.rows(data, '2026-10-31'), [])
        data['suppliers'][0]['status'] = 'Blocked'
        self.assertEqual(self.rows(data), [])

    def test_purchase_on_as_of_date_is_excluded(self):
        data = self.data()
        self.assertEqual(self.rows(data, '2026-10-01'), [])
        self.assertEqual(len(self.rows(data, '2026-10-02')), 1)
        self.assertEqual(self.rows(data, '2028-11-01'), [])

    def test_top_three_ties_and_no_category_topups(self):
        data = self.data()
        for n in range(2,6):
            sid = 'S'+str(n)
            data['suppliers'].append(dict(data['suppliers'][0], supplier_id=sid))
            data['po_history'].append(dict(data['po_history'][0],supplier_id=sid))
        self.assertEqual([r['supplier_id'] for r in self.rows(data)], ['S1','S2','S3'])
        self.assertEqual([r['rank'] for r in self.rows(data)], ['1','2','3'])
        data['po_history'] = []
        self.assertEqual(self.rows(data), [])

    def test_policy_timeline_and_invalid_inputs(self):
        data = self.data()
        data['suppliers'].append(dict(data['suppliers'][0],supplier_id='S2',quality_rating='5',on_time_pct='0'))
        data['po_history'].append(dict(data['po_history'][0],supplier_id='S2'))
        amendment = copy.deepcopy(POLICY)
        amendment.update(version='5.1',effective_date='2026-11-01')
        amendment['ranking_weights'] = dict(frequency='0',recency='0',contract='0',delivery='0',quality='1')
        data['policies'].append(amendment)
        self.assertEqual(self.rows(data,version='5.0')[0]['supplier_id'],'S1')
        self.assertEqual(self.rows(data,version='5.1')[0]['supplier_id'],'S2')
        self.assertEqual(self.rows(data,'2026-10-31','5.1')[0]['supplier_id'],'S1')
        for day in [None,'','20261101','2026-02-30','2020-01-01']:
            with self.subTest(day=day), self.assertRaises(ValueError): render(data,day)
        with self.assertRaises(ValueError): render(data,'2026-11-01','missing')


if __name__ == '__main__':
    unittest.main(verbosity=2)
