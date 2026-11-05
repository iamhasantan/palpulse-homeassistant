"""Prevent accidental regression to older PalPulse Marketplace branding."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
NAME = "PalPulse for Home Assistant"
OLD = "PalPulse Historical Energy"


class BrandingTests(unittest.TestCase):
    def test_marketplace_and_manifest_names_agree(self):
        manifest = json.loads(
            (ROOT / "custom_components/palpulse/manifest.json").read_text()
        )
        hacs = json.loads((ROOT / "hacs.json").read_text())
        self.assertEqual(manifest["name"], NAME)
        self.assertEqual(hacs["name"], NAME)
        self.assertEqual(manifest["domain"], "palpulse")

    def test_setup_and_default_entry_use_correct_name(self):
        flow = (ROOT / "custom_components/palpulse/config_flow.py").read_text()
        strings = (ROOT / "custom_components/palpulse/strings.json").read_text()
        translation = (
            ROOT / "custom_components/palpulse/translations/en.json"
        ).read_text()
        self.assertIn('title="PalPulse for Home Assistant"', flow)
        self.assertEqual(json.loads(strings)["config"]["step"]["user"]["title"], NAME)
        self.assertEqual(json.loads(translation)["config"]["step"]["user"]["title"], NAME)

    def test_old_marketplace_name_is_absent(self):
        for location in (
            "README.md",
            "hacs.json",
            "custom_components/palpulse/manifest.json",
            "custom_components/palpulse/config_flow.py",
            "custom_components/palpulse/strings.json",
            "custom_components/palpulse/translations/en.json",
        ):
            with self.subTest(location=location):
                self.assertNotIn(OLD, (ROOT / location).read_text())


if __name__ == "__main__":
    unittest.main()
