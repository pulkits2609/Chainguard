"""Offline checkpoint verifier; no network connection or implicit key discovery."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from checkpoints import verify_checkpoint
from ledger import parse_json, reject_duplicate_keys


MAX_EVIDENCE_BYTES = 64 * 1024 * 1024


def read_bounded(path, maximum):
    with Path(path).open("rb") as source:
        value = source.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("Evidence file exceeds its maximum supported size")
    return value


def read_history(path):
    # Validate each original block at its own canonical depth. An export envelope
    # must not reduce the nesting allowance of historical records.
    def reject_number(value):
        raise ValueError("Unsupported JSON number in history: " + value)
    return json.loads(read_bounded(path, MAX_EVIDENCE_BYTES), object_pairs_hook=reject_duplicate_keys,
                      parse_constant=reject_number, parse_float=reject_number)


def main():
    parser = argparse.ArgumentParser(description="Verify a ChainGuard checkpoint with an independently trusted Ed25519 public key")
    parser.add_argument("--checkpoint", required=True, help="Downloaded signed-checkpoint JSON")
    parser.add_argument("--public-key", required=True, help="Separately trusted exported JSON or PEM public key")
    parser.add_argument("--ledger-id", required=True, help="Expected ledger UUID, supplied independently")
    parser.add_argument("--history", help="Owner/admin GET-ledger JSON export; verifies full records against the checkpoint")
    args = parser.parse_args()
    try:
        checkpoint = parse_json(read_bounded(args.checkpoint, 65536))
        raw_key = read_bounded(args.public_key, 65536)
        key = raw_key if raw_key.startswith(b"-----BEGIN PUBLIC KEY-----") else parse_json(raw_key)
        history = read_history(args.history) if args.history else None
        result = verify_checkpoint(checkpoint, key, args.ledger_id, history)
        result["trust_notice"] = "Trust depends on independently obtaining this public key. Signature-only verification does not verify redacted shipment records or physical delivery."
    except (ValueError, OSError, UnicodeError, RecursionError) as error:
        result = {"valid": False, "errors": [{"message": str(error)}]}
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
