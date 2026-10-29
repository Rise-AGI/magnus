"""Directory custody remains safe on Python builds without extraction filters."""

import importlib.util
import io
import os
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "src/magnus/_archive.py"
spec = importlib.util.spec_from_file_location(
    "custody_archive_test",
    SOURCE,
)
archive_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_module)


class CustodyArchiveTests(unittest.TestCase):
    def setUp(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / "payload.tar"
        self.destination = self.root / "extracted"
        self.destination.mkdir()

    def create(
        self,
        members: list[tuple[str, bytes]],
        *,
        mode: int = 0o7777,
    ) -> None:
        with tarfile.open(
            self.archive,
            "w",
        ) as stream:
            for name, kind in members:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.mode = mode
                item.uid = item.gid = 12345
                if item.isreg():
                    item.size = 4
                    stream.addfile(
                        item,
                        io.BytesIO(b"DATA"),
                    )
                else:
                    item.linkname = "../../outside"
                    stream.addfile(item)

    @contextmanager
    def legacy_api(
        self,
    ):
        @contextmanager
        def open_archive(
            path: Path,
        ):
            with tarfile.open(path) as stream:
                class LegacyStream:
                    def __iter__(
                        self,
                    ):
                        return iter(stream)

                    # No filter argument: this is the pre-PEP 706 API contract.
                    def extractall(
                        self,
                        path,
                        members,
                        numeric_owner,
                    ) -> None:
                        options = (
                            {"filter": "fully_trusted"}
                            if hasattr(tarfile, "data_filter") else {}
                        )
                        stream.extractall(
                            path,
                            members=members,
                            numeric_owner=numeric_owner,
                            **options,
                        )

                yield LegacyStream()

        with patch.object(
            archive_module,
            "tarfile",
            SimpleNamespace(
                open=open_archive,
                TarError=tarfile.TarError,
            ),
        ):
            yield

    def extract(
        self,
    ) -> None:
        archive_module.extract_custody_archive(
            self.archive,
            self.destination,
        )

    def test_legacy_api_preserves_directory_contents_and_executable_files(
        self,
    ) -> None:
        self.create([
            ("results/nested", tarfile.DIRTYPE),
            ("results/nested/run", tarfile.REGTYPE),
        ])
        with self.legacy_api():
            self.extract()
        output = self.destination / "results/nested/run"
        self.assertEqual(
            output.read_bytes(),
            b"DATA",
        )
        if os.name != "nt":
            self.assertEqual(
                output.stat().st_mode & 0o7777,
                0o755,
            )
            self.assertEqual(
                output.stat().st_uid,
                self.root.stat().st_uid,
            )

    def test_legacy_api_rejects_escaping_and_nonportable_paths(
        self,
    ) -> None:
        for name in (
            "../outside", "/absolute", "C:/outside", "results/../outside",
            r"results\..\outside", "results/NUL.txt",
        ):
            with self.subTest(name=name):
                self.create([(name, tarfile.REGTYPE)])
                with self.legacy_api(), self.assertRaises(tarfile.TarError):
                    self.extract()
                self.assertEqual(
                    list(self.destination.iterdir()),
                    [],
                )
                self.assertFalse((self.root / "outside").exists())

    def test_legacy_api_does_not_keep_group_execute_without_owner_execute(
        self,
    ) -> None:
        self.create(
            [("results/data", tarfile.REGTYPE)],
            mode=0o055,
        )
        with self.legacy_api():
            self.extract()
        output = self.destination / "results/data"
        self.assertEqual(
            output.read_bytes(),
            b"DATA",
        )
        if os.name != "nt":
            self.assertEqual(
                output.stat().st_mode & 0o7777,
                0o644,
            )

    def test_legacy_api_rejects_links_and_special_files(
        self,
    ) -> None:
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            with self.subTest(kind=kind):
                self.create([("results/item", kind)])
                with self.legacy_api(), self.assertRaises(tarfile.TarError):
                    self.extract()
                self.assertEqual(
                    list(self.destination.iterdir()),
                    [],
                )

    def test_native_api_extracts_normal_data(
        self,
    ) -> None:
        self.create([("results/answer", tarfile.REGTYPE)])
        self.extract()
        self.assertEqual(
            (self.destination / "results/answer").read_bytes(),
            b"DATA",
        )

    def test_native_api_keeps_path_confinement(
        self,
    ) -> None:
        self.create([("../outside", tarfile.REGTYPE)])
        with self.assertRaises(tarfile.TarError):
            self.extract()
        self.assertFalse((self.root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
