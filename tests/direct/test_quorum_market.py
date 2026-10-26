"""Direct-mode tests for QuorumMarket.

    python -m pytest tests/direct -q

The model and the three pages are mocked, the contract is not: every test drives
the code the validators run — the strict answer parser, the quorum counting, the
credit ledger and the largest-remainder payout. Nothing here talks to a network
or to a chain.

Two things worth knowing about this suite:

* `gl.nondet.web.render` is mockable in direct mode: `direct_vm.mock_web(pattern,
  {"status": 200, "body": ...})` answers a page, so the three reads inside the
  consensus callback are real calls to the contract's own fetch path and each
  source can be given its own page.
* time is moved with `direct_vm.warp(iso(ts))`, because `closes_at` is a unix
  timestamp the contract compares against `datetime.now(timezone.utc)`.
"""

import json
import time
from datetime import datetime, timedelta, timezone

CONTRACT = "contracts/quorum_market.py"

SRC_A = "https://attest-demo-dusky.vercel.app/"
SRC_B = "https://attest-stale.vercel.app/"
SRC_C = "https://claimwatch-genlayer.surge.sh/"

PAT_A = r"attest-demo-dusky\.vercel\.app"
PAT_B = r"attest-stale\.vercel\.app"
PAT_C = r"claimwatch-genlayer\.surge\.sh"
PAT_PROMPT = r"You are reading three named sources"

QUESTION = "Did the v2 rollout finish before Friday?"

PAGE_YES = "Release notes: the v2 rollout finished before Friday, as planned."
PAGE_NO = "Changelog: the v2 rollout slipped to the following Monday."
PAGE_MUTE = "Sign in to read the rest of this post."


# ----------------------------------------------------------------------- helpers

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def now():
    return int(time.time())


def hexaddr(raw):
    return "0x" + bytes(raw).hex()


def deploy(direct_deploy):
    return direct_deploy(CONTRACT)


def mock_pages(direct_vm, a=PAGE_YES, b=PAGE_YES, c=PAGE_YES):
    direct_vm.mock_web(PAT_A, {"status": 200, "body": a})
    direct_vm.mock_web(PAT_B, {"status": 200, "body": b})
    direct_vm.mock_web(PAT_C, {"status": 200, "body": c})


def mock_votes(direct_vm, votes, outcome=None):
    """Mock the one consensus answer: a vote per source, in source order."""
    payload = {"votes": votes}
    if outcome is not None:
        payload["outcome"] = outcome
    direct_vm.mock_llm(PAT_PROMPT, json.dumps(payload))


def votes_of(*picks):
    return [
        {"source": i + 1, "vote": pick, "quote": f"quote {i + 1}"}
        for i, pick in enumerate(picks)
    ]


def open_market(
    contract,
    direct_vm,
    sender,
    market_id="ship",
    ahead=3600,
    question=QUESTION,
    sources=(SRC_A, SRC_B, SRC_C),
):
    direct_vm.sender = sender
    return contract.open_market(
        market_id, question, sources[0], sources[1], sources[2], now() + ahead
    )


def closed_market(contract, direct_vm, sender, market_id="ship", ahead=60):
    """Open a market that closes in `ahead` seconds and move past the close."""
    direct_vm.sender = sender
    opened = open_market(contract, direct_vm, sender, market_id=market_id, ahead=ahead)
    direct_vm.warp(iso(now() + ahead + 60))
    return opened


def resolve_now(contract, direct_vm, sender, picks=("yes", "yes", "unclear"), outcome="yes", market_id="ship"):
    """Move past the close time of an open market and resolve it with a mocked answer."""
    direct_vm.warp(iso(now() + 31 * 24 * 3600 - 120))
    mock_pages(direct_vm)
    mock_votes(direct_vm, votes_of(*picks), outcome=outcome)
    direct_vm.sender = sender
    return contract.resolve(market_id)


def resolved(contract, direct_vm, sender, picks=("yes", "yes", "unclear"), outcome="yes"):
    """Open a fresh market that closes in a minute, then resolve it."""
    closed_market(contract, direct_vm, sender, ahead=60)
    return resolve_now(contract, direct_vm, sender, picks=picks, outcome=outcome)


def buy(contract, direct_vm, sender, side, amount, market_id="ship"):
    direct_vm.sender = sender
    return contract.buy(market_id, side, amount)


# ----------------------------------------------------------------------- TestOpen

class TestOpen:
    def test_a_market_opens_with_its_three_sources(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        out = open_market(contract, direct_vm, direct_alice)

        assert out["state"] == "open"
        assert out["question"] == QUESTION
        assert out["sources"] == [SRC_A, SRC_B, SRC_C]
        assert out["pool"] == "0"
        assert out["yes_total"] == "0"
        assert out["no_total"] == "0"
        assert out["votes"] == []
        assert out["outcome"] == ""
        assert out["resolved_at"] == "0"

        market = contract.get_market("ship")
        assert market["id"] == "ship"
        assert market["state"] == "open"
        assert contract.stats() == {"markets": 1, "resolved": 0, "void": 0, "pool": "0"}

    def test_the_opener_is_the_owner_and_only_their_market_lists(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice, market_id="ship")
        open_market(contract, direct_vm, direct_bob, market_id="rain")

        assert contract.get_market("ship")["owner"] == hexaddr(direct_alice)
        assert contract.markets_of(direct_alice) == ["ship"]
        assert contract.markets_of(direct_bob) == ["rain"]

    def test_a_close_time_in_the_past_is_refused(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice

        with direct_vm.expect_revert("closes_at must be in the future"):
            contract.open_market("ship", QUESTION, SRC_A, SRC_B, SRC_C, now() - 1)
        with direct_vm.expect_revert("closes_at must be in the future"):
            contract.open_market("ship", QUESTION, SRC_A, SRC_B, SRC_C, now())
        assert contract.stats()["markets"] == 0

    def test_a_close_time_beyond_thirty_days_is_refused(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice

        with direct_vm.expect_revert("more than 30 days ahead"):
            contract.open_market(
                "ship", QUESTION, SRC_A, SRC_B, SRC_C, now() + 31 * 24 * 3600
            )
        # Exactly thirty days is still inside the window.
        contract.open_market(
            "ship", QUESTION, SRC_A, SRC_B, SRC_C, now() + 30 * 24 * 3600
        )
        assert contract.stats()["markets"] == 1

    def test_three_distinct_sources_are_required(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice

        with direct_vm.expect_revert("must be distinct"):
            contract.open_market("ship", QUESTION, SRC_A, SRC_A, SRC_C, now() + 3600)
        with direct_vm.expect_revert("must be distinct"):
            contract.open_market("ship", QUESTION, SRC_A, SRC_B, SRC_A, now() + 3600)
        assert contract.stats()["markets"] == 0

    def test_a_source_that_is_not_an_http_url_is_refused(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice

        for bad in ("ftp://example.com/x", "example.com", "https://", "", "  "):
            with direct_vm.expect_revert("is not an http(s) URL"):
                contract.open_market("ship", QUESTION, SRC_A, SRC_B, bad, now() + 3600)
        assert contract.stats()["markets"] == 0

    def test_the_question_is_capped_and_cannot_be_blank(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice

        with direct_vm.expect_revert("longer than 300 characters"):
            contract.open_market("ship", "x" * 301, SRC_A, SRC_B, SRC_C, now() + 3600)
        with direct_vm.expect_revert("needs a question"):
            contract.open_market("ship", "   ", SRC_A, SRC_B, SRC_C, now() + 3600)
        assert contract.stats()["markets"] == 0

        # Exactly 300 characters is allowed, and trailing space is trimmed.
        out = open_market(contract, direct_vm, direct_alice, question="y" * 300)
        assert len(out["question"]) == 300

    def test_a_market_id_is_written_once(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)

        with direct_vm.expect_revert("that market id is taken"):
            open_market(contract, direct_vm, direct_alice, question="A different one?")
        assert contract.stats()["markets"] == 1
        assert contract.get_market("ship")["question"] == QUESTION

    def test_an_unknown_market_is_an_error(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice

        with direct_vm.expect_revert("no such market"):
            contract.get_market("nope")
        with direct_vm.expect_revert("no such market"):
            contract.is_resolved("nope")
        with direct_vm.expect_revert("no such market"):
            contract.claim("nope")


# ----------------------------------------------------------------------- TestBuy

class TestBuy:
    def test_buying_spends_credits_and_records_shares(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        assert contract.balance_of(direct_alice) == 100

        out = buy(contract, direct_vm, direct_alice, "yes", 30)
        assert out["side"] == "yes"
        assert out["amount"] == "30"
        assert out["shares"] == {"yes": "30", "no": "0"}
        assert out["pool"] == "30"
        assert out["balance"] == "70"
        assert contract.balance_of(direct_alice) == 70

        market = contract.get_market("ship")
        assert market["pool"] == "30"
        assert market["yes_total"] == "30"
        assert market["no_total"] == "0"
        assert market["buyers"] == 1

    def test_shares_accumulate_per_side(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)

        buy(contract, direct_vm, direct_alice, "yes", 10)
        buy(contract, direct_vm, direct_alice, "yes", 15)
        buy(contract, direct_vm, direct_alice, "no", 5)
        buy(contract, direct_vm, direct_bob, "no", 40)

        pos = contract.positions_of("ship", direct_alice)
        assert pos["yes"] == "25"
        assert pos["no"] == "5"
        assert pos["paid"] == "30"
        assert pos["claimed"] is False

        market = contract.get_market("ship")
        assert market["yes_total"] == "25"
        assert market["no_total"] == "45"
        assert market["pool"] == "70"
        assert market["buyers"] == 2
        assert contract.balance_of(direct_alice) == 70
        assert contract.balance_of(direct_bob) == 60

    def test_the_side_must_be_yes_or_no(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)

        with direct_vm.expect_revert("side must be 'yes' or 'no'"):
            buy(contract, direct_vm, direct_alice, "maybe", 5)
        with direct_vm.expect_revert("side must be 'yes' or 'no'"):
            buy(contract, direct_vm, direct_alice, "", 5)
        assert contract.get_market("ship")["pool"] == "0"

    def test_the_amount_must_be_at_least_one_credit(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)

        with direct_vm.expect_revert("at least 1 credit"):
            buy(contract, direct_vm, direct_alice, "yes", 0)
        with direct_vm.expect_revert("at least 1 credit"):
            buy(contract, direct_vm, direct_alice, "yes", -5)
        assert contract.get_market("ship")["pool"] == "0"
        assert contract.balance_of(direct_alice) == 100

    def test_a_buy_beyond_the_balance_names_the_shortfall(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 90)

        with direct_vm.expect_revert("not enough credits: you have 10, buying 25 is short 15"):
            buy(contract, direct_vm, direct_alice, "yes", 25)
        assert contract.get_market("ship")["pool"] == "90"
        assert contract.balance_of(direct_alice) == 10

        # Exactly the remaining balance is allowed.
        assert buy(contract, direct_vm, direct_alice, "no", 10)["balance"] == "0"

    def test_buying_after_the_close_time_is_refused(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice, ahead=60)
        buy(contract, direct_vm, direct_alice, "yes", 10)

        direct_vm.warp(iso(now() + 61))
        with direct_vm.expect_revert("closed for buying"):
            buy(contract, direct_vm, direct_alice, "yes", 10)
        with direct_vm.expect_revert("closed for buying"):
            buy(contract, direct_vm, direct_alice, "yes", 10)
        assert contract.get_market("ship")["pool"] == "10"

    def test_buying_after_resolution_is_refused(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        resolved(contract, direct_vm, direct_alice)

        with direct_vm.expect_revert("already resolved"):
            buy(contract, direct_vm, direct_alice, "yes", 10)


# -------------------------------------------------------------------- TestQuorum

class TestQuorum:
    def test_two_yes_votes_resolve_yes(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        mock_pages(direct_vm)
        mock_votes(direct_vm, votes_of("yes", "unclear", "yes"), outcome="yes")

        direct_vm.sender = direct_alice
        out = contract.resolve("ship")

        assert out["state"] == "resolved"
        assert out["outcome"] == "yes"
        assert out["yes_votes"] == 2
        assert out["unclear_votes"] == 1
        assert int(out["resolved_at"]) > 0

        market = contract.get_market("ship")
        assert market["state"] == "resolved"
        assert market["outcome"] == "yes"
        assert market["votes"] == [
            {"source": SRC_A, "vote": "yes", "quote": "quote 1"},
            {"source": SRC_B, "vote": "unclear", "quote": "quote 2"},
            {"source": SRC_C, "vote": "yes", "quote": "quote 3"},
        ]
        assert contract.is_resolved("ship") is True

    def test_two_no_votes_resolve_no(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        mock_pages(direct_vm)
        mock_votes(direct_vm, votes_of("no", "no", "unclear"), outcome="no")

        direct_vm.sender = direct_alice
        out = contract.resolve("ship")
        assert out["state"] == "resolved"
        assert out["outcome"] == "no"
        assert out["no_votes"] == 2

    def test_one_yes_one_no_and_one_unclear_is_void(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        mock_pages(direct_vm)
        # The model claims "yes"; the votes do not support it and code wins.
        mock_votes(direct_vm, votes_of("yes", "no", "unclear"), outcome="yes")

        direct_vm.sender = direct_alice
        out = contract.resolve("ship")
        assert out["state"] == "void"
        assert out["outcome"] == "void"
        assert out["yes_votes"] == 1
        assert out["no_votes"] == 1
        assert out["unclear_votes"] == 1

    def test_two_unclear_votes_are_void(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        mock_pages(direct_vm, PAGE_MUTE, PAGE_MUTE, PAGE_NO)
        mock_votes(direct_vm, votes_of("unclear", "unclear", "no"), outcome="no")

        direct_vm.sender = direct_alice
        out = contract.resolve("ship")
        assert out["state"] == "void"
        assert out["outcome"] == "void"
        assert contract.is_resolved("ship") is True

    def test_resolving_before_the_close_time_is_refused(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice, ahead=3600)

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("not closed yet"):
            contract.resolve("ship")
        assert contract.get_market("ship")["state"] == "open"

    def test_a_market_resolves_once(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        resolved(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("already resolved"):
            contract.resolve("ship")

    def test_anyone_may_resolve_after_the_close(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)

        direct_vm.sender = direct_bob
        mock_pages(direct_vm)
        mock_votes(direct_vm, votes_of("no", "no", "yes"), outcome="no")
        out = contract.resolve("ship")
        assert out["outcome"] == "no"

    def test_a_votes_list_that_does_not_name_three_sources_reverts(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice

        for short in (votes_of("yes", "yes"), votes_of("yes", "yes", "yes", "no")):
            direct_vm.clear_mocks()
            mock_pages(direct_vm)
            mock_votes(direct_vm, short)
            with direct_vm.expect_revert("must name all 3 sources"):
                contract.resolve("ship")

        market = contract.get_market("ship")
        assert market["state"] == "open"
        assert market["votes"] == []
        assert market["outcome"] == ""

    def test_a_vote_outside_the_enum_reverts(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        mock_pages(direct_vm)
        mock_votes(direct_vm, votes_of("yes", "probably", "yes"))

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("not one of yes, no, unclear"):
            contract.resolve("ship")
        assert contract.get_market("ship")["state"] == "open"
        assert contract.is_resolved("ship") is False

    def test_an_answer_that_is_not_an_object_reverts(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        mock_pages(direct_vm)
        direct_vm.mock_llm(PAT_PROMPT, json.dumps(["yes", "no", "unclear"]))

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("is not an object with a votes list"):
            contract.resolve("ship")
        assert contract.get_market("ship")["state"] == "open"

    def test_a_missing_votes_key_reverts(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        mock_pages(direct_vm)
        direct_vm.mock_llm(PAT_PROMPT, json.dumps({"outcome": "yes"}))

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("votes list is missing"):
            contract.resolve("ship")
        assert contract.get_market("ship")["outcome"] == ""

    def test_quotes_may_be_empty_and_are_trimmed(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        mock_pages(direct_vm)
        mock_votes(
            direct_vm,
            [
                {"source": 1, "vote": " YES ", "quote": ""},
                {"source": 2, "vote": "yes"},
                {"source": 3, "vote": "unclear", "quote": "   "},
            ],
        )

        direct_vm.sender = direct_alice
        out = contract.resolve("ship")
        assert out["outcome"] == "yes"
        assert out["votes"][0]["vote"] == "yes"
        assert out["votes"][0]["quote"] == ""
        assert out["votes"][1]["quote"] == ""
        assert out["votes"][2]["quote"] == ""

    def test_the_prompt_carries_all_three_pages_in_source_order(
        self, direct_vm, direct_deploy, direct_alice
    ):
        """The LLM mock only fires if the three rendered bodies reach the prompt
        in source order, so the reads really happen inside the callback."""
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        direct_vm.mock_web(PAT_A, {"status": 200, "body": "PAGE-ONE says yes"})
        direct_vm.mock_web(PAT_B, {"status": 200, "body": "PAGE-TWO says yes"})
        direct_vm.mock_web(PAT_C, {"status": 200, "body": "PAGE-THREE is silent"})
        direct_vm.mock_llm(
            r"(?s)PAGE-ONE says yes.*PAGE-TWO says yes.*PAGE-THREE is silent",
            json.dumps({"votes": votes_of("yes", "yes", "unclear"), "outcome": "yes"}),
        )

        direct_vm.sender = direct_alice
        out = contract.resolve("ship")
        assert out["outcome"] == "yes"
        assert [v["vote"] for v in out["votes"]] == ["yes", "yes", "unclear"]

    def test_unreadable_pages_are_a_refused_resolution(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        closed_market(contract, direct_vm, direct_alice)
        # No web mocks at all: every render raises inside the callback.
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("none of the three sources could be read"):
            contract.resolve("ship")
        assert contract.get_market("ship")["state"] == "open"


# -------------------------------------------------------------------- TestPayout

class TestPayout:
    def test_a_three_to_one_pool_split(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 30)
        buy(contract, direct_vm, direct_bob, "no", 10)
        assert contract.get_market("ship")["pool"] == "40"

        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "yes", "unclear"))

        direct_vm.sender = direct_alice
        out = contract.claim("ship")
        assert out["payout"] == "40"
        assert out["balance"] == "110"
        assert contract.balance_of(direct_alice) == 110

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("hold no yes shares"):
            contract.claim("ship")
        assert contract.balance_of(direct_bob) == 90

    def test_a_tie_in_shares_splits_the_pool_evenly(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 20)
        buy(contract, direct_vm, direct_bob, "yes", 20)

        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "unclear", "yes"))

        direct_vm.sender = direct_alice
        first = contract.claim("ship")["payout"]
        direct_vm.sender = direct_bob
        second = contract.claim("ship")["payout"]
        assert (first, second) == ("20", "20")
        assert int(first) + int(second) == 40
        assert contract.balance_of(direct_alice) == 100
        assert contract.balance_of(direct_bob) == 100

    def test_the_payouts_sum_to_the_pool_exactly(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 7)
        buy(contract, direct_vm, direct_bob, "yes", 11)
        buy(contract, direct_vm, direct_bob, "no", 13)

        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "yes", "unclear"))
        pool = int(contract.get_market("ship")["pool"])
        assert pool == 31

        direct_vm.sender = direct_alice
        first = int(contract.claim("ship")["payout"])
        direct_vm.sender = direct_bob
        second = int(contract.claim("ship")["payout"])
        assert first + second == pool

    def test_leftover_credits_go_to_the_earliest_buyer(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 1)
        buy(contract, direct_vm, direct_bob, "yes", 1)
        buy(contract, direct_vm, direct_charlie, "yes", 1)
        buy(contract, direct_vm, direct_bob, "no", 7)

        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "yes", "no"))
        assert contract.get_market("ship")["pool"] == "10"

        # 10 over three equal shares: 3 each, and the odd credit to the first buyer.
        direct_vm.sender = direct_alice
        assert contract.claim("ship")["payout"] == "4"
        direct_vm.sender = direct_bob
        assert contract.claim("ship")["payout"] == "3"
        direct_vm.sender = direct_charlie
        assert contract.claim("ship")["payout"] == "3"

    def test_two_leftover_credits_go_one_each_to_the_first_two_buyers(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 1)
        buy(contract, direct_vm, direct_bob, "yes", 1)
        buy(contract, direct_vm, direct_charlie, "yes", 1)
        buy(contract, direct_vm, direct_charlie, "no", 8)

        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "yes", "no"))
        # 11 over three equal shares: 3 each leaves 2, one each to alice and bob.
        assert contract.get_market("ship")["pool"] == "11"

        direct_vm.sender = direct_alice
        first = contract.claim("ship")["payout"]
        direct_vm.sender = direct_bob
        second = contract.claim("ship")["payout"]
        direct_vm.sender = direct_charlie
        third = contract.claim("ship")["payout"]
        assert (first, second, third) == ("4", "4", "3")
        assert int(first) + int(second) + int(third) == 11

    def test_the_remainder_does_not_follow_claim_order(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 1)
        buy(contract, direct_vm, direct_bob, "yes", 1)
        buy(contract, direct_vm, direct_charlie, "yes", 1)
        buy(contract, direct_vm, direct_bob, "no", 7)

        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "yes", "no"))

        # Charlie claims first and still gets the floor, not the odd credit.
        direct_vm.sender = direct_charlie
        assert contract.claim("ship")["payout"] == "3"
        direct_vm.sender = direct_alice
        assert contract.claim("ship")["payout"] == "4"
        direct_vm.sender = direct_bob
        assert contract.claim("ship")["payout"] == "3"

    def test_a_void_market_refunds_every_buyer_exactly(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 40)
        buy(contract, direct_vm, direct_bob, "no", 25)
        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "no", "unclear"))
        assert contract.get_market("ship")["state"] == "void"

        direct_vm.sender = direct_alice
        assert contract.claim("ship")["payout"] == "40"
        assert contract.balance_of(direct_alice) == 100
        direct_vm.sender = direct_bob
        assert contract.claim("ship")["payout"] == "25"
        assert contract.balance_of(direct_bob) == 100

    def test_claiming_twice_is_refused(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 10)
        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "yes", "no"))

        direct_vm.sender = direct_alice
        contract.claim("ship")
        with direct_vm.expect_revert("already claimed"):
            contract.claim("ship")
        assert contract.balance_of(direct_alice) == 100

    def test_claiming_before_resolution_is_refused(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 10)

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("not resolved yet"):
            contract.claim("ship")

        direct_vm.warp(iso(now() + 3601))
        with direct_vm.expect_revert("not resolved yet"):
            contract.claim("ship")

    def test_a_winning_side_with_no_holders_pays_nobody(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        """The quorum decides the outcome, not the book: nobody backed yes, so the
        pool has no winners to pay, and nothing divides by zero."""
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_bob, "no", 30)
        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "yes", "no"))
        assert contract.get_market("ship")["outcome"] == "yes"

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("hold no yes shares"):
            contract.claim("ship")
        assert contract.balance_of(direct_bob) == 70
        assert contract.stats()["pool"] == "30"

    def test_a_stranger_with_no_position_cannot_claim(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 10)
        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "yes", "no"))

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("hold no shares in this market"):
            contract.claim("ship")


# --------------------------------------------------------------------- TestViews

class TestViews:
    def test_is_resolved_is_false_before_resolution(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        assert contract.is_resolved("ship") is False

        direct_vm.warp(iso(now() + 3601))
        assert contract.is_resolved("ship") is False
        assert contract.get_market("ship")["state"] == "open"

    def test_every_address_starts_with_a_hundred_credits(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        assert contract.balance_of(direct_alice) == 100
        assert contract.balance_of(direct_bob) == 100
        assert contract.balance_of("0x" + "11" * 20) == 100

    def test_positions_of_an_address_with_no_shares(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "no", 12)

        pos = contract.positions_of("ship", direct_bob)
        assert pos == {
            "address": hexaddr(direct_bob),
            "yes": "0",
            "no": "0",
            "paid": "0",
            "claimed": False,
        }
        assert contract.positions_of("ship", direct_alice)["no"] == "12"

    def test_stats_counts_markets_and_the_pool(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice, market_id="ship")
        open_market(contract, direct_vm, direct_bob, market_id="rain", ahead=600)

        buy(contract, direct_vm, direct_alice, "yes", 10, market_id="ship")
        buy(contract, direct_vm, direct_bob, "no", 20, market_id="rain")
        assert contract.stats() == {
            "markets": 2,
            "resolved": 0,
            "void": 0,
            "pool": "30",
        }

        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "yes", "no"))
        assert contract.stats() == {
            "markets": 2,
            "resolved": 1,
            "void": 0,
            "pool": "30",
        }

        # The resolve above moved time past rain's close too; only the answer
        # changes, and the two unclear votes make it void. (Direct mode matches
        # the first registered LLM mock, so the earlier answer is cleared first.)
        direct_vm.clear_mocks()
        mock_pages(direct_vm, PAGE_MUTE, PAGE_MUTE, PAGE_MUTE)
        mock_votes(direct_vm, votes_of("unclear", "unclear", "yes"), outcome="void")
        direct_vm.sender = direct_bob
        contract.resolve("rain")
        assert contract.stats() == {
            "markets": 2,
            "resolved": 1,
            "void": 1,
            "pool": "30",
        }

    def test_markets_of_reports_ids_not_objects(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice, market_id="ship")
        open_market(contract, direct_vm, direct_alice, market_id="rain")
        open_market(contract, direct_vm, direct_bob, market_id="sun")

        assert contract.markets_of(direct_alice) == ["ship", "rain"]
        assert contract.markets_of(direct_bob) == ["sun"]
        assert contract.stats()["markets"] == 3


# ------------------------------------------------- TestNoModelAfterResolution

class TestNoModelAfterResolution:
    """Once the votes are stored, nothing downstream may need a model."""

    def test_views_still_answer_without_any_mock(
        self, direct_vm, direct_deploy, direct_alice
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 15)
        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "no", "yes"))

        direct_vm.clear_mocks()

        market = contract.get_market("ship")
        assert market["outcome"] == "yes"
        assert [v["vote"] for v in market["votes"]] == ["yes", "no", "yes"]
        assert contract.is_resolved("ship") is True
        assert contract.positions_of("ship", direct_alice)["yes"] == "15"
        assert contract.balance_of(direct_alice) == 85
        assert contract.stats()["resolved"] == 1

    def test_claim_still_pays_without_any_mock(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = deploy(direct_deploy)
        open_market(contract, direct_vm, direct_alice)
        buy(contract, direct_vm, direct_alice, "yes", 25)
        buy(contract, direct_vm, direct_bob, "no", 15)
        resolve_now(contract, direct_vm, direct_alice, picks=("yes", "yes", "no"))

        direct_vm.clear_mocks()

        direct_vm.sender = direct_alice
        assert contract.claim("ship")["payout"] == "40"
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("hold no yes shares"):
            contract.claim("ship")
        assert contract.balance_of(direct_alice) == 115


class TestMarketList:
    """The list view is the product surface: newest first, pageable, no ids leaked twice."""

    def test_newest_first_and_pageable(self, direct_vm, direct_deploy, direct_alice):
        contract = deploy(direct_deploy)
        direct_vm.sender = direct_alice
        closes = int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())
        for n in range(3):
            contract.open_market(f"m-{n}", f"question {n}", "https://a.example/1", "https://b.example/2", "https://c.example/3", closes)

        listing = contract.list_markets(0, 2)
        assert [m["id"] for m in listing] == ["m-2", "m-1"]
        assert [m["id"] for m in contract.list_markets(2, 2)] == ["m-0"]
        assert contract.list_markets(9, 2) == []
