#!/usr/bin/env python3
"""Offline regression tests for layout-check.sh reports; no device or progress IO."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


spec = importlib.util.spec_from_file_location(
    'layout_assertions', Path(__file__).with_name('assert-layout.py'))
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)

BASE = {
    'size': '390x844', 'page': 'main', 'vp': [390, 844], 'availableH': 844,
    'homeVariant': 'v2', 'homeH': 844, 'secH': 844, 'homeOverflow': 0,
    'slotOverflow': 0, 'slotOverflowY': 'auto', 'uVar': '1px', 'ready': True,
    'bodyOverflowX': 0, 'mainKids': ['div.page-slot.page-enter'],
    'rules': [['main > .page-slot', 1]],
    'centerRect': [74, 0, 238, 693], 'idRect': [74, 26, 207, 46],
    'capsRect': [74, 86, 207, 54], 'capOverflow': [0, 0],
    'panelRect': [74, 156, 238, 335], 'deckRect': [0, 709, 390, 135],
    'funcRect': [324, 244, 50, 218], 'listRect': [90, 195, 206, 284],
    'listLastVisible': True, 'ringHit': True,
}


class LayoutAssertionsTest(unittest.TestCase):
    def check_report(self, report):
        with TemporaryDirectory() as root:
            path = Path(root, 'report.jsonl')
            path.write_text(json.dumps(report) + '\n', encoding='utf-8')
            with redirect_stdout(StringIO()):
                return layout.main(str(path))

    def test_normal_home(self):
        self.assertEqual(self.check_report(BASE), 0)

    def test_wide_stage_scale_and_collapsed_list(self):
        report = dict(BASE, uVar='3.348837px', listRect=[807, 794, 0, 1031])
        self.assertGreaterEqual(len(layout.responsive_failures(report)), 2)

    def test_fixed_card_overflows_small_center(self):
        report = dict(BASE, centerRect=[74, 0, 168, 417])
        self.assertTrue(layout.responsive_failures(report))

    def test_capsule_overflow(self):
        self.assertTrue(layout.responsive_failures(dict(BASE, capOverflow=[0, 44])))

    def test_blocked_ring(self):
        self.assertTrue(layout.responsive_failures(dict(BASE, ringHit=False)))

    def test_panel_overlaps_deck(self):
        self.assertTrue(layout.responsive_failures(dict(BASE, deckRect=[0, 285, 390, 135])))

    def test_function_bar_overlaps_deck(self):
        self.assertTrue(layout.responsive_failures(dict(BASE, funcRect=[324, 680, 50, 140])))

    def test_last_task_is_not_reachable(self):
        self.assertTrue(layout.responsive_failures(dict(BASE, listLastVisible=False)))

    def test_short_expanded_page_can_scroll(self):
        report = deepcopy(BASE)
        report.update(
            page='main-open', vp=[390, 420], availableH=420,
            homeH=574, secH=574, slotOverflow=154, expanded=True,
            centerRect=[74, 0, 238, 423], panelRect=[74, 283, 238, 140],
            listRect=[90, 318, 206, 97], deckRect=[0, 439, 390, 135],
            funcRect=[324, 216, 50, 88],
        )
        self.assertEqual(self.check_report(report), 0)
        report['slotOverflowY'] = 'hidden'
        self.assertEqual(self.check_report(report), 1)

    def test_missing_data_or_height_is_failure(self):
        self.assertEqual(self.check_report(dict(BASE, ready=False)), 1)
        self.assertEqual(self.check_report(dict(BASE, homeH=0)), 1)

    def test_video_fills_card_and_tracks_aspect(self):
        report = dict(BASE, page='shot', liveRect=[10, 58, 360, 640],
                      videoRect=[10, 58, 360, 640], videoSize=[720, 1280])
        self.assertFalse(layout.responsive_failures(report))
        report['videoRect'] = [-170, -422, 720, 1600]
        self.assertTrue(layout.responsive_failures(report))

    def test_stale_video_aspect_is_failure(self):
        report = dict(BASE, page='shot', liveRect=[10, 58, 288, 640],
                      videoRect=[10, 58, 288, 640], videoSize=[720, 1280])
        self.assertTrue(layout.responsive_failures(report))


if __name__ == '__main__':
    unittest.main()
