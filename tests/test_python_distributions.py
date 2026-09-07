import importlib.util
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "check_python_distributions", Path(__file__).parents[1] / "scripts/check_python_distributions.py"
)
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


class DistributionTests(unittest.TestCase):
    def test_package_sources_are_allowed(self):
        checks.validate_members(["machboost/cli.py", "machboost-1.2.3.dist-info/METADATA"])

    def test_local_data_and_weights_are_rejected(self):
        for name in (
            "../credentials", "/absolute/file", "machboost/.env",
            "machboost/__pycache__/cli.pyc", "machboost/model.safetensors",
            "machboost/chat.sqlite3", "machboost/runtime/python",
            "machboost-1.2.3.dist-info/direct_url.json",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                checks.validate_members([name])

    def test_unexpected_upload_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "exactly"):
                checks.check(Path(folder), "1.2.3")

    def test_invalid_release_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "stable"):
            checks.check(Path("unused"), "../main")
