"""Doc↔code contract: flag tokens, EX_* binding, discover prose banlist.

Docs-only corpus (never scripts/**): SKILL.md, references/*.md, README.md.
CHANGELOG and REVIEW_CONVERGE intentionally out of scope.
"""
from __future__ import annotations

import re
from pathlib import Path

import lennox_s40 as m

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "lennox-s40"
DOC_FILES = [
    SKILL / "SKILL.md",
    ROOT / "README.md",
    *sorted((SKILL / "references").glob("*.md")),
]
# Out of gate scope: CHANGELOG.md, REVIEW_CONVERGE.md, scripts/**

BAN_PHRASES = (
    "persist first good",
    "first good IP",
    "--verify-session",
)

FLAG_RE = re.compile(r"--[a-z][a-z0-9-]*")
EX_RE = re.compile(r"^EX_([A-Z_]+) = ([0-9]+)\s*$", re.M)
EXIT_LINE_RE = re.compile(r"^Exit codes:\s*(.+)$", re.M)


def _parser_flags() -> set[str]:
    p = m.build_parser()
    flags: set[str] = set()

    def walk(parser):
        for action in parser._actions:
            for opt in action.option_strings:
                if opt.startswith("--"):
                    flags.add(opt)
        for sp in getattr(parser, "_subparsers", None) and parser._subparsers._group_actions or []:
            for _name, sub in getattr(sp, "choices", {}).items():
                walk(sub)

    # also walk subparsers via _actions
    for action in p._actions:
        if getattr(action, "choices", None) and isinstance(action.choices, dict):
            for sub in action.choices.values():
                for a in sub._actions:
                    for opt in a.option_strings:
                        if opt.startswith("--"):
                            flags.add(opt)
    for action in p._actions:
        for opt in action.option_strings:
            if opt.startswith("--"):
                flags.add(opt)
    return flags


def _wrapper_install_flags() -> set[str]:
    text = (SKILL / "scripts" / "lennox-s40").read_text(encoding="utf-8")
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    flags = set(FLAG_RE.findall(text)) | set(FLAG_RE.findall(install))
    flags.add("--from")  # skill-craft foreign installer
    return flags


def _allowed_flags() -> set[str]:
    return _parser_flags() | _wrapper_install_flags()


def _doc_text() -> str:
    parts = []
    for p in DOC_FILES:
        assert p.is_file(), f"missing doc {p}"
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_corpus_floors():
    assert len(DOC_FILES) >= 4
    text = _doc_text()
    tokens = FLAG_RE.findall(text)
    assert len(tokens) >= 8, f"too few flag tokens extracted: {len(tokens)}"


def test_no_scripts_in_doc_corpus():
    for p in DOC_FILES:
        assert "scripts" not in p.parts or p.suffix == ".md"
        assert p.name != "lennox_s40.py"


def test_flag_tokens_in_allow_union():
    allowed = _allowed_flags()
    text = _doc_text()
    # skip code fences that are pure trees / non-cli noise: still check all --tokens
    unknown = sorted({t for t in FLAG_RE.findall(text) if t not in allowed})
    assert unknown == [], f"unknown flags in docs: {unknown}"


def test_ex_star_set_equals_skill_exit_line():
    py = (SKILL / "scripts" / "lennox_s40.py").read_text(encoding="utf-8")
    code_ex = {int(n) for _name, n in EX_RE.findall(py)}
    assert code_ex == {0, 1, 2, 3, 4, 5}
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    mline = EXIT_LINE_RE.search(skill)
    assert mline, "SKILL.md missing Exit codes: line"
    line = mline.group(1)
    docs = {int(x) for x in re.findall(r"`(\d)`", line)}
    if not docs:
        docs = {int(x) for x in re.findall(r"\b([0-5])\b", line)}
    assert docs == code_ex, f"doc exits {docs} != code {code_ex}"


def test_discover_phrase_banlist():
    text = _doc_text().lower()
    for phrase in BAN_PHRASES:
        assert phrase.lower() not in text, f"banned phrase present: {phrase!r}"


def test_gate_rejects_bare_verify_session_fragment(tmp_path, monkeypatch):
    """Plant bare fragment (historical bug form) — membership check must fail."""
    bad = "use `discover --verify-session` when sticky\n"
    # Simulate reading polluted ops
    polluted = _doc_text() + "\n" + bad
    allowed = _allowed_flags()
    unknown = {t for t in FLAG_RE.findall(polluted) if t not in allowed}
    assert "--verify-session" in unknown


def test_public_flags_documented_or_waived():
    """Reverse: public argparse flags appear in docs-only corpus."""
    waived = {
        "--no-lan-scan",  # legacy alias; --lan-scan documented
        "--help",  # argparse universal
    }
    text = _doc_text()
    missing = []
    for flag in sorted(_parser_flags()):
        if flag in waived:
            continue
        if flag not in text:
            missing.append(flag)
    assert missing == [], f"public flags missing from docs: {missing}"
