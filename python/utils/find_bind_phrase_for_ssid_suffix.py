#!/usr/bin/env python3
"""Find a bind phrase that produces a target TX Backpack Wi-Fi SSID hex suffix.

Usage quick start
=================

1) Show built-in help:
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py --help

2) Random brute-force for a target suffix (runs until found):
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py A1B2C3

3) Random brute-force with your own constraints:
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py A1B2C3 \
     --mode random \
     --alphabet abcdef0123456789 \
     --min-len 6 --max-len 10 \
     --prefix my- --postfix -tx \
     --seed 42 --progress-every 100000

4) Exhaustive search in a small space (good for deterministic checks):
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py 8C86DA \
     --mode exhaustive --alphabet abc --min-len 3 --max-len 3

5) Limit runtime by attempts (both modes):
   python3 python/utils/find_bind_phrase_for_ssid_suffix.py A1B2C3 --max-attempts 5000000

How to run from repository root
===============================
Run commands from the Backpack repo root:
  cd /workspace/Backpack
  python3 python/utils/find_bind_phrase_for_ssid_suffix.py <HEX6>

Result interpretation
=====================
- Script prints FOUND + bind phrase + uid bytes + final SSID if a match is found.
- If no match in the searched space (or attempt limit reached), it exits with code 1.

Notes
=====
- Target suffix must be exactly 6 hex characters (e.g. A1B2C3).
- Allowed symbols in target suffix are only hexadecimal: `0-9` and `A-F`.
  So characters like `G-Z`, `_`, `-`, or lowercase-only letters outside hex are impossible
  in the generated SSID suffix because firmware prints bytes as `%02X`.
- Search is for SSID suffix produced by uid[3:6], i.e.:
    ExpressLRS TX Backpack %02X%02X%02X
- This script matches the same UID derivation used in build flags.

Backpack TX firmware builds SSID in MAVLink mode as:
  ExpressLRS TX Backpack %02X%02X%02X
from firmwareOptions.uid[3], uid[4], uid[5].

The UID is derived from bind phrase in build_flags.py with:
  hashlib.md5(f'-DMY_BINDING_PHRASE="{phrase}"'.encode()).digest()[0:6]

This script brute-forces phrases until uid[3:6] matches the requested hex suffix.
"""

import argparse
import hashlib
import itertools
import random
import string
import time


DEFAULT_ALPHABET = string.ascii_lowercase + string.digits


def uid_from_phrase(phrase: str) -> bytes:
    define = f'-DMY_BINDING_PHRASE="{phrase}"'
    return hashlib.md5(define.encode()).digest()[0:6]


def suffix_from_phrase(phrase: str) -> str:
    return uid_from_phrase(phrase)[3:6].hex().upper()


def random_phrase(rng: random.Random, alphabet: str, min_len: int, max_len: int, prefix: str, postfix: str) -> str:
    n = rng.randint(min_len, max_len)
    body = ''.join(rng.choice(alphabet) for _ in range(n))
    return f"{prefix}{body}{postfix}"


def exhaustive_candidates(alphabet: str, min_len: int, max_len: int, prefix: str, postfix: str):
    for n in range(min_len, max_len + 1):
        for chars in itertools.product(alphabet, repeat=n):
            yield f"{prefix}{''.join(chars)}{postfix}"


def find_phrase(target: str, args):
    target = target.upper()
    attempts = 0
    t0 = time.time()

    if args.mode == 'exhaustive':
        generator = exhaustive_candidates(args.alphabet, args.min_len, args.max_len, args.prefix, args.postfix)
    else:
        rng = random.Random(args.seed)

    while args.max_attempts <= 0 or attempts < args.max_attempts:
        attempts += 1
        if args.mode == 'exhaustive':
            try:
                phrase = next(generator)
            except StopIteration:
                break
        else:
            phrase = random_phrase(rng, args.alphabet, args.min_len, args.max_len, args.prefix, args.postfix)

        uid = uid_from_phrase(phrase)
        suffix = uid[3:6].hex().upper()
        if suffix == target:
            elapsed = time.time() - t0
            return phrase, uid, attempts, elapsed

        if args.progress_every and attempts % args.progress_every == 0:
            rate = attempts / max(1e-9, (time.time() - t0))
            print(f"attempts={attempts} rate={rate:,.0f}/s last_suffix={suffix}")

    return None, None, attempts, time.time() - t0


def parse_args():
    p = argparse.ArgumentParser(
        description='Brute-force bind phrase for target TX Backpack SSID suffix (6 hex chars).'
    )
    p.add_argument('target_suffix', help='Target suffix from SSID (e.g. A1B2C3)')
    p.add_argument('--mode', choices=['random', 'exhaustive'], default='random',
                   help='Search strategy (default: random)')
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
    p.add_argument('--progress-every', type=int, default=50000,
                   help='Print progress every N attempts (0 disables)')

    args = p.parse_args()

    target = args.target_suffix.strip().upper()
    if len(target) != 6 or any(c not in string.hexdigits.upper() for c in target):
        p.error('target_suffix must be exactly 6 hex characters (0-9, A-F)')
    if args.min_len < 0 or args.max_len < args.min_len:
        p.error('Invalid --min-len/--max-len range')
    if not args.alphabet:
        p.error('--alphabet must not be empty')

    return args


def main():
    args = parse_args()

    print('Target SSID:', f'ExpressLRS TX Backpack {args.target_suffix.upper()}')
    phrase, uid, attempts, elapsed = find_phrase(args.target_suffix, args)

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
