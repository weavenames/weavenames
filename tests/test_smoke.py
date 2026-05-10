"""Smoke test — ensures the package imports and CLI is wired."""

from click.testing import CliRunner

import weavenames
from weavenames.cli import main


def test_version():
    assert weavenames.__version__ == "0.0.1"


def test_cli_no_args():
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code == 0
    assert "Weavenames" in result.output
