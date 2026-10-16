"""Failure policy shared by the production deployer and isolated tests."""


def activate(backend):
    backend.database_started = False
    maintenance = False
    switched = False
    database_started = False
    try:
        backend.preflight()
        maintenance = True
        backend.maintenance(True)
        backend.stop_workers()
        before = backend.snapshot()
        backend.backup()
        switched = True
        backend.switch()
        database_started = True
        backend.database_started = True
        backend.upgrade()
        backend.verify_snapshot(before)
        backend.start_workers()
        backend.probe()
        backend.record()
        backend.maintenance(False)
    except Exception:
        if database_started:
            # Even a zero-exit PHP failure may have partially changed the schema.
            # Never restore a database or reopen writers automatically.
            try:
                backend.maintenance(True)
            finally:
                backend.stop_workers()
        elif maintenance:
            try:
                if switched:
                    backend.restore_code()
                backend.start_workers()
                backend.maintenance(False)
            except Exception:
                try:
                    backend.maintenance(True)
                finally:
                    backend.stop_workers()
                raise
        raise
