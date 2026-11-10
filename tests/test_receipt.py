import contextlib
import io
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch


cli = runpy.run_path(str(Path(__file__).resolve().parents[1] / "gh-ci-receipt"))
functions = cli["main"].__globals__
REPO = "example/project"


def run(run_id=1, attempt=1):
    return dict(id=run_id, run_attempt=attempt, status="completed", name="CI",
                run_started_at="2026-11-09T12:00:00Z", conclusion="success",
                workflow_id=123, event="pull_request", head_branch="feature")


def job(job_id, start, end, conclusion="success"):
    began = f"2026-11-09T12:{start}:00Z" if start else None
    ended = f"2026-11-09T12:{end}:00Z" if end else None
    return dict(id=job_id, name=f"Job {job_id}", started_at=began,
                completed_at=ended, conclusion=conclusion, steps=[
                    dict(name="Install | compile", started_at=began,
                         completed_at=ended, conclusion=conclusion)
                ])


class ReceiptTests(unittest.TestCase):
    def test_parallel_jobs_have_distinct_wall_and_runner_time(self):
        receipt = cli["summarize"](run(), [
            job(1, "01", "10"), job(2, "02", "08"), job(3, None, None, "skipped")
        ])
        self.assertEqual(receipt["wall"], 600)
        self.assertEqual(receipt["runner"], 900)

    def test_missing_timing_and_reruns_do_not_claim_full_wall(self):
        incomplete = cli["summarize"](run(), [job(1, "01", "10"), job(2, None, None)])
        self.assertIsNone(incomplete["wall"])
        self.assertIsNone(incomplete["runner"])
        rerun = cli["summarize"](run(attempt=2), [job(1, "01", "10")])
        self.assertIsNone(rerun["wall"])
        self.assertEqual(rerun["runner"], 540)

    def test_pagination_keeps_all_jobs_in_correct_attempt(self):
        pages = [{"total_count": 2, "jobs": [job(1, "01", "10")]},
                 {"total_count": 2, "jobs": [job(2, "01", "10")]}]
        with patch.dict(functions, {"api": unittest.mock.Mock(return_value=pages)}):
            self.assertEqual(len(cli["fetch_jobs"](REPO, run(attempt=2))), 2)
            functions["api"].assert_called_once_with(
                "repos/example/project/actions/runs/1/attempts/2/jobs?per_page=100",
                paginate=True)
            functions["api"].return_value = pages[:1]
            with self.assertRaisesRegex(ValueError, "Incomplete job data"):
                cli["fetch_jobs"](REPO, run(attempt=2))

    def test_compare_flags_job_set_changes_and_keeps_timing_separate(self):
        def api(path, paginate=False):
            if path.endswith("/1"):
                return run(1)
            if path.endswith("/2"):
                return run(2)
            jobs = [job(1, "00", "10")] if "/runs/1/" in path else [
                job(1, "00", "06"), job(2, "00", "06")]
            return [{"total_count": len(jobs), "jobs": jobs}]

        output = io.StringIO()
        with patch.dict(functions, {"api": api}), patch(
            "sys.argv", ["gh-ci-receipt", "compare", "1", "2", "--repo", REPO]
        ), contextlib.redirect_stdout(output):
            self.assertEqual(cli["main"](), 0)
        report = output.getvalue()
        self.assertIn("−4m 00s (-40.0%)", report)
        self.assertIn("+2m 00s (+20.0%)", report)
        self.assertIn("job set", report)

    def test_audit_escapes_step_names_and_links_evidence(self):
        def api(path, paginate=False):
            if not paginate:
                return {"workflow_runs": [run(1), run(2)]}
            return [{"total_count": 1, "jobs": [job(7, "00", "10")]}]

        with patch.dict(functions, {"api": api}):
            report = cli["audit"](REPO, 2, None)
        self.assertIn("Install &#124; compile | 10m 00s | 2", report)
        self.assertIn("/actions/runs/2/job/7", report)


if __name__ == "__main__":
    unittest.main()
