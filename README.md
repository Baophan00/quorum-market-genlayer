# QuorumMarket

A binary prediction market whose outcome is decided by a quorum of three named sources, with the credits held inside the contract. A market is opened with a question and exactly three distinct `http(s)` URLs; anyone can buy `yes` or `no` shares with credits; after the close time anyone can call `resolve` and the contract runs a single consensus round in which the validators read all three pages and report one vote per source — `yes`, `no` or `unclear` — with a short quote. The contract then counts the votes itself: two `yes` votes resolve `yes`, two `no` votes resolve `no`, and any other shape is void and refunds every buyer. Shares are bought into a parimutuel pool, and the payout is arithmetic: the winning side splits the pool by floor division, with the credits lost to rounding going one each to the earliest buyers on the winning side, in first-buy order. Nothing about the money is a model decision, and nothing about the outcome is a model decision either — the model is only asked what each page says.

The interesting part is where the two halves meet. A single page is a weak oracle: it can be edited after the fact, it can 404, and two nodes can read the same wall of text charitably and strictly. So the contract asks three named sources and requires two of them to agree before credits move — one bad read cannot settle a market, and sources that genuinely disagree are not averaged into a fake answer, they are void and refunded. The consensus principle compares the *ordered* list of three votes and the derived outcome, so a validator that reads source 2 differently is a mismatch even when the final outcome happens to be the same; the wording of a quote is free, the votes are not. The model's own `"outcome"` field is ignored, because the counting is code. On top of that, the payout is largest-remainder arithmetic over the stored share counts, so the same market always pays exactly the same amounts, to the same addresses, whatever order people claim in.

The credits are a ledger inside this contract and not money. Every address starts with 100 credits the first time it is seen, there is no deposit, no withdrawal and no transfer of value, and `balance_of` is simply `100 - spent + won`. That makes this a demonstration of quorum resolution and in-contract accounting, not a money market: nothing here is backed by, or redeemable for, anything. Claiming is per market per address, once.

## API

| Function | Kind | What it does |
| --- | --- | --- |
| `open_market(market_id, question, source_a, source_b, source_c, closes_at)` | write | Opens a market. The question is capped at 300 characters, the three sources must be distinct `http(s)` URLs, `closes_at` must be in the future and at most 30 days ahead, the caller becomes the owner and a market id can be used once. |
| `buy(market_id, side, amount)` | write | Buys `amount` (a whole number, at least 1) shares of `yes` or `no` before the close time, spending credits. Refuses with the shortfall when the balance is too small. |
| `resolve(market_id)` | write | Anyone may call it once the market is closed. One consensus round reads all three sources; the contract counts the votes, stores them with the outcome, and moves the market to `resolved` or `void`. |
| `claim(market_id)` | write | After resolution: the winning side receives its arithmetic share of the pool, or a void market refunds exactly what the caller put in. Once per address per market. |
| `get_market(market_id)` | view | Question, the three sources, `closes_at`, `state` (`open` / `resolved` / `void`), `pool`, `yes_total`, `no_total`, `buyers`, the stored votes, `outcome`, `resolved_at`. |
| `positions_of(market_id, address)` | view | `{address, yes, no, paid, claimed}` for one address in one market. |
| `balance_of(address)` | view | `100 - spent + won`, as an integer. |
| `markets_of(owner)` | view | The ids of the markets an address opened. |
| `is_resolved(market_id)` | view | `True` once the market is `resolved` or `void`. |
| `dispute(market_id, source_url, reason)` | write | Challenge a resolved/void market within 7 days. The challenger names a fourth distinct source, states why, and stakes a bond of 25 credits. One consensus round reads all four pages; three of four decides. Only buyers or the owner, only before any claim, once per market. |
| `stats()` | view | `{markets, resolved, void, pool}`. |

Integers are returned as strings in the dict views (`pool`, `yes_total`, `closes_at`, …) so that no JSON reader rounds them; `balance_of` returns a whole number.

## Quorum rules

| Votes across the three sources (in source order) | Outcome | Credits |
| --- | --- | --- |
| `yes` on at least two sources | `yes` | the `yes` side splits the pool |
| `no` on at least two sources | `no` | the `no` side splits the pool |
| one `yes`, one `no`, one `unclear` | `void` | every buyer refunded exactly what they paid |
| two or three `unclear` | `void` | every buyer refunded exactly what they paid |
| any answer that is not three votes from `{yes, no, unclear}` | refused | the market stays `open`, nothing is stored |
| all three sources unreadable | refused | the market stays `open`, nothing is stored |
| two votes agree but nobody holds that side | the outcome stands | nobody can claim; the pool stays in the contract |

The last row is the honest edge of the design: the quorum decides the outcome, not the book, so a market with no backers on the winning side has nothing to pay out.

## Dispute resolution (V2)

V1 let a buyer or the owner re-run the same three sources for free within seven days. That answered a lucky or unlucky first read, but it could not answer the case the market was built for: all three named sources agree on a stale or wrong sentence, and no amount of re-reading them helps.

V2 changes what a dispute is. The challenger must bring a fourth source, one that is not already in the market, and stake a bond of 25 credits. A single consensus round then reads all four pages and the contract requires three of four to agree: three `yes` resolves `yes`, three `no` resolves `no`, anything else voids the market. The challenger is not asked to be right, only to put a source behind the claim and pay for the round if the reading holds. When the outcome changes the bond comes back in full; when it does not the bond joins the pool for the winning side, so a failed dispute pays the people who were right. The first claim closes the window, and a market can be disputed once, so a losing side cannot drain a pool with repeated rounds.

`get_market` now returns `dispute_source`, `dispute_bond`, `disputed_by`, `dispute_reason`, `disputed_at` and `original_outcome`, so both readings and the money behind the challenge are auditable from the chain alone.

## Live

- App: https://quorum-market.surge.sh
- Contract V0 (Studio Network): `0x226AB8e4A914B7F8fACAe1f20cBc3C719669dA3C`
- Contract V1 (Studio Network): `0x40258BEC230E312233a33F926a24bb3b729A1617`
- Contract V2 (Studio Network): `0x68689539cB8a977901EF6395F7259704061Fc818`
- Explorer V0: https://explorer-studio.genlayer.com/address/0x226AB8e4A914B7F8fACAe1f20cBc3C719669dA3C
- Explorer V1: https://explorer-studio.genlayer.com/address/0x40258BEC230E312233a33F926a24bb3b729A1617
- Explorer V2: https://explorer-studio.genlayer.com/address/0x68689539cB8a977901EF6395F7259704061Fc818

## Three sources, a dispute on a fourth, a payout on the live contract

Two accounts buy opposite sides of a question that all three hosted pages answer in their own text. After the market resolves, the losing side disputes with a fourth source and a bond, and the pool is claimed only once the second reading settles.

| what | transaction |
| --- | --- |
| deploy `0x68689539cB8a977901EF6395F7259704061Fc818` | `0x4fefcea8b42a59d30e7ddbbc8e5b8c159da1f10138a4ae739aa6c4d33f7aa6ef` |
| `open_market` with three static sources | `0xec2a6acc3d3df37261aa1a886fd1f5f5451b0736c7cd4cf0f634c933223aecfc` |
| buyer A `buy yes 40` | `0x75153cbb405a951f8283602d97cde517889316f7d0f93c4870b6e0df93171dcc` |
| buyer B `buy no 25` — pool 65 | `0x194002b99ad1983f95ef9481708d9b215e80db103eb220d329aed4612a261713` |
| `resolve` — three votes read, **3 yes**, outcome **yes**, 46s | `0x530cfc274131d9f9e42e0a2153baa1adcfd06cde79f5e88415490c3068f63712` |
| `dispute` — fourth source, four votes, **4 yes**, bond 25, 51s | `0x6e8aa760d84d425e5c6d5d853d98a380d090ca3222d1ca8c13b61701f41b3cbd` |
| winner `claim` — pool plus the disputing side's bond, 90 credits | `0x1690a34b2fbf8ff86a74853d69213c20a4f35877fccaeec70057d54db9220c35` |
| losing side `claim` — refused, it holds no winning shares | `0x333ded06f246c9699f3fb2909150e5d02f9567a9b731fec1bf6d3ba50f4c5c97` |
| a second `claim` — refused | `0x2f57e21cd87282c14e273f4a3c27b21f3bc58df3f9b038e761274d5f49677a5f` |

The dispute round stored one vote per source, four in total, in source order:

```
1. yes  https://attest-demo-dusky.vercel.app/       "DomainAttest"
2. yes  https://attest-proof.vercel.app/            "DomainAttest"
3. yes  https://attest-stale.vercel.app/            "Well-known proof file for GenLayer DomainAttest."
4. yes  https://attest-proof.vercel.app/?dispute=1  "DomainAttest"
```

The market had already resolved `yes` on three sources, so the dispute confirmed the reading and the 25 credit bond stayed in the pool: the winning claim took 65 staked credits plus the bond, 90 in total, and the disputing account finished 25 credits poorer for asking.

## What the live run taught

The first attempt resolved against a source that is a JavaScript app: its rendered text is empty, so
validators split between `unclear` and `no` and the round was **rejected by consensus** — the leader
produced `yes, yes, unclear`, the validators did not match it, and the write was never applied
(`0xd1a95bde922576771e7dd945853a9e6e894432c76a3b781bd9b79e301a9f25f3` reads `SUCCESS` in the leader
receipt while every validator voted `disagree`). That is the system behaving correctly: a quorum
contract must not settle on a source whose text is unknowable, and the strict per-source comparison is
what catches it. Two fixes followed, both visible above: sources whose text actually states the answer,
and a question that can be answered from the text alone ("does the served text contain the word")
rather than one that asks for an opinion.

Also worth knowing when driving this from a script: the public Studio RPC answers **500 requests per
hour**, and a run that reads state after every write will spend them. The claim that printed last was
the winner's; the per-position read after it was refused with `Rate limit exceeded`, so the balance was
left for the app to show.

## Tests

```
~/agent-escrow-genlayer/.venv/bin/python -m pytest tests/direct -q
```

58 tests in `tests/direct/test_quorum_market.py` (`58 passed`), all direct mode — no network, no chain, no keys. The suites are `TestOpen` (market creation and its refusals), `TestBuy` (ledger, sides, shortfall, close time), `TestQuorum` (every vote shape, and the strict parser that refuses a malformed answer), `TestPayout` (3:1 split, ties, exact sums, deterministic remainder, void refunds, double claim), `TestViews`, `TestNoModelAfterResolution` (mocks cleared, then views and `claim` still work), and `TestDispute` (the fourth source, the bond, the window closed by the first claim, the 3-of-4 count and the void case).

Both nondeterministic pieces are mocked and the contract's own code path is exercised: `direct_vm.mock_web(...)` answers each of the three `gl.nondet.web.render` calls, so every source can be given its own page (and one test only lets the LLM mock fire when all three bodies arrive in the prompt, in source order), and `direct_vm.mock_llm(...)` answers the single `gl.eq_principle.prompt_comparative` round. Time is moved with `direct_vm.warp(...)` because `closes_at` is a unix timestamp.

Two direct-mode limits are worth stating plainly. First, mock matching is first-registered-wins for both web and LLM, so a test that changes the answer for a second resolution calls `direct_vm.clear_mocks()` first. Second, `run_nondet` in direct mode runs only the leader function and skips the validator, and the `ExecPromptTemplate` request that the comparative principle uses to judge two answers is not implemented there at all — so the suite proves the reads, the prompt, the strict parse and the counting, while the equivalence judgement itself is only enforced on a real network. That is why the principle text spells out source order and the outcome instead of saying "equivalent answers".

The contract header pins the `py-genlayer` runner hash that `genlayer-test` resolves the SDK from; `py-genlayer:test` is not a resolvable hash and fails at import time in this toolchain.

## Scripts

```
node scripts/deploy_studionet.js            # deploy, then read stats() back
node scripts/e2e_studionet.js <address>     # open, buy both sides, resolve, claim
```

The e2e script opens a market on three real, distinct pages, funds two accounts from the contract's ledger, waits for the close time, resolves, prints the three votes with their quotes and the counted outcome, and then claims as both buyers. Studio's RPC intermittently answers with an HTML error page, so writes and receipt waits go through a retry wrapper.
