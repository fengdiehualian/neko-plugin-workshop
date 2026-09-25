"""Report format node."""

from __future__ import annotations

from typing import Any

from .node_logging import LoggedNode
from .utils import format_report_markdown, safe_str


class ReportFormatNode(LoggedNode):
    """Format the final display report."""

    node_name = "report.format"

    def run(self, analysis: dict[str, Any]) -> str:
        """Format normalized analysis into markdown."""

        started = self._begin(title=safe_str(analysis.get("title")))
        try:
            report = format_report_markdown(analysis)
            self._end(started, report_chars=len(report))
            return report
        except Exception as exc:
            self._fail(started, exc)
            raise
