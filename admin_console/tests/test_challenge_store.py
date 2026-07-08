import time

from admincore.challenge_store import ChallengeStore


def test_issue_and_consume_once():
    cs = ChallengeStore()
    token, nonce = cs.issue()
    assert cs.consume(token) == nonce
    assert cs.consume(token) is None          # 一次性（反 replay）


def test_unknown_token():
    assert ChallengeStore().consume("nope") is None


def test_expiry():
    cs = ChallengeStore(ttl_sec=0)
    token, _ = cs.issue()
    time.sleep(0.01)
    assert cs.consume(token) is None
