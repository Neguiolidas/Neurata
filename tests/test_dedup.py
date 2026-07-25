"""tests/test_dedup.py — shingles, hashing, Jaccard (dedup near-dup)."""
from neurata.dedup import MIN_WORDS, SHINGLE_N, jaccard, shingle_hashes


def test_shingle_hashes_deterministic():
    body = "a b c d e f g"
    assert shingle_hashes(body) == shingle_hashes(body)


def test_shingle_hashes_short_body_returns_empty():
    body = "a b c"  # 3 < MIN_WORDS (5)
    assert shingle_hashes(body) == []


def test_shingle_hashes_exactly_min_words_nonempty():
    body = " ".join(["word"] * MIN_WORDS)
    assert shingle_hashes(body) != []


def test_shingle_hashes_markdown_vs_plaintext_equivalent():
    md = "# Title\n\nThis is **bold** text with a [link](http://x.com)."
    plain = "Title This is bold text with a link http x com"
    assert shingle_hashes(md) == shingle_hashes(plain)


def test_shingle_hashes_ordered_list_stable():
    body = "the quick brown fox jumps over the lazy dog"
    result = shingle_hashes(body)
    assert result == sorted(result)


def test_shingle_hashes_are_hex16_sha256_prefix():
    body = "word " * 10
    hashes = shingle_hashes(body)
    assert all(len(h) == 16 for h in hashes)
    assert all(all(c in "0123456789abcdef" for c in h) for h in hashes)


def test_shingle_n_produces_fewer_shingles_than_words():
    words = ["w" + str(i) for i in range(20)]
    body = " ".join(words)
    hashes = shingle_hashes(body, n=SHINGLE_N)
    # 20 words, n=5 -> up to 16 shingles (sliding window), set dedup.
    assert 0 < len(hashes) <= 20 - SHINGLE_N + 1


def test_jaccard_identical_sets_is_one():
    s = frozenset({"a", "b", "c"})
    assert jaccard(s, s) == 1.0


def test_jaccard_disjoint_sets_is_zero():
    a = frozenset({"a", "b"})
    b = frozenset({"c", "d"})
    assert jaccard(a, b) == 0.0


def test_jaccard_both_empty_is_zero_no_division_error():
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_jaccard_one_empty_is_zero():
    assert jaccard(frozenset(), frozenset({"a"})) == 0.0


def test_jaccard_partial_overlap():
    a = frozenset({"a", "b", "c", "d"})
    b = frozenset({"c", "d", "e", "f"})
    # intersection=2, union=6
    assert jaccard(a, b) == 2 / 6
