#!/usr/bin/env python3
"""Importer tests (fictional people only)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from stateverge.cn_news.archive import importer  # noqa: E402
from stateverge.cn_news.archive.database import ArchiveDB  # noqa: E402
from stateverge.cn_news.archive.models import ArchiveStatus, CopyrightType, SourceTier  # noqa: E402

EXAMPLE = _ROOT / "docs" / "archive_import_example.json"

BASE = {
    "person_name": "Alex Rivera",
    "topic": "Iran",
    "statement_date": "2025-03-01",
    "statement_text_original": "We will not send troops",
    "source_name": "White House",
    "source_url": "https://www.whitehouse.gov/x",
    "transcript": "Question first. We will not send troops. Thanks.",
}


def _write(records) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "claims.json"
    p.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return p


class TestImporter(unittest.TestCase):
    def test_example_file(self):
        db = ArchiveDB(":memory:")
        report = importer.import_file(EXAMPLE, db)
        self.assertEqual((report.imported, report.failed), (2, 0), report.render())
        first = db.get(report.records[0].claim_id)
        self.assertTrue(first.transcript_verified)
        self.assertEqual((first.video_start, first.video_end), (64.5, 70.0))
        self.assertEqual(first.source_type, SourceTier.SOURCE_A.value)
        self.assertEqual(first.copyright_type, CopyrightType.US_GOVERNMENT_WORK.value)
        second = db.get(report.records[1].claim_id)
        self.assertTrue(second.transcript_verified)  # via transcript_file
        self.assertEqual(second.copyright_type, CopyrightType.FAIR_USE_COMMENTARY.value)

    def test_cannot_self_certify(self):
        with self.assertRaises(ValueError):
            importer.build_claim({**BASE, "transcript_verified": True})
        with self.assertRaises(ValueError):
            importer.build_claim({**BASE, "source_type": "SOURCE_A"})

    def test_paraphrase_not_verified(self):
        claim, warnings = importer.build_claim({**BASE, "statement_text_original": "We won't send any troops"})
        self.assertFalse(claim.transcript_verified)
        self.assertTrue(any("逐字" in w for w in warnings))

    def test_missing_transcript_warns(self):
        rec = dict(BASE)
        del rec["transcript"]
        claim, warnings = importer.build_claim(rec)
        self.assertFalse(claim.transcript_verified)
        self.assertTrue(any("未提供逐字稿" in w for w in warnings))

    def test_tier_e_warns(self):
        claim, warnings = importer.build_claim({**BASE, "source_name": "anon", "source_url": "https://www.tiktok.com/@a/1"})
        self.assertEqual(claim.source_type, SourceTier.SOURCE_E.value)
        self.assertTrue(any("线索" in w for w in warnings))

    def test_bad_records_reported_not_fatal(self):
        db = ArchiveDB(":memory:")
        path = _write([BASE, {**BASE, "statement_date": "2025-13-01"}, {"person_name": "x"}, {**BASE, "typo_field": 1},
                       {**BASE, "claim_type": "LIE"}])
        report = importer.import_file(path, db)
        self.assertEqual((report.imported, report.failed), (1, 4), report.render())

    def test_reimport_keeps_workflow_state(self):
        db = ArchiveDB(":memory:")
        path = _write({"claims": [BASE]})
        cid = importer.import_file(path, db).records[0].claim_id
        c = db.get(cid)
        c.archive_status = ArchiveStatus.PUBLISHED.value
        db.upsert(c)
        report = importer.import_file(path, db)
        self.assertEqual(report.records[0].claim_id, cid)
        self.assertEqual(db.get(cid).archive_status, ArchiveStatus.PUBLISHED.value)
        self.assertEqual(len(db.search()), 1)

    def test_dry_run_writes_nothing(self):
        db = ArchiveDB(":memory:")
        importer.import_file(_write([BASE]), db, dry_run=True)
        self.assertEqual(db.search(), [])

    def test_fact_check_validated(self):
        with self.assertRaises(ValueError):
            importer.build_claim({**BASE, "fact_check": {"source": "Some Blog", "url": "https://b.example", "rating": "False"}})


if __name__ == "__main__":
    unittest.main()
