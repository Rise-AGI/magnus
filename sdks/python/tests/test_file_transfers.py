"""Offline SDK regressions; import source without reading the user's SDK config."""
import gzip
import errno
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

import httpx


PACKAGE = "_magnus_transfer_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1] / "src" / "magnus")]
sys.modules[PACKAGE] = package
client_module = importlib.import_module(f"{PACKAGE}.client")
download = importlib.import_module(f"{PACKAGE}.http_download")
errors = importlib.import_module(f"{PACKAGE}._http_errors")
exceptions = importlib.import_module(f"{PACKAGE}.exceptions")


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks, failure=None):
        self.chunks = chunks
        self.failure = failure
        self.read_count = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk
        if self.failure:
            raise self.failure

    def close(self):
        self.closed = True


class ErrorTests(unittest.TestCase):
    def test_json_shapes_and_empty_bodies(self):
        for body in ["detail text", ["first", "second"], {"message": "nested"}, None, 14]:
            response = httpx.Response(422, json={"detail": body})
            self.assertIn("HTTP 422", errors.http_error_detail(response))
        for body in [[], None, "text", 1]:
            response = httpx.Response(403, json=body)
            with self.assertRaises(exceptions.ForbiddenError):
                self._client()._handle_error(response)

    def _client(self):
        return client_module.MagnusClient.__new__(client_module.MagnusClient)

    def test_error_details_and_request_id_are_retained(self):
        response = httpx.Response(503, json={"detail": "storage temporarily full"}, headers={"x-request-id": "test-123"})
        with self.assertRaises(exceptions.APIError) as context:
            self._client()._handle_error(response)
        self.assertIn("storage temporarily full", str(context.exception))
        self.assertIn("test-123", str(context.exception))
        self.assertEqual(context.exception.status_code, 503)

    def test_validation_payload_credentials_and_html_are_not_echoed(self):
        request = httpx.Request("POST", "http://invalid", headers={"Authorization": "Bearer testcredential"})
        response = httpx.Response(422, request=request, json={"detail": [{"msg": "Expected integer", "input": "PRIVATE REQUEST", "ctx": {"token": "secret"}}]})
        detail = errors.http_error_detail(response)
        self.assertIn("Expected integer", detail)
        self.assertNotIn("PRIVATE", detail)
        response = httpx.Response(502, request=request, text="token=testcredential; Cookie=session=secret")
        self.assertNotIn("testcredential", errors.http_error_detail(response))
        response = httpx.Response(502, text="<!doctype html><html>PRIVATE PAGE</html>", headers={"cf-ray": "trace-abc"})
        self.assertEqual(errors.http_error_detail(response), "HTTP 502 [cf-ray: trace-abc]")

    def test_streamed_error_reads_bounded_wire_prefix(self):
        stream = Chunks([b"x" * 1024] * 100)
        response = httpx.Response(500, stream=stream)
        self.assertLessEqual(len(errors.http_error_detail(response)), 1040)
        self.assertEqual(stream.read_count, 8)
        response.close()

    def test_compressed_error_is_not_decompressed(self):
        stream = Chunks([gzip.compress(b"private" * 10000)])
        response = httpx.Response(503, stream=stream, headers={"content-encoding": "gzip", "x-request-id": "trace"})
        self.assertEqual(errors.http_error_detail(response), "HTTP 503 [x-request-id: trace]")
        self.assertEqual(stream.read_count, 0)
        response.close()

    def test_upload_retries_and_auth_exception_are_preserved(self):
        hits = []
        streams = []
        def handler(request):
            hits.append(request.read())
            stream = Chunks([b'{"detail":"Storage full"}'])
            streams.append(stream)
            return httpx.Response(503, stream=stream, headers={"x-request-id": "storage-123"})
        client = self._client()
        with httpx.Client(base_url="http://invalid", transport=httpx.MockTransport(handler)) as http:
            client._client = http
            with patch.object(client_module.time, "sleep"), self.assertRaises(exceptions._ServerError) as context:
                client._post_file_with_retry("data.bin", io.BytesIO(b"DATA"), {}, 1)
        self.assertEqual(len(hits), 3)
        self.assertTrue(all(b"DATA" in body for body in hits))
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertIn("Storage full", str(context.exception))
        self.assertIn("storage-123", str(context.exception))
        with httpx.Client(base_url="http://invalid", transport=httpx.MockTransport(lambda request: httpx.Response(401, json=["Login required"]))) as http:
            client._client = http
            with self.assertRaises(exceptions.AuthenticationError):
                client._post_file_with_retry("data.bin", io.BytesIO(b"DATA"), {}, 1)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "result.bin"
        self.target.write_bytes(b"OLD")

    def fetch(self, chunks, headers=None, failure=None, overwrite=True):
        stream = Chunks(chunks, failure)
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, stream=stream))) as client:
            with patch.object(download.httpx, "stream", client.stream):
                result = download._download_once("http://invalid/file", str(self.target), 1, overwrite)
        self.assertTrue(stream.closed)
        return result

    def test_interrupted_download_keeps_existing_target(self):
        with self.assertRaises(httpx.ReadError):
            self.fetch([b"PART"], {"content-length": "9"}, httpx.ReadError("connection lost"))
        self.assertEqual(self.target.read_bytes(), b"OLD")
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_truncated_content_length_keeps_existing_target(self):
        with self.assertRaises(httpx.RemoteProtocolError):
            self.fetch([b"SHORT"], {"content-length": "20"})
        self.assertEqual(self.target.read_bytes(), b"OLD")

    def test_compressed_content_length_counts_wire_bytes(self):
        data = b"DECOMPRESSED DATA" * 300
        encoded = gzip.compress(data)
        self.fetch([encoded], {"content-encoding": "gzip", "content-length": str(len(encoded))})
        self.assertEqual(self.target.read_bytes(), data)

    def test_no_overwrite_keeps_existing_target(self):
        with self.assertRaises(FileExistsError):
            self.fetch([b"NEW"], overwrite=False)
        self.assertEqual(self.target.read_bytes(), b"OLD")

    def test_no_overwrite_file_publishes_on_native_platform(self):
        self.target.unlink()
        self.fetch([b"NEW"], overwrite=False)
        self.assertEqual(self.target.read_bytes(), b"NEW")

    def test_posix_unsupported_links_fall_back_to_exclusive_copy(self):
        self.target.unlink()
        with patch.object(download, "_WINDOWS", False), patch.object(download.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "unsupported")):
            self.fetch([b"NEW"], overwrite=False)
        self.assertEqual(self.target.read_bytes(), b"NEW")

    def test_link_failure_other_than_unsupported_is_not_hidden(self):
        self.target.unlink()
        with patch.object(download, "_WINDOWS", False), patch.object(download.os, "link", side_effect=OSError(errno.EIO, "I/O error")), self.assertRaises(OSError):
            self.fetch([b"NEW"], overwrite=False)
        self.assertFalse(self.target.exists())

    def test_exclusive_copy_rejects_concurrently_created_file(self):
        self.target.unlink()
        def unsupported_link(source, destination):
            Path(destination).write_bytes(b"OTHER")
            raise OSError(errno.EOPNOTSUPP, "unsupported")
        with patch.object(download, "_WINDOWS", False), patch.object(download.os, "link", unsupported_link), self.assertRaises(FileExistsError):
            self.fetch([b"NEW"], overwrite=False)
        self.assertEqual(self.target.read_bytes(), b"OTHER")

    def test_failed_exclusive_copy_removes_only_created_target(self):
        self.target.unlink()
        def failed_copy(source, output, **kwargs):
            output.write(b"PART")
            raise OSError("copy failed")
        with patch.object(download, "_WINDOWS", False), patch.object(download.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "unsupported")), patch.object(download.shutil, "copyfileobj", failed_copy), self.assertRaises(OSError):
            self.fetch([b"NEW"], overwrite=False)
        self.assertFalse(self.target.exists())

    def test_cleanup_does_not_remove_a_replacement_inode(self):
        source = self.root / "created"
        source.write_bytes(b"OURS")
        download._remove_created_target(self.target, source.stat())
        self.assertEqual(self.target.read_bytes(), b"OLD")

    def test_posix_directory_reservation_rejects_concurrent_directory(self):
        source = self.root / "source"
        source.mkdir()
        self.target.unlink()
        self.target.mkdir()
        with patch.object(download, "_WINDOWS", False), self.assertRaises(FileExistsError):
            download._publish_without_overwrite(source, self.target)
        self.assertTrue(self.target.is_dir())

    def test_posix_directory_copy_and_failure_cleanup(self):
        source = self.root / "source"
        source.mkdir()
        (source / "data").write_bytes(b"NEW")
        self.target.unlink()
        with patch.object(download, "_WINDOWS", False):
            download._publish_without_overwrite(source, self.target)
        self.assertEqual((self.target / "data").read_bytes(), b"NEW")
        failure_target = self.root / "failed"
        with patch.object(download, "_WINDOWS", False), patch.object(download.shutil, "copytree", side_effect=OSError("copy failed")), self.assertRaises(OSError):
            download._publish_without_overwrite(source, failure_target)
        self.assertFalse(failure_target.exists())

    def test_invalid_archive_keeps_existing_target(self):
        with self.assertRaises(tarfile.TarError):
            self.fetch([b"not an archive"], {"x-magnus-directory": "true"})
        self.assertEqual(self.target.read_bytes(), b"OLD")

    def test_content_disposition_is_portably_sanitized(self):
        cases = {
            'attachment; filename="../../outside"': "outside",
            'attachment; filename="C:/outside"': "outside",
            "attachment; filename*=utf-8''caf%C3%A9.txt": "café.txt",
            'attachment; filename="../../.."': None,
            'attachment; filename="NUL.txt"': None,
            'attachment; filename="x:stream"': None,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(download._parse_filename(httpx.Headers({"content-disposition": value})), expected)

    def test_directory_publication_failure_restores_previous(self):
        self.target.unlink()
        self.target.mkdir()
        (self.target / "original").write_bytes(b"OLD")
        temporary = self.root / "temporary"
        temporary.mkdir()
        source = temporary / "new"
        source.mkdir()
        original_replace = Path.replace
        def fail_new(path, destination):
            if path == source:
                raise PermissionError("injected rename failure")
            return original_replace(path, destination)
        with patch.object(Path, "replace", fail_new), self.assertRaises(PermissionError):
            download._publish_download(source, self.target, True, temporary)
        self.assertEqual((self.target / "original").read_bytes(), b"OLD")

    def test_rollback_failure_preserves_recoverable_old_data(self):
        self.target.unlink()
        self.target.mkdir()
        (self.target / "original").write_bytes(b"OLD")
        original_replace = Path.replace
        def fail_publication_and_restore(path, destination):
            if path != self.target:
                raise PermissionError("injected publication/rollback failure")
            return original_replace(path, destination)
        with self.assertRaises(PermissionError):
            with download._temporary_download(self.root) as temporary:
                source = temporary / "new"
                source.mkdir()
                with patch.object(Path, "replace", fail_publication_and_restore):
                    download._publish_download(source, self.target, True, temporary)
        self.assertEqual((temporary / "previous" / "original").read_bytes(), b"OLD")

    def test_directory_replaces_existing_directory_after_extraction(self):
        self.target.unlink()
        self.target.mkdir()
        (self.target / "original").write_bytes(b"OLD")
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            info = tarfile.TarInfo("results/new")
            info.size = 3
            tar.addfile(info, io.BytesIO(b"NEW"))
        self.fetch([archive.getvalue()], {"x-magnus-directory": "true"})
        self.assertEqual((self.target / "new").read_bytes(), b"NEW")
        self.assertFalse((self.target / "original").exists())
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_absolute_filename_does_not_escape_staging(self):
        outside = self.root / "outside"
        self.fetch([b"NEW"], {"content-disposition": f'attachment; filename="{outside.as_posix()}"'})
        self.assertFalse(outside.exists())
        self.assertEqual(self.target.read_bytes(), b"NEW")

    def test_staged_copy_failure_keeps_existing_target(self):
        entry = self.root / "dropin" / "token"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(json.dumps({"filename": "source.bin"}))
        (entry / "source.bin").write_bytes(b"NEW")
        def broken_copy(source, target):
            Path(target).write_bytes(b"PART")
            raise OSError("copy failed")
        with patch.dict(os.environ, {"MAGNUS_CUSTODY_DROPIN_DIR": str(entry.parent)}), patch.object(download.shutil, "copy2", broken_copy), self.assertRaises(OSError):
            download._read_staged_dropin("token", str(self.target), True)
        self.assertEqual(self.target.read_bytes(), b"OLD")


if __name__ == "__main__":
    unittest.main()
