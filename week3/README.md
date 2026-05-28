# Week 3 — Data Quality Validation in CI/CD Pipeline

## Before You Start

1. Read [READING.md](READING.md) - data quality failures, validation strategies, graceful degradation

2. Have Week 2 deployed and running
  - Re-deploy Week 2 API: Since you deleted your GKE cluster at the end of Week 2, you'll need to get the API running again before starting Week 3. You have two options:
    - Local: Run backend/main.py directly with uvicorn — no GCP needed, fastest option for this week's work
    - GKE: Recreate your cluster using the same commands from Week 2 Part 1. Your GCS bucket and data files are still there, so setup will be faster than the first time.

3. Install: `pip install pandas numpy pytest pyarrow`

## Assignment

Add automated data quality validation to your Week 2 deployed API. New upstream data has issues.

**Your tasks:**
1. Identify data quality issues in the corrupted dataset
2. Write validation checks to detect them
3. Add validation workflow to GitHub Actions (runs on schedule)
4. **YOU DECIDE:** How often to validate? (15min? 1hr? 1day? At startup?)
5. Make the API gracefully degrade on bad data (log issues, don't crash)

**Deliverables:**
- `.github/workflows/validate-data.yml` - new validation workflow
- `validation/check_data_quality.py` - validation functions
- Updated `data.py` - graceful degradation
- Tests - verify validation works
- A **Report:** which claarifies the following (and any other points of your choosing):
  - Issues found + impact
  - Validation schedule choice + justification
  - Graceful degradation strategy

---

## What You Have

```
week3/
├── .github/workflows/
│   └── validate-data.yml     (TEMPLATE: Fill in TODOs - decide frequency, implement validation)
├── backend/                  ← Copy from week2, you modify data.py
│   ├── main.py
│   ├── data.py               (MODIFY: add validation + fallbacks)
│   └── requirements.txt
├── data/
│   └── demand_enriched_corrupted.parquet  (clean data before Jan 16, 2026; dirty data on and after Jan 16, 2026)
├── validation/
│   ├── __init__.py          (makes validation/ a Python package)
│   ├── check_data_quality_template.py    (TEMPLATE: Implement validation checks)
│   └── test_data_quality_template.py     (TEMPLATE: Write tests)
└── README.md (this file)
```

**All files are provided. All work happens in week3/.**

### GitHub Actions Workflow

You have a template `.github/workflows/validate-data.yml`. Fill in the TODOs:
1. Choose validation frequency (15min? 1hr? daily?)
2. Implement validation logic in the workflow step
3. Report validation results (fail if issues found)

---

## Setup: Pull from GitHub

Clone the course GitHub repository which has now removed LFS tracking. After cloning, verify the parquet files are downloaded correctly:

```bash
# Should show file sizes in MB, not bytes
ls -lh week3/data/*.parquet
```

If the files are missing or very small (a few hundred bytes), re-clone the repository. You should now receive all files, including datasets, directly without LFS pointers. 

**IMPORTANT: Do not upload these datasets to your own GitHub repo with solutions. It is not recommended to upload large files to GitHub.** While there is no explicit penalty for uploading these at the moment, your code will raise warnings if you try pushing these large files directly to GitHub.

---

## Part 1: Set Up CI/CD Workflow

Edit `.github/workflows/validate-data.yml`:
1. Choose your validation frequency in the `schedule` cron expression
   - `0 * * * *` = Every hour
   - `0 0 * * *` = Daily at midnight
   - `*/15 * * * *` = Every 15 minutes
2. Update the `run` step to call your validation code
3. Fill in other TODOs in the workflow

Then move to Part 2.

## Part 2: Identify Data Quality Issues

Load the parquet file provided this week (`week3/data/demand_enriched_corrupted.parquet`) and find at least 2 issues that would break the model.

To find the issues, you can start by exploring the new data. You can do this exploration in a notebook or a scratch script — it doesn't need to be part of your final submission. You can also choose to store this script in a sub-folder in your submission.

### Load Data

Load the parquet file with corrupted data during your exploration and inspect it to detect issues. Once you have found issues, you will write validation code to automate running checks for those issues. Below is suggested starter code for this, but feel free to modify this code snippet.

```python
import pandas as pd

CUTOFF = pd.Timestamp("2026-01-16")

df = pd.read_parquet('week3/data/demand_enriched_corrupted.parquet')
baseline = df[df['time_bucket'] < CUTOFF]   # clean historical window
corrupted = df[df['time_bucket'] >= CUTOFF]  # new potentially corrupted window

print(f"Baseline: {len(baseline)} rows")
print(f"Corrupted: {len(corrupted)} rows")
print(f"\nBaseline columns: {baseline.columns.tolist()}")
print(f"\nBaseline null rates:\n{baseline.isna().mean()}")
print(f"\nCorrupted null rates:\n{corrupted.isna().mean()}")
```

### What to Look For

Compare the baseline (pre-Jan 16) to the corrupted (post-Jan 16) window:

- **Missing values**: Increase in nulls in any column?
- **Outliers**: Values outside expected ranges? (e.g., trip_count > 10x normal)
- **Duplicates**: Rows repeated multiple times?
- **Distribution shifts**: Mean/std significantly different?
- **Schema changes**: New/missing columns?
- **Type issues**: Integer columns with floats? Strings instead of numbers?

### Document Each Issue

For each issue, note:
- **What**: Type of problem (nulls, outliers, duplicates, etc.)
- **Where**: Which zones/dates/times affected? How many rows?
- **Impact**: How does this break predictions? What's the output quality cost?
- **Root cause**: Why did this happen?

---

## Part 3: Write Validation Code

Based on the issues you found, write `validation/check_data_quality.py` with functions to detect each issue:

```python
def validate_data(df: pd.DataFrame, baseline_df: pd.DataFrame) -> dict:
    """Check data quality. Return {is_valid: bool, issues: list}"""
    issues = []
    # TODO: Check for each of your issues
    return {
        'is_valid': len(issues) == 0,
        'issues': issues
    }
```

This code will be called by your CI/CD workflow. Note: the existing file contains a suggested code structure; you are free to modify it.

---

## Part 4: Add Validation Workflow

Write `.github/workflows/validate-data.yml` to run validation on schedule. Note: the existing file contains a suggested code structure; you are free to modify it.

**YOU DECIDE:** How often should this run?

**Options:**
- **Every 15 minutes:** Catch issues immediately (higher cost, faster detection)
- **Every 30 minutes:** Balance between cost and responsiveness
- **Every 1 hour:** Good for daily operations
- **Every 1 day:** Cost-effective, checks overnight
- **At startup:** Only when pod starts (no ongoing monitoring)
- **Hybrid:** Startup + hourly checks

**Workflow pattern:**
```yaml
name: Data Quality Check

on:
  schedule:
    - cron: '0 * * * *'  # Every hour (YOU CHOOSE)
  push:
    branches: [main]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python -m validation.check_data_quality week3/data/
      - if: failure()
        run: |
          echo "Data validation failed - blocking deployment"
          # Alert ops, don't deploy
```

**Justification in report:** Why did you choose that frequency?

---

## Part 5: Implement Graceful Degradation

Modify `week3/backend/data.py` to run validation at startup and log any issues found. The API is already serving clean week 2 data — graceful degradation here means the API **never crashes** due to data quality issues, and any problems are **always visible** to operators through logs.

Add a function that:
1. Loads the corrupted parquet and runs your validation checks from `validation/check_data_quality.py`
2. Logs all issues found using Python's `logging` module
3. Does not crash or block the API from starting if validation fails

```python
import logging
from validation.check_data_quality import DataQualityValidator

logger = logging.getLogger(__name__)

CORRUPTED_DATA_PATH = Path(__file__).parent.parent / "data" / "demand_enriched_corrupted.parquet"

def check_and_log_data_quality():
    """
    Run validation on incoming data and log any issues found.
    The API continues running regardless of validation outcome.
    Call this at startup so operators are immediately aware of data problems.
    """
    try:
        df = pd.read_parquet(CORRUPTED_DATA_PATH)
        validator = DataQualityValidator()
        result = validator.validate(df)
        if not result['is_valid']:
            logger.warning(f"Data quality issues detected: {len(result['issues'])} issue(s) found.")
            for issue in result['issues']:
                logger.warning(f"  [{issue['severity'].upper()}] {issue['type']}: {issue['description']}")
        else:
            logger.info("Data quality check passed — no issues found.")
    except Exception as e:
        logger.error(f"Data quality check failed to run: {e}")
```

Call `check_and_log_data_quality()` at the bottom of `data.py` where the other startup functions are called (e.g. alongside `_load()` and `_load_model()`).

**Key:** The API must never crash due to bad data. Always log what was detected so operators can respond.

## Part 6: Write Tests

Write `validation/test_data_quality.py`:
- Baseline data should pass validation
- Corrupted data should fail (there are at least 4 issues, detect at least 2)
- Test each issue separately
- Test that API doesn't crash with bad data

Note: the existing file contains a suggested code structure; you are free to modify it.

## Part 7: Report

**Summary of Issues & Strategy**
- List at least 2 issues found in corrupted data (what, how many rows, impact)
- Your validation schedule choice (15min/hourly/daily?) + brief justification (cost vs detection speed)
- How API gracefully degrades on bad data (drop rows? fill nulls? fallback to baseline?)

---

## Common Mistakes

**Validation too strict:** Rejects valid edge cases. Balance sensitivity/specificity.

**Graceful degradation silent:** API returns wrong answers without logging. Always log what degraded.

**No segmentation:** Check null rates globally. Should check per-zone, per-hour, etc.

**Tests don't match issues:** Test files exist but don't actually check for the issues you identified.

---

## Grading

| Criterion | Weight |
|-----------|--------|
| At least 2 issues identified and documented | 30% |
| Validation code works correctly | 25% |
| Graceful degradation (API handles bad data) | 20% |
| Tests verify validation works | 15% |
| Report (clear and logical) | 10% |

---

## Due

End of Week 3 (see syllabus)
