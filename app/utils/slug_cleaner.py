import re

def clean_slug(title: str, max_length: int = 50) -> str:
    """
    Normalizes a repository issue/PR title into a clean URL-friendly slug.
    Expected: Lowercase, alphanumeric characters and hyphens only, no trailing hyphens.
    """
    # BUG: Does not handle None or empty input (raises AttributeError)
    # BUG: Leaves leading/trailing hyphens intact
    slug = title.lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return slug[:max_length]