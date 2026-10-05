"""Advisory locks on POSIX and Windows; locks remain process-wide, never no-op."""
import os

if os.name != 'nt':
    from fcntl import flock, LOCK_EX, LOCK_SH, LOCK_NB, LOCK_UN
else:
    import msvcrt
    import time
    LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8

    def flock(handle, operation):
        fd = handle if isinstance(handle, int) else handle.fileno()
        # Lock a fixed byte; unlike text-handle seeking this works for empty files.
        position = os.lseek(fd, 0, os.SEEK_CUR)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b'\0')
        try:
            if operation & LOCK_UN:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                return
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    return
                except OSError as exc:
                    if operation & LOCK_NB:
                        raise BlockingIOError(str(exc)) from exc
                    time.sleep(.02)
        finally:
            os.lseek(fd, position, os.SEEK_SET)
