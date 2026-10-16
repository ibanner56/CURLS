"""Reject upstream unit-test skips except the two unsupported theme tests."""

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


ALLOWED_SKIPS = {
    ("ThemesTest", "test_get_themes"),
    ("ThemesTest", "test_theme_activate"),
}


def check_results(report):
    root = ET.parse(report).getroot()
    cases = root.findall(".//testcase")
    if root.tag != "testsuites" or not cases:
        raise ValueError("Expected a nonempty PHPUnit JUnit report")
    if root.find(".//failure") is not None or root.find(".//error") is not None:
        raise ValueError("Upstream unit report contains failures or errors")
    allowed = set()
    unexpected = []
    for case in cases:
        for skipped in case.findall("skipped"):
            key = (case.get("class"), case.get("name"))
            if key not in ALLOWED_SKIPS or key in allowed:
                unexpected.append(f"{key[0]}::{key[1]}")
            else:
                allowed.add(key)
    if unexpected:
        raise ValueError("Unexpected skipped upstream tests: " + ", ".join(unexpected))
    return len(allowed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    count = check_results(args.report)
    print(f"Upstream skip policy passed; known unsupported theme skips: {count}")
