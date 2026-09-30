"""Unit tests for the repo-to-deployment matcher.  python3 -m unittest discover tests"""
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from verify_mapping import classify, vyper_match  # noqa: E402

DEFS = frozenset(f"f{i}" for i in range(12))


def idx(names=(), paths=(), vy=()):
    return {"names": set(names), "paths": set(paths), "vy": list(vy)}


class Classify(unittest.TestCase):
    def test_distinctive_name_confirms_generic_does_not(self):
        me = idx(names={"RocketNodeManager", "Pool"})
        spread = Counter({"RocketNodeManager": 1, "Pool": 9})
        self.assertEqual(classify({"name": "RocketNodeManager"}, me, spread, Counter())[1], "hit")
        self.assertEqual(classify({"name": "Pool"}, me, spread, Counter())[1], "generic")
        self.assertIsNone(classify({"name": "WETH9"}, me, spread, Counter())[1])

    def test_path_rescues_a_generic_name_but_interfaces_never_count(self):
        me = idx(names={"Pool"}, paths={"pool/Pool.sol", "interfaces/IPool.sol"})
        spread, pspread = Counter({"Pool": 9}), Counter({"pool/Pool.sol": 1, "interfaces/IPool.sol": 1})
        hit = {"name": "Pool", "files": ["contracts/protocol/pool/Pool.sol"]}
        iface = {"name": "Pool", "files": ["src/interfaces/IPool.sol"]}
        self.assertEqual(classify(hit, me, spread, pspread)[1], "hit")
        self.assertEqual(classify(iface, me, spread, pspread)[1], "generic")

    def test_vyper_matched_by_function_set(self):
        me = idx(vy=[("StableSwap3Pool", DEFS)])
        self.assertEqual(vyper_match(sorted(DEFS | {"extra"}), me), "StableSwap3Pool")
        self.assertIsNone(vyper_match(["transfer", "approve"], me))   # too small to be distinctive
        label, kind = classify({"name": "Vyper_contract", "defs": sorted(DEFS)}, me, Counter(), Counter())
        self.assertEqual((label, kind), ("StableSwap3Pool.vy", "hit"))


if __name__ == "__main__":
    unittest.main()
