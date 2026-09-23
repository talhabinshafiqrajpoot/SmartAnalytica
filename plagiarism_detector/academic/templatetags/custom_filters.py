"""Template filters for SmartAnalytica."""

from django import template

register = template.Library()


@register.filter(name='get_by_key')
def get_by_key(dictionary, key):
    """Look up a dict value by key inside a template."""
    try:
        return dictionary.get(key)
    except AttributeError:
        return None


@register.filter(name='severity_band')
def severity_band(score):
    """Map a similarity percentage to the CSS severity class used in reports."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return 'none'
    if value >= 85:
        return 'critical'
    if value >= 65:
        return 'high'
    if value >= 45:
        return 'moderate'
    if value >= 25:
        return 'low'
    return 'none'


@register.filter(name='percent_of')
def percent_of(value, total):
    """Express ``value`` as a percentage of ``total``."""
    try:
        value, total = float(value), float(total)
    except (TypeError, ValueError):
        return 0
    return round(value / total * 100, 1) if total else 0
