"""Read atomic snapshots without blocking their replacement on Windows.

The ordinary Windows text open does not share delete access. Keeping that
handle open can reject os.replace in a separate recorder, even though the
reader only needs the old complete snapshot. Share all three access modes;
the opened handle still refers to the same file after an atomic replacement.
Errors propagate to the consumer's existing unavailable/stale handling.
"""
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO
import uuid


def _windows_read_fd(path: str) -> int:
    """Transfer exactly one read-only, non-inheritable Windows handle to a fd."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                            wintypes.HANDLE)
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    # GENERIC_READ; FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    # OPEN_EXISTING; FILE_ATTRIBUTE_NORMAL. This never creates or writes a file.
    handle = create_file(path, 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.WinError(ctypes.get_last_error())
        error.filename = path
        raise error
    try:
        # On success the fd owns the handle, including closing it. On failure
        # ownership remains here. Do not close the raw handle after transfer.
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close_handle(handle)
        raise


@contextmanager
def open_shared_text(path: str | os.PathLike[str], *, encoding: str = 'utf-8',
                     errors: str | None = None, newline: str | None = None) -> Iterator[TextIO]:
    """Open read-only text in a context that owns and closes the descriptor."""
    if os.name != 'nt':
        with Path(path).open('r', encoding=encoding, errors=errors, newline=newline) as handle:
            yield handle
        return
    fd = _windows_read_fd(os.fspath(path))
    try:
        # Keep descriptor ownership here even if text-wrapper construction fails
        # or the caller explicitly closes the wrapper. Never close a reused fd.
        with open(fd, 'r', encoding=encoding, errors=errors, newline=newline,
                  closefd=False) as handle:
            yield handle
    finally:
        os.close(fd)


def read_shared_text(path: str | os.PathLike[str], *, encoding: str = 'utf-8',
                     errors: str | None = None) -> str:
    """Read one complete snapshot, closing the handle even if decoding fails."""
    with open_shared_text(path, encoding=encoding, errors=errors) as handle:
        return handle.read()


def _windows_replace(source: Path, destination: Path, backup: Path) -> None:
    """Use the Windows replacement API, preserving existing attributes/ACLs."""
    import ctypes
    from ctypes import wintypes

    replace = ctypes.WinDLL('kernel32', use_last_error=True).ReplaceFileW
    replace.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID)
    replace.restype = wintypes.BOOL
    # No IGNORE_ACL_ERRORS / IGNORE_MERGE_ERRORS. A unique same-directory backup
    # preserves the original even for documented partial failures 1176/1177.
    if not replace(str(destination), str(source), str(backup), 0, None, None):
        error = ctypes.WinError(ctypes.get_last_error())
        error.filename = str(source)
        error.filename2 = str(destination)
        error.snapshot_backup = str(backup)
        raise error


def replace_shared_snapshot(source: str | os.PathLike[str],
                            destination: str | os.PathLike[str]) -> None:
    """Replace a same-volume snapshot while delete-sharing readers stay open.

    Python's Windows os.replace uses MoveFileExW, which can still reject an open
    destination even when its readers share delete access. ReplaceFileW permits
    these readers. Failed replacement backups are retained as recovery copies;
    a successful replacement removes its redundant old snapshot if possible.
    """
    source, destination = Path(source), Path(destination)
    if os.name != 'nt':
        os.replace(source, destination)
        return
    backup = destination.with_name(destination.name + f'.replace-backup.{uuid.uuid4().hex}.bak')
    try:
        _windows_replace(source, destination, backup)
    except OSError as error:
        # First publication has no destination for ReplaceFileW to open. No
        # permission/ACL/sharing/partial error is retried with a weaker API.
        if getattr(error, 'winerror', None) == 2 and not destination.exists():
            os.replace(source, destination)
            return
        raise
    try:
        backup.unlink(missing_ok=True)
    except OSError:
        # Replacement has already succeeded. Retaining the old valid backup is
        # safer than declaring the current projection unavailable on cleanup.
        pass
