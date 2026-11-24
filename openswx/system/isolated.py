from __future__ import annotations

import os

DEFAULT_TIMEOUT = 180.0
ISOLATED_TYPES = (".sldprt", ".x_t", ".x_b", ".xmt_txt", ".xmt_bin", ".igs", ".iges")


class IsolationError(RuntimeError):
    pass


def enabled() -> bool:
    return os.environ.get("OPENSWX_ISOLATE", "on").strip().lower() not in ("off", "0", "no", "false")


def read_geometry_here(path: str, config: int | None = None):
    """(shape, notes) of a SOLIDWORKS part, Parasolid or IGES file, in this process."""
    from pathlib import Path
    ext = Path(path).suffix.lower()
    if ext in (".igs", ".iges"):  # (OpenCASCADE's IGES reader can crash outright on a damaged file)
        from openswx.session import _read_iges
        return _read_iges(path)
    if ext == ".sldprt":
        from openswx.exchange.sldprt import read_sldprt
        return read_sldprt(path, config)
    from openswx.exchange.parasolid import read_xt_shape
    return read_xt_shape(path)


def _worker(path: str, config, conn, fn=None) -> None:
    try:
        from openswx.core.features import encode_shape
        shape, notes = (fn or read_geometry_here)(path, config)
        conn.send(("ok", encode_shape(shape), list(notes or [])))
    except BaseException as exc:  # noqa: BLE001 - whatever went wrong is the answer
        try:
            conn.send(("error", f"{type(exc).__name__}: {exc}", []))
        except Exception:  # noqa: BLE001
            pass
    finally:
        conn.close()


def read_geometry(path: str, config: int | None = None, timeout: float | None = None, _fn=None):
    """(shape, notes) read in a child process; IsolationError if it runs past `timeout` seconds or the child
    dies, an ordinary ValueError if the reader itself refuses the file."""
    import multiprocessing as mp
    import time
    from multiprocessing.connection import wait

    from openswx.core.features import decode_shape
    timeout = float(timeout if timeout is not None else os.environ.get("OPENSWX_ISOLATE_TIMEOUT", DEFAULT_TIMEOUT))
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker, args=(path, config, child, _fn), daemon=True)
    proc.start()
    result = None
    deadline = time.monotonic() + timeout
    try:
        # (the child end stays open until the child is done: see sw_rebuild.rebuild_isolated)
        while result is None:
            left = deadline - time.monotonic()
            ready = wait([parent, proc.sentinel], max(left, 0)) if left > 0 else []
            if not ready:
                raise IsolationError(f"reading {os.path.basename(path)} took longer than {timeout:g} s and was stopped")
            if parent in ready or parent.poll():
                result = parent.recv()
            elif not proc.is_alive():
                break
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        child.close()
        parent.close()
    if result is None:
        raise IsolationError(f"the geometry kernel crashed while reading {os.path.basename(path)}")
    status, data, notes = result
    if status != "ok":
        raise ValueError(data)
    return decode_shape(data), notes


# stand-ins for a reader that hangs / dies, for the tests (top level: the child imports them by name)
def _test_hang(path, config):
    import time
    time.sleep(120)


def _test_crash(path, config):
    os._exit(3)
