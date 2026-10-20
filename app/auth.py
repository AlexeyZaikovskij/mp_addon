"""Toolkit's own sign-in: password hashing and a simple lock-out against password guessing."""
import base64
import hashlib
import hmac
import secrets
import time

# scrypt parameters: ~50 ms per check on a normal PC, 16 MB of memory
_N, _R, _P = 2 ** 14, 8, 1
MIN_PASSWORD_LENGTH = 8

MAX_FAILURES = 5
LOCK_SECONDS = 300
_failures: dict[str, tuple[int, float]] = {}  # login (lower-case) -> (failed attempts, locked until)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        check = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                               dklen=len(base64.b64decode(digest)))
        return hmac.compare_digest(check, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# A dummy hash so that a login that does not exist costs as much time as a wrong password.
_DUMMY = hash_password(secrets.token_hex(8))


def locked_for(login: str) -> int:
    """Seconds left until this login may try again (0 = not locked)."""
    fails, until = _failures.get(login.lower(), (0, 0.0))
    return max(0, int(until - time.monotonic())) if fails >= MAX_FAILURES else 0


def check(login: str, user: dict | None, password: str) -> bool:
    ok = verify_password(password, user["password"] if user else _DUMMY) and user is not None
    key = login.lower()
    if ok:
        _failures.pop(key, None)
    else:
        fails, _ = _failures.get(key, (0, 0.0))
        fails += 1
        _failures[key] = (fails, time.monotonic() + LOCK_SECONDS if fails >= MAX_FAILURES else 0.0)
    return ok
