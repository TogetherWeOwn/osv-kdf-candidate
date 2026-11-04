import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import unittest
from unittest import mock


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
    def lint(self, title="ci: add scaffold validation", body=None, **overrides):
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
        env.update(overrides)
        return subprocess.run(
            [sys.executable, "-I", "-c", script], env=env,
            capture_output=True, text=True, timeout=5,
        )

    def test_valid_ci_pr_passes(self):
        result = self.lint()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_valid_fix_pr_passes(self):
        body = ("## Linked Issues or Issue Description\n- Fixes #1 with a public issue link.\n"
                "## Verification\n- Dependency-free scaffold guard tests passed locally.\n")
        result = self.lint("fix: repair scaffold validation", body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_merge_group_checks_supplied_commits_and_allows_empty_payload(self):
        self.assertEqual(self.lint(EVENT="merge_group", COMMITS="").returncode, 0)
        result = self.lint(EVENT="merge_group", COMMITS=json.dumps([{"message": "bad subject"}]))
        self.assertNotEqual(result.returncode, 0)

    def test_bad_title_empty_body_and_missing_issue_fail(self):
        for title, body in (("unconventional title", None),
                            ("ci: add scaffold validation", ""),
                            ("fix: repair scaffold validation", None)):
            with self.subTest(title=title, body=body):
                self.assertNotEqual(self.lint(title, body).returncode, 0)

    def test_tracker_and_private_host_text_fail(self):
        for text in ("QAX-" + "123", "qax-" + "123", "Qax-" + "123",
                     "service." + "internal", "local" + "host"):
            with self.subTest(text=text):
                result = self.lint(body="## Verification\n- Validation passed for " + text)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("::error", result.stdout)

    def test_configured_prefixes_allow_public_standard_names(self):
        result = self.lint(body="## Verification\n- Verified a SHA-" + "256 checksum locally.")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_contradictory_card_reference_setting_fails_closed(self):
        result = self.lint(body="## Verification\n- Validation passed for QAX-" + "123",
                           REQUIRE_CARD_REF="true")
        self.assertNotEqual(result.returncode, 0)

    def test_bot_titles_warn_but_private_references_fail(self):
        for author in ("dependabot[bot]", "renovate[bot]"):
            with self.subTest(author=author):
                self.assertEqual(self.lint(title="dependency update", body="", AUTHOR=author).returncode, 0)
                self.assertNotEqual(self.lint(body="<!-- service." + "internal -->", AUTHOR=author).returncode, 0)
                self.assertNotEqual(self.lint(title="ci: update QAX-" + "123", AUTHOR=author).returncode, 0)

    def test_private_text_in_comments_queries_and_addresses_fails(self):
        addresses = ((10, 1, 2, 3), (172, 16, 1, 2), (192, 168, 1, 2), (127, 0, 0, 1))
        texts = ["<!-- QAX-" + "123 -->", "<!-- service." + "internal -->",
                 "https://github.com/org/repo/issues/1?next=service." + "internal/"]
        texts.extend(".".join(map(str, address)) for address in addresses)
        for text in texts:
            with self.subTest(text=text):
                self.assertNotEqual(self.lint(body="## Verification\n- Validation passed for " + text).returncode, 0)
        self.assertEqual(self.lint(body="## Verification\n- See https://github.com/org/repo/issues/1").returncode, 0)

    def test_only_exact_template_header_is_exempt(self):
        workflow = (WORKFLOWS / "pr-lint.yml").read_text()
        script = textwrap.dedent(
            workflow.split("python3 - <<'PYEOF'\n", 1)[1].rsplit("          PYEOF", 1)[0]
        )
        header = next(ast.literal_eval(node.value) for node in ast.parse(script).body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "TEMPLATE_HEADER"
                              for target in node.targets))
        body = header + "\n## Verification\n- Scaffold validation passed locally.\n"
        self.assertEqual(self.lint(body=body).returncode, 0)
        self.assertEqual(self.lint(EVENT="push", COMMITS=json.dumps([
            {"message": "ci: add scaffold\n\n" + body}
        ])).returncode, 0)
        self.assertNotEqual(self.lint(body=body + "<!-- service." + "internal -->").returncode, 0)
        self.assertNotEqual(self.lint(body=body.replace("Title:", "Title: service." + "internal")).returncode, 0)

    def test_crlf_template_header_is_exempt_but_added_private_text_is_not(self):
        workflow = (WORKFLOWS / "pr-lint.yml").read_text()
        script = textwrap.dedent(
            workflow.split("python3 - <<'PYEOF'\n", 1)[1].rsplit("          PYEOF", 1)[0]
        )
        header = next(ast.literal_eval(node.value) for node in ast.parse(script).body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "TEMPLATE_HEADER"
                              for target in node.targets))
        body = (header + "\n## Verification\n- Scaffold validation passed locally.\n").replace("\n", "\r\n")
        for event in ("pull_request", "push"):
            with self.subTest(event=event):
                def check(text):
                    return self.lint(body=text, EVENT=event, COMMITS=json.dumps([
                        {"message": "ci: add scaffold\n\n" + text}
                    ]))
                result = check(body)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotEqual(check(body + "<!-- service." + "internal -->").returncode, 0)
                self.assertNotEqual(check(body.replace("Title:", "Title: service." + "internal")).returncode, 0)

    def test_github_paths_do_not_hide_private_hosts_or_addresses(self):
        texts = ["service." + "internal", "local" + "host"]
        texts.extend(".".join(map(str, address)) for address in (
            (10, 1, 2, 3), (172, 16, 1, 2), (192, 168, 1, 2), (127, 0, 0, 1)
        ))
        for text in texts:
            with self.subTest(text=text):
                url = "https://github.com/org/repo/blob/main/" + text
                self.assertNotEqual(self.lint(body="## Verification\n- See " + url).returncode, 0)
                self.assertNotEqual(self.lint(EVENT="push", COMMITS=json.dumps([
                    {"message": "ci: add scaffold\n\nSee " + url}
                ])).returncode, 0)
        body = "## Verification\n- See https://github.com/org/repo/blob/main/README.md"
        self.assertEqual(self.lint(body=body).returncode, 0)
        body = "## Verification\n- See https://github.com/org/repo/ABC/issues/1"
        self.assertEqual(self.lint(body=body).returncode, 0)

    def test_push_scans_private_references_even_in_merge_and_revert_commits(self):
        for message in ("ci: update QAX-" + "123", "ci: update\n\nservice." + "internal",
                        "Merge branch QAX-" + "123", 'Revert "ci: update QAX-' + '123"'):
            with self.subTest(message=message):
                result = self.lint(EVENT="push", COMMITS=json.dumps([{"id": "abc", "message": message}]))
                self.assertNotEqual(result.returncode, 0)
        for message in ("ci: update scaffold", "Merge branch clean", 'Revert "ci: update scaffold"'):
            self.assertEqual(self.lint(EVENT="push", COMMITS=json.dumps([{"message": message}])).returncode, 0)


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
        ci_job = text.split("\n  ci-ok:\n", 1)[1]
        self.assertRegex(ci_job, r"(?m)^    if: always\(\)$")
        self.assertRegex(ci_job, r"(?m)^    needs: \[lint\]$")
        self.assertIn("python3 -I -m unittest discover -s .github/tests -v", text)
        self.assertIn("python3 -I .github/scripts/check_ci.py", text)
        self.assertIn("023070a287cd8cccd71515fedc843f1985bf96c436b7effaecce67290e7e0757", text)
        self.assertIn('"$bin_dir/actionlint" -shellcheck= -pyflakes=', text)
        self.assertNotIn("paths:", text)
        self.assertNotIn("goes here", text)

    def test_secret_scan_verifies_archive_before_execution(self):
        text = "\n".join(line for line in (WORKFLOWS / "gitleaks.yml").read_text().splitlines()
                         if not line.lstrip().startswith("#"))
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
            self.assertRegex(text, r"(?m)^  " + re.escape(setting) + r"(?:\s*#.*)?$")
        self.assertNotIn("$default-branch", text)

    def test_comments_cannot_replace_active_workflow_guards(self):
        cases = (
            ("starter-ci.yml", "    if: always()", "    # if: always()",
             self.test_ci_has_real_validation_and_always_reports),
            ("pr-lint.yml", '  STRICT_INTERNAL_LEVEL: "error"',
             '  # STRICT_INTERNAL_LEVEL: "error"\n  STRICT_INTERNAL_LEVEL: "warning"',
             self.test_pr_lint_remains_strict),
            ("gitleaks.yml", '            "$download_dir/gitleaks.tgz" | sha256sum -c -',
             '            "$download_dir/gitleaks.tgz"\n          # sha256sum -c -',
             self.test_secret_scan_verifies_archive_before_execution),
        )
        for filename, before, after, check in cases:
            with self.subTest(filename=filename):
                text = (WORKFLOWS / filename).read_text()
                self.assertIn(before, text)
                with mock.patch.object(Path, "read_text", return_value=text.replace(before, after)):
                    with self.assertRaises((AssertionError, ValueError)):
                        check()

    def test_secret_scan_rejects_repository_policy_overrides(self):
        text = (WORKFLOWS / "gitleaks.yml").read_text()
        for filename in (".gitleaks.toml", ".gitleaksignore"):
            guard = 'test ! -e "$GITHUB_WORKSPACE/' + filename + '"'
            self.assertIn(guard, text)
            self.assertLess(text.index(guard), text.index('"$bin_dir/gitleaks" detect'))

    def test_manual_and_pr_size_runs_have_separate_groups(self):
        text = (WORKFLOWS / "pr-size.yml").read_text()
        self.assertIn("group: ${{ github.workflow }}-${{ github.event_name }}-", text)
        self.assertRegex(text, r"(?m)^    if: github\.event_name == 'pull_request' \|\| inputs\.pr_number != ''$")


if __name__ == "__main__":
    unittest.main()
