"""Synthesis Agent — the reasoner: KG → scored, ranked weekly brief."""
from app.synthesis.agent import run_synthesis
from app.synthesis.convergence import ThemeConvergence, compute_convergence

__all__ = ["run_synthesis", "compute_convergence", "ThemeConvergence"]
