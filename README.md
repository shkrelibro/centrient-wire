# centrient-wire

Standalone monitoring wire for the Centrient credit. Deliberately separate from the generic
newsflow engine: its own acquisition, its own schedule, its own exports, its own failure domain.

Layers: (1) deterministic acquisition — multilingual Google/Bing News RSS sweep per
config/universe.yaml, plus price scrapers (ChemicalBook, PharmaCompass, Eastmoney broker PDFs)
and the monthly trade mirror (Comtrade; Comext experimental); (2) exports —
docs/centrient_news.json (36h rolling window, per-query counts, deterministic trip-wire tags)
and docs/centrient_data.json (series with latest/prev/delta/history, per-feed statuses,
thresholds, guardrails); (3) judgement — a Claude scheduled task (TASK_PROMPT.md) reads the two
JSONs daily at 06:15 London and updates one standing brief artifact.

No model calls in acquisition; recall is auditable via counts_this_run. Failures are visible
statuses, never silent gaps. See UPLOAD_GUIDE.md for setup.
