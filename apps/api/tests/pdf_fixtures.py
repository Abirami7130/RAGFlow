"""Helpers for building PDF bytes in tests."""

from __future__ import annotations

import io

import pymupdf


def build_pdf_bytes(*page_specs: str, default_fontsize: float = 11.0) -> bytes:
    """Each spec is newline-separated lines for one page (plain text at fontsize 11)."""
    doc = pymupdf.open()
    try:
        for spec in page_specs:
            page = doc.new_page()
            y = 72.0
            for line in spec.split("\n"):
                page.insert_text((72.0, y), line, fontsize=default_fontsize)
                y += 16.0
        return doc.tobytes()
    finally:
        doc.close()


def build_pdf_with_sizes(page_lines: list[tuple[str, float]]) -> bytes:
    doc = pymupdf.open()
    try:
        page = doc.new_page()
        y = 72.0
        for text, size in page_lines:
            page.insert_text((72.0, y), text, fontsize=size)
            y += max(18.0, size + 4.0)
        return doc.tobytes()
    finally:
        doc.close()


def build_multi_page_pdf(page_count: int, *, mark_first_page: bool = True) -> bytes:
    doc = pymupdf.open()
    try:
        for index in range(page_count):
            page = doc.new_page()
            if mark_first_page and index == 0:
                page.insert_text((72.0, 72.0), f"Page {index + 1}", fontsize=11)
        return doc.tobytes()
    finally:
        doc.close()


def build_encrypted_pdf(user_password: str = "secret") -> bytes:
    doc = pymupdf.open()
    try:
        page = doc.new_page()
        page.insert_text((72.0, 72.0), "Encrypted body", fontsize=11)
        buffer = io.BytesIO()
        doc.save(
            buffer,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=user_password,
            owner_pw="owner",
        )
        return buffer.getvalue()
    finally:
        doc.close()


def build_image_only_pdf() -> bytes:
    doc = pymupdf.open()
    try:
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64), 0)
        pix.clear_with(0)
        page.insert_image(pymupdf.Rect(72, 72, 200, 200), pixmap=pix)
        return doc.tobytes()
    finally:
        doc.close()