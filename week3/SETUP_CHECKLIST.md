# Week 3 Setup Checklist

## Before Starting

- [ ] Read READING.md (context on data quality)
- [ ] Install dependencies: `pip install pandas numpy pytest pyarrow`
- [ ] Verify data files are downloaded: `ls -lh week3/data/*.parquet` (should show MB, not bytes)

## Part 1: Identify Issues

- [ ] Load baseline and corrupted data into Python
- [ ] Explore both datasets - compare:
  - Row counts and date ranges
  - Null rates per column
  - Value ranges (min/max)
  - Data types
  - Unique values for categorical fields
  
- [ ] Document at least 2 distinct issues found:
  - [ ] Issue 1: __________ (affected rows: ____)
  - [ ] Issue 2: __________ (affected rows: ____)

- [ ] For each issue, note:
  - What is wrong (specific problem)
  - How many rows affected
  - Why it breaks the model/API
  - Root cause hypothesis

## Part 2: Build Validation

- [ ] Create `week3/validation/check_data_quality.py`
  - Write functions to detect each of your 2 or more issues
  - Return structured results (which rows/columns failed)
  
- [ ] Test your validation against both datasets:
  - Baseline should pass (or mostly pass)
  - Corrupted should fail on your identified issues

- [ ] Add a validation workflow:
  - Open `.github/workflows/validate-data.yml`, choose your cron frequency, and update the run step to call your validation code
  - Push and verify the workflow runs in GitHub Actions → Actions tab

## Part 3: Graceful Degradation

- [ ] Copy `week2/backend/data.py` to `week3/backend/data.py`
- [ ] Add validation calls to data loading
- [ ] Implement fallbacks for each issue type
- [ ] Add logging when degradation happens
- [ ] Test that API still runs with corrupted data

## Part 4: Write Tests

- [ ] Create `week3/validation/test_data_quality.py`
- [ ] Write tests that:
  - [ ] Pass for clean (baseline) data
  - [ ] Fail for corrupted data
  - [ ] Test each issue type individually
  - [ ] Verify graceful degradation

- [ ] Run tests: `python -m pytest week3/validation/test_data_quality.py -v`

## Part 5: Combined Report

Write a single report. Below is a suggested structure, but you are free to change this:
- [ ] Part 1: Issues found (what, how many rows, why it matters)
- [ ] Part 2: Validation approach + graceful degradation (how you fix each issue)
- [ ] Part 3: Strategy (where validation runs, monitoring, trade-offs)

## Deliverables Checklist

- [ ] `week3/validation/check_data_quality.py` - validation code
- [ ] `week3/validation/test_data_quality.py` - tests
- [ ] `week3/backend/data.py` - graceful degradation
- [ ] `.github/workflows/validate-data.yml` - scheduled validation workflow
- [ ] `REPORT.md` or `REPORT.pdf` with a logical structure and enough explanation of Issues identified, Validation approach + fallbacks, and Strategy & trade-offs
