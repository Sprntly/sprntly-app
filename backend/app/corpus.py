"""Corpus loader: reads the static markdown notes for a dataset and exposes them
as plain strings for inclusion in LLM prompts. No vector store yet — at this
size (single dataset, ~50KB) just feed everything in.
"""
import logging
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CorpusDoc:
    name: str
    path: str
    text: str


@dataclass(frozen=True)
class Corpus:
    dataset: str
    docs: tuple[CorpusDoc, ...]

    def total_chars(self) -> int:
        return sum(len(d.text) for d in self.docs)

    def joined(self) -> str:
        parts = []
        for d in self.docs:
            parts.append(f"<<< SOURCE: {d.name} >>>\n{d.text}\n<<< END SOURCE >>>")
        return "\n\n".join(parts)


def load_corpus(dataset: str = "asurion") -> Corpus:
    base = settings.data_path / dataset
    if not base.exists():
        # No static corpus for this tenant — they rely on the knowledge graph.
        # Return an empty corpus so the KG-only answer path still works.
        logger.info("No corpus directory for dataset %r at %s; using empty corpus", dataset, base)
        return Corpus(dataset=dataset, docs=())
    docs: list[CorpusDoc] = []
    # Skip _reference/ which holds the answer key — never feed that to the LLM
    for p in sorted(base.glob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            # Fall back to latin-1 (never fails) for files with non-UTF-8 bytes
            # (e.g. smart quotes, em-dashes from pasted Word/PDF content).
            logger.warning("Corpus file %s is not valid UTF-8, falling back to latin-1", p.name)
            text = p.read_text(encoding="latin-1")
        docs.append(CorpusDoc(name=p.stem, path=str(p), text=text))
    if not docs:
        logger.warning("No corpus docs found for dataset %r; using empty corpus", dataset)
        return Corpus(dataset=dataset, docs=())
    return Corpus(dataset=dataset, docs=tuple(docs))


def load_prd_template() -> str:
    # Templates ship in-repo (TEMPLATE_DIR), separate from user uploads (DATA_DIR).
    return (settings.template_path / "sprntly_prd_template.md").read_text(encoding="utf-8")


def load_evidence_template() -> str:
    return (settings.template_path / "sprntly_evidence_template.md").read_text(encoding="utf-8")
