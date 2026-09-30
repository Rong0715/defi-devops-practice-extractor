"""Unit tests for the site-data build.  python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from build_site_data import headline_ids  # noqa: E402


def p(id, parent, year, tvl):
    return {"id": id, "parent": parent, "launch_year": year, "tvl": tvl}


class Headline(unittest.TestCase):
    def test_latest_version_per_family(self):
        ps = [p("uniswap-v2", "uniswap", 2020, 8e8), p("uniswap-v3", "uniswap", 2021, 9e8),
              p("uniswap-v4", "uniswap", 2025, 7e8), p("morpho-blue", "morpho", 2024, 5e9)]
        self.assertEqual(headline_ids(ps), {"uniswap-v4", "morpho-blue"})

    def test_same_year_falls_back_to_tvl(self):
        ps = [p("frax-eth", "frax", 2022, 1.4e8), p("fraxlend", "frax", 2022, 2.4e7)]
        self.assertEqual(headline_ids(ps), {"frax-eth"})

    def test_no_parent_is_its_own_family(self):
        ps = [p("a", None, None, None), p("b", "", 2020, 1)]
        self.assertEqual(headline_ids(ps), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
