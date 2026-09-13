# Upload guide — centrient-wire (standalone; no git needed, all via the GitHub website)

One-time setup, ~10 minutes:

1. Create the repo: github.com > New repository > name "centrient-wire", PUBLIC (unlimited Actions
   minutes; private also works, this cadence fits the free tier), initialise empty (no README).
2. Upload the pack: Add file > Upload files; drag the unzipped folders in, keeping paths exactly:
   README.md, requirements.txt, TASK_PROMPT.md, UPLOAD_GUIDE.md,
   config/universe.yaml, wire/news.py, wire/watchers.py, wire/feeds.py, wire/run.py,
   .github/workflows/wire.yml
   (If drag-and-drop flattens folders, use the filename box trick: Add file > Create new file and
   type e.g. wire/run.py, pasting the content; the slash creates the folder.)
3. Actions tab: the "centrient-wire" workflow appears after the first commit. Run workflow with
   force_data, force_weekly and force_monthly all true to seed everything. Expect the Comext leg
   to report an error status at first; that is a visible gap, not a failure.
4. Optional secret: Settings > Secrets and variables > Actions > New repository secret,
   COMTRADE_KEY, from a free comtradeapi.un.org registration (without it the capped preview is used).
5. Verify the exports:
   https://raw.githubusercontent.com/shkrelibro/centrient-wire/main/docs/centrient_news.json
   https://raw.githubusercontent.com/shkrelibro/centrient-wire/main/docs/centrient_data.json
6. Create the scheduled task the same way as the existing brief triggers: name "Centrient daily
   wire", daily 06:15 Europe/London (05:15 UTC in summer; shift with the clocks like the other
   triggers), instructions = TASK_PROMPT.md, web access on. The first run creates the standing
   artifact; keep its link.

Separation, for the record: nothing here reads or writes shkrelibro/newsflow-engine. The engine's
own generic Centrient coverage (wired 6 Sep) keeps running untouched; harmless overlap, two
independent failure domains. The two earlier engine-patch files (config/names/centrient.yaml,
lukang.yaml) are superseded by this repo and should NOT be uploaded to the engine; the wire-2
terms, policy watchers and Lukang all live in config/universe.yaml here instead.

What stays manual, on purpose: Healthoo (paywalled; the Monday broker PDFs re-print it), GACC
8-digit (captcha + foreign-IP; order HS6 294110/294190 from chinadata.live from $9.90 when hard
volumes are needed), India Tradestat (JS portal; two minutes by hand monthly, or a Playwright leg
later).

Tuning: everything sweepable is in config/universe.yaml (queries, languages, trip-wire regex,
guardrails); edit that one file on the website and the next hourly run picks it up.

v2 one-time verifications (10 minutes, after the seed run):
- Blanket editions flagged 'verify' (AT, CH, IE): open centrient_news.json and confirm items from
  those routes are in the expected language; a silent fallback only duplicates de/en items, dedupe
  absorbs it, but confirm once.
- Boerse Frankfurt bond leg: if bond_quotes reports empty, the header salt rotated or the notes do
  not quote on XFRA; use Boerse Stuttgart (product-finder by ISIN) or tradegate.de/orderbuch.php?isin=
  manually and leave the status red, it is a stated gap.
- DGFT watcher: if reg_dgft never changes after two weeks, its JS shell is opaque; rely on the
  policy_mip news query and TaxGuru mirror instead.
- Regulator baselines: the first run only sets page hashes ('baseline set'); changes print from the
  second run onward.
