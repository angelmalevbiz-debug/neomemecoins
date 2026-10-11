"""Bounded append-only PAPER recording parts with a continuous byte cursor.

No pruning, reset, inferred observations, or trading calls. A legacy file is
retained verbatim. One recorder owns appends; readers only use committed parts.
"""
import json
import os
from pathlib import Path

from engine_runtime import atomic_json

VERSION = 'PAPER_OBSERVATION_PARTS_V1'
PART_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def manifest_path(source):
    source = Path(source)
    return source.with_name(source.name + '.parts.json')


def part_name(source, index):
    source = Path(source)
    return f'{source.stem}.part-{index:06d}{source.suffix}'


def _file(source, name):
    path = source.parent / name
    if path.is_symlink() or path.resolve().parent != source.parent.resolve():
        raise ValueError('Unsafe observation part; recording refused')
    return path


def _exists(path):
    # Path.exists() can suppress other OS errors on newer Python versions.
    # An unreadable manifest is not permission to create a replacement.
    try:
        path.stat()
        return True
    except FileNotFoundError:
        return False


def layout(source):
    """Validated immutable prefixes plus one growable tail, in byte order."""
    source = Path(source)
    manifest = manifest_path(source)
    if not _exists(manifest):
        _file(source, source.name)
        return [(source, 0, source.stat().st_size)] if source.exists() else []
    if manifest.is_symlink() or manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError('Unsafe/oversized observation manifest')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    parts = data.get('parts') if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get('version') != VERSION
            or data.get('source') != source.name or not isinstance(parts, list)
            or not 2 <= len(parts) <= 10000):
        raise ValueError('Invalid observation manifest')
    rows, start = [], 0
    for index, item in enumerate(parts):
        expected = source.name if index == 0 else part_name(source, index)
        if (not isinstance(item, dict) or set(item) != {'name', 'bytes'}
                or item.get('name') != expected):
            raise ValueError('Invalid observation part identity/order')
        path = _file(source, expected)
        size = path.stat().st_size
        sealed = item.get('bytes')
        if index == len(parts)-1:
            if sealed is not None:
                raise ValueError('Observation tail must be unsealed')
        elif type(sealed) is not int or sealed < 0 or size != sealed:
            raise ValueError('Sealed observation part changed/missing')
        rows.append((path, start, size))
        start += size
    return rows


def total_size(source):
    rows = layout(source)
    return sum(size for _, _, size in rows)


def files(source):
    """Exact files to archive together (writers must be stopped)."""
    source = Path(source)
    result = [path.name for path, _, _ in layout(source)]
    if manifest_path(source).exists():
        result.append(manifest_path(source).name)
    return result


def _ends_with_newline(path, size):
    if not size:
        return True
    with path.open('rb') as handle:
        handle.seek(size-1)
        return handle.read(1) == b'\n'


def _rotate(source, rows):
    tail, _, size = rows[-1]
    if not _ends_with_newline(tail, size):
        raise ValueError('Incomplete observation tail; never skip it by rotating')
    index = len(rows)
    next_path = _file(source, part_name(source, index))
    # A crash before manifest commit may leave an EMPTY next file. Nonempty
    # uncommitted data is never overwritten, adopted, or silently skipped.
    if next_path.exists():
        if next_path.stat().st_size:
            raise ValueError('Uncommitted nonempty observation part; recovery required')
    else:
        with next_path.open('xb') as handle:
            handle.flush()
            os.fsync(handle.fileno())
    data = dict(version=VERSION, source=source.name,
                parts=[dict(name=p.name, bytes=n) for p, _, n in rows]
                      + [dict(name=next_path.name, bytes=None)])
    # Commit identity BEFORE any records enter the next part. Readers can see
    # an empty tail and retry; they cannot ingest an uncommitted part.
    atomic_json(manifest_path(source), data)
    return next_path


def append(source, payload, *, part_bytes=PART_BYTES):
    source = Path(source)
    if not isinstance(payload, bytes) or not payload or not payload.endswith(b'\n'):
        raise ValueError('Complete encoded observation batch required')
    if type(part_bytes) is not int or part_bytes <= 0 or len(payload) > part_bytes:
        raise ValueError('Observation batch exceeds part bound')
    rows = layout(source)
    if not rows:
        source.parent.mkdir(parents=True, exist_ok=True)
        with source.open('xb') as handle:
            pass  # The following append durably syncs this same new file.
        rows = [(source, 0, 0)]
    tail, _, size = rows[-1]
    if not _ends_with_newline(tail, size):
        raise ValueError('Incomplete observation tail; append refused without deleting data')
    if size and size + len(payload) > part_bytes:
        tail = _rotate(source, rows)
    with tail.open('ab') as handle:
        written = handle.write(payload)
        if written != len(payload):
            raise OSError('Short observation append; evidence gap remains explicit')
        handle.flush()
        os.fsync(handle.fileno())


class Reader:
    """Read-only logical file; offsets stay compatible with legacy checkpoints."""
    def __init__(self, source):
        self.rows = layout(source)
        if not self.rows:
            raise FileNotFoundError(f'Observation source missing: {source}')
        self.size = sum(size for _, _, size in self.rows)
        self.offset = 0
        self.handle = None
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.handle is not None:
            self.handle.close()
        self.handle = None
        self.current = None

    def seek(self, offset, whence=0):
        if type(offset) is not int or whence not in (0, 1, 2):
            raise ValueError('Invalid observation cursor')
        target = offset + (self.offset if whence == 1 else self.size if whence == 2 else 0)
        if target < 0:
            raise ValueError('Negative observation cursor')
        self.offset = target
        return target

    def tell(self):
        return self.offset

    def _at(self):
        for path, start, size in self.rows:
            if start <= self.offset < start+size:
                if self.current != path:
                    self.close()
                    self.handle = path.open('rb')
                    self.current = path
                self.handle.seek(self.offset-start)
                return start+size-self.offset
        return 0

    def read(self, size=-1):
        if size < 0:
            size = max(0, self.size-self.offset)
        chunks = []
        while size:
            remaining = self._at()
            if not remaining:
                break
            chunk = self.handle.read(min(size, remaining))
            if not chunk:
                raise OSError('Observation part shortened during read')
            self.offset += len(chunk)
            size -= len(chunk)
            chunks.append(chunk)
        return b''.join(chunks)

    def readline(self):
        remaining = self._at()
        if not remaining:
            return b''
        line = self.handle.readline(remaining)
        if not line:
            raise OSError('Observation part shortened during read')
        self.offset += len(line)
        # Every sealed prefix must end at a complete record boundary. An
        # active incomplete final line is returned for the existing worker to
        # retry from its last committed cursor, never joined to another part.
        if not line.endswith(b'\n') and self.offset < self.size:
            raise ValueError('Incomplete sealed observation record')
        return line

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line
