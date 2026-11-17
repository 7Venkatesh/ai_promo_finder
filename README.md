# AI Promo Finder

A small, repeatable prototype for finding and ranking 20–60 second promotional moments in timestamped microdrama screenplays.

## What it does
1. Accepts one or many `.txt` screenplay files.
2. Extracts episode metadata and timestamped screenplay events.
3. Generates continuous 20, 30, 40, 50 and 60-second candidate windows.
4. Uses Gemini (optional) to evaluate hook, cliffhanger, emotional impact and standalone clarity.
5. Validates duration by construction and exports ranked results as JSON or CSV.

## Setup
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env`, add your Gemini API key, then load the variables into your shell.
For Windows PowerShell:
```powershell
$env:GEMINI_API_KEY="YOUR_KEY"
$env:GEMINI_MODEL="gemini-2.5-flash"
```

Run:
```bash
streamlit run app.py
```

## Important limitations
- The current parser is designed around the timestamp pattern in the supplied Episode 1 TXT file. Test it against other episodes before processing the full pack.
- The first version generates continuous candidate windows. It does not yet combine non-contiguous moments from multiple episodes.
- The heuristic ranking is only a fallback and must not be presented as validated AI quality.
- Screenplay timestamps are treated as the source of truth. The prototype does not inspect rendered video, so a human should verify visual continuity and final edit points.
- LLM scores are subjective prioritization signals, not measured ad performance.

## Evaluation
Manually review a sample of candidates. Record false positives, missed promos, timestamp errors and context problems. Use those findings to refine the prompt and scoring weights.
