from django import template

register = template.Library()


@register.filter
def dict_get(d, key):
    """Return ``d[key]``, or ``None`` if missing.  Works with plain dicts."""
    try:
        return d[key]
    except (KeyError, TypeError):
        return None
