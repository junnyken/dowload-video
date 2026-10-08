"""
Print a NEW Ed25519 key pair for the Windows app's claim tokens (PLAN-32E P3, task #6172).

    python backend/scripts/gen_claim_signing_key.py [kid]

Output (two lines you copy to two different places):
  CLIENT_QUOTA_SIGNING_KEY=<base64 seed>   → backend env ONLY (Vibe Host set_env) +
                                              the owner's password manager. SECRET.
  VIDGRAB_CLAIM_PUBKEYS=<kid>:<base64 pub> → build env of the app (public).

Nothing is written to disk. Never commit the seed. Rotation:
docs/desktop/P3-KEY-ROTATION.md.
"""
import base64
import re
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    kid = sys.argv[1] if len(sys.argv) > 1 else "k1"
    if not re.match(r"^[A-Za-z0-9_-]{1,16}$", kid):
        print("kid must be 1-16 chars of A-Z a-z 0-9 _ -", file=sys.stderr)
        return 2
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print(f"CLIENT_QUOTA_SIGNING_KEY={base64.b64encode(seed).decode()}")
    print(f"CLIENT_QUOTA_SIGNING_KID={kid}")
    print(f"VIDGRAB_CLAIM_PUBKEYS={kid}:{base64.b64encode(pub).decode()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
