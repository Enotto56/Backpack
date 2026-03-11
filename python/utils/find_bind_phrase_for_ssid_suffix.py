#!/usr/bin/env python3
"""Find a bind phrase that produces a target TX Backpack Wi-Fi SSID hex suffix.

Usage quick start
=================

1) Show built-in help:
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py --help

2) Random brute-force for a target suffix (runs until found):
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py A1B2C3

3) Random brute-force in parallel (no extra console windows, single main output line):
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py A1B2C3 --workers 8

4) Random brute-force with your own constraints:
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py A1B2C3 \
     --mode random \
     --workers 8 \
     --alphabet abcdef0123456789 \
     --min-len 6 --max-len 10 \
     --prefix my- --postfix -tx \
     --seed 42 --progress-every 200000

5) Exhaustive search in a small space (deterministic checks):
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py 8C86DA \
     --mode exhaustive --alphabet abc --min-len 3 --max-len 3

6) Limit runtime by attempts (both modes):
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py A1B2C3 --max-attempts 5000000

Notes
=====
- Target suffix must be exactly 6 hex characters (e.g. A1B2C3).
- Allowed symbols in target suffix are only hexadecimal: `0-9` and `A-F`.
  So characters like `G-Z`, `_`, `-`, or lowercase-only letters outside hex are impossible
  in the generated SSID suffix because firmware prints bytes as `%02X`.
- Search is for SSID suffix produced by uid[3:6], i.e.:
    ExpressLRS TX Backpack %02X%02X%02X
- This script matches the same UID derivation used in build flags.
"""

import argparse
import hashlib
import itertools
import multiprocessing as mp
import queue
import random
import string
import time


DEFAULT_ALPHABET = string.ascii_lowercase + string.digits


def uid_from_phrase(phrase: str) -> bytes:
    define = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(define.encode()).digest()[0:6]


def random_phrase(rng: random.Random, alphabet: str, min_len: int, max_len: int, prefix: str, postfix: str) -> str:
    n = rng.randint(min_len, max_len)
    body = ''.join(rng.choice(alphabet) for _ in range(n))
    return f"{prefix}{body}{postfix}"


def exhaustive_candidates(alphabet: str, min_len: int, max_len: int, prefix: str, postfix: str):
    for n in range(min_len, max_len + 1):
        for chars in itertools.product(alphabet, repeat=n):
            yield f"{prefix}{''.join(chars)}{postfix}"


def worker_random(worker_id: int, target: str, args, seed: int, stop_event, out_queue, max_attempts: int):
    rng = random.Random(seed)
    attempts = 0
    t0 = time.time()
    last_suffix = "------"

    try:
        while not stop_event.is_set() and (max_attempts <= 0 or attempts < max_attempts):
            phrase = random_phrase(rng, args.alphabet, args.min_len, args.max_len, args.prefix, args.postfix)
            uid = uid_from_phrase(phrase)
            suffix = uid[3:6].hex().upper()
            last_suffix = suffix
            attempts += 1

            if suffix == target:
                out_queue.put(("found", worker_id, phrase, bytes(uid), attempts, time.time() - t0))
                stop_event.set()
                return

            if args.progress_every and attempts % args.progress_every == 0:
                out_queue.put(("progress", worker_id, attempts, last_suffix, time.time() - t0))

        out_queue.put(("done", worker_id, attempts, last_suffix, time.time() - t0))
    except Exception as exc:  # pragma: no cover
        out_queue.put(("error", worker_id, repr(exc)))


def worker_exhaustive(worker_id: int, target: str, args, stop_event, out_queue, max_attempts: int):
    attempts = 0
    t0 = time.time()
    last_suffix = "------"
    gen = exhaustive_candidates(args.alphabet, args.min_len, args.max_len, args.prefix, args.postfix)

    try:
        while not stop_event.is_set() and (max_attempts <= 0 or attempts < max_attempts):
            try:
                phrase = next(gen)
            except StopIteration:
                out_queue.put(("done", worker_id, attempts, last_suffix, time.time() - t0))
                return

            uid = uid_from_phrase(phrase)
            suffix = uid[3:6].hex().upper()
            last_suffix = suffix
            attempts += 1

            if suffix == target:
                out_queue.put(("found", worker_id, phrase, bytes(uid), attempts, time.time() - t0))
                stop_event.set()
                return

            if args.progress_every and attempts % args.progress_every == 0:
                out_queue.put(("progress", worker_id, attempts, last_suffix, time.time() - t0))

        out_queue.put(("done", worker_id, attempts, last_suffix, time.time() - t0))
    except Exception as exc:  # pragma: no cover
        out_queue.put(("error", worker_id, repr(exc)))


def print_status_line(total_attempts: int, t0: float, workers: int, done_workers: int, found: bool, last_suffix: str):
    elapsed = max(1e-9, time.time() - t0)
    rate = total_attempts / elapsed
    state = "FOUND" if found else "RUN"
    msg = (
        f"\r[{state}] workers={workers} done={done_workers}/{workers} "
        f"attempts={total_attempts:,} rate={rate:,.0f}/s last={last_suffix} elapsed={elapsed:,.1f}s"
    )
    print(msg, end="", flush=True)


def run_search(target: str, args):
    workers = args.workers
    if args.mode == 'exhaustive' and workers > 1:
        raise ValueError("--mode exhaustive currently supports only --workers 1")

    ctx = mp.get_context("spawn")
    out_queue = ctx.Queue()
    stop_event = ctx.Event()
    procs = []
    t0 = time.time()

    attempts_by_worker = [0] * workers
    last_by_worker = ["------"] * workers
    done_workers = 0
    found_result = None

    max_attempts_per_worker = 0
    if args.max_attempts > 0:
        max_attempts_per_worker = (args.max_attempts + workers - 1) // workers

    for i in range(workers):
        if args.mode == 'random':
            seed = (args.seed if args.seed is not None else int(time.time_ns() & 0xFFFFFFFF)) ^ ((i + 1) * 0x9E3779B1)
            p = ctx.Process(target=worker_random,
                            args=(i, target, args, seed, stop_event, out_queue, max_attempts_per_worker),
                            daemon=True)
        else:
            p = ctx.Process(target=worker_exhaustive,
                            args=(i, target, args, stop_event, out_queue, max_attempts_per_worker),
                            daemon=True)
        p.start()
        procs.append(p)

    last_render = 0.0
    try:
        while done_workers < workers and found_result is None:
            try:
                event = out_queue.get(timeout=0.2)
            except queue.Empty:
                now = time.time()
                if now - last_render > 0.5:
                    total_attempts = sum(attempts_by_worker)
                    last_suffix = next((s for s in reversed(last_by_worker) if s != "------"), "------")
                    print_status_line(total_attempts, t0, workers, done_workers, False, last_suffix)
                    last_render = now
                continue

            etype = event[0]
            if etype == "progress":
                _, wid, attempts, last_suffix, _ = event
                attempts_by_worker[wid] = attempts
                last_by_worker[wid] = last_suffix
            elif etype == "done":
                _, wid, attempts, last_suffix, _ = event
                attempts_by_worker[wid] = attempts
                last_by_worker[wid] = last_suffix
                done_workers += 1
            elif etype == "found":
                _, wid, phrase, uid, attempts, _ = event
                attempts_by_worker[wid] = attempts
                found_result = (phrase, uid)
                stop_event.set()
            elif etype == "error":
                _, wid, err = event
                stop_event.set()
                raise RuntimeError(f"Worker {wid} failed: {err}")

            total_attempts = sum(attempts_by_worker)
            last_suffix = next((s for s in reversed(last_by_worker) if s != "------"), "------")
            print_status_line(total_attempts, t0, workers, done_workers, found_result is not None, last_suffix)
            last_render = time.time()
    finally:
        stop_event.set()
        for p in procs:
            p.join(timeout=2.0)
        for p in procs:
            if p.is_alive():
                p.terminate()
                p.join(timeout=1.0)
        out_queue.close()
        out_queue.join_thread()

    print()  # newline after status line

    total_attempts = sum(attempts_by_worker)
    elapsed = time.time() - t0

    if found_result is None:
        return None, None, total_attempts, elapsed
    return found_result[0], found_result[1], total_attempts, elapsed


def parse_args():
    p = argparse.ArgumentParser(
        description='Brute-force bind phrase for target TX Backpack SSID suffix (6 hex chars).'
    )
    p.add_argument('target_suffix', help='Target suffix from SSID (e.g. A1B2C3)')
    p.add_argument('--mode', choices=['random', 'exhaustive'], default='random',
                   help='Search strategy (default: random)')
    p.add_argument('--workers', type=int, default=1,
                   help='Number of parallel worker processes (default: 1)')
    p.add_argument('--alphabet', default=DEFAULT_ALPHABET,
                   help='Characters to use for generated phrase body')
    p.add_argument('--min-len', type=int, default=6,
                   help='Minimum generated body length (default: 6)')
    p.add_argument('--max-len', type=int, default=12,
                   help='Maximum generated body length (default: 12)')
    p.add_argument('--prefix', default='', help='Prefix appended before generated body')
    p.add_argument('--postfix', default='', help='Postfix appended after generated body')
    p.add_argument('--seed', type=int, default=None, help='Random seed for reproducible random mode')
    p.add_argument('--max-attempts', type=int, default=0,
                   help='0 = unlimited; otherwise stop after this many attempts')
    p.add_argument('--progress-every', type=int, default=200000,
                   help='Print progress every N attempts per worker (0 disables)')

    args = p.parse_args()

    target = args.target_suffix.strip().upper()
    if len(target) != 6 or any(c not in string.hexdigits.upper() for c in target):
        p.error('target_suffix must be exactly 6 hex characters (0-9, A-F)')
    if args.min_len < 0 or args.max_len < args.min_len:
        p.error('Invalid --min-len/--max-len range')
    if not args.alphabet:
        p.error('--alphabet must not be empty')
    if args.workers < 1:
        p.error('--workers must be >= 1')

    return args


def main():
    args = parse_args()

    print('Target SSID:', f'ExpressLRS TX Backpack {args.target_suffix.upper()}')
    phrase, uid, attempts, elapsed = run_search(args.target_suffix.upper(), args)

    if phrase is None:
        print(f'Not found after {attempts} attempts in {elapsed:.2f}s')
        return 1

    uid_hex = uid.hex().upper()
    print('FOUND')
    print('  bind phrase :', phrase)
    print('  uid bytes   :', ','.join(str(b) for b in uid), f'({uid_hex})')
    print('  ssid suffix :', uid[3:6].hex().upper())
    print('  ssid        :', f'ExpressLRS TX Backpack {uid[3:6].hex().upper()}')
    print('  attempts    :', attempts)
    print('  elapsed     :', f'{elapsed:.2f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
