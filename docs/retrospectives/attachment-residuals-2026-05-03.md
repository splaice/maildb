# Attachment Drain — Residuals Report (Post Tier-1/2/3 Phase 2 Drain)

**Date:** 2026-05-03
**Drains covered:** Phase 1 (300s extract-timeout, 3h 52m) + Phase 2 (900s extract-timeout, 8h 00m)
**Stack:** Tier 1 (Docling fallback + NUL sanitizer), Tier 2 (yield dashboard, MPS-discipline, tiny-image filter, smoke-marker, orphan detector), Tier 3 (per-run logs, --max-runtime ceiling)

## Executive summary

After the two-phase drain, **10,746 attachments are extracted, 790 are failed, 24,034 are skipped, 0 pending**. The 790 failed pool is dominated by one bucket — **681 mid-size PDFs / oversized images / large xlsx files that exceed the 900s extraction budget** (the "slow tail"). The remaining 109 are split across upstream surya MPS bugs (64), encrypted PDFs (25), 900s timeouts (13), and a handful of edge cases. The 24,034 skipped are intentionally unsupported content types — most of them outside Marker's scope by design (audio, calendar invites, archives, vcards), but some (csv, doc/xls legacy, octet-stream re-detection) are recoverable with modest engineering.

## Final state

| Status | Count | % of total |
|---|---:|---:|
| extracted | 10,746 | 30.2% |
| skipped | 24,034 | 67.6% |
| failed | **790** | **2.2%** |

The on-disk size of the 790 failed pool is **1,813 MB** (~2.3 MB/file average — most are PDFs).

---

## 1. The 790 failed pool

### Failure-class breakdown

| Bucket | Count | % | Recoverable? | Notes |
|---|---:|---:|---|---|
| **deferred-slow-tail** | **681** | **86.2%** | yes, with more compute | Set by us at end of Phase 2; see §2 |
| surya-mps-bug | 64 | 8.1% | upstream wait | unpatched call sites in surya |
| pdf-encrypted | 25 | 3.2% | no | password-protected PDFs |
| hard-timeout-900s | 13 | 1.6% | maybe (90 min budget) | tried twice, timed out at 900s |
| embed-failed | 3 | 0.4% | trivial retry | Ollama hiccup on 1 chunk |
| pptx-both-failed | 3 | 0.4% | format fix | Marker (no weasyprint) + Docling rejected |
| other (corrupt PNG) | 1 | 0.1% | no | unidentifiable image |

### Content-type distribution within failed

| Content type | Count | Notes |
|---|---:|---|
| application/pdf | 744 | 643 deferred + 60 mps-bug + 25 encrypted + 13 timeout + 2 pptx-fail + 1 embed |
| image/png | 15 | 12 deferred + 2 mps-bug + 1 corrupt |
| image/jpeg | 12 | 8 deferred + 2 mps-bug + 2 misc |
| xlsx | 17 | all deferred |
| text/plain | 2 | both embed-failed |
| pptx | 1 | both extractors failed |

---

## 2. Deferred slow-tail (681 rows) — deep dive

### Composition

| Type | Count | Median size |
|---|---:|---:|
| application/pdf | 643 | ~1 MB |
| image/png + jpeg + jpg | 21 | ~1.5 MB |
| xlsx | 17 | ~6.5 MB |

### PDF page-count distribution (643 deferred PDFs, 24,830 total pages, mean 38.6/doc)

```
  1-5     pages   131  ##########
  6-20    pages   256  ###################
  21-50   pages   172  #############
  51-100  pages    45  ###
  101-200 pages    12
  201-500 pages    22  #
  500+    pages     5
```

**Key insight:** 87% of deferred PDFs have ≤50 pages. They are *not* mostly long documents — Surya is structurally slow on certain page content (image-heavy/scanned), not just on volume.

### Bytes-per-page (proxy for scanned/image content)

Sampled 200 each from deferred and extracted PDFs:

| Set | p25 | p50 | p75 | p95 |
|---|---:|---:|---:|---:|
| deferred | 31,963 | 55,707 | **121,203** | **536,171** |
| extracted | 35,663 | 53,624 | 82,172 | 343,755 |

Tail of the deferred distribution is meaningfully heavier (p75 ~50% higher; p95 ~55% higher), consistent with a higher mix of scanned/image-heavy pages that drive Surya's per-page OCR cost up.

### Top page-heavy deferred PDFs (the long-doc minority)

| Pages | Size | Filename |
|---:|---:|---|
| 2,366 | 4.0 MB | Quick_Bill_Summary_11-1-2013.pdf |
| 754 | 1.5 MB | 2014-06-30 Inv 608371 $76,061.12.PDF |
| 569 | 1.1 MB | 2014-07-31 AISS Sterling Inv 612123 $56,797.43.pdf |
| 569 | 1.1 MB | 2014-07-31 AISS Sterling Inv 612123 $56,797.43.PDF (dup) |
| 568 | 1.1 MB | 2014-07-31 AISS Sterling Inv 612122 $57,344.99.PDF |
| 322 | 8.2 MB | 635386ACR.pdf |
| 322 | 8.2 MB | 635386ACL.pdf |
| 314 | 1.2 MB | Postmates - Series E - Stock and Warrant Purchase Agreement |
| 307 | 13.8 MB | Marked proof against 2_28 1_18 AM.pdf |
| 307 | 13.8 MB | Showing all changes that went in today.pdf |
| 307 | 13.8 MB | Full Clean.pdf |
| 304 | 7.9 MB | 635386_DRSA_Clean with Banners.pdf |

Cluster: SEC-style legal filings (Series E paperwork, S-1 redactions), long invoice batches, IPO / corporate finance documents.

### Filename pattern clusters (deferred PDFs)

| Pattern | Count | Notes |
|---|---:|---|
| pdf-other (no clear pattern) | 529 | the long tail |
| legal-contract (NDA, SOW, contract, agreement) | 37 | usually scanned-then-signed |
| report (analysis, report, summary) | 31 | mixed text + chart pages |
| form/tax (W9, 1099, application) | 17 | scanned forms |
| finance/receipt | 12 | invoices, statements |
| deck/presentation | 7 | exported slide PDFs |
| resume | 5 | |
| marketing | 2 | |
| scanned-from-device | 2 | only 2 explicit scanner names — the rest are scanned but named differently |

### Deferred images (21) — almost all giant screenshots

Heaviest 10 images by byte size:

| Type | Pixels | Bytes | Pattern |
|---|---|---:|---|
| png | **3,300 × 2,550** | 4.0 MB | menu print export |
| jpeg | 4,032 × 3,024 | 3.4 MB | iPhone photo |
| png | 2,400 × **14,125** | 2.8 MB | full-page report screencap |
| jpeg | 3,366 × 2,100 | 2.4 MB | photo |
| jpg | 4,032 × 3,024 | 1.9 MB | iPhone photo |
| png | **3,420 × 8,523** | 1.9 MB | chartio dashboard screenshot |
| png | **3,420 × 8,478** | 1.9 MB | chartio dashboard screenshot |
| png | **3,322 × 7,966** | 1.8 MB | chartio dashboard screenshot |
| png | 2,400 × **10,025** | 1.6 MB | "real-time-by-queue" dashboard |
| jpeg | 2,448 × 3,264 | 1.2 MB | photo |

**Pattern:** the cluster of 4+ chartio/dashboard PNG screenshots with absurdly tall dimensions (8,000+ pixels of vertical pixels) is a known pathological case for Surya — the layout model walks the image line-by-line and never returns. Same effect explains the multi-page receipt scans and the giant menu PDF.

### Deferred xlsx (17) — large operational spreadsheets

All deferred xlsx files are 2.4–10.9 MB — these are *real* business spreadsheets, not tiny ones:

- 7 are Postmates "operational-model 2014-2020 - <month> '<yy> CONFIDENTIAL" files (monthly model snapshots — multi-tab, formula-heavy)
- 2 datadumps (3.9 MB and 2.4 MB)
- 2 jobs/loss tracking tools
- 6 misc (pricing scenarios, relationship maps, voucher data)

**Hypothesis:** Docling can hit pathological slow-paths on multi-sheet spreadsheets with thousands of formulas / formatted ranges / pivot tables. Worth investigating before another retry pass.

### Why the slow tail exists (root cause classification for the 681)

| Pattern | Approx count | Mechanism |
|---|---:|---|
| Scanned/image-heavy mid-size PDFs | ~450 | Surya OCR slow on image pages; >900s on docs with 20-50 dense scanned pages |
| Page-heavy legal docs (>200 pages) | ~40 | Volume — 200+ pages × multi-second per-page processing exceeds budget |
| Pathologically tall PNG screenshots | ~10 | Surya layout model degenerate on extreme aspect ratios |
| Large multi-tab xlsx | 17 | Docling pathological path on real-world business spreadsheets |
| Mixed/normal PDFs (~50 page band) | ~120 | Borderline — most would complete at 1800s |
| Long mystery tail | ~50 | Various; would need per-file investigation |

---

## 3. Other failure classes (109 rows)

### surya-mps-bug (64) — wait for upstream

```
60 PDFs, 4 images. Median size 278 KB. Error:
  marker: index 1 is out of bounds: 0, range 0 to 1
  marker: index 8192 is out of bounds: 2, range 0 to 4560
```

These are residual MPS reduce-kernel bugs at PyTorch ops the `scripts/patches/surya-mps-fix.patch` does not cover (`argmax`, `gather`, `topk`, `take_along_dim`, etc.). PR #56 covered `.max().item()` only. They will refail on retry under the current patch.

**Action:** Track issue #75 (surya MPS bug filing). When a new surya release ships with broader MPS fixes, retry these — historically this class drops from ~6,500 → 64 on patched call sites alone.

### pdf-encrypted (25) — permanent skip

```
marker: Failed to load document (PDFium: Incorrect password error).
```

Median 258 KB. These are password-protected (legal counsel files, sealed filings, etc.). No path forward without per-file passwords.

**Action:** Reclassify these as `skipped: encrypted` (separate from `failed`) for honesty. They are not a fix candidate.

### hard-timeout-900s (13) — the slowest of the slow

All PDFs, 400 KB – 7.8 MB. Filenames suggest long redacted business filings (`635386ACR.pdf`, `Marked_against_prior_submission.pdf`, restaurant LLC registrations, etc.). Same root cause as deferred but already given a 900s shot.

**Action:** Group with the deferred-slow-tail for any future "huge timeout" retry pass.

### embed-failed (3) — trivial retry

| id | type | reason |
|---|---|---|
| 24612 | application/pdf | embed failed: 1/97 chunks |
| 35313 | text/plain | embed failed: 1/7 chunks |
| 35316 | text/plain | embed failed: 1/18 chunks |

Each lost a single chunk on Ollama. Re-running these three in isolation should succeed.

**Action:** Flip → pending, run a tiny retry. Cost: seconds.

### pptx-both-failed (3) — true edge

3 pptx files where Marker's PPTX→PDF leg failed (no weasyprint module) AND Docling rejected the file format. Likely corrupted .pptx or unsupported variants (Keynote-exported, very old PowerPoint).

**Action:** Spot-check the 3 files manually. Likely permanent skip.

### corrupt PNG (1)

```
id=8734  size=1163B  filename=core.views.api:JobFsmView.post.png
marker: cannot identify image file
```

A 1 KB PNG named after a Python function — almost certainly a colon-delimited path-encoded artifact, not a real image. Permanent skip.

---

## 4. The 24,034 skipped pool — what's there, what's recoverable

Listed by count, with assessment.

| ctype | Count | Status | Recoverable how |
|---|---:|---|---|
| audio/mpeg + audio/x-mpeg-3 + audio/* | **12,665** | unsupported by Marker | Whisper transcription pipeline (separate concern) |
| application/ics + text/calendar | **8,259** | unsupported by Marker | Trivial text parser (subject + date + description) — easy fix |
| application/octet-stream | 1,705 | mystery | Re-detect MIME from magic bytes → route through new pipeline |
| application/msword (.doc) | 602 | doc_legacy: needs LibreOffice | LibreOffice headless conversion (already noted as v2 work) |
| application/zip + x-zip-compressed | 270 | unsupported | Recursive unpack + re-process inner files |
| text/csv | 120 | unsupported by Marker | Trivial — read as text or parse as table |
| application/vnd.ms-excel (.xls) | 79 | xls_legacy: needs LibreOffice | Same as .doc |
| video/quicktime + video/mp4 | 49 | unsupported | Out of scope (or audio extraction → Whisper) |
| empty extraction (image/png + jpeg) | 38 | tried, no text | These ARE image attachments where Marker correctly produced nothing (clip art, logos, signatures). Honest skip. |
| application/x-iwork-keynote/pages | 27 | unsupported | Convert via Keynote/Pages export, niche |
| application/json + xml | 19 | unsupported | Trivial text dump |
| application/rtf | 14 | unsupported | LibreOffice or strip-and-text |
| application/pgp-signature + pkcs7-signature + x-x509-ca-cert | 35 | crypto material | Permanent skip |
| text/x-vcard + ms-tnef | 16 | unsupported | tnef is winmail.dat; could parse; vcards trivial text |
| image/svg+xml + image/bmp | 12 | unsupported by Marker | SVG → text via parsing; bmp → convert to png first |
| application/x-python | 5 | source code | Skip or trivially text-dump |
| application/force-download | 8 | mystery (HTTP header artifact) | Re-detect MIME |
| other low-count | ~10 | various | per-type assessment |

**Coverage gaps that are cheap to close:**
1. **csv (120)** + **json/xml (19)** + **rtf (14)** + **calendar (8,259)** — all readable as plain text with negligible engineering. **Total ~8,400 rows recoverable** if we add a "fallback: read-as-text" path for known text-shaped MIME types.
2. **doc_legacy (602)** + **xls_legacy (79)** — LibreOffice headless route, marked as "not implemented in v1." Real engineering, ~2 days.
3. **zip (270)** — recursive unpack: useful but introduces new edge cases (nested archives, malicious payloads). Smaller win.
4. **octet-stream (1,705)** — many of these are actually .pdf, .docx, etc. that arrived without a Content-Type. Magic-byte sniffing on ingest would route them to Marker/Docling correctly. Possibly large win.
5. **audio (12,665)** — Whisper integration is a major project (scope: GBs of weights, hours of compute). Out of scope for this report.

---

## 5. Recommended action paths (cost / yield)

Sorted by yield-per-effort.

### Cheap, high-yield

| Action | Yield | Effort | Risk |
|---|---:|---|---|
| Read-as-text fallback for csv / json / xml / rtf / ical / calendar / vcard | **~8,400 rows** | small (~1 day) | low — text is text |
| Re-detect octet-stream via magic bytes during ingest | up to ~1,000 rows | small (~½ day) | low — new ingest pass |
| Retry the 3 embed-failed | 3 rows | trivial | none |

### Medium

| Action | Yield | Effort | Risk |
|---|---:|---|---|
| LibreOffice route for .doc / .xls (#43?) | **681 rows** | medium (~2 days) | medium — same supervisor model needed |
| Investigate slow xlsx (17 deferred) — Docling pathological cases | 17 rows | medium | low — diagnostic work |
| Recursive zip unpack | 270 rows + nested files | medium | medium — security surface |

### Expensive but tractable

| Action | Yield | Effort | Risk |
|---|---:|---|---|
| Larger-budget retry pass on deferred (1800s, 2-3 nights of 8h drains) | **~300–500 rows** | low engineering, ~24h compute | none |
| Pre-filter giant aspect-ratio images, slice into chunks, re-OCR | 21 image rows | medium | low |
| Pre-detect scanned PDFs, route through faster Tesseract path before Surya | possibly 200+ rows | medium-large | medium — new dependency |

### Wait

| Action | Yield | Effort | When |
|---|---:|---|---|
| Surya release with broader MPS patches | 64 rows | none, just pin-bump | watch issue #75 |

### Permanent skip (reclassify as `skipped`, not `failed`)

| Action | Count |
|---|---:|
| pdf-encrypted | 25 |
| pptx-both-failed | 3 |
| corrupt PNG | 1 |
| **subtotal** | **29** |

This would shrink the failed pool from 790 → 761 with no compute, just honest accounting.

---

## 6. Suggested first move

If we want a single high-leverage next step, **the read-as-text fallback for csv/json/xml/rtf/ical/calendar** is the cleanest. It would:

- Move ~8,400 rows from `skipped` to `extracted` in a single drain pass (1–3 hours)
- Land as a tier-4 bundled PR (small surface, similar shape to Tier 1 Docling fallback)
- Open the door for searchable calendar invites — operationally valuable, especially for `unreplied()` and `top_contacts()` correlation

The **deferred-slow-tail (681)** is realistic but lower-leverage — heavy compute for moderate yield, and the surya MPS upstream fix may make some of these go faster on its own when it lands.

The **LibreOffice .doc / .xls route** is the next-most-strategic but is real engineering; it would be the right thing to ship as Tier 5.

---

## Appendix: numbers used in this report

Generated from the live database 2026-05-03 against `process_attachments` runs:
- `20260502T193246Z-e3c8d7fe` (Phase 1, 300s, 3h 52m, +1,155 extracted)
- `20260503T052113Z-4fe64e6d` (Phase 2, 900s, 8h 00m, +45 extracted, +13 failed, 681 reverted to pending then deferred)

Final: extracted=10,746, failed=790, skipped=24,034.
