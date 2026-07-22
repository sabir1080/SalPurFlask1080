"""Pagination utilities."""

from flask_paginate import Pagination, get_page_args


def get_paginated_results(query, per_page=10):
    """Paginate query results using Flask-Paginate.

    Args:
        query: SQLAlchemy query object
        per_page: Results per page (default 10)

    Returns:
        Tuple of (results_list, pagination_object)
    """
    page, _, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    total = query.count()
    results = query.offset(offset).limit(per_page).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework="bootstrap5")
    return results, pagination


__all__ = ['get_paginated_results']
