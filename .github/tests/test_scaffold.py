import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


class CiEvidenceTests(unittest.TestCase):
    def run_guard(self, value):
        env = os.environ.copy()
        env.pop("NEEDS_JSON", None)
        if value is not None:
            env["NEEDS_JSON"] = value
        return subprocess.run(
            [sys.executable, "-I", str(ROOT / ".github/scripts/check_ci.py")],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_success_discloses_scaffold_only(self):
        result = self.run_guard(json.dumps({"lint": {"result": "success"}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no candidate implementation tested", result.stdout)

    def test_failed_cancelled_and_skipped_jobs_fail_closed(self):
        for status in ("failure", "cancelled", "skipped", "pending", "", None):
            with self.subTest(status=status):
                result = self.run_guard(json.dumps({"lint": {"result": status}}))
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("scaffold validation succeeded", result.stdout)

    def test_missing_malformed_or_wrong_shape_fails_closed(self):
        for value in (None, "", "{", "null", "[]", "{}", '"success"',
                      '{"lint": null}', '{"lint": {}}',
                      '{"lint": "success"}', '{"other": {"result": "success"}}',
                      '{"lint": {"result": "success"}, "extra": {}}'):
            with self.subTest(value=value):
                result = self.run_guard(value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ci-ok:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


class PrLintTests(unittest.TestCase):
    def lint(self, title="ci: add scaffold validation", body=None):
        workflow = (WORKFLOWS / "pr-lint.yml").read_text()
        script = textwrap.dedent(
            workflow.split("python3 - <<'PYEOF'\n", 1)[1].rsplit("          PYEOF", 1)[0]
        )
        env = os.environ.copy()
        env.update({
            "EVENT": "pull_request", "TITLE": title,
            "BODY": body if body is not None else (
                "## Verification\n- Dependency-free scaffold guard tests passed locally.\n"
            ),
            "AUTHOR": "contributor", "REQUIRE_CARD_REF": "false",
            "INTERNAL_ID_LEVEL": "error", "INTERNAL_ID_PREFIXES": "QAX",
            "STRICT_INTERNAL_LEVEL": "error", "PR_STANDARDS_MODE": "error",
        })
        return subprocess.run(
            [sys.executable, "-I", "-c", script], env=env,
            capture_output=True, text=True, timeout=5,
        )

    def test_valid_ci_pr_passes(self):
        result = self.lint()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bad_title_empty_body_and_missing_issue_fail(self):
        for title, body in (("unconventional title", None),
                            ("ci: add scaffold validation", ""),
                            ("fix: repair scaffold validation", None)):
            with self.subTest(title=title, body=body):
                self.assertNotEqual(self.lint(title, body).returncode, 0)

    def test_tracker_and_private_host_text_fail(self):
        for text in ("QAX-" + "123", "service." + "internal", "local" + "host"):
            with self.subTest(text=text):
                result = self.lint(body="## Verification\n- Validation passed for " + text)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("::error", result.stdout)


class WorkflowContractTests(unittest.TestCase):
    def test_exact_bootstrap_workflow_set(self):
        self.assertEqual(
            {path.name for path in WORKFLOWS.iterdir()},
            {"starter-ci.yml", "pr-lint.yml", "gitleaks.yml", "pr-size.yml"},
        )

    def test_actions_and_reusable_workflows_are_sha_pinned(self):
        for path in WORKFLOWS.glob("*.yml"):
            text = path.read_text()
            uses = re.findall(r"\buses:\s*(\S+)", text)
            if path.name != "pr-lint.yml":
                self.assertTrue(uses, path.name)
            for ref in uses:
                with self.subTest(path=path.name, ref=ref):
                    self.assertRegex(ref, r"^[\w./-]+@[0-9a-f]{40}$")
            self.assertNotIn("secrets:", text)
            self.assertNotIn("secrets.", text)
            for minutes in re.findall(r"timeout-minutes:\s*(\d+)", text):
                self.assertLessEqual(int(minutes), 20)

    def test_ci_has_real_validation_and_always_reports(self):
        text = (WORKFLOWS / "starter-ci.yml").read_text()
        self.assertIn("if: always()", text)
        self.assertIn("needs: [lint]", text)
        self.assertIn("python3 -I -m unittest discover -s .github/tests -v", text)
        self.assertIn("python3 -I .github/scripts/check_ci.py", text)
        self.assertIn("023070a287cd8cccd71515fedc843f1985bf96c436b7effaecce67290e7e0757", text)
        self.assertIn('"$bin_dir/actionlint" -shellcheck= -pyflakes=', text)
        self.assertNotIn("paths:", text)
        self.assertNotIn("goes here", text)

    def test_secret_scan_verifies_archive_before_execution(self):
        text = (WORKFLOWS / "gitleaks.yml").read_text()
        self.assertIn("gitleaks_8.30.1_linux_x64.tar.gz", text)
        self.assertIn("551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb", text)
        self.assertIn('test "$(wc -c < "$download_dir/gitleaks.tgz")" -eq 8230402', text)
        self.assertLess(text.index("sha256sum -c -"), text.index("tar -xzf"))
        self.assertIn("fetch-depth: 0", text)
        self.assertIn('detect --source "$GITHUB_WORKSPACE" --redact --exit-code 1', text)
        self.assertNotIn("docker", text)

    def test_pr_lint_remains_strict(self):
        text = (WORKFLOWS / "pr-lint.yml").read_text()
        for setting in ('REQUIRE_CARD_REF: "false"', 'INTERNAL_ID_LEVEL: "error"',
                        'STRICT_INTERNAL_LEVEL: "error"', 'PR_STANDARDS_MODE: "error"'):
            self.assertIn(setting, text)
        self.assertNotIn("$default-branch", text)


if __name__ == "__main__":
    unittest.main()
