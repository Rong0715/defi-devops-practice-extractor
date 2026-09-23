"""Unit tests for the CI engine and the merge rules.  python3 -m unittest discover tests"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aggregate import apply_context, merge  # noqa: E402
from levels import RANK, adoption, parse_triggers  # noqa: E402
from probes import VARS  # noqa: E402
from repo import Repo  # noqa: E402


def make_repo(files):
    d = Path(tempfile.mkdtemp())
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return Repo("t", "https://github.com/o/t", d)


WF = "name: ci\non:\n  pull_request:\n  push:\n    branches: [main]\njobs:\n  a:\n    steps:\n      - run: {cmd}\n"
SLITHER = r"\bslither\b"


class Triggers(unittest.TestCase):
    def test_block_inline_and_list(self):
        self.assertEqual(parse_triggers(WF.format(cmd="x").splitlines()), {"pull_request", "push"})
        self.assertEqual(parse_triggers(["on: [push, workflow_dispatch]"]), {"push", "workflow_dispatch"})
        self.assertEqual(parse_triggers(["on: push"]), {"push"})


class Levels(unittest.TestCase):
    def test_direct_run_in_ci(self):
        r = make_repo({".github/workflows/a.yml": WF.format(cmd="slither .")})
        self.assertEqual(adoption(r, ci=SLITHER)[0], "runs_in_ci")

    def test_one_hop_through_make_and_yarn(self):
        r = make_repo({".github/workflows/a.yml": WF.format(cmd="make analyze"),
                       "Makefile": "analyze:\n\tyarn sec\n",
                       "package.json": '{"scripts": {"sec": "slither ."}}'})
        lvl, ev = adoption(r, ci=SLITHER)
        self.assertEqual(lvl, "runs_in_ci")
        self.assertIn("package.json", ev[0]["via"])

    def test_makefile_only_is_configured(self):
        r = make_repo({"Makefile": "analyze:\n\tslither .\n"})
        self.assertEqual(adoption(r, ci=SLITHER)[0], "configured")

    def test_manual_or_scheduled_only_is_not_ci(self):
        r = make_repo({".github/workflows/a.yml": "on:\n  workflow_dispatch:\njobs:\n  a:\n    steps:\n      - run: slither .\n"})
        self.assertEqual(adoption(r, ci=SLITHER)[0], "configured")

    def test_continue_on_error_and_swallowed_exit(self):
        coe = ("on: push\njobs:\n  a:\n    steps:\n      - name: s\n        run: slither .\n"
               "        continue-on-error: true\n")
        self.assertEqual(adoption(make_repo({".github/workflows/a.yml": coe}), ci=SLITHER)[0], "configured")
        sw = WF.format(cmd="slither . || true")
        self.assertEqual(adoption(make_repo({".github/workflows/a.yml": sw}), ci=SLITHER)[0], "configured")

    def test_enforcing_step_beats_an_earlier_advisory_one(self):
        wf = ("on:\n  pull_request:\njobs:\n  a:\n    steps:\n"
              "      - name: advisory\n        run: slither . || true\n"
              "      - name: gate\n        run: slither . --fail-high\n")
        lvl, ev = adoption(make_repo({".github/workflows/a.yml": wf}), ci=SLITHER)
        self.assertEqual(lvl, "runs_in_ci")
        self.assertNotIn("advisory", ev[0])

    def test_job_level_continue_on_error(self):
        wf = "on: push\njobs:\n  a:\n    continue-on-error: true\n    steps:\n      - run: slither .\n"
        self.assertEqual(adoption(make_repo({".github/workflows/a.yml": wf}), ci=SLITHER)[0], "configured")

    def test_continue_on_error_does_not_leak_between_jobs(self):
        wf = ("on: push\njobs:\n  a:\n    continue-on-error: true\n    steps:\n      - run: echo hi\n"
              "  b:\n    steps:\n      - run: slither .\n")
        self.assertEqual(adoption(make_repo({".github/workflows/a.yml": wf}), ci=SLITHER)[0], "runs_in_ci")

    def test_workflow_filename_is_a_fallback(self):
        wf = "on: push\njobs:\n  a:\n    steps:\n      - run: ./ci.sh\n"
        lvl, ev = adoption(make_repo({".github/workflows/run-slither.yaml": wf}), ci=SLITHER)
        self.assertEqual(lvl, "runs_in_ci")
        self.assertTrue(ev[0]["by_filename"])

    def test_filename_does_not_override_an_advisory_command(self):
        wf = "on: pull_request\njobs:\n  a:\n    steps:\n      - run: slither . || true\n"
        self.assertEqual(adoption(make_repo({".github/workflows/run-slither.yaml": wf}), ci=SLITHER)[0],
                         "configured")

    def test_data_lines_never_count_as_running(self):
        """An env var or a cache path naming a tool is a trace, not an invocation:
        it may read as 'mentioned', but must never reach configured/runs_in_ci."""
        wf = ("on: push\njobs:\n  a:\n    env:\n      SLITHER_KEY: x\n    steps:\n"
              "      - uses: actions/cache@v4\n        with:\n          path: slither/out\n")
        lvl = adoption(make_repo({".github/workflows/a.yml": wf}), ci=SLITHER)[0]
        self.assertLess(RANK[lvl], RANK["configured"], f"data line reached {lvl}")

    def test_multiline_run_block(self):
        wf = "on: push\njobs:\n  a:\n    steps:\n      - run: |\n          cd x\n          slither .\n"
        self.assertEqual(adoption(make_repo({".github/workflows/a.yml": wf}), ci=SLITHER)[0], "runs_in_ci")

    def test_mention_only_in_docs(self):
        r = make_repo({"README.md": "we use slither"})
        self.assertEqual(adoption(r, ci=SLITHER)[0], "mentioned")

    def test_technique_needs_code_present(self):
        r = make_repo({".github/workflows/a.yml": WF.format(cmd="forge test")})
        self.assertEqual(adoption(r, ci="forge test", ci_needs_present=True, present=lambda: [], mention=False)[0], "absent")


class Merge(unittest.TestCase):
    def test_strongest_level_wins_and_error_blocks_negative(self):
        v = VARS["D3_static.slither"]
        ok = lambda x: {"status": "ok", "value": x, "evidence": [{"path": "p"}]}
        self.assertEqual(merge(v, {"a": ok("absent"), "b": ok("runs_in_ci")})["value"], "runs_in_ci")
        err = {"status": "error", "error": "boom"}
        self.assertEqual(merge(v, {"a": ok("absent"), "b": err})["status"], "error")
        self.assertEqual(merge(v, {"a": ok("configured"), "b": err})["value"], "configured")

    def test_na_and_unknown_come_from_protocol_labels(self):
        ok = {"status": "ok", "value": "absent", "evidence": []}
        v = VARS["D6_upgrade.upgrade_safety_check"]
        self.assertEqual(apply_context(v, ok, {"upgradeability": "immutable"})["status"], "na")
        self.assertEqual(apply_context(v, ok, {"upgradeability": "upgradeable"})["status"], "ok")
        g = VARS["D6_upgrade.proposal_simulation"]
        neg = {"status": "ok", "value": False, "evidence": []}
        self.assertEqual(apply_context(g, neg, {"gap_tags": {"governance"}, "has_admin_role": "yes"})["status"], "unknown")
        self.assertEqual(apply_context(g, {"status": "ok", "value": True, "evidence": []},
                                       {"gap_tags": {"governance"}})["status"], "ok")


if __name__ == "__main__":
    unittest.main()
