from __future__ import annotations

import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from maildb.ingest.__main__ import main

if TYPE_CHECKING:
    from pathlib import Path


def test_skip_embed_flag_parsed(tmp_path: Path) -> None:
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    with (
        patch("maildb.ingest.__main__.run_pipeline") as mock_pipeline,
        patch("maildb.ingest.__main__.create_pool") as mock_pool,
        patch("maildb.ingest.__main__.init_db"),
        patch.object(sys, "argv", ["prog", str(mbox), "--skip-embed"]),
    ):
        mock_pool.return_value = MagicMock()
        mock_pipeline.return_value = {}

        main()
        mock_pipeline.assert_called_once()
        call_kwargs = mock_pipeline.call_args[1]
        assert call_kwargs["skip_embed"] is True


def test_no_skip_embed_by_default(tmp_path: Path) -> None:
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    with (
        patch("maildb.ingest.__main__.run_pipeline") as mock_pipeline,
        patch("maildb.ingest.__main__.create_pool") as mock_pool,
        patch("maildb.ingest.__main__.init_db"),
        patch.object(sys, "argv", ["prog", str(mbox)]),
    ):
        mock_pool.return_value = MagicMock()
        mock_pipeline.return_value = {}

        main()
        mock_pipeline.assert_called_once()
        call_kwargs = mock_pipeline.call_args[1]
        assert call_kwargs.get("skip_embed", False) is False
