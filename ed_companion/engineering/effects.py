def _effect_text(effect):
    prop = str(effect.get("Property") or "Unspecified effect").strip()
    value = str(effect.get("Effect") or "").strip()
    if value in {"✓", "Yes", "True"}:
        return prop
    return f"{prop} {value}".strip()


def describe_engineering_effect(name, effects, experimental=False):
    """Build an honest, readable guide from the bundled engineering stats."""
    effects = [row for row in (effects or []) if isinstance(row, dict)]
    benefits = [_effect_text(row) for row in effects if row.get("IsGood")]
    tradeoffs = [_effect_text(row) for row in effects if not row.get("IsGood")]
    if benefits and tradeoffs:
        summary = (
            f"{name} improves {', '.join(benefits)}. "
            f"Trade-off: {', '.join(tradeoffs)}."
        )
    elif benefits:
        summary = f"{name} provides {', '.join(benefits)}."
    elif tradeoffs:
        summary = f"{name} changes {', '.join(tradeoffs)}."
    else:
        summary = (
            f"{name} has no numerical stat changes stored in the local catalog. "
            "Review its ingredients and compatibility before pinning."
        )
    if experimental:
        summary = "Experimental effect: " + summary
    return {
        "summary": summary,
        "benefits": " · ".join(benefits) or "No listed benefit",
        "tradeoffs": " · ".join(tradeoffs) or "No listed drawback",
    }
