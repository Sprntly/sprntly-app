"""Corpus loader: reads the static markdown notes for a dataset and exposes them
as plain strings for inclusion in LLM prompts. No vector store yet — at this
size (single dataset, ~50KB) just feed everything in.
"""
from dataclasses import dataclass

from app.config import settings


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
        raise FileNotFoundError(f"Dataset {dataset!r} not found at {base}")
    docs: list[CorpusDoc] = []
    # Skip _reference/ which holds the answer key — never feed that to the LLM
    for p in sorted(base.glob("*.md")):
        if p.name.startswith("_"):
            continue
        docs.append(CorpusDoc(name=p.stem, path=str(p), text=p.read_text()))
    if not docs:
        raise RuntimeError(f"No corpus docs found for dataset {dataset!r}")
    return Corpus(dataset=dataset, docs=tuple(docs))


def load_prd_template() -> str:
    # Templates ship in-repo (TEMPLATE_DIR), separate from user uploads (DATA_DIR).
    return (settings.template_path / "sprntly_prd_template.md").read_text()


def load_prd_v2_template() -> str:
    return (settings.template_path / "sprntly_prd_v2_template.md").read_text()


def load_evidence_template() -> str:
    return (settings.template_path / "sprntly_evidence_template.md").read_text()
