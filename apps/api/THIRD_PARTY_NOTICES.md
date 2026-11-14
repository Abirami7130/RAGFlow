# Third-party notices

## PyMuPDF (`pymupdf`)

PDF text extraction in Phase 2C.2 uses [PyMuPDF](https://pymupdf.readthedocs.io/) (Artifex Software).

- **License:** GNU Affero General Public License v3.0 (AGPL-3.0)
- **Use in RAGFlow:** opening uploaded PDF bytes, reading text and geometry for canonical blocks (page and bbox locators). No OCR.

If you distribute this software, review AGPL-3.0 obligations for PyMuPDF and your deployment model.

## python-docx

DOCX text extraction in Phase 2C.3 uses [python-docx](https://python-docx.readthedocs.io/) (Steve Canny).

- **License:** MIT License
- **Use in RAGFlow:** opening uploaded DOCX bytes, walking the document body for headings, paragraphs, lists, and tables with `heading_path` locators. Headers, footers, comments, and text boxes are not extracted.

## python-pptx

PPTX text extraction in Phase 2C.4 uses [python-pptx](https://python-pptx.readthedocs.io/) (Steve Canny).

- **License:** MIT License
- **Use in RAGFlow:** opening uploaded PPTX bytes, walking slides for titles, body text, tables, and speaker notes with one-based `slide` locators. Pictures and charts are not extracted as text.

## openpyxl

XLSX text extraction in Phase 2C.6 uses [openpyxl](https://openpyxl.readthedocs.io/) (See AUTHORS file in the project).

- **License:** MIT License
- **Use in RAGFlow:** opening uploaded XLSX bytes, walking worksheets for row blocks with `sheet` and `row` locators. Formulas are not evaluated; cached values are read when present.

## httpx

URL ingestion in Phase 2C.8 uses [httpx](https://www.python-httpx.org/) for testable HTTP transports and redirect handling without automatic follow.

- **License:** BSD-3-Clause
- **Use in RAGFlow:** bounded web fetches with manual redirect hops and pinned DNS resolution in the worker (production path uses stdlib `http.client` for pin connect).

## FAISS (`faiss-cpu`)

Vector search in Phase 3G uses [FAISS](https://github.com/facebookresearch/faiss) via the `faiss-cpu` package (Meta Platforms).

- **License:** MIT License
- **Use in RAGFlow:** in-memory `IndexIDMap2` over `IndexFlatIP` for exact inner-product search on L2-normalized embedding vectors. On-disk `index.faiss` persistence is not enabled until a later milestone.

## Beautiful Soup

HTML-to-text extraction uses [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) with the stdlib `html.parser` backend.

- **License:** MIT License
- **Use in RAGFlow:** stripping non-content tags and emitting heading, paragraph, list, and code blocks with `url` and `heading_path` locators.