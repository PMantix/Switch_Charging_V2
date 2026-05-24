"""Extract text from all PDF datasheets into companion _text.txt files."""
import os
import sys
from PyPDF2 import PdfReader

DATASHEET_DIR = os.path.dirname(os.path.abspath(__file__))

def extract(pdf_path):
    """Extract text from a PDF, return (text, error)."""
    try:
        reader = PdfReader(pdf_path)
    except Exception as e:
        return None, f"PDF read error: {e}"

    pages = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
            pages.append(f"=== PAGE {i+1} ===\n{text}")
        except Exception as e:
            pages.append(f"=== PAGE {i+1} ===\n[extraction failed: {e}]")

    full_text = "\n\n".join(pages)
    if len(full_text.strip()) < 50:
        return full_text, "WARNING: very little text extracted (possibly image-only PDF)"
    return full_text, None

def main():
    pdfs = sorted(f for f in os.listdir(DATASHEET_DIR) if f.lower().endswith(".pdf"))
    print(f"Found {len(pdfs)} PDF files\n")

    results = {"ok": [], "warn": [], "fail": []}

    for pdf_name in pdfs:
        pdf_path = os.path.join(DATASHEET_DIR, pdf_name)
        base = os.path.splitext(pdf_name)[0]
        txt_path = os.path.join(DATASHEET_DIR, f"{base}_text.txt")

        text, error = extract(pdf_path)

        if text is None:
            print(f"FAIL  {pdf_name}: {error}")
            results["fail"].append((pdf_name, error))
            continue

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)

        char_count = len(text.strip())
        if error:
            print(f"WARN  {pdf_name}: {error} ({char_count} chars)")
            results["warn"].append((pdf_name, error))
        else:
            print(f"OK    {pdf_name} -> {base}_text.txt ({char_count} chars)")
            results["ok"].append(pdf_name)

    print(f"\n--- Summary ---")
    print(f"OK:   {len(results['ok'])}")
    print(f"WARN: {len(results['warn'])}")
    print(f"FAIL: {len(results['fail'])}")

    for name, err in results["fail"]:
        print(f"  FAIL: {name} — {err}")
    for name, err in results["warn"]:
        print(f"  WARN: {name} — {err}")

if __name__ == "__main__":
    main()
