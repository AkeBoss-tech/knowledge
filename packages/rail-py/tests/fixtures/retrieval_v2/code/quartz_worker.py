def reconcile_quartz_token(token: str) -> str:
    """Normalize the benchmark-only QuartzToken symbol."""
    return token.strip().lower()

