import pytest
from app.utils.slug_cleaner import clean_slug

def test_clean_slug_standard():
    assert clean_slug("Fix AST Parser Bug") == "fix-ast-parser-bug"

def test_clean_slug_strip_surrounding_hyphens():
    # Failing test: expects surrounding hyphens to be stripped
    assert clean_slug("---Feature: Dynamic Routing!!!---") == "feature-dynamic-routing"

def test_clean_slug_none_input():
    # Failing test: expects graceful fallback for None or non-string inputs
    assert clean_slug(None) == ""
