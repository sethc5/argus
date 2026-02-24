import pytest
from github_research_feed.embeddings import cosine_similarity, score_against_contexts


def test_cosine_similarity_basic():
    a = [1, 0, 0]
    b = [0, 1, 0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)
    assert cosine_similarity(a, a) == pytest.approx(1.0)


def test_score_against_contexts():
    # contexts without embeddings should be skipped
    repo_emb = [1, 0]
    contexts = [{"name": "foo"}, {"name": "bar", "embedding": "[1,0]"}]
    score, name = score_against_contexts(repo_emb, contexts)
    assert score == pytest.approx(1.0)
    assert name == "bar"
