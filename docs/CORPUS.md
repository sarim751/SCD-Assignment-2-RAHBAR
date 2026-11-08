# RAHBAR Document Corpus Specification

**Corpus Source**: FAST-NUCES Public Academic Portal (`nu.edu.pk`)  
**Total Documents**: 8 documents (including HTML policy and prospectus tables)  
**Total Pages**: Over 140 pages of regulatory statutes, financial guidelines, and handbook policies.

---

## 1. Corpus Property Mapping

Every property required by Section 2 of the case study is intentionally mapped to reach and test each potential RAG failure mode:

| # | Required Property | Document(s) Providing Property | Technical Evidence and RAG Failure Mode Addressed |
|---|-------------------|--------------------------------|---------------------------------------------------|
| **1** | **At least 6 documents, 40+ pages in total** | 1. Academic Rules Handbook (Jun 2026) - 53 pages<br>2. Undergraduate Academic Rules (Aug 2023) - 12 pages<br>3. Student Handbook (Jun 2026) - 60 pages<br>4. Prospectus 2026: Academic Rules - 5 pages<br>5. Annual Report 2019-20 - 45 pages<br>6. Annual Report 2023-24 - 48 pages<br>7. Fee Structure & Grading HTML pages<br>8. Faculty Entrepreneurship Policy - 4 pages | **Scale & Diversity**: Totals over 220 combined pages. Sets a production-grade scale where naive prompt stuffing is physically impossible due to LLM context limits. |
| **2** | **Two versions of one policy (version pair)** | • Old: *Undergraduate Academic Rules (Aug 2023)* (`doc_version: "2023-08"`, `superseded: true`)<br>• Current: *Academic Rules Handbook (Jun 2026)* (`doc_version: "2026-06"`, `superseded: false`) | **Failure Mode E (Outdated Source Retrieval)**: The 2023 policy specifies probation removal criteria and repeating course rules that were revised in the 2026 edition. Without explicit `superseded: false` metadata filtering, vector similarity alone retrieves repealed clauses. |
| **3** | **Scanned, image-only PDF with no text layer** | *Faculty Entrepreneurship and Technical Start-ups Policy* (`27.pdf?20220530`) | **Failure Mode A (Bad Extraction)**: Verified using `pdffonts`: output yields an empty font list, confirming that pages are pure raster scanned images. Extraction requires an OCR fallback pipeline using `tesseract-ocr` and `pdf2image`. |
| **4** | **Document with structured tables carrying real information** | • *Fee Structure Page* (`https://www.nu.edu.pk/Admissions/FeeStructure`)<br>• *Grading System Page* (`https://www.nu.edu.pk/Student/Grading`)<br>• *Annual Report 2023-24* faculty distribution tables | **Failure Mode A (Mangled Table Layout)**: Tabular credit hour tuition rates, admission processing fees, refund timelines, and letter grade to grade point mappings (e.g. A = 4.00, B+ = 3.33). Linear text extraction jumbles columns across rows unless structured table extraction is applied. |
| **5** | **Long definitions split by fixed-size chunkers** | *Student Handbook (Jun 2026)*, Section 7: Comprehensive Generative AI Academic Conduct & Code of Ethics | **Failure Mode B (Poor Chunking / Split Context)**: Contains continuous multi-paragraph legal definitions outlining unauthorized assistance and sanctions spanning over 800 tokens. A fixed-size 100-token chunker cuts definitions in half, severing prohibited actions from their contextual exceptions. |
| **6** | **Deliberately absent topic** | *Hostel and Residential Accommodation Rules & Allotment Procedures* (The prospectus's *Transport and Hostel Facilities* PDF is deliberately excluded from `sources.yaml`) | **Failure Mode: Hallucination / Overconfidence (Section 3.4 Abstention)**: Students frequently query campus hostel curfews, mess dues, and room allotment policies. The corpus mentions that hostels exist but omits allotment rules. The system must abstain honestly (`answered_from_context: false`) rather than fabricating rules. |
| **7** | **Exact-term content: codes, thresholds, fees** | • Course Codes: `CS4032`, `CS2001`, `EE1002`<br>• Clause numbers: Section `1.21` (Attendance Requirement) vs Prospectus Clause `1`<br>• Academic thresholds: Probation threshold `CGPA < 2.00`, Warning threshold `SGPA < 2.00`<br>• Tuition fee: PKR 10,500 per credit hour | **Embeddings Blur Exact Numbers / Terms**: Dense vector embeddings project semantically similar numbers into close proximity (blurring 2.00 vs 2.50). Exact-term content demonstrates why hybrid retrieval or exact metadata anchoring is necessary. |

---

## 2. Ingestion and Provenance

- **Fetch Strategy**: Polymorphic retrieval via `corpus/fetch.py`. Respects server rate limits with a mandatory 2.0s delay between requests.
- **Verification**: Document integrity is verified through cryptographic SHA-256 hashes recorded in `corpus/MANIFEST.md`.
- **Storage**: Raw PDFs reside in `corpus/raw/` (strictly `.gitignore`d). Metadata mappings (including `doc_version` and `superseded` flags) are saved in `corpus/raw/_index.json` and transferred into ChromaDB chunk metadata during indexing.
