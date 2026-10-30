"""Tiny download fixtures for real disconnect/resume cases; no network or matrices."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from publish_8b_additions_dataset import stream_verified_file


class Response:
    def __init__(self, body, status=200, start=0, fail=False):
        self.body, self.status_code, self.fail = body, status, fail
        self.headers = {'Content-Range': f'bytes {start}-7/8'}
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def raise_for_status(self): pass
    def iter_content(self, chunk_size):
        yield self.body
        if self.fail: raise requests.ConnectionError('fixture disconnect')


class Streaming(unittest.TestCase):
    def run_case(self, prefix, responses):
        payload = b'abcdefgh'
        meta = dict(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'fixture.npy'
            if prefix: target.write_bytes(prefix)
            with patch('requests.get', side_effect=responses) as get, patch('publish_8b_additions_dataset.time.sleep'):
                stream_verified_file(lambda: 'https://example.invalid/not-requested', target, meta)
            self.assertEqual(target.read_bytes(), payload)
            return [call.kwargs['headers'] for call in get.call_args_list]

    def test_disconnect_resumes_from_written_offset(self):
        headers = self.run_case(b'', [Response(b'abc', fail=True), Response(b'defgh', 206, start=3)])
        self.assertEqual(headers, [{}, {'Range': 'bytes=3-'}])

    def test_server_ignoring_range_does_not_append_whole_body(self):
        self.run_case(b'abcd', [Response(b'abcdefgh')])

    def test_corrupt_prefix_forces_clean_download(self):
        headers = self.run_case(b'xxxx', [Response(b'efgh', 206, start=4), Response(b'abcdefgh')])
        self.assertEqual(headers, [{'Range': 'bytes=4-'}, {}])


if __name__ == '__main__': unittest.main()
