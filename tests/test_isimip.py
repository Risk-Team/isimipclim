import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "script" / "isimip.py"
SPEC = importlib.util.spec_from_file_location("isimip", MODULE_PATH)
isimip = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = isimip
SPEC.loader.exec_module(isimip)


class FileSelectionTests(unittest.TestCase):
    def test_default_historical_window_has_all_five_blocks(self):
        files = isimip.build_file_list("GFDL-ESM4", "tas", "historical")
        self.assertEqual([(item.start_year, item.end_year) for item in files], [
            (1971, 1980),
            (1981, 1990),
            (1991, 2000),
            (2001, 2010),
            (2011, 2014),
        ])

    def test_future_window_includes_final_2091_2100_block(self):
        files = isimip.build_file_list("GFDL-ESM4", "tas", "ssp126")
        self.assertEqual((files[-1].start_year, files[-1].end_year), (2091, 2100))
        self.assertEqual(len(files), 8)

    def test_2015_request_selects_transition_block(self):
        files = isimip.build_file_list("GFDL-ESM4", "tas", "ssp585", 2015, 2015)
        self.assertEqual([(item.start_year, item.end_year) for item in files], [(2015, 2020)])

    def test_ukesm_uses_correct_ensemble_member(self):
        file = isimip.build_file_list("UKESM1-0-LL", "pr", "ssp126", 2021, 2021)[0]
        self.assertIn("ukesm1-0-ll_r1i1p1f2_w5e5", file.name)

    def test_invalid_bbox_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "longitudes"):
            isimip.validate_bbox((20, 10, -5, 5))


class FailureReportingTests(unittest.TestCase):
    def test_worker_failure_makes_download_fail(self):
        with (
            mock.patch.object(isimip, "_download_one", side_effect=RuntimeError("boom")),
            mock.patch.object(isimip.LOGGER, "error"),
            self.assertRaisesRegex(RuntimeError, "1 download"),
        ):
            isimip.download_isimip_data(
                "GFDL-ESM4", "tas", "historical", start_year=2011, end_year=2011
            )


if __name__ == "__main__":
    unittest.main()
