"""Headless book generation: python cli.py --topic "..." --language fa --pages 12 --out book.pdf"""
import argparse
import shutil
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description="Generate a children's book without the web UI")
    ap.add_argument("--topic", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--language", default="en", choices=["en", "fa"])
    ap.add_argument("--age", default="6-8", choices=["3-5", "6-8", "9-12"])
    ap.add_argument("--pages", type=int, default=12)
    ap.add_argument("--style", default="watercolor")
    ap.add_argument("--author", default="")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default="book.pdf")
    a = ap.parse_args()

    from app.db import init_db, session_scope
    from app.layout.text import register_fonts
    from app.logging_setup import setup_logging
    from app.models import Project
    from app.pipeline.store import absf
    from app.pipeline.worker import Worker, enqueue
    from app.providers.registry import seed_builtins
    from app.schemas import BookSettings

    setup_logging("WARNING")
    init_db()
    register_fonts()
    with session_scope() as s:
        seed_builtins(s)
        st = BookSettings(topic=a.topic, title=a.title, language=a.language, age_group=a.age, page_count=a.pages,
                          illustration_style=a.style, author=a.author, seed=a.seed)
        p = Project(title=st.title, author=st.author, language=st.language, age_group=st.age_group, settings=st.model_dump())
        s.add(p)
        s.flush()
        pid = p.id
    enqueue(pid, "generate_book")
    w = Worker(max_books=1)
    w.drain(1800)
    w.stop(wait=True)
    with session_scope() as s:
        p = s.get(Project, pid)
        summary = (p.qa_report or {}).get("summary", {})
        print(f"status={p.status} errors={summary.get('errors')} warnings={summary.get('warnings')} project={pid}")
        f = absf(pid, p.pdf_file)
        if not f or not f.exists():
            print("no PDF produced:", p.error, file=sys.stderr)
            sys.exit(1)
        shutil.copy(f, a.out)
        print("PDF:", Path(a.out).resolve())
        sys.exit(0 if summary.get("errors") == 0 else 2)


if __name__ == "__main__":
    main()
