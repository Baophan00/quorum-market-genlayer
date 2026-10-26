import { createClient, createAccount, generatePrivateKey } from "genlayer-js";
import { studionet } from "genlayer-js/chains";

const CONTRACT = "0x226AB8e4A914B7F8fACAe1f20cBc3C719669dA3C";
const EXPLORER = `https://explorer-studio.genlayer.com/address/${CONTRACT}`;
const KEY_SLOT = "quorummarket.keys";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

let logTimer;
function log(msg) {
  const box = $("log");
  box.textContent = msg;
  box.style.display = "block";
  clearTimeout(logTimer);
  logTimer = setTimeout(() => (box.style.display = "none"), 8000);
}

function loadKeys() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY_SLOT) || "[]");
    return Array.isArray(raw) ? raw.filter((k) => typeof k === "string" && k) : [];
  } catch {
    return [];
  }
}

let keys = loadKeys();
if (!keys.length) {
  keys = [generatePrivateKey(), generatePrivateKey(), generatePrivateKey()];
  localStorage.setItem(KEY_SLOT, JSON.stringify(keys));
}
let active = 0;
let account = createAccount(keys[0]);
let me = String(account.address);
let client = createClient({ chain: studionet, account });
const reader = createClient({ chain: studionet, account: createAccount(generatePrivateKey()) });

const short = (a) => (a ? `${String(a).slice(0, 6)}…${String(a).slice(-4)}` : "");

function useKey(i) {
  active = i;
  account = createAccount(keys[i]);
  me = String(account.address);
  client = createClient({ chain: studionet, account });
  paintKey();
  refresh();
}

function paintKey() {
  const sel = $("acting");
  sel.replaceChildren();
  keys.forEach((k, i) => {
    const o = el("option", "", `key ${i + 1} · ${short(String(createAccount(k).address))}`);
    o.value = String(i);
    sel.append(o);
  });
  sel.value = String(active);
  $("addr").textContent = short(me);
  $("walletchip").textContent = `key ${active + 1} · ${short(me)}`;
}

$("contractlink").textContent = short(CONTRACT);
$("contractlink").href = EXPLORER;
paintKey();
$("acting").onchange = (e) => useKey(Number(e.target.value) || 0);

$("genkey").onclick = () => {
  keys.push(generatePrivateKey());
  localStorage.setItem(KEY_SLOT, JSON.stringify(keys));
  useKey(keys.length - 1);
  log("new key added · 100 credits on first buy");
};

$("importkey").onclick = () => {
  const k = prompt("Private key (0x…)");
  if (!k) return;
  keys.push(k.trim());
  localStorage.setItem(KEY_SLOT, JSON.stringify(keys));
  useKey(keys.length - 1);
};

async function read(functionName, args) {
  return reader.readContract({ address: CONTRACT, functionName, args });
}

async function send(functionName, args) {
  log(`${functionName}: signing`);
  const hash = await client.writeContract({ address: CONTRACT, functionName, args, value: 0n });
  log(`${functionName}: ${hash.slice(0, 12)}… waiting for the round`);
  const receipt = await client.waitForTransactionReceipt({ hash, status: "FINALIZED", retries: 200, interval: 3000 });
  const leader = (receipt && receipt.consensus_data && receipt.consensus_data.leader_receipt) || [];
  if (String(leader[0] && leader[0].execution_result).toUpperCase() === "ERROR") {
    throw new Error("the contract refused this call");
  }
  log(`${functionName}: done`);
  await refresh();
  return hash;
}

function when(ts) {
  const n = Number(ts || 0);
  if (!n) return "—";
  return new Date(n * 1000).toLocaleString();
}

function left(ts) {
  const n = Number(ts || 0) * 1000 - Date.now();
  if (n <= 0) return "closed";
  const m = Math.floor(n / 60000);
  const s = Math.floor((n % 60000) / 1000);
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s left` : `${s}s left`;
}

function actionButton(label, cls, fn) {
  const b = el("button", `small ${cls}`, label);
  b.onclick = async () => {
    b.disabled = true;
    try {
      await fn();
    } catch (e) {
      log(String((e && e.message) || e).slice(0, 220));
    } finally {
      b.disabled = false;
    }
  };
  return b;
}

let cache = [];

function renderStats(stats, balance) {
  const host = $("stats");
  host.replaceChildren();
  const cells = [
    ["Markets", stats.markets || 0],
    ["Resolved", stats.resolved || 0],
    ["Void", stats.void || 0],
    ["Pool", stats.pool || 0],
  ];
  for (const [label, value] of cells) {
    const box = el("div", "stat");
    box.append(el("span", "", label));
    box.append(el("b", "num", String(value)));
    host.append(box);
  }
  if (balance !== null) $("balance").textContent = String(balance);
}

function renderVotes(market) {
  const votes = market.votes || [];
  if (!votes.length) return null;
  const box = el("div", "votes");
  votes.forEach((v, i) => {
    const card = el("div", `vote ${v.vote}`);
    card.append(el("b", "", `source ${"ABC"[i]} · ${v.vote}`));
    if (v.quote) card.append(el("q", "", v.quote.slice(0, 120)));
    box.append(card);
  });
  return box;
}

function renderMarket(market) {
  const card = el("div", "market");
  const head = el("div", "meta");
  head.append(el("span", `pill ${market.state}`, market.state));
  head.append(el("span", "pill", left(market.closes_at)));
  head.append(el("span", "pill", `${market.buyers} buyer${market.buyers === 1 ? "" : "s"}`));
  card.append(head);
  card.append(el("h3", "", market.question));

  const sources = el("div", "sources");
  (market.sources || []).forEach((url, i) => {
    const line = el("span");
    const a = el("a", "", `${"ABC"[i]} · ${url.replace(/^https?:\/\//, "")}`);
    a.href = url;
    a.target = "_blank";
    a.rel = "noreferrer";
    line.append(a);
    sources.append(line);
  });
  card.append(sources);

  const pool = Number(market.pool || 0);
  const yes = Number(market.yes_total || 0);
  const no = Number(market.no_total || 0);
  const bar = el("div", "bar");
  const y = el("i", "y");
  const n = el("i", "n");
  y.style.width = `${pool ? (yes / pool) * 100 : 50}%`;
  n.style.width = `${pool ? (no / pool) * 100 : 50}%`;
  bar.append(y, n);
  card.append(bar);
  card.append(el("div", "kv", `pool ${pool} · yes ${yes} · no ${no}`));

  const votes = renderVotes(market);
  if (votes) card.append(votes);

  if (market.state === "resolved" || market.state === "void") {
    const box = el("div", "outcome");
    if (market.state === "void") {
      box.append(el("b", "", "void · every buyer is refunded in full"));
    } else {
      box.append(el("b", "", `outcome: ${market.outcome}`));
    }
    box.append(document.createTextNode(`resolved ${when(market.resolved_at)}`));
    card.append(box);
  }

  const actions = el("div", "actions");
  const open = market.state === "open" && Number(market.closes_at) * 1000 > Date.now();
  if (open) {
    const amount = el("input");
    amount.type = "number";
    amount.min = "1";
    amount.value = "10";
    amount.style.width = "86px";
    actions.append(amount);
    actions.append(
      actionButton("Buy YES", "yes", () => send("buy", [market.id, "yes", Number(amount.value) || 0]))
    );
    actions.append(
      actionButton("Buy NO", "no", () => send("buy", [market.id, "no", Number(amount.value) || 0]))
    );
  }
  if (market.state === "open" && Number(market.closes_at) * 1000 <= Date.now()) {
    actions.append(actionButton("Resolve from the three sources", "primary", () => send("resolve", [market.id])));
  }
  if (market.state === "resolved" || market.state === "void") {
    actions.append(actionButton("Claim", "primary", () => send("claim", [market.id])));
  }
  if (actions.childNodes.length) card.append(actions);
  return card;
}

function showBanner(message) {
  const box = $("banner");
  box.replaceChildren();
  box.append(document.createTextNode(message));
  const retry = el("button", "", "Retry");
  retry.onclick = () => {
    box.style.display = "none";
    refresh();
  };
  box.append(retry);
  box.style.display = "flex";
}

const CACHE = "quorummarket.lastread";

function saveCache(markets, stats) {
  try {
    localStorage.setItem(CACHE, JSON.stringify({ at: Date.now(), markets, stats }));
  } catch {}
}

function loadCache() {
  try {
    const raw = JSON.parse(localStorage.getItem(CACHE) || "null");
    return raw && Array.isArray(raw.markets) ? raw : null;
  } catch {
    return null;
  }
}

function paintMarkets(markets, note) {
  cache = markets || [];
  const host = $("markets");
  host.replaceChildren();
  if (!cache.length) {
    const empty = el("div", "card");
    empty.append(el("div", "hint", "No market yet. Open one with three sources, then buy a side while it is open."));
    host.append(empty);
  }
  for (const market of cache) host.append(renderMarket(market));
  $("marketsub").textContent = note || `${cache.length} on this contract, newest first.`;
}

async function refresh() {
  try {
    const [markets, stats, balance] = await Promise.all([
      read("list_markets", [0, 30]),
      read("stats", []),
      read("balance_of", [me]),
    ]);
    $("banner").style.display = "none";
    saveCache(markets || [], stats || {});
    renderStats(stats || {}, balance);
    paintMarkets(markets);
  } catch (e) {
    const message = String((e && e.message) || e);
    const cached = loadCache();
    if (cached) {
      renderStats(cached.stats || {}, null);
      paintMarkets(cached.markets, `showing what the chain returned at ${new Date(cached.at).toLocaleTimeString()} — the live read is refused right now`);
    }
    if (/rate limit|failed to fetch|unknown rpc/i.test(message)) {
      showBanner(
        "The public Studio RPC is refusing reads right now (it allows 500 requests an hour), so this page is showing the last state it read. The markets and the payouts are on chain. Press Retry in a minute."
      );
    } else {
      showBanner(`Could not read the contract: ${message.slice(0, 140)}`);
    }
    log("read failed, see the banner above");
  }
}

$("open").onclick = async () => {
  const button = $("open");
  const question = $("q").value.trim();
  const sources = [$("s1").value.trim(), $("s2").value.trim(), $("s3").value.trim()];
  if (!question || sources.some((s) => !s)) {
    log("a question and three sources are required");
    return;
  }
  const minutes = Number($("closes").value) || 5;
  const closesAt = Math.floor(Date.now() / 1000) + minutes * 60;
  const id = `q-${Date.now().toString(36)}`;
  button.disabled = true;
  try {
    await send("open_market", [id, question, sources[0], sources[1], sources[2], closesAt]);
    $("q").value = "";
  } catch (e) {
    log(String((e && e.message) || e).slice(0, 220));
  } finally {
    button.disabled = false;
  }
};

setInterval(() => {
  for (const market of cache) {
    const card = [...document.querySelectorAll(".market")].find((c) =>
      c.querySelector("h3") && c.querySelector("h3").textContent === market.question
    );
    if (!card) continue;
    const pill = card.querySelector(".pill:nth-child(2)");
    if (pill && market.state === "open") pill.textContent = left(market.closes_at);
  }
}, 1000);

setInterval(() => {
  if (document.visibilityState === "visible") refresh();
}, 45000);

(async () => {
  const cached = loadCache();
  if (cached) {
    renderStats(cached.stats || {}, null);
    paintMarkets(cached.markets, `last read at ${new Date(cached.at).toLocaleTimeString()}`);
  }
  refresh();
})();
