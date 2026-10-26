"""Incremental import of immutable custody drops, with independent bulk transfers."""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import threading
from time import monotonic

from .._custody_receipts import _read_custody_receipts, _write_custody_receipts
from .._slurm_manager._ssh_auto_connect import remaining_timeout


SMALL_FILE_BYTES = 8 * 1024 * 1024
BULK_PRIORITY_BURST = 3

# Compatible with the Python 3.6 supplied by older HPC login nodes. Known,
# published tokens are immutable; avoid reopening every old checkpoint manifest.
_CATALOGUE = r'''import bisect, json, os, re, stat, sys
root = sys.argv[1]
request = json.load(sys.stdin)
known = set(request['known'])
limit = request['limit']
try:
    names = sorted(os.listdir(root))
except FileNotFoundError:
    names = []
records = {}
errors = []
incomplete = False
cursor = request['cursor']
start = bisect.bisect_right(names, cursor)
for token in names[start:] + names[:start]:
    if not re.fullmatch(r'relay-[0-9a-f]{32}', token):
        incomplete = True
        continue
    if token in known:
        continue
    if len(records) + len(errors) >= limit:
        incomplete = True
        break
    cursor = token
    try:
        directory = os.path.join(root, token)
        if not stat.S_ISDIR(os.lstat(directory).st_mode):
            raise ValueError('Invalid entry')
        metadata = os.path.join(directory, 'meta.json')
        info = os.lstat(metadata)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError('Invalid metadata')
        published_ns = info.st_mtime_ns
        with open(metadata, encoding='utf-8') as f:
            meta = json.loads(f.read(65537))
        stored = meta.get('storage_filename', meta.get('filename'))
        if not isinstance(stored, str) or stored in ('', '.', '..', 'meta.json') or any(c in stored for c in ('/', '\\', '\x00')):
            raise ValueError('Invalid filename')
        info = os.lstat(os.path.join(directory, stored))
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Invalid payload')
        records[token] = {'metadata': meta, 'size': info.st_size, 'published_ns': published_ns}
    except Exception:
        errors.append(token)
print(json.dumps({'records': records, 'errors': errors, 'incomplete': incomplete,
                  'retained_errors': sorted(set(names).intersection(request['failed'])), 'cursor': cursor}))
'''


@dataclass
class _Drop:
    token: str
    filename: str
    stored_name: str
    metadata: dict
    digest: str
    size: int
    published_ns: int
    retry_after: float = 0


@dataclass
class _JobTransfers:
    lock: threading.Lock = field(default_factory=threading.Lock)
    pending: dict[str, _Drop] = field(default_factory=dict)
    inflight: set[str] = field(default_factory=set)
    failed: dict[str, float] = field(default_factory=dict)
    cursor: str = ""
    logs_pending: bool = False
    logs_retry_after: float = 0
    logs_yield_to_payload: bool = False
    bulk_bypasses: int = 0
    ack_pending: bool = False


class CustodyRelay:
    def __init__(
        self,
        transport,
        custody,
        local_table,
        remote_table,
        validate_metadata,
        logger,
        transfer_timeout: float,
        transfer_logs = None,
    ) -> None:
        self.transport = transport
        self.custody = custody
        self.local_table = local_table
        self.remote_table = remote_table
        self.validate_metadata = validate_metadata
        self.logger = logger
        self.transfer_timeout = transfer_timeout
        self.transfer_logs = transfer_logs
        self._lock = threading.Lock()
        self._jobs: dict[str, _JobTransfers] = {}
        self._cancel = threading.Event()

    def _state(
        self,
        job_id: str,
    ) -> _JobTransfers:
        with self._lock:
            return self._jobs.setdefault(job_id, _JobTransfers())

    def poll(
        self,
        job_id: str,
        deadline: float,
    ) -> bool:
        state = self._state(job_id)
        with state.lock:
            receipts = _read_custody_receipts(self.local_table(job_id), durable=True)
            for token, digest in receipts['entries'].items():
                cached = state.pending.get(token)
                if cached is not None and cached.digest != digest:
                    raise ValueError('Custody import receipt conflicts with publication')
                state.pending.pop(token, None)
                state.failed.pop(token, None)
            self.custody.acknowledge_receipts(job_id, receipts['entries'])
            state.ack_pending = False
            deferred = {token for token, retry in state.failed.items() if retry > monotonic()}
            known = receipts['entries'].keys() | state.pending.keys() | deferred
            failed = list(state.failed)
            cursor = state.cursor
        result = self.transport.run(
            ['python3', '-c', _CATALOGUE, f'{self.remote_table(job_id)}/.magnus_custody_drop'],
            input = json.dumps({'known': sorted(known), 'limit': 64, 'failed': failed, 'cursor': cursor}),
            check = True,
            timeout = remaining_timeout(deadline, 15),
        )
        catalogue = json.loads(result.stdout)
        if catalogue['errors']:
            self.logger.warning('Job %s has unpublished or pending custody entries', job_id)
        with state.lock:
            state.cursor = catalogue['cursor']
            retained_errors = set(catalogue['retained_errors'])
            state.failed = {token: retry for token, retry in state.failed.items() if token in retained_errors}
            for token in catalogue['errors']:
                state.failed[token] = monotonic() + 60
            for token, record in catalogue['records'].items():
                try:
                    meta = record['metadata']
                    filename, stored_name = self.validate_metadata(meta, token)
                    size = record['size']
                    if type(size) is not int or size < 0 or (meta.get('is_directory') and size == 0):
                        raise ValueError('Invalid payload size')
                    if meta.get('size_bytes') is not None and meta['size_bytes'] != size:
                        raise ValueError('Payload size disagrees with publication')
                    digest = hashlib.sha256(json.dumps(meta, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    state.pending[token] = _Drop(
                        token = token,
                        filename = filename,
                        stored_name = stored_name,
                        metadata = {
                            name: meta[name]
                            for name in ("is_directory", "expire_minutes", "max_downloads")
                            if name in meta
                        },
                        digest = digest,
                        size = size,
                        published_ns = record['published_ns'],
                    )
                    state.failed.pop(token, None)
                    if len(state.pending) > 256:
                        # The remote publication remains the queue of record.
                        # Publication order survives descriptor eviction and
                        # restart, unlike the order of discovery in this cache.
                        oldest_bulk = min(
                            (item for item in state.pending.values() if item.size > SMALL_FILE_BYTES),
                            key = lambda item: (item.published_ns, item.token),
                            default = None,
                        )
                        evicted = max(
                            (item for item in state.pending.values() if item.token not in state.inflight and item is not oldest_bulk),
                            key = lambda item: item.size,
                        )
                        del state.pending[evicted.token]
                        catalogue['incomplete'] = True
                except (KeyError, TypeError, ValueError):
                    state.failed[token] = monotonic() + 60
                    self.logger.warning('Job %s has an invalid custody publication', job_id)
            if len(state.failed) > 256:
                state.failed = dict(sorted(state.failed.items(), key=lambda item: item[1])[-256:])
            small = sorted(
                (drop for drop in state.pending.values() if drop.size <= SMALL_FILE_BYTES),
                key = lambda drop: drop.size,
            )
        for drop in small:
            if self._cancel.is_set() or monotonic() >= deadline:
                break
            self._transfer(job_id, state, drop, deadline)
        with state.lock:
            return not (catalogue['incomplete'] or state.pending or state.failed or state.inflight or state.ack_pending)

    def _transfer(
        self,
        job_id: str,
        state: _JobTransfers,
        drop: _Drop,
        deadline: float,
    ) -> None:
        with state.lock:
            if drop.token in state.inflight or drop.retry_after > monotonic() or drop.token not in state.pending:
                return
            state.inflight.add(drop.token)
        try:
            previous = self.custody.import_receipt(job_id, drop.token)
            if previous is not None and previous != drop.digest:
                raise ValueError('Custody publication changed after import')
            if previous is None:
                def receive(
                    destination: Path,
                ) -> None:
                    self.transport.fetch_bounded(
                        f'{self.remote_table(job_id)}/.magnus_custody_drop/{drop.token}/{drop.stored_name}',
                        str(destination),
                        size = drop.size,
                        timeout = remaining_timeout(deadline),
                        cancel_event = self._cancel,
                    )

                self.custody.store_file(
                    filename = drop.filename,
                    file_obj = None,
                    expire_minutes = drop.metadata.get('expire_minutes'),
                    is_directory = drop.metadata.get('is_directory', False),
                    max_downloads = drop.metadata.get('max_downloads'),
                    explicit_token = drop.token,
                    job_id = job_id,
                    source_digest = drop.digest,
                    expected_size = drop.size,
                    receive = receive,
                    cancel_event = self._cancel,
                )
            with state.lock:
                receipts = _read_custody_receipts(self.local_table(job_id), durable=True)
                receipts['entries'][drop.token] = drop.digest
                _write_custody_receipts(self.local_table(job_id), receipts)
                del state.pending[drop.token]
                self.custody.acknowledge_import(job_id, drop.token, drop.digest)
        except Exception as error:
            with state.lock:
                drop.retry_after = monotonic() + 60
                if drop.token not in state.pending:
                    state.ack_pending = True
            self.logger.warning('Job %s custody transfer pending (%s)', job_id, type(error).__name__)
        finally:
            with state.lock:
                state.inflight.remove(drop.token)

    def bulk_job_ids(
        self,
    ) -> set[str]:
        with self._lock:
            states = list(self._jobs.items())
        result = set()
        for job_id, state in states:
            if not state.lock.acquire(blocking=False):
                continue
            try:
                if state.logs_pending and state.logs_retry_after <= monotonic() or any(
                    drop.size > SMALL_FILE_BYTES and drop.retry_after <= monotonic()
                    for drop in state.pending.values()
                ):
                    result.add(job_id)
            finally:
                state.lock.release()
        return result

    def transfer_bulk(
        self,
        job_id: str,
    ) -> None:
        state = self._state(job_id)
        with state.lock:
            if self._cancel.is_set():
                return
            now = monotonic()
            eligible = [
                drop for drop in state.pending.values()
                if drop.size > SMALL_FILE_BYTES and drop.retry_after <= now and drop.token not in state.inflight
            ]
            logs_due = state.logs_pending and state.logs_retry_after <= now
            transfer_logs = logs_due and (not eligible or not state.logs_yield_to_payload)
            drop = None
            if transfer_logs:
                state.logs_yield_to_payload = True
            elif eligible:
                # Absolute age priority would put an entire old cache ahead of
                # a new result again. Bound bypasses instead of draining it FIFO.
                oldest = min(
                    eligible,
                    key = lambda item: (item.published_ns, item.token),
                )
                drop = oldest if state.bulk_bypasses >= BULK_PRIORITY_BURST else min(
                    eligible,
                    key = lambda item: item.size,
                )
                state.bulk_bypasses = 0 if drop is oldest else state.bulk_bypasses + 1
                state.logs_yield_to_payload = False
        if transfer_logs:
            try:
                self.transfer_logs(
                    job_id,
                    timeout = self.transfer_timeout,
                    cancel_event = self._cancel,
                )
                self.mark_logs_complete(job_id)
                with state.lock:
                    state.logs_pending = False
            except Exception as error:
                with state.lock:
                    state.logs_retry_after = monotonic() + 60
                self.logger.warning('Job %s final log transfer pending (%s)', job_id, type(error).__name__)
            return
        if drop is not None and not self._cancel.is_set():
            self._transfer(job_id, state, drop, monotonic() + self.transfer_timeout)

    def cancel_transfers(
        self,
    ) -> None:
        self._cancel.set()

    def request_final_logs(
        self,
        job_id: str,
    ) -> bool:
        state = self._state(job_id)
        with state.lock:
            complete = _read_custody_receipts(self.local_table(job_id), durable=True)['logs_complete']
            state.logs_pending = not complete
            return complete

    def mark_logs_complete(
        self,
        job_id: str,
    ) -> None:
        with self._state(job_id).lock:
            receipts = _read_custody_receipts(self.local_table(job_id), durable=True)
            receipts['logs_complete'] = True
            _write_custody_receipts(self.local_table(job_id), receipts)

    def forget(
        self,
        job_id: str,
    ) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
