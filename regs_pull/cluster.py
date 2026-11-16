"""Group near-duplicate comments without a language model.

Each comment is normalized, split into word shingles, and sketched with
MinHash. Locality-sensitive banding proposes candidate pairs; a pair joins
the same cluster only when the MinHash estimate of Jaccard similarity is at
least ``threshold``. A cluster smaller than ``min_cluster_size`` is treated
as unique rather than a form letter, so two people who happen to agree are
not labeled a campaign.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ClusterConfig:
    threshold: float = 0.8
    shingle_size: int = 5
    num_perm: int = 64
    bands: int = 16
    min_cluster_size: int = 3
    representative_chars: int = 480

    def __post_init__(self) -> None:
        if self.num_perm < 1 or self.num_perm % self.bands != 0:
            raise ValueError("num_perm must be a positive multiple of bands")
        if not 0 < self.threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        if self.shingle_size < 1 or self.min_cluster_size < 2:
            raise ValueError("shingle_size must be >= 1 and min_cluster_size >= 2")


_NON_WORD = re.compile(r"[^a-z0-9\s]+")
_SPACE = re.compile(r"\s+")
_URL = re.compile(r"https?://\S+|www\.\S+")


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = value.lower()
    value = _URL.sub(" ", value)
    value = _NON_WORD.sub(" ", value)
    return _SPACE.sub(" ", value).strip()


def word_shingles(text: str, size: int) -> set[str]:
    words = text.split()
    if not words:
        return set()
    if len(words) <= size:
        return {" ".join(words)}
    return {" ".join(words[index : index + size]) for index in range(len(words) - size + 1)}


def minhash_signature(tokens: set[str], num_perm: int) -> tuple[int, ...]:
    if not tokens:
        return tuple(2**64 - 1 for _ in range(num_perm))
    signature: list[int] = []
    for perm in range(num_perm):
        prefix = f"{perm}:".encode()
        best = 2**64 - 1
        for token in tokens:
            digest = hashlib.blake2b(prefix + token.encode(), digest_size=8).digest()
            best = min(best, int.from_bytes(digest, "big"))
        signature.append(best)
    return tuple(signature)


def signature_similarity(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    matches = sum(1 for a, b in zip(left, right) if a == b)
    return matches / len(left)


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        self.add(left)
        self.add(right)
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            # Keep the smaller id as the root so cluster ids are stable.
            if root_left < root_right:
                self.parent[root_right] = root_left
            else:
                self.parent[root_left] = root_right


def cluster_comments(
    comments: list[dict[str, str]],
    config: ClusterConfig | None = None,
) -> list[dict[str, object]]:
    """Return one assignment per input comment, in the same order.

    Each input dict needs ``id`` and ``text``. Output fields: ``id``,
    ``clusterId`` (empty when the comment is not a form letter),
    ``isFormLetter``, ``clusterSize``, and ``representative``.
    """
    settings = config or ClusterConfig()
    prepared: list[tuple[str, str, tuple[int, ...]]] = []
    for comment in comments:
        comment_id = str(comment.get("id") or "")
        normalized = normalize_text(str(comment.get("text") or ""))
        signature = minhash_signature(word_shingles(normalized, settings.shingle_size), settings.num_perm)
        prepared.append((comment_id, normalized, signature))

    buckets: dict[tuple[int, tuple[int, ...]], list[int]] = {}
    rows = settings.num_perm // settings.bands
    for index, (_comment_id, _text, signature) in enumerate(prepared):
        for band in range(settings.bands):
            chunk = signature[band * rows : (band + 1) * rows]
            buckets.setdefault((band, chunk), []).append(index)

    groups = _UnionFind()
    for comment_id, _text, _signature in prepared:
        groups.add(comment_id)
    compared: set[tuple[int, int]] = set()
    for indexes in buckets.values():
        if len(indexes) < 2:
            continue
        for left_pos in range(len(indexes)):
            for right_pos in range(left_pos + 1, len(indexes)):
                left, right = indexes[left_pos], indexes[right_pos]
                pair = (left, right) if left < right else (right, left)
                if pair in compared:
                    continue
                compared.add(pair)
                score = signature_similarity(prepared[left][2], prepared[right][2])
                if score >= settings.threshold:
                    groups.union(prepared[left][0], prepared[right][0])

    members: dict[str, list[int]] = {}
    for index, (comment_id, _text, _signature) in enumerate(prepared):
        members.setdefault(groups.find(comment_id), []).append(index)

    representative_index: dict[str, int] = {}
    for root, indexes in members.items():
        representative_index[root] = max(indexes, key=lambda item: len(prepared[item][1]))

    assignments: list[dict[str, object]] = []
    for index, (comment_id, _text, _signature) in enumerate(prepared):
        root = groups.find(comment_id)
        size = len(members[root])
        is_form = size >= settings.min_cluster_size
        assignments.append(
            {
                "id": comment_id,
                "clusterId": root if is_form else "",
                "isFormLetter": is_form,
                "clusterSize": size if is_form else 1,
                "representative": is_form and representative_index[root] == index,
            }
        )
    return assignments


def cluster_summaries(
    comments: list[dict[str, str]],
    assignments: list[dict[str, object]],
    config: ClusterConfig | None = None,
) -> list[dict[str, object]]:
    """One row per form-letter cluster, largest first."""
    settings = config or ClusterConfig()
    text_by_id = {str(comment.get("id") or ""): str(comment.get("text") or "") for comment in comments}
    grouped: dict[str, list[dict[str, object]]] = {}
    for assignment in assignments:
        if not assignment["isFormLetter"]:
            continue
        grouped.setdefault(str(assignment["clusterId"]), []).append(assignment)
    summaries: list[dict[str, object]] = []
    for cluster_id, members in grouped.items():
        representative = next(item for item in members if item["representative"])
        raw = text_by_id.get(str(representative["id"]), "")
        trimmed = raw.strip()
        if len(trimmed) > settings.representative_chars:
            trimmed = trimmed[: settings.representative_chars].rstrip() + "…"
        summaries.append(
            {
                "clusterId": cluster_id,
                "count": len(members),
                "representativeId": representative["id"],
                "representativeText": trimmed,
            }
        )
    summaries.sort(key=lambda item: (-int(item["count"]), str(item["clusterId"])))
    return summaries
