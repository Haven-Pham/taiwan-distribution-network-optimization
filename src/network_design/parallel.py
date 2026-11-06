"""Helpers that let long experiments survive a sleeping laptop or a hung worker."""
import os
import pickle
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from pathlib import Path


def run_jobs(function, jobs, workers=8, seconds_per_job=30, attempts=3, initializer=None, initargs=()):
    """Run jobs in worker processes and return results in job order.

    If no job finishes for a long time, the workers are stopped and the unfinished jobs start again. A plain pool would
    wait for ever: when a computer sleeps, a message between processes can be lost and every process then waits on another.
    """
    jobs = list(jobs)
    results = [None] * len(jobs)
    waiting = list(range(len(jobs)))
    quiet_limit = seconds_per_job * 4 + 120
    for attempt in range(attempts):
        pool = ProcessPoolExecutor(max_workers=workers, initializer=initializer, initargs=initargs)
        futures = {pool.submit(function, jobs[i]): i for i in waiting}
        try:
            remaining = set(futures)
            while remaining:
                # The limit applies to the gap between finished jobs, so a large batch is not treated as a hang.
                done = next(as_completed(remaining, timeout=quiet_limit))
                results[futures[done]] = done.result()
                remaining.discard(done)
            pool.shutdown()
            return results
        except FuturesTimeout:
            waiting = [i for i in waiting if results[i] is None]
            print(f"No job finished for {quiet_limit:.0f}s; restarting {len(waiting)} jobs (attempt {attempt + 1})", flush=True)
            for process in list((pool._processes or {}).values()):
                process.terminate()
            pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError(f"{len(waiting)} jobs did not finish after {attempts} attempts")


def keep_awake():
    """Ask Windows not to sleep while this process runs. It ends with the process and changes no system setting."""
    if os.name == "nt":
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


def checkpointed(folder, name, compute):
    """Return a saved result if there is one, otherwise compute and save it, so a stopped run can resume."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    value = compute()
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(pickle.dumps(value))
    temporary.replace(path)
    return value
