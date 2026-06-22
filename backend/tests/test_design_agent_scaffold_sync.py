"""Scaffold-completeness guards (scaffold-completeness chore, 2026-05-30).

Two layers:

1. FAST drift guards (no node) — keep the THREE sources of truth aligned:
   on-disk `prototype-runtime/src/components/ui/*.tsx`  ==  autofixer
   SHADCN_REGISTRY  ⊇  the prompt's advertised inventory. The original bug was
   that the registry advertised components (incl. `sidebar`) the scaffold never
   shipped, so a converged generation that imported them passed the autofixer
   and then failed `vite build`. These tests fail at authorship time if anyone
   adds a component to one place but not the others.

2. REAL build (`@pytest.mark.integration`) — actually `vite build`s real
   shadcn-importing code through the REAL pipeline (NOT the mocked subprocess
   the other storage tests use). This closes the "real generated code was never
   built in CI" gap: every prior P1/P2 build test fed mocks/fixtures, which is
   how the scaffold incompleteness hid behind green CI.

   These RUN IN CI today: `.github/workflows/test-backend.yml` sets up node 20
   and installs `prototype-runtime/node_modules`, and the pytest step runs the
   whole suite with no marker exclusion — so `_NODE_OK` is True there. The
   `skipif` guard exists only for local/node-less lanes; it never silently
   disables the gate in CI.
"""
from __future__ import annotations

import re
import shutil

import pytest

import app.design_agent.storage as storage
from app.design_agent.autofixer_data import SHADCN_REGISTRY
from app.design_agent.prompts import SHADCN_COMPONENT_INVENTORY
from app.design_agent.storage import _RUNTIME_ROOT, vite_build

_UI_DIR = _RUNTIME_ROOT / "src" / "components" / "ui"


@pytest.fixture
def generous_vite_timeout(monkeypatch):
    """600s headroom so a slow-but-valid real `vite build` finishes on a contended
    CI runner (prod default 180s untouched). See test_design_agent_storage for the
    rationale; durable fix is a larger runner."""
    monkeypatch.setattr(
        storage.settings, "design_agent_vite_build_timeout_seconds", 600, raising=False
    )

# PascalCase → kebab is mechanical except where an acronym is fully capitalised.
_KEBAB_OVERRIDES = {"InputOTP": "input-otp"}


def _ondisk_components() -> set[str]:
    """The canonical set: what the scaffold actually vendors on disk."""
    return {p.stem for p in _UI_DIR.glob("*.tsx")}


def _pascal_to_kebab(name: str) -> str:
    if name in _KEBAB_OVERRIDES:
        return _KEBAB_OVERRIDES[name]
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def _prompt_inventory_components() -> set[str]:
    """Parse the PascalCase component names out of the prompt's inventory block
    (the comma list between the 'import from' line and the 'Icons:' line)."""
    block = SHADCN_COMPONENT_INVENTORY.split("Icons:")[0]
    block = block.split('<name>"):')[-1]
    names = re.findall(r"[A-Z][A-Za-z]+", block)
    return {_pascal_to_kebab(n) for n in names}


# ─── Layer 1: fast drift guards (no node) ────────────────────────────────────

def test_scaffold_ui_dir_is_populated():
    """Regression: the scaffold used to ship zero ui components."""
    on_disk = _ondisk_components()
    assert len(on_disk) >= 40, (
        f"prototype-runtime/src/components/ui only has {len(on_disk)} components; "
        "the scaffold-completeness chore vendored ~46. Did node_modules/components "
        "get wiped?"
    )


def test_registry_exactly_matches_ondisk():
    """SHADCN_REGISTRY MUST equal the on-disk inventory.

    If the registry has MORE → the autofixer passes an import that fails the
    build (the original bug). If it has FEWER → a vendored, buildable component
    is falsely flagged as hallucinated. Either way, fix this file AND the
    scaffold in the same change.
    """
    on_disk = _ondisk_components()
    registry = set(SHADCN_REGISTRY)
    assert registry == on_disk, (
        f"registry-only (advertised but NOT vendored — will fail vite build): "
        f"{sorted(registry - on_disk)}\n"
        f"ondisk-only (vendored but not allow-listed — falsely flagged): "
        f"{sorted(on_disk - registry)}"
    )


def test_prompt_inventory_subset_of_registry():
    """The prompt must never advertise a component that isn't allow-listed +
    on disk (advertising a phantom component is the drift that broke builds)."""
    advertised = _prompt_inventory_components()
    extra = advertised - set(SHADCN_REGISTRY)
    assert not extra, (
        f"prompt advertises components absent from SHADCN_REGISTRY / on-disk: "
        f"{sorted(extra)}"
    )


# ─── Layer 2: REAL build (integration; skipped without node) ─────────────────

_NODE_OK = shutil.which("npx") is not None and (_RUNTIME_ROOT / "node_modules").exists()
_skipif_no_node = pytest.mark.skipif(
    not _NODE_OK,
    reason="needs npx + prototype-runtime/node_modules (real vite build)",
)


def _skip_no_node(func):
    """Guard a real-`vite build` registry/prototype test. Applies the `real_build`
    marker so CI runs these in an isolated sequential step (no 3,200-test storm
    starving the build → no SIGKILL flake), plus the toolchain skipif for
    Python-only dev envs. See test_design_agent_storage.py for the rationale."""
    return pytest.mark.real_build(_skipif_no_node(func))


@pytest.mark.integration
@_skip_no_node
async def test_real_build_every_registry_component_resolves(generous_vite_timeout):
    """REAL vite build of an App that imports EVERY allow-listed component.

    Proves every component the autofixer permits actually resolves + compiles —
    the strongest scaffold guarantee. A missing `@/` alias, an un-vendored
    component, or a component with an uninstalled internal dep all fail here.
    """
    comps = sorted(SHADCN_REGISTRY)
    imports = "\n".join(
        f'import * as M{i} from "@/components/ui/{c}";' for i, c in enumerate(comps)
    )
    refs = ", ".join(f"M{i}" for i in range(len(comps)))
    app = (
        f"{imports}\n"
        f"const _all = [{refs}];\n"
        "export default function App() {\n"
        "  return <div data-n={_all.length}>ok</div>;\n"
        "}\n"
    )
    dist = await vite_build({"src/App.tsx": app})
    assert any(f.endswith(".js") for f in dist), dist
    assert any(f.endswith(".css") for f in dist), "Tailwind CSS asset not emitted"


@pytest.mark.integration
@_skip_no_node
async def test_real_build_representative_prototype(generous_vite_timeout):
    """REAL vite build of a representative generated prototype shape: multiple
    `@/components/ui/*` named imports + lucide-react + `cn` from @/lib/utils,
    matching what a real scaffold generation emits."""
    app = """\
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Plus, Check } from "lucide-react";
import { cn } from "@/lib/utils";

export default function App() {
  const [n, setN] = useState(0);
  return (
    <Card className={cn("m-4", n > 0 && "border-primary")}>
      <CardHeader><CardTitle>Demo <Badge>{n}</Badge></CardTitle></CardHeader>
      <CardContent>
        <Tabs defaultValue="a">
          <TabsList><TabsTrigger value="a">A</TabsTrigger></TabsList>
          <TabsContent value="a">
            <Label htmlFor="x">Name</Label>
            <Input id="x" />
            <Button onClick={() => setN(n + 1)}><Plus /> Add</Button>
            <Button variant="outline"><Check /> Done</Button>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
"""
    dist = await vite_build({"src/App.tsx": app})
    assert any(f.endswith(".js") for f in dist), dist
    assert any(f.endswith(".css") for f in dist), "Tailwind CSS asset not emitted"
