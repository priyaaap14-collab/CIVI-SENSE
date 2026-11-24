"""Signed tokens used to keep identity and human approval OUT of the model's hands.

Two kinds of token exist, both HMAC-SHA256 signed with a server-side secret that Claude never sees:

* identity token  - minted by Django from the *logged-in session user* (never from browser input or from the
                    model). It only carries the numeric user id; the role is always re-read from the database.
* approval token  - minted by Django only after the human clicked "Confirm" in the UI. It is bound to one user,
                    one tool and the exact arguments (SHA-256 digest), and it expires quickly.

The module is pure Python (no Django import) so that it can be unit-tested anywhere.
"""
import base64
import hashlib
import hmac
import json
import time

IDENTITY_SALT = "civisense.mcp.identity.v1"
APPROVAL_SALT = "civisense.mcp.approval.v1"
IDENTITY_TTL = 60 * 60          # one hour
APPROVAL_TTL = 10 * 60          # ten minutes


class TokenError(Exception):
    """Token is missing, malformed, forged, expired or bound to something else."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key(secret: str, salt: str) -> bytes:
    if not secret:
        raise TokenError("No signing secret configured.")
    return hmac.new(secret.encode("utf-8"), salt.encode("utf-8"), hashlib.sha256).digest()


def sign(payload: dict, secret: str, salt: str, ttl: int, now: float | None = None) -> str:
    body = dict(payload)
    body["exp"] = int((now if now is not None else time.time()) + ttl)
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(_key(secret, salt), raw, hashlib.sha256).digest()
    return f"{_b64e(raw)}.{_b64e(sig)}"


def verify(token: str, secret: str, salt: str, now: float | None = None) -> dict:
    try:
        raw_b64, sig_b64 = (token or "").split(".")
        raw, sig = _b64d(raw_b64), _b64d(sig_b64)
    except Exception as exc:  # noqa: BLE001 - any malformed input is simply "invalid"
        raise TokenError("Malformed token.") from exc
    expected = hmac.new(_key(secret, salt), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise TokenError("Token signature is invalid.")
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise TokenError("Malformed token body.") from exc
    if not isinstance(body, dict) or body.get("exp", 0) < (now if now is not None else time.time()):
        raise TokenError("Token has expired.")
    return body


# ---- identity -------------------------------------------------------------------------------------
def make_identity_token(user_id: int, secret: str, ttl: int = IDENTITY_TTL, now: float | None = None) -> str:
    return sign({"uid": int(user_id)}, secret, IDENTITY_SALT, ttl, now)


def read_identity_token(token: str, secret: str, now: float | None = None) -> int:
    body = verify(token, secret, IDENTITY_SALT, now)
    uid = body.get("uid")
    if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
        raise TokenError("Token carries no valid user id.")
    return uid


# ---- human approval -------------------------------------------------------------------------------
def args_digest(tool: str, args: dict) -> str:
    """Stable digest of a tool call (the approval token itself is never part of the digest)."""
    clean = {k: v for k, v in args.items() if k != "approval_token"}
    raw = json.dumps({"tool": tool, "args": clean}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_approval_token(user_id: int, tool: str, digest: str, secret: str, ttl: int = APPROVAL_TTL,
                        now: float | None = None) -> str:
    return sign({"uid": int(user_id), "tool": tool, "digest": digest}, secret, APPROVAL_SALT, ttl, now)


def check_approval(token: str | None, user_id: int, tool: str, args: dict, secret: str, now: float | None = None) -> bool:
    """True only if `token` was issued for exactly this user, tool and arguments and has not expired."""
    if not token:
        return False
    try:
        body = verify(token, secret, APPROVAL_SALT, now)
    except TokenError:
        return False
    return (body.get("uid") == int(user_id) and body.get("tool") == tool
            and hmac.compare_digest(str(body.get("digest", "")), args_digest(tool, args)))
