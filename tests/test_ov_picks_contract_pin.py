"""The picks-file contract is shared with ashwinurao/ov-picks (private), which writes the picks files.

Both repos hold the same ov_picks/contract.py, CONTRACT.md and samples/, and both pin
their hash here. A change to any of them needs the same change in the other repo, in a
paired PR, with the new hash in both (CLAUDE.md, "The contract is shared").
"""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "ov_picks"

# sha256 over each shared file's path and LF content, in path order (see _contract_digest).
CONTRACT_SHA256 = "b3520b7f34d0aab63429ab2a1884f0039f3767b42f3ce129f3a9c45ef6d72baa"


def _shared_files():
    return [CONTRACT_DIR / "contract.py", CONTRACT_DIR / "CONTRACT.md",
            *sorted(p for p in (CONTRACT_DIR / "samples").rglob("*") if p.is_file())]


def _contract_digest():
    # LF content, so a Windows checkout with autocrlf gets the same hash.
    h = hashlib.sha256()
    for path in _shared_files():
        h.update(path.relative_to(CONTRACT_DIR).as_posix().encode() + b"\0")
        h.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def test_the_shared_contract_matches_the_pinned_hash():
    assert _contract_digest() == CONTRACT_SHA256, (
        "The shared picks-file contract changed. Make the same change in the other repo "
        "in a paired PR, then set CONTRACT_SHA256 to the new hash in both repos.")


def test_the_pin_covers_the_contract_its_doc_and_every_sample():
    names = {p.relative_to(CONTRACT_DIR).as_posix() for p in _shared_files()}
    assert {"contract.py", "CONTRACT.md"} <= names
    assert any(n.startswith("samples/frozen/") for n in names)
