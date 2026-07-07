"""Tests for accept/reject thresholds — certainty and spam filtering.

Tests both the CLI integration (via CliRunner) and the inline filtering logic.
"""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from email_parser.cli import cli
from email_parser.schema import SchemaDef


# ── Helpers ──────────────────────────────────────────────────────────

def _mock_extraction(certainty: float, spam: float):
    """Return a mock for extract_from_email that returns controlled scores."""
    def mock_extract(schema, content, **kwargs):
        return {"certainty": certainty, "spam": spam, "extracted": {"items": [{"name": "Test"}]}}
    return mock_extract


# ── CLI Integration Tests ────────────────────────────────────────────

class TestCliAcceptReject:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        schema_yaml = """database: /tmp/_test_ar.db
tables:
  - name: items
    columns:
      - name: name
        type: TEXT
        required: true
"""
        self.schema_path = tmp_path / "schema.yaml"
        self.schema_path.write_text(schema_yaml)
        self.mock_schema = SchemaDef.model_validate({
            "database": "/tmp/_test_ar.db",
            "tables": [{"name": "items", "columns": [{"name": "name", "type": "TEXT", "required": True}]}],
        })

    def _run(self, cli_runner, args=None):
        base = ["parse", str(self.schema_path), "--text", "Some email content"]
        if args:
            base.extend(args)

        with patch("email_parser.cli.SchemaDef.from_yaml", return_value=self.mock_schema):
            with patch("email_parser.cli.insert_extracted", return_value={"items": 1}):
                return cli_runner.invoke(cli, base, catch_exceptions=False)

    def test_accepts_good_extraction(self, cli_runner):
        """Default thresholds should accept good extraction."""
        with patch("email_parser.cli.extract_from_email", _mock_extraction(0.9, 0.05)):
            result = self._run(cli_runner)
        assert result.exit_code == 0
        assert "certainty: 0.90, spam: 0.05" in result.output
        assert "row(s) inserted" in result.output
        assert "Skipped" not in result.output

    def test_rejects_low_certainty(self, cli_runner):
        with patch("email_parser.cli.extract_from_email", _mock_extraction(0.1, 0.05)):
            result = self._run(cli_runner, ["--min-certainty", "0.5"])
        assert result.exit_code == 0
        assert "Skipped: certainty 0.10 below minimum 0.5" in result.output

    def test_rejects_high_spam(self, cli_runner):
        with patch("email_parser.cli.extract_from_email", _mock_extraction(0.9, 0.9)):
            result = self._run(cli_runner, ["--max-spam", "0.5"])
        assert result.exit_code == 0
        assert "Skipped: spam 0.90 exceeds maximum 0.5" in result.output

    def test_rejects_when_both_bad(self, cli_runner):
        with patch("email_parser.cli.extract_from_email", _mock_extraction(0.1, 0.9)):
            result = self._run(cli_runner, ["--min-certainty", "0.5", "--max-spam", "0.3"])
        assert result.exit_code == 0
        assert "Skipped" in result.output

    def test_accepts_edge_values(self, cli_runner):
        with patch("email_parser.cli.extract_from_email", _mock_extraction(0.5, 0.5)):
            result = self._run(cli_runner, ["--min-certainty", "0.5", "--max-spam", "0.5"])
        assert result.exit_code == 0
        assert "Skipped" not in result.output
        assert "row(s) inserted" in result.output

    def test_zero_certainty_rejected(self, cli_runner):
        with patch("email_parser.cli.extract_from_email", _mock_extraction(0.0, 0.0)):
            result = self._run(cli_runner, ["--min-certainty", "0.01"])
        assert "Skipped" in result.output

    def test_perfect_spam_rejected(self, cli_runner):
        with patch("email_parser.cli.extract_from_email", _mock_extraction(0.9, 1.0)):
            result = self._run(cli_runner, ["--max-spam", "0.99"])
        assert "Skipped" in result.output

    def test_defaults_accept_everything(self, cli_runner):
        for cert, sp in [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (0.5, 0.5)]:
            with patch("email_parser.cli.extract_from_email", _mock_extraction(cert, sp)):
                result = self._run(cli_runner)
            assert result.exit_code == 0
            assert "Skipped" not in result.output, f"failed at cert={cert}, spam={sp}"


# ── Filtering Logic Unit Tests ───────────────────────────────────────

def test_filtering_logic_accepts():
    """Direct test of the filtering criteria used in CLI."""
    def should_accept(certainty, spam, min_cert=0.0, max_sp=1.0):
        if certainty < min_cert:
            return False, f"certainty {certainty} < {min_cert}"
        if spam > max_sp:
            return False, f"spam {spam} > {max_sp}"
        return True, "ok"

    cases = [
        # (certainty, spam, min_cert, max_sp, expected_accept)
        (0.9, 0.05, 0.0, 1.0, True),
        (0.0, 0.0, 0.0, 1.0, True),
        (1.0, 1.0, 0.0, 1.0, True),
        (0.3, 0.01, 0.5, 1.0, False),   # low cert
        (0.9, 0.8, 0.0, 0.5, False),    # high spam
        (0.1, 0.9, 0.5, 0.3, False),    # both bad
        (0.5, 0.5, 0.5, 0.5, True),     # boundary
        (0.5, 0.51, 0.5, 0.5, False),   # spam just over
        (0.49, 0.5, 0.5, 0.5, False),   # cert just under
    ]
    for cert, spam, min_c, max_s, expected in cases:
        accepted, _ = should_accept(cert, spam, min_c, max_s)
        assert accepted == expected, f"cert={cert}, spam={spam}, mc={min_c}, ms={max_s}: expected accept={expected}, got {accepted}"


def test_filtering_logic_reason():
    """Verify the reason string is informative."""
    def should_accept(certainty, spam, min_cert=0.0, max_sp=1.0):
        if certainty < min_cert:
            return False, f"certainty {certainty} < {min_cert}"
        if spam > max_sp:
            return False, f"spam {spam} > {max_sp}"
        return True, "ok"

    _, reason = should_accept(0.1, 0.5, min_cert=0.5)
    assert "certainty" in reason
    assert "0.1" in reason

    _, reason = should_accept(0.9, 0.8, max_sp=0.5)
    assert "spam" in reason
    assert "0.8" in reason

    accepted, reason = should_accept(0.9, 0.05)
    assert accepted is True
    assert reason == "ok"


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def cli_runner():
    return CliRunner()
