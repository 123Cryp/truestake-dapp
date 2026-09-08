![License](https://img.shields.io/badge/license-MIT-blue)
![GenLayer](https://img.shields.io/badge/GenLayer-genlayer--js-3E9B4F)
![Tests](https://img.shields.io/badge/tests-197%2F197%20passing-3E9B4F)

# TrueStake

A two-party, address-bound, deadline-gated, multi-source **sports score**
settlement Intelligent Contract for GenLayer that also **custodies both
parties' GEN stakes and pays the winner automatically, on-chain** — plus a
minimal no-build-step frontend that talks to it directly.

TrueStake builds on a previously-reviewed settlement core (party binding,
deadline-gated resolution, mandatory multi-source corroboration with a
quality-gated locked voting set, and a documented quorum rule) without
changing any of it, and adds a completely new escrow/payout layer on top:
the settlement decision no longer just gets recorded, it actually **moves
funds** — both stakes are escrowed by the contract itself and paid out (or
refunded) automatically depending on how the agreement resolves.

**Contract:** deploy `contract.py` via GenLayer Studio (or the CLI) and
point `index.html`'s `CONTRACT_ADDRESS` at the resulting address — see
[Deploying](#deploying) below.

---

## Repository layout

```
contract.py     the Intelligent Contract (GenLayer / GenVM, Python)
index.html      the entire frontend — HTML/CSS/JS, no build step
tests/          contract.py's offline test suite (197/197 passing)
README.md
LICENSE
```

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

`pytest` also works with these same files (they're written as plain
`unittest.TestCase` classes, which `pytest` auto-discovers) if you have it
installed; the command above uses the standard-library `unittest` runner
instead so the suite needs no installation step at all.

---

## The settlement trust model (unchanged from the prior design)

### 1. Party binding
`party_a` and `party_b` are never free-text names. `party_a` is always
whoever calls `create_agreement` (`gl.message.sender_address`); `party_b`
is an on-chain address supplied at creation time, and that *exact* address
must itself call `accept_agreement` before the agreement becomes binding
(`status` moves from `pending_acceptance` to `open`). Both sides are
therefore cryptographically tied to real wallets that actually signed a
transaction — never to a string either side could have typed on behalf of
someone else.

### 2. Resolution timing / deadline
Every agreement carries a `resolution_deadline` (an ISO-8601 UTC
timestamp) fixed at creation time — the expected full-time whistle — at
least **2 hours** and at most **7 days** out. `resolve_agreement` cannot
be called before that deadline (so nobody can race to resolve before the
match has actually finished) and cannot be called after
`resolution_deadline + 24 hours` (a deliberately short, single-day window:
sports results are reported live and go stale in the news cycle far
faster than a forex rate does). Once that window closes unresolved,
anyone can permissionlessly call `expire_agreement` — which also refunds
whichever stake(s) are still escrowed; see below.

### 3. Mandatory multi-source corroboration, a locked voting set, and one documented quorum rule
`required_source_domains` is not optional. Every agreement must commit at
least **2** distinct, reputable, allowlisted sports-data domains at
creation time (from `REPUTABLE_SPORTS_DOMAINS` — ESPN, BBC, Sky Sports,
Reuters, AP, Goal.com, FIFA.com, and the major US league sites, among
others), and `resolve_agreement` only succeeds once evidence from *all*
of those committed domains has been fetched, classified, and found to
agree. A single caller-chosen web page can never decide a settlement —
and, since a real verdict now also triggers a real payout, it can never
move money either.

Two further controls close what a purely domain-level commitment leaves
open:

- **Locked voting set, gated on quality.** The `source_urls` submitted on
  the first `resolve_agreement` attempt are only locked into
  `locked_source_urls` once that attempt actually clears every quality
  check (fetched cleanly, correct fixture, final score, no duplicate
  domains). An attempt that fails validation leaves `locked_source_urls`
  untouched, so a later, better-evidenced attempt can still choose its own
  URLs. Once a set *has* locked, every later attempt — resolved or not —
  must resubmit that identical set (order-independent,
  case/whitespace-normalized) or is rejected before any fetch. Locking
  only on a passing attempt (rather than on the first attempt
  unconditionally) is what stops a caller from permanently pinning
  irrelevant allowlisted pages before they've proven usable.
- **One documented quorum rule.** A verdict requires at least
  `MIN_INDEPENDENT_SOURCES` (2) eligible, independent sources, and the
  winning category must strictly outnumber both other categories. Any
  tie, or too few eligible sources, is `Indeterminate`. Dissenting
  sources can prevent a verdict but can never manufacture one alone —
  see `tests/test_aggregation.py` for explicit dissenting-source cases
  (a lone dissenter among three, a two-way split among four, a three-way
  tie).

---

## Escrow / stake model (new in TrueStake)

Both parties' GEN stakes are held by **this contract itself** — never an
external escrow, a multisig, or an off-chain custodian.

1. **Funding is part of the binding act, not a separate step.**
   `create_agreement` is `@gl.public.write.payable`: whatever GEN value
   party_a sends *with* that call becomes `stake_amount` — there is no
   separate amount parameter that could drift out of sync with what was
   actually sent. `accept_agreement` is likewise payable and requires
   party_b to send **exactly** that same `stake_amount`; an agreement only
   becomes `"open"` once both stakes are actually escrowed on-chain, not
   merely promised.
2. **Automatic, permissionless payout on a real verdict.** The instant
   `resolve_agreement`'s multi-source pipeline produces a non-`unresolved`
   winner, the full pot (both stakes) is credited to that winner — nobody
   has to separately "release" funds, and the resolver (who may be neither
   party, since resolution is permissionless) never touches the money.
3. **Every non-resolving outcome has an explicit refund path.** A contract
   that only pays out on a clean win can trap funds otherwise, so every
   other terminal state refunds accordingly: `cancel_agreement` (before
   acceptance) refunds party_a; `expire_agreement` on a never-accepted
   agreement refunds party_a; `expire_agreement` on an `open` agreement
   whose resolution window closed unresolved refunds **both** parties
   equally. No state transition neither pays out nor refunds.
4. **Credit-then-withdraw (pull payment), never push-on-resolve.** A
   payout or refund never itself sends GEN — it only credits an internal
   `pending_withdrawals[address]` ledger entry. A separate,
   unprivileged `withdraw()` — callable by anyone, for their own credited
   balance only — is what actually moves GEN out, following
   checks-effects-interactions: it zeroes the caller's credited balance
   *first*, then performs the external transfer. This means one address
   that reverts on receiving value (or a slow external message) can never
   jam the shared settlement pipeline for the other party, and
   `withdraw()` cannot be re-entered to drain more than it was credited.

See `contract.py`'s class docstring for the complete write-up, including
why stakes are fixed at creation (no top-up/partial-withdraw), and why
`pending_withdrawals` is additive per address rather than per agreement
(an address owed GEN from more than one agreement gets it all in a single
`withdraw()` call).

---

## How resolution works

1. `resolve_agreement(agreement_id, source_urls)` is called with 2–6
   candidate URLs, after the deadline and before the resolution window
   closes.
2. Every domain (and any pinned path) committed at creation time must be
   matched by the submitted URLs, or the call is rejected before any
   network access happens.
3. Inside a single `gl.eq_principle.prompt_comparative` closure (so every
   validator runs the identical logic and only the *categorical* result
   needs to match, not exact wording):
   - each URL is fetched with `gl.nondet.web.render`;
   - fetched content is deterministically classified as usable, empty, or
     malformed;
   - usable content is sent to `gl.nondet.exec_prompt` with a hardened
     prompt asking four fixed-vocabulary questions: does this source
     cover the exact requested fixture (`TEAM_MATCH`), is the score
     clearly the FINAL result (`FRESHNESS`), what is the final score
     (`SCORE`, as `home-away`), and how does the total compare to the
     threshold (`COMPARISON`);
   - the contract — never the model — deterministically parses `SCORE`
     and computes the authoritative Above/Below/Equal comparison itself;
     the model's self-reported `COMPARISON` is used only as a
     self-consistency check;
   - sources are aggregated: a source only counts as independent evidence
     if it fetched cleanly, isn't a duplicate domain, is on the
     allowlist, and passed every quality check;
   - a verdict (`Above` / `Below` / `Equal` / `Indeterminate`) requires at
     least 2 independent agreeing sources, or the match is
     `Indeterminate` and the agreement stays open for a retry.
4. The winner is derived from the agreement's `comparison` field
   (`above`/`below`) crossed with the `final_verdict` — `Equal` and
   `Indeterminate` never resolve the agreement (or credit anyone) in
   either party's favor.
5. On a real winner, the full pot is credited to them (see
   [Escrow / stake model](#escrow--stake-model-new-in-truestake) above);
   they then call `withdraw()` whenever they like to actually receive it.

## v1 scope

The settlement metric is **total combined goals/points** scored by both
teams, compared to a fixed threshold (over/under style — e.g. "over 2.5
goals"). Margin-of-victory markets ("Team A wins by 2+") are intentionally
out of scope for v1; stakes are fixed at whatever party_a sends at
creation (no top-up, partial withdrawal, or renegotiation mid-agreement).
See the class docstring in `contract.py` for the full, exact list of
disclosed limitations.

---

## Frontend

`index.html` is a single, dependency-free file (loads `genlayer-js` from
`esm.sh` at runtime) with panels for every public method: create (with a
GEN stake field), accept/cancel (with a "load exact amount from
agreement" helper so party_b never has to guess or retype the stake by
hand), resolve, expire, read (with a final-verdict hero, escrow fields,
and a per-source evidence table), check role, total agreements, and a
dedicated withdraw/balances panel (`get_pending_withdrawal`, `withdraw`,
`get_contract_balance`). Every dynamic value on the page is written with
`textContent` / `document.createElement` only — `innerHTML` is never used
anywhere in the file, so no value returned from the contract or fetched
off-chain can ever be interpreted as markup. GEN↔wei conversion is done
with `BigInt` throughout, never floating-point, since wei amounts need
exact integer precision.

`CONTRACT_ADDRESS` is a placeholder — set it to your deployed contract's
address before use (see [Deploying](#deploying)).

---

## Test suite

197 offline `unittest` tests across five files, all passing:

- `test_party_binding_and_timing.py` — create/accept/cancel binding rules,
  deadline lead-time bounds, the 24h resolution window, `expire_agreement`,
  and the view methods (`get_role`, `total_agreements`).
- `test_teams_and_score_parsing.py` — fixture ("Team A vs Team B")
  validation, domain extraction and the domain-allowlist round-trip
  guarantee, endpoint (domain+path) parsing, and the two numeric parsers
  (`_parse_score` for "home-away" final scores, `_parse_plain_number` for
  the threshold).
- `test_aggregation.py` — the deterministic verdict-aggregation logic in
  isolation, plus the winner-assignment mapping.
- `test_end_to_end.py` — full `create → accept → resolve` flows with
  `gl.nondet.web.render` / `gl.nondet.exec_prompt` mocked, covering the
  happy path, every `quality_flag` (team mismatch, not-final score,
  score-unparseable, comparison mismatch), fetch failures, retry-after-
  `Indeterminate`, and every resolution guardrail (early/late calls,
  wrong source counts, missing committed domains/paths, double-resolve).
- `test_escrow_and_stakes.py` — **new**: funding correctness on create/
  accept (including rejecting zero, below-minimum, underpaid, and
  overpaid stakes), refunds on cancel and on both `expire_agreement`
  branches (party_a-only vs. both-parties-equally), automatic payout
  crediting on a real verdict (mocked evidence), `withdraw()` including
  double-withdraw rejection and additive credit across multiple
  agreements, and access control on every money-affecting method.

All tests use the same offline `genlayer` SDK stub pattern (see
`tests/genlayer_stub/`): storage types behave like plain dicts/lists,
`gl.message.sender_address` and `gl.message.value` are settable per test
to simulate different callers and payable calls, `gl.evm.contract_interface`
/ `emit_transfer` are stubbed with an inspectable transfer ledger, and
`gl.nondet.web.render` / `gl.nondet.exec_prompt` are monkeypatched per test
case rather than hitting the network or a real model. This does not
simulate real multi-validator consensus or a real EVM/ghost-contract
balance layer — that requires GenLayer Studio or testnet, where this
contract has also been manually verified end-to-end (funding, exact-match
enforcement, cancel/expire refunds, quality-gated resolve with real match
data, automatic payout crediting, and real `withdraw()` transfers to an
EOA wallet).

---

## Deploying

1. Deploy `contract.py` via GenLayer Studio (or the CLI) to get a contract
   address.
2. Set `CONTRACT_ADDRESS` in `index.html` to that address.
3. Push this repository to GitHub and enable GitHub Pages (or host
   `index.html` anywhere static) for the frontend.
4. Submit the repository + release to the GenLayer Portal.

---

## Known limitations (disclosed, not hidden)

See the `contract.py` class docstring's "v1 SCOPE / KNOWN LIMITATIONS"
section for the full, exact list — summarized: total-goals-only settlement
metric (no margin markets yet), stakes fixed at creation with no top-up/
partial-withdrawal, `withdraw()` always sends a caller's *entire* credited
balance in one call, and a domain-only reputable allowlist (paths are
pinned via `required_source_domains`, not baked into the allowlist).

---

## License

MIT — see `LICENSE`.
