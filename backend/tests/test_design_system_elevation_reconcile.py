"""The website elevation token must never contradict the brief's separation
language. _reconcile_elevation_with_separation enforces that once the brief is
generated: a decisive separation drives the token, while "both" leaves the
sampler's prevalence answer untouched."""
from app.design_agent.design_system.models import DesignSystem
from app.design_agent.runner import _reconcile_elevation_with_separation


def test_borders_separation_forces_borders_elevation():
    ds = DesignSystem()
    ds.tokens.elevation_style = "shadows"
    ds.component_language.separation = "borders"
    _reconcile_elevation_with_separation(ds)
    assert ds.tokens.elevation_style == "borders"


def test_shadows_separation_forces_shadows_elevation():
    ds = DesignSystem()
    ds.tokens.elevation_style = "borders"
    ds.component_language.separation = "shadows"
    _reconcile_elevation_with_separation(ds)
    assert ds.tokens.elevation_style == "shadows"


def test_both_separation_keeps_sampler_elevation():
    # "both" subsumes either treatment, so the sampler's prevalence answer is
    # preserved in both directions.
    ds_borders = DesignSystem()
    ds_borders.tokens.elevation_style = "borders"
    ds_borders.component_language.separation = "both"
    _reconcile_elevation_with_separation(ds_borders)
    assert ds_borders.tokens.elevation_style == "borders"

    ds_shadows = DesignSystem()
    ds_shadows.tokens.elevation_style = "shadows"
    ds_shadows.component_language.separation = "both"
    _reconcile_elevation_with_separation(ds_shadows)
    assert ds_shadows.tokens.elevation_style == "shadows"
