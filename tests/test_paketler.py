"""Every place that installs Python packages installs the same exact versions.

The full image (Dockerfile), the code-only image (Dockerfile.code), the CI tests
(.github/workflows/testler.yml) and the OSS Scanner image (.oss-scanner/Dockerfile)
each carry one `pip install` line. If one drifts, production runs something the
tests did not.

    python -m unittest tests.test_paketler
"""

from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = (
    "Dockerfile",
    "Dockerfile.code",
    os.path.join(".github", "workflows", "testler.yml"),
    os.path.join(".oss-scanner", "Dockerfile"),
)

PIN = re.compile(r'"([A-Za-z0-9_.\-]+)==([0-9][A-Za-z0-9_.\-]*)"')


def _pins(name: str) -> dict[str, str]:
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        lines = [line for line in fh if "pip install" in line]
    pins: dict[str, str] = {}
    for line in lines:
        pins.update(PIN.findall(line))
    return pins


class PackagePinsTest(unittest.TestCase):
    def test_full_image_pins_every_package(self) -> None:
        pins = _pins("Dockerfile")
        for name in ("de-eli-mcp", "pypdf", "numpy", "pypdfium2"):
            self.assertIn(name, pins)

    def test_every_install_matches_the_full_image(self) -> None:
        expected = _pins("Dockerfile")
        for name in FILES[1:]:
            with self.subTest(file=name):
                self.assertEqual(_pins(name), expected)


if __name__ == "__main__":
    unittest.main()
