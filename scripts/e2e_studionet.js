#!/usr/bin/env node
/**
 * End to end run of QuorumMarket on GenLayer Studio Network.
 *
 *   node scripts/e2e_studionet.js <contractAddress>
 *
 * One market, three real pages, two accounts on opposite sides.
 *
 *   open_market   a question and three distinct, reachable sources; closes in 4 minutes
 *   buy           one account backs yes, the other backs no, with credits
 *   resolve       validators read all three pages and vote per source; the
 *                 contract counts the votes and decides
 *   claim         the winning side splits the pool; both accounts claim
 *
 * Only resolve is a consensus round. Buying, the counting and the payout are
 * arithmetic, so their receipts finalize without a model in the loop.
 */
const fs = require("fs");
const path = require("path");
const { createClient, createAccount, generatePrivateKey } = require("genlayer-js");
const { studionet } = require("genlayer-js/chains");

const ADDRESS = process.argv[2];
if (!ADDRESS) {
  console.error("usage: node scripts/e2e_studionet.js <contractAddress>");
  process.exit(1);
}

const KEYFILE = path.join(__dirname, ".e2e-keys.json");
function keys() {
  if (fs.existsSync(KEYFILE)) return JSON.parse(fs.readFileSync(KEYFILE, "utf8"));
  const k = {
    owner: generatePrivateKey(),
    yes: generatePrivateKey(),
    no: generatePrivateKey(),
  };
  fs.writeFileSync(KEYFILE, JSON.stringify(k, null, 2), { mode: 0o600 });
  return k;
}
const k = keys();
const owner = createAccount(k.owner);
const yesAcct = createAccount(k.yes || generatePrivateKey());
const noAcct = createAccount(k.no || generatePrivateKey());
const ownerClient = createClient({ chain: studionet, account: owner });
const yesClient = createClient({ chain: studionet, account: yesAcct });
const noClient = createClient({ chain: studionet, account: noAcct });
const reader = createClient({ chain: studionet, account: createAccount(generatePrivateKey()) });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function withRetry(fn, label) {
  let last;
  for (let i = 0; i < 8; i++) {
    try {
      return await fn();
    } catch (e) {
      last = e;
      const msg = String(e && e.message);
      if (!/DOCTYPE|fetch failed|ECONNRESET|timeout|temporarily unavailable|not available/i.test(msg)) throw e;
      console.log(`  ${label}: rpc hiccup, retry ${i + 1}`);
      await sleep(4000);
    }
  }
  throw last;
}

function refused(receipt) {
  const leader = (receipt.consensus_data && receipt.consensus_data.leader_receipt) || [];
  return String(leader[0] && leader[0].execution_result).toUpperCase() === "ERROR";
}

async function send(client, who, functionName, args) {
  console.log(`\n[${who}] ${functionName}(${args.map((a) => JSON.stringify(a).slice(0, 90)).join(", ")})`);
  const started = Date.now();
  const hash = await withRetry(
    () => client.writeContract({ address: ADDRESS, functionName, args, value: 0n }),
    "write"
  );
  console.log(`  tx ${hash}`);
  const receipt = await withRetry(
    () => client.waitForTransactionReceipt({ hash, status: "FINALIZED", retries: 200, interval: 3000 }),
    "receipt"
  );
  const elapsed = Math.round((Date.now() - started) / 1000);
  const reverted = refused(receipt);
  console.log(`  ${receipt.status_name || receipt.status}  (${reverted ? "refused by the contract" : "executed"}, ${elapsed}s)`);
  return { hash, refused: reverted, elapsed };
}

async function write(client, who, functionName, args) {
  const { hash, refused: reverted } = await send(client, who, functionName, args);
  if (reverted) throw new Error(`${functionName} failed inside the contract`);
  return hash;
}

const read = (functionName, args) =>
  withRetry(() => reader.readContract({ address: ADDRESS, functionName, args }), "read");

/** The three sources are real pages, and they are all different. */
const SOURCE_A = "https://attest-demo-dusky.vercel.app/";
const SOURCE_B = "https://attest-proof.vercel.app/";
const SOURCE_C = "https://attest-stale.vercel.app/";

const QUESTION =
  process.argv[4] ||
  "Does the served text contain the word DomainAttest?";

(async () => {
  const marketId = process.argv[3] || `quorum-${Date.now().toString(36)}`;
  const closesAt = Math.floor(Date.now() / 1000) + 240;

  console.log("contract", ADDRESS);
  console.log("market  ", marketId);
  console.log("owner   ", owner.address);
  console.log("yes     ", yesAcct.address);
  console.log("no      ", noAcct.address);
  console.log("closes  ", new Date(closesAt * 1000).toISOString());

  console.log("\n[owner] open_market with three distinct sources");
  await write(ownerClient, "owner", "open_market", [
    marketId,
    QUESTION,
    SOURCE_A,
    SOURCE_B,
    SOURCE_C,
    closesAt,
  ]);

  let market = await read("get_market", [marketId]);
  console.log(`  state ${market.state} | sources ${market.sources.length} | pool ${market.pool}`);
  if (market.sources.length !== 3) throw new Error("the market did not store three sources");
  if (new Set(market.sources).size !== 3) throw new Error("the market stored duplicate sources");

  console.log("\n[credits] both accounts start from the contract's ledger");
  for (const [label, acct] of [["yes", yesAcct], ["no", noAcct]]) {
    console.log(`  ${label} ${acct.address} balance ${await read("balance_of", [acct.address])}`);
  }

  console.log("\n[buyers] opposite sides of the same question");
  await write(yesClient, "yes", "buy", [marketId, "yes", 40]);
  await write(noClient, "no", "buy", [marketId, "no", 25]);

  market = await read("get_market", [marketId]);
  console.log(
    `  pool ${market.pool} | yes ${market.yes_total} | no ${market.no_total} | buyers ${market.buyers}`
  );
  if (Number(market.pool) !== Number(market.yes_total) + Number(market.no_total)) {
    throw new Error("the pool does not equal the two sides");
  }
  console.log(`  yes balance ${await read("balance_of", [yesAcct.address])}`);
  console.log(`  no  balance ${await read("balance_of", [noAcct.address])}`);

  const waitMs = Math.max(0, (closesAt + 12) * 1000 - Date.now());
  console.log(`\n[time] waiting ${Math.round(waitMs / 1000)}s for closes_at`);
  await sleep(waitMs);

  console.log("\n[validators] read the three sources and vote (this is the consensus round)");
  const started = Date.now();
  let resolvedOnce = false;
  for (let attempt = 1; attempt <= 6 && !resolvedOnce; attempt++) {
    const { refused: reverted } = await send(ownerClient, "owner", "resolve", [marketId]);
    if (!reverted) {
      resolvedOnce = true;
      break;
    }
    market = await read("get_market", [marketId]);
    if (market.state !== "open") {
      resolvedOnce = true;
      break;
    }
    console.log("  the market was not closed yet by the chain clock, waiting 15s");
    await sleep(15000);
  }
  if (!resolvedOnce) throw new Error("resolve never succeeded");
  console.log(`  resolve took ${Math.round((Date.now() - started) / 1000)}s`);

  market = await read("get_market", [marketId]);
  console.log(`\n[result] state ${market.state} | outcome ${market.outcome} | pool ${market.pool}`);
  console.log("  votes, one per source, in source order:");
  market.votes.forEach((v, i) => {
    const quote = v.quote ? `"${v.quote.slice(0, 90)}"` : "(no quote)";
    console.log(`    ${i + 1}. ${v.vote.padEnd(8)} ${v.source}`);
    console.log(`       ${quote}`);
  });
  const votes = market.votes.map((v) => v.vote);
  const yesVotes = votes.filter((v) => v === "yes").length;
  const noVotes = votes.filter((v) => v === "no").length;
  console.log(`  counted: ${yesVotes} yes, ${noVotes} no, ${3 - yesVotes - noVotes} unclear`);
  const expected = yesVotes >= 2 ? "yes" : noVotes >= 2 ? "no" : "void";
  console.log(`  the contract's rule says ${expected}, the market says ${market.outcome}`);
  if (expected !== market.outcome) throw new Error("the stored outcome does not follow the votes");

  console.log("\n[claim] each buyer claims once");
  const claimed = {};
  for (const [label, client, acct, paid] of [
    ["yes", yesClient, yesAcct, 40],
    ["no", noClient, noAcct, 25],
  ]) {
    const { refused: reverted } = await send(client, label, "claim", [marketId]);
    claimed[label] = !reverted;
    const balance = await read("balance_of", [acct.address]);
    if (reverted) {
      console.log(`  ${label} had nothing to claim (it did not hold the winning side)`);
      // Nobody else bid, so a losing buyer is refunded nothing beyond its stake.
      if (market.outcome !== "void" && Number(balance) !== 100 - paid) {
        throw new Error(`${label} lost but its balance is ${balance}, not ${100 - paid}`);
      }
      continue;
    }
    const pos = await read("positions_of", [marketId, acct.address]);
    console.log(`  ${label} position: yes ${pos.yes} | no ${pos.no} | paid ${pos.paid} | claimed ${pos.claimed}`);
    console.log(`  ${label} balance after claiming: ${balance}`);
    // One holder per side, so the winning buyer takes the whole pool.
    if (market.outcome === label && Number(balance) !== 100 - paid + Number(market.pool)) {
      throw new Error(`${label} won but its balance is ${balance}, not ${100 - paid + Number(market.pool)}`);
    }
  }

  console.log("\n[guard] a second claim is refused");
  if (claimed.yes) {
    const again = await send(yesClient, "yes", "claim", [marketId]);
    console.log(`  a second claim was refused: ${again.refused}`);
    if (!again.refused) throw new Error("claiming twice was allowed");
  } else {
    console.log("  skipped: the yes account had nothing to claim this round");
  }

  console.log("\nstats");
  console.log(JSON.stringify(await read("stats", []), null, 2));
  console.log(
    "\nRESULT: three sources read by consensus, votes stored in source order, the outcome counted by the contract, the pool paid out by arithmetic"
  );
})().catch((e) => {
  console.error("\nFAILED:", e.message);
  if (e.cause) console.error(String(e.cause).slice(0, 900));
  process.exit(1);
});
