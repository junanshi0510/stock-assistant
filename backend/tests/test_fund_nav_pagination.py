# -*- coding: utf-8 -*-

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import funds  # noqa: E402


class FundNavPaginationTests(unittest.TestCase):
    def test_fallback_fetches_every_real_twenty_row_page(self):
        items = []
        latest = date(2026, 7, 10)
        for index in range(184):
            nav_date = latest - timedelta(days=index)
            items.append({
                "FSRQ": nav_date.isoformat(),
                "DWJZ": str(1 + index / 10000),
                "LJJZ": str(1 + index / 10000),
                "JZZZL": "0.1",
                "SGZT": "开放申购",
                "SHZT": "开放赎回",
            })

        session = Mock()

        def get_page(*_args, **kwargs):
            page = int(kwargs["params"]["pageIndex"])
            start = (page - 1) * 20
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "Data": {"LSJZList": items[start:start + 20]},
                "TotalCount": len(items),
                "PageSize": 20,
                "PageIndex": page,
            }
            return response

        session.get.side_effect = get_page

        with patch.object(funds, "_cache_get", return_value=None), \
             patch.object(funds, "_cache_put"), \
             patch.object(funds, "_fetch_detail_js", side_effect=RuntimeError("primary unavailable")), \
             patch.object(funds, "_session", return_value=session):
            result = funds._fetch_nav_history("001480", 6)

        self.assertEqual(len(result), 184)
        self.assertEqual(session.get.call_count, 10)
        requested_pages = sorted(
            int(call.kwargs["params"]["pageIndex"])
            for call in session.get.call_args_list
        )
        self.assertEqual(requested_pages, list(range(1, 11)))
        self.assertTrue(all(
            call.kwargs["params"]["pageSize"] == "20"
            for call in session.get.call_args_list
        ))


if __name__ == "__main__":
    unittest.main()
