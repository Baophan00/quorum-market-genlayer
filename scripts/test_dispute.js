const fs = require("fs");
const { createClient, createAccount } = require("genlayer-js");
const { studionet } = require("genlayer-js/chains");
const KEYFILE = "./scripts/.e2e-keys.json";
const k = JSON.parse(fs.readFileSync(KEYFILE, "utf8"));
const client = createClient({ chain: studionet, account: createAccount(k.owner) });
const CONTRACT = "0x40258BEC230E312233a33F926a24bb3b729A1617";
const MARKET = "dispute-test-2";
(async () => {
  console.log("=== Testing dispute feature ===");
  // Open market closing in 60s
  const now = Math.floor(Date.now() / 1000);
  const closesAt = now + 120;
  const hash1 = await client.writeContract({ address: CONTRACT, functionName: "open_market", args: [MARKET, "Does the text contain DomainAttest?", "https://attest-demo-dusky.vercel.app/", "https://attest-proof.vercel.app/", "https://attest-stale.vercel.app/", closesAt] });
  console.log("open tx:", hash1);
  await client.waitForTransactionReceipt({ hash: hash1, status: "FINALIZED", retries: 100, interval: 3000 });
  console.log("Market opened");

  // Buy yes
  const hash2 = await client.writeContract({ address: CONTRACT, functionName: "buy", args: [MARKET, "yes", 10] });
  await client.waitForTransactionReceipt({ hash: hash2, status: "FINALIZED", retries: 100, interval: 3000 });
  console.log("Bought yes 10");

  // Wait for close
  console.log("Waiting 130s for market to close...");
  await new Promise(r => setTimeout(r, 130000));

  // Resolve
  const hash3 = await client.writeContract({ address: CONTRACT, functionName: "resolve", args: [MARKET] });
  console.log("resolve tx:", hash3);
  const r3 = await client.waitForTransactionReceipt({ hash: hash3, status: "FINALIZED", retries: 200, interval: 3000 });
  console.log("resolve result:", r3.result, "status:", r3.status);
  const market = await client.readContract({ address: CONTRACT, functionName: "get_market", args: [MARKET] });
  console.log("outcome:", market.outcome, "state:", market.state);

  // Dispute
  console.log("Filing dispute...");
  const hash4 = await client.writeContract({ address: CONTRACT, functionName: "dispute", args: [MARKET, "I believe the sources were misread"] });
  console.log("dispute tx:", hash4);
  const r4 = await client.waitForTransactionReceipt({ hash: hash4, status: "FINALIZED", retries: 200, interval: 3000 });
  console.log("dispute result:", r4.result, "status:", r4.status);
  if (r4.result === 7 || r4.status === 7) {
    const m2 = await client.readContract({ address: CONTRACT, functionName: "get_market", args: [MARKET] });
    console.log("After dispute - outcome:", m2.outcome, "state:", m2.state, "original:", m2.original_outcome, "disputed_by:", m2.disputed_by);
  } else {
    const lr = (r4.consensus_data?.leader_receipt || [{}])[0];
    console.log("exec_result:", lr.execution_result);
    console.log("stderr:", (lr.genvm_result?.stderr || "").slice(0, 500));
  }
})().catch(e => { console.error("FAIL:", e.message); if (e.cause) console.error(String(e.cause).slice(0, 500)); process.exit(1); });
